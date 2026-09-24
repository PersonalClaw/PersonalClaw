import { useCallback, useEffect, useState } from 'react'
import { notify } from '../../app/appSdk'
import { fvs } from '../../design/fontWeight'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Terminal as TermIcon, Plus, X, ChevronDown, Maximize2, Loader2 } from 'lucide-react'
import { spring } from '../../design/motion'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { api } from '../../lib/api'
import { TerminalView } from './TerminalView'
import type { TermTab } from './TerminalPage'
import { tabListKeys } from '../../lib/tabListKeys'
import { useResizablePanel } from '../../ui/useResizablePanel'

const HEIGHT_KEY = 'terminal-drawer-h'
const MIN_H = 160, MAX_FRAC = 0.85, DEF_H = 320

/** Quick terminal drawer — a slide-up bottom panel reachable from ANY page
 *  (toggle ⌘`, or the ⌘K command). Holds its own PTY session tab(s), shares the
 *  same backend session pool as the full Terminal page, and reuses TerminalView
 *  (so exit/reconnect/restart all work identically). "Open full" jumps to the
 *  Terminal page. Drag the top edge to resize; height persists. */
export function TerminalDrawer({ open, onClose, onOpenFull }: {
  open: boolean
  onClose: () => void
  onOpenFull: () => void
}) {
  const [tabs, setTabs] = useState<TermTab[]>([])
  const [active, setActive] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // Height + drag/keyboard handlers from the shared window-splitter primitive. This drawer is
  // the one adopter that needed the primitive's two generalisations: a DYNAMIC max (the ceiling
  // is `innerHeight × MAX_FRAC`, viewport-relative, which a static `max` can't express), and a
  // `storageKey` override — its key `terminal-drawer-h` predates the `-w` convention, so pinning
  // it here preserves every saved height. side 'bottom': the handle is the drawer's top edge and
  // dragging/ArrowUp grows it upward.
  const { width: height, onHandleDown, onHandleKey, min, max } = useResizablePanel(
    'terminal-drawer', { storageKey: HEIGHT_KEY, def: DEF_H, min: MIN_H, max: () => window.innerHeight * MAX_FRAC, side: 'bottom' })

  // Lazily open one session the first time the drawer is shown.
  useEffect(() => {
    if (open && tabs.length === 0 && !busy) void newSession()
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const newSession = useCallback(async () => {
    setBusy(true); setError('')
    try {
      const r = await api.createTerminal()
      setTabs((t) => [...t, { id: r.session_id, label: `Session ${t.length + 1}`, cwd: r.cwd, shell: r.shell }])
      setActive(r.session_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open a terminal session.')
    } finally { setBusy(false) }
  }, [])

  const closeSession = useCallback(async (id: string) => {
    // Same defect as TerminalPage's close: the tab disappeared whether or not the session did,
    // orphaning a live PTY behind a UI that no longer lists it.
    //
    // 🪤 NOT this component's `error` state, which would have been an INERT fix: it renders only in
    // the `tabs.length === 0` branch — unreachable here, because a failed close leaves the tab —
    // and its copy is hardcoded "Couldn't open a session", the wrong noun for a close. `notify` is
    // the channel the app already uses for a failed action on a surface that has no room for a line.
    try {
      await api.deleteTerminal(id)
    } catch (e) {
      notify(`Couldn't close the terminal session: ${String((e as Error)?.message || e)}`, 'error')
      return
    }
    setTabs((t) => {
      const next = t.filter((x) => x.id !== id)
      setActive((cur) => (cur === id ? (next.length ? next[next.length - 1].id : '') : cur))
      return next
    })
  }, [])

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-x-0 bottom-0 z-40 flex flex-col border-t border-outline-variant/50 bg-surface/97 shadow-2xl backdrop-blur-md"
          style={{ height }}
          initial={{ y: '100%' }} animate={{ y: 0 }} exit={{ y: '100%' }} transition={spring.spatialDefault}>
          {/* top drag-resize edge — WAI-ARIA window-splitter: focusable, arrow-key operable,
              reports its height. A horizontal separator (it resizes the vertical axis). */}
          <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="horizontal"
            tabIndex={0} aria-label="Resize terminal drawer — arrow keys to resize"
            aria-valuenow={Math.round(height)} aria-valuemin={min} aria-valuemax={Math.round(max)}
            className="group absolute inset-x-0 -top-1 z-10 h-2 cursor-ns-resize outline-none">
            <span className="absolute inset-x-0 top-1 h-px bg-transparent transition-colors group-hover:bg-primary/60 group-focus-visible:bg-primary" />
          </div>

          {/* header: tabs + actions */}
          <div className="flex items-center gap-1 border-b border-outline-variant/40 px-2 py-1.5">
            <TermIcon size={13} className="ml-1 shrink-0 text-on-surface-low" />
            <div className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto">
              {/* The drawer carries the SAME strip as `#/terminal`, so it had the same defect: tabs
                  announced as tabs with no owning tablist, no tab stop and no selected state. Fixed
                  the same way, in the same change — the reasoning is written out on the page's strip.
                  🪤 The tablist wraps ONLY the tabs: "New session" is a sibling, because a tablist
                  whose owned children are not all tabs trips `aria-required-children` (critical). */}
              <div role="tablist" aria-label="Terminal sessions" onKeyDown={tabListKeys((i) => setActive(tabs[i].id))}
                className="flex items-stretch gap-1">
              {tabs.map((t) => {
                const on = t.id === active
                return (
                  <div key={t.id} role="tab" aria-selected={on} tabIndex={on ? 0 : -1}
                    onClick={() => setActive(t.id)}
                    aria-keyshortcuts="Delete"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setActive(t.id); return }
                      if (e.key === 'Delete') { e.preventDefault(); closeSession(t.id) }
                    }}
                    className="group inline-flex h-7 shrink-0 cursor-pointer items-center gap-1 rounded-md pl-2.5 pr-1 text-[0.75rem] transition-colors"
                    style={on ? { background: 'var(--color-surface-high)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
                    <span className="mr-0.5">{t.label}</span>
                    {/* 24px hit box (SC 2.5.8 — the glyph is 11px), named per session. */}
                    <button type="button" onClick={(e) => { e.stopPropagation(); closeSession(t.id) }}
                      aria-label={`Close ${t.label}`} title="Close session"
                      className="grid size-6 -mr-0.5 shrink-0 place-items-center rounded opacity-50 hover:bg-surface-highest hover:opacity-100"><X size={11} /></button>
                  </div>
                )
              })}
              </div>
              <SquareIconButton label="New session" onClick={() => newSession()} className="shrink-0">
                {busy ? <Loader2 size={13} className="animate-spin" /> : <Plus size={14} />}
              </SquareIconButton>
            </div>
            <SquareIconButton icon={Maximize2} iconSize={13} label="Open full terminal" onClick={onOpenFull} className="shrink-0" />
            <SquareIconButton icon={ChevronDown} iconSize={15} label="Hide terminal (⌘`)" onClick={onClose} className="shrink-0" />
          </div>

          {/* body — keep each session mounted so scrollback + socket persist */}
          <div className="relative min-h-0 flex-1">
            {tabs.length === 0 ? (
              error ? (
                <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
                  <div className="text-on-surface text-[0.8125rem]" style={fvs(500)}>Couldn’t open a session</div>
                  <div className="max-w-md text-on-surface-low text-[0.8125rem]">{error}</div>
                  <button type="button" onClick={() => newSession()} disabled={busy}
                    className="inline-flex items-center gap-1.5 rounded-pill px-4 h-9 text-[0.8125rem] disabled:opacity-50"
                    style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
                    {busy ? <Loader2 size={14} className="animate-spin" /> : null} Retry
                  </button>
                </div>
              ) : (
                <div className="flex h-full items-center justify-center text-on-surface-low text-[0.8125rem]">Opening a session…</div>
              )
            ) : tabs.map((t) => (
              <div key={t.id} className="absolute inset-0 p-1.5" style={{ display: t.id === active ? 'block' : 'none' }}>
                <TerminalView tab={t} onExited={() => {}} onClose={() => closeSession(t.id)} />
              </div>
            ))}
          </div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}
