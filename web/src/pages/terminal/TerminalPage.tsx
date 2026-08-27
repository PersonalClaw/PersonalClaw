import { useCallback, useEffect, useState } from 'react'
import { Terminal as TermIcon, Plus, X, Loader2, SplitSquareHorizontal, Anchor } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { EmptyState } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { api } from '../../lib/api'
import { panesAfterClose, type PaneSelection } from './paneState'
import { TerminalView } from './TerminalView'
import { PageTitle } from '../../ui/PageTitle'
import { tabListKeys } from '../../lib/tabListKeys'

export interface TermTab { id: string; label: string; cwd?: string; shell?: string; custom?: boolean }

const LABELS_KEY = 'terminal-labels'  // persisted {id: customLabel}

function loadLabels(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(LABELS_KEY) || '{}') } catch { return {} }
}
function saveLabel(id: string, label: string) {
  const m = loadLabels(); m[id] = label; localStorage.setItem(LABELS_KEY, JSON.stringify(m))
}

/** Built-in PTY terminal. Tabs (each a session over WS /api/ws/terminal/{id}),
 *  restored on reload from the live session list, reconnect on drop, clear
 *  "exited" state with restart, rename, cwd/shell display, and a split view that
 *  tiles two sessions side-by-side. */
export function TerminalPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [tabs, setTabs] = useState<TermTab[]>([])
  // active session tab + split pane are URL-backed (?active / ?split) so a
  // refresh / shared link reopens the same session(s).
  const [activeRaw, setActiveQ] = useQueryParam(query, setQuery, 'active', '')
  const active = activeRaw
  const setActive = (id: string) => setActiveQ(id)
  const [splitRaw, setSplitQ] = useQueryParam(query, setQuery, 'split', '')
  const split = splitRaw || null
  const setSplit = (id: string | null) => setSplitQ(id ?? '')
  // Both panes in ONE patch. Closing a tab can move both, and writing them as two
  // sequential setQuery calls pushes an intermediate history entry holding the
  // half-updated pair — so Back would land the user on exactly the collapsed
  // ?active=X&split=X state the close is resolving.
  const setPanes = (p: PaneSelection) => setQuery({ active: p.active || null, split: p.split || null })
  const [busy, setBusy] = useState(false)
  // A refused create (e.g. the server's 3-session cap → 429 "Max 3 sessions")
  // must be visible on the page, not only in devtools — same contract as the
  // drawer. Cleared on the next attempt.
  const [error, setError] = useState('')
  const [restored, setRestored] = useState(false)
  // P25: opt-in tmux-backed persistence — when on, terminal sessions survive a
  // gateway restart (the shell lives in a detached tmux daemon, re-attached on
  // reconnect). null = still loading; the toggle is hidden until known.
  const [persist, setPersist] = useState<boolean | null>(null)
  useEffect(() => {
    api.personalclawConfig()
      .then((c) => setPersist(Boolean(c?.dashboard?.terminal?.persist)))
      .catch(() => setPersist(false))
  }, [])
  const togglePersist = () => {
    const next = !persist
    setPersist(next)  // optimistic
    api.patchConfig('dashboard.terminal.persist', next).catch(() => setPersist(!next))
  }

  // Restore live sessions on mount (survives page reload — the PTYs persist
  // server-side until the orphan reaper or an explicit close).
  useEffect(() => {
    let alive = true
    api.terminalSessions().then((r) => {
      if (!alive) return
      const labels = loadLabels()
      const live = (r.sessions || []).filter((s) => s.alive !== false)
      if (live.length) {
        const restoredTabs = live.map((s, i) => ({
          id: s.session_id, cwd: s.cwd, shell: s.shell,
          label: labels[s.session_id] || `Session ${i + 1}`,
          custom: !!labels[s.session_id],
        }))
        setTabs(restoredTabs)
        // honor a URL-pinned active tab if it still exists, else the first.
        const wanted = restoredTabs.find((t) => t.id === active)
        if (!wanted) setActive(restoredTabs[0].id)
      }
      setRestored(true)
    }).catch(() => setRestored(true))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const newSession = useCallback(async (intoSplit = false) => {
    setBusy(true); setError('')
    try {
      const r = await api.createTerminal()
      setTabs((t) => {
        const tab: TermTab = { id: r.session_id, label: `Session ${t.length + 1}`, cwd: r.cwd, shell: r.shell }
        return [...t, tab]
      })
      if (intoSplit) setSplit(r.session_id)
      else setActive(r.session_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open a terminal session.')
    } finally { setBusy(false) }
  }, [])

  const closeSession = useCallback(async (id: string) => {
    // 🔴 `catch(() => {})` then dropping the tab regardless told the user they had closed a terminal
    // while the PTY kept running server-side — a live process the UI no longer offers any way to
    // reach, let alone stop. A close that did not happen must not remove the tab; this surface
    // already owns an `error` state and renders it through InlineError, so the failure has a home.
    try {
      await api.deleteTerminal(id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not close the terminal session.')
      return
    }
    setError('')
    const next = tabs.filter((x) => x.id !== id)
    setTabs(next)
    setPanes(panesAfterClose(next.map((x) => x.id), id, { active, split }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabs, active, split])

  const rename = useCallback((id: string, label: string) => {
    saveLabel(id, label)
    setTabs((t) => t.map((x) => (x.id === id ? { ...x, label, custom: true } : x)))
  }, [])

  // The shell exited (or session died): drop it from the live registry view.
  // Keep the TAB so the user sees "exited" + can restart in place.
  const onExited = useCallback(() => { /* tab stays; TerminalView shows the overlay */ }, [])

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={<PageTitle>Terminal</PageTitle>}
        right={<HeaderActions>
          {persist !== null && (
            /* The label names WHAT THE CLICK DOES, and the explanation moves to `hint`. It used to be a
               status sentence — "Persistent sessions off — enable tmux-backed survival" — which made this
               the widest header control in the app by 3x (measured 387px against the widest sibling's
               129px: `Sync agents`), so it was the first thing the header's degradation ladder had to
               demote. Worse, `label` IS the accessible name and the icon-tier tooltip, so a screen-reader
               user heard the current state and an instruction instead of an action, and "Persistent
               sessions on" does not say whether clicking turns it off. `active` already carries the state
               visually; `Hide explorer` / `Show explorer` on the Files header is the same shape done
               right. `hint` is where the tmux detail belongs — it is what the overflow menu shows as its
               secondary line. */
            <HeaderControl icon={Anchor}
              label={persist ? 'Disable persistent sessions' : 'Enable persistent sessions'}
              hint={persist
                ? 'Sessions are tmux-backed, so they survive a restart.'
                : 'Sessions are lost on restart. Enabling keeps them alive with tmux.'}
              active={persist} priority="low" onClick={togglePersist} />
          )}
          {tabs.length > 0 && (
            <HeaderControl icon={SplitSquareHorizontal} label={split ? 'Close split' : 'Split right'}
              active={!!split}
              onClick={() => { if (split) setSplit(null); else if (tabs.length >= 2) setSplit(tabs.find((t) => t.id !== active)?.id ?? null); else newSession(true) }} />
          )}
          <HeaderControl icon={busy ? Loader2 : Plus} label="New terminal session" priority="primary" onClick={() => newSession(false)} />
        </HeaderActions>}
      />

      {error && <InlineError icon className="mx-2 mt-2" onDismiss={() => setError('')}>{error}</InlineError>}

      {tabs.length > 0 && (
        // 🔴 THE STRIP ANNOUNCED TABS IT COULD NOT REACH. Each chip carried `role="tab"` on a bare
        // `div` with no `tabIndex`, no `aria-selected` and no owning `role="tablist"` — measured with
        // two live sessions on `#/terminal`: 2 tabs, `tabindex` null on both, `aria-selected` null on
        // both, tablists 0, and 0 of 45 Tab presses ever landed on one. axe agreed once the strip
        // existed at all: `aria-required-parent` (critical) and `nested-interactive` (serious), ×2
        // each — findings four earlier audits of this surface missed because sessions were off, so
        // the strip was never rendered to scan.
        <div role="tablist" aria-label="Terminal sessions" onKeyDown={tabListKeys((i) => setActive(tabs[i].id))}
          className="flex items-stretch gap-1 overflow-x-auto border-b border-outline/40 px-2 pt-2">
          {tabs.map((t) => (
            <TermTabChip key={t.id} tab={t} active={t.id === active} inSplit={t.id === split}
              onSelect={() => setActive(t.id)} onClose={() => closeSession(t.id)} onRename={(l) => rename(t.id, l)} />
          ))}
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        {!restored ? (
          <div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>
        ) : tabs.length === 0 ? (
          <EmptyState icon={TermIcon} title="No terminal sessions" hint="Open a PTY session to run shell commands in your workspace." action={{ label: 'New session', onClick: () => newSession(false), icon: Plus }} />
        ) : (
          // keep every session mounted (hidden when not visible) so scrollback +
          // the live socket survive tab/split changes. Split shows active|split.
          <div className="absolute inset-0 flex gap-px p-2">
            {tabs.map((t) => {
              const visible = t.id === active || t.id === split
              const pane = t.id === active ? 'left' : t.id === split ? 'right' : null
              return (
                <div key={t.id} className="min-w-0 flex-1"
                  style={{ display: visible ? 'block' : 'none', order: pane === 'right' ? 2 : 1 }}>
                  <TerminalView tab={t} onExited={onExited} onClose={() => closeSession(t.id)} />
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

/** A renamable terminal tab chip. Double-click the label to rename. */
function TermTabChip({ tab, active, inSplit, onSelect, onClose, onRename }: {
  tab: TermTab; active: boolean; inSplit: boolean
  onSelect: () => void; onClose: () => void; onRename: (label: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [v, setV] = useState(tab.label)
  const on = active || inSplit
  const chrome = on
    ? { background: 'var(--color-surface-container)', color: 'var(--color-on-surface)', borderColor: 'var(--color-outline)' }
    : { color: 'var(--color-on-surface-low)', borderColor: 'transparent' }
  return (
    // 🪤 THE CLOSE BUTTON STAYS INSIDE THE TAB, DELIBERATELY, AND axe STILL REPORTS ONE
    // `nested-interactive`. Both alternatives were built and measured on this surface:
    //
    //   tab as a real button element, close control as a SIBLING in a presentational wrapper
    //     → `aria-required-parent` and `nested-interactive` both clear, and
    //       **`aria-required-children` (critical) appears**: a tablist's owned children must be
    //       tabs, and axe does not look through the wrapper. `aria-owns` on the tablist listing
    //       the tab ids does not help either — measured, it changed nothing, because the tabs are
    //       already DOM descendants. Net: two findings traded for one CRITICAL one.
    //   close glyph as a non-interactive <span>, closing via Delete only
    //     → axe fully green, but the close control leaves the accessibility tree entirely. That
    //       is optimising the scanner at the user's expense, so it was rejected.
    //
    // So the residual is named rather than chased: a closable tab is `nested-interactive` by
    // construction unless its close control stops being a control. What this cycle DID fix is
    // everything that kept the strip from being operable at all — see the tablist above.
    <div role="tab" aria-selected={on} tabIndex={on ? 0 : -1}
      onClick={onSelect} title={tab.cwd || tab.id}
      aria-keyshortcuts="F2 Delete"
      onKeyDown={(e) => {
        // A `div` carrying a role owns the keys a button element would have given for free.
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); return }
        // F2 renames — the platform convention, and the ONLY keyboard route to a rename that
        // was double-click-only. Delete closes, as the APG suggests for a closable tab.
        if (e.key === 'F2') { e.preventDefault(); setV(tab.label); setEditing(true) }
        if (e.key === 'Delete') { e.preventDefault(); onClose() }
      }}
      style={chrome}
      className="group inline-flex h-9 shrink-0 cursor-pointer items-center gap-1 rounded-t-lg border border-b-0 pl-3 pr-1.5 text-[0.8125rem] transition-colors"
    >
      <TermIcon size={13} className={inSplit && !active ? 'text-primary' : 'opacity-70'} />
      {editing ? (
        <input autoFocus aria-label={`Rename ${tab.label}`} value={v} onChange={(e) => setV(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={() => { setEditing(false); if (v.trim()) onRename(v.trim()) }}
          onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Enter') { setEditing(false); if (v.trim()) onRename(v.trim()) } if (e.key === 'Escape') { setEditing(false); setV(tab.label) } }}
          className="w-24 rounded bg-transparent text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
      ) : (
        <span className="mr-1" onDoubleClick={(e) => { e.stopPropagation(); setV(tab.label); setEditing(true) }}>{tab.label}</span>
      )}
      {/* 24px hit box around the 12px glyph (SC 2.5.8 — it was 16×16), and a name that says WHICH
          session: "Close session" was one name for every tab on the strip. `-mr-0.5` returns the
          added width so the strip does not reflow. */}
      <button type="button" onClick={(e) => { e.stopPropagation(); onClose() }}
        aria-label={`Close ${tab.label}`} title="Close session"
        className="grid size-6 -mr-0.5 shrink-0 place-items-center rounded opacity-50 hover:bg-surface-high hover:opacity-100"><X size={12} /></button>
    </div>
  )
}
