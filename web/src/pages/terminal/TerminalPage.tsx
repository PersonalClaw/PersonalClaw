import { useCallback, useEffect, useState } from 'react'
import { Terminal as TermIcon, Plus, X, Loader2, SplitSquareHorizontal, Anchor } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { EmptyState } from '../../ui/ListScaffold'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { api } from '../../lib/api'
import { TerminalView } from './TerminalView'

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
  const [busy, setBusy] = useState(false)
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
    setBusy(true)
    try {
      const r = await api.createTerminal()
      setTabs((t) => {
        const tab: TermTab = { id: r.session_id, label: `Session ${t.length + 1}`, cwd: r.cwd, shell: r.shell }
        return [...t, tab]
      })
      if (intoSplit) setSplit(r.session_id)
      else setActive(r.session_id)
    } catch { /* ignore */ } finally { setBusy(false) }
  }, [])

  const closeSession = useCallback(async (id: string) => {
    await api.deleteTerminal(id).catch(() => {})
    const next = tabs.filter((x) => x.id !== id)
    setTabs(next)
    if (active === id) setActive(next.length ? next[next.length - 1].id : '')
    if (split === id) setSplit(null)
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
        left={<span data-type="title-l" className="text-on-surface">Terminal</span>}
        right={<HeaderActions>
          {persist !== null && (
            <HeaderControl icon={Anchor}
              label={persist ? 'Persistent sessions on — survive a restart (tmux)' : 'Persistent sessions off — enable tmux-backed survival'}
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

      {tabs.length > 0 && (
        <div className="flex items-stretch gap-1 overflow-x-auto border-b border-outline/40 px-2 pt-2">
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
  return (
    <div role="tab" onClick={onSelect} title={tab.cwd || tab.id}
      className="group inline-flex h-9 shrink-0 cursor-pointer items-center gap-2 rounded-t-lg border border-b-0 pl-3 pr-2 text-[0.8125rem] transition-colors"
      style={on ? { background: 'var(--color-surface-container)', color: 'var(--color-on-surface)', borderColor: 'var(--color-outline)' } : { color: 'var(--color-on-surface-low)', borderColor: 'transparent' }}>
      <TermIcon size={13} className={inSplit && !active ? 'text-primary' : 'opacity-70'} />
      {editing ? (
        <input autoFocus value={v} onChange={(e) => setV(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={() => { setEditing(false); if (v.trim()) onRename(v.trim()) }}
          onKeyDown={(e) => { if (e.key === 'Enter') { setEditing(false); if (v.trim()) onRename(v.trim()) } if (e.key === 'Escape') { setEditing(false); setV(tab.label) } }}
          className="w-24 bg-transparent text-on-surface outline-none" />
      ) : (
        <span onDoubleClick={(e) => { e.stopPropagation(); setV(tab.label); setEditing(true) }}>{tab.label}</span>
      )}
      <button onClick={(e) => { e.stopPropagation(); onClose() }} aria-label="Close session" className="rounded p-0.5 opacity-50 hover:bg-surface-high hover:opacity-100"><X size={12} /></button>
    </div>
  )
}
