import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Terminal as TermIcon, Plus, X, ChevronDown, Maximize2, Loader2 } from 'lucide-react'
import { spring } from '../../design/motion'
import { api } from '../../lib/api'
import { TerminalView } from './TerminalView'
import type { TermTab } from './TerminalPage'

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
  const [height, setHeight] = useState<number>(() => {
    const v = Number(localStorage.getItem(HEIGHT_KEY))
    return v >= MIN_H ? v : DEF_H
  })
  useEffect(() => { localStorage.setItem(HEIGHT_KEY, String(height)) }, [height])

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
    await api.deleteTerminal(id).catch(() => {})
    setTabs((t) => {
      const next = t.filter((x) => x.id !== id)
      setActive((cur) => (cur === id ? (next.length ? next[next.length - 1].id : '') : cur))
      return next
    })
  }, [])

  const onResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    const sy = e.clientY, sh = height
    const move = (ev: PointerEvent) => setHeight(Math.max(MIN_H, Math.min(window.innerHeight * MAX_FRAC, sh + (sy - ev.clientY))))
    const up = () => { document.body.style.userSelect = ''; window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    document.body.style.userSelect = 'none'
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }, [height])

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-x-0 bottom-0 z-40 flex flex-col border-t border-outline-variant/50 bg-surface/97 shadow-2xl backdrop-blur-md"
          style={{ height }}
          initial={{ y: '100%' }} animate={{ y: 0 }} exit={{ y: '100%' }} transition={spring.spatialDefault}>
          {/* top drag-resize edge */}
          <div onPointerDown={onResize} className="group absolute inset-x-0 -top-1 z-10 h-2 cursor-ns-resize">
            <span className="absolute inset-x-0 top-1 h-px bg-transparent transition-colors group-hover:bg-primary/60" />
          </div>

          {/* header: tabs + actions */}
          <div className="flex items-center gap-1 border-b border-outline-variant/40 px-2 py-1.5">
            <TermIcon size={13} className="ml-1 shrink-0 text-on-surface-low" />
            <div className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto">
              {tabs.map((t) => {
                const on = t.id === active
                return (
                  <div key={t.id} role="tab" onClick={() => setActive(t.id)}
                    className="group inline-flex h-7 shrink-0 cursor-pointer items-center gap-1.5 rounded-md pl-2.5 pr-1.5 text-[0.75rem] transition-colors"
                    style={on ? { background: 'var(--color-surface-high)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
                    {t.label}
                    <button onClick={(e) => { e.stopPropagation(); closeSession(t.id) }} aria-label="Close session" className="rounded p-0.5 opacity-50 hover:bg-surface-highest hover:opacity-100"><X size={11} /></button>
                  </div>
                )
              })}
              <button type="button" onClick={() => newSession()} aria-label="New session" title="New session"
                className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface">
                {busy ? <Loader2 size={13} className="animate-spin" /> : <Plus size={14} />}
              </button>
            </div>
            <button type="button" onClick={onOpenFull} aria-label="Open full terminal" title="Open in full Terminal page"
              className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface"><Maximize2 size={13} /></button>
            <button type="button" onClick={onClose} aria-label="Hide terminal (⌘`)" title="Hide (⌘`)"
              className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface"><ChevronDown size={15} /></button>
          </div>

          {/* body — keep each session mounted so scrollback + socket persist */}
          <div className="relative min-h-0 flex-1">
            {tabs.length === 0 ? (
              error ? (
                <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
                  <div className="text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 500' }}>Couldn’t open a session</div>
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
