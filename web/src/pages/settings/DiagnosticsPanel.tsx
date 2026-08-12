import { useEffect, useMemo, useRef, useState } from 'react'
import { Pause, Play, Trash2, ArrowDownToLine } from 'lucide-react'
import { Surface } from '../../ui/Surface'
import { SearchField } from '../../ui/SearchField'
import { withWeight } from '../../design/fontWeight'
import { api } from '../../lib/api'
import { accentChip } from '../../design/accent'
import { PanelHeader, Section } from './settingsUI'

/** A single streamed log entry (backend emits {level, msg} JSON per SSE frame,
 *  msg already formatted as "<ts> <LEVEL> <logger>: <message>"). */
interface LogEntry { level: string; msg: string; key: number }

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'] as const
type Level = (typeof LEVELS)[number]

// Rank for "this entry is at or above the selected floor" filtering.
const RANK: Record<string, number> = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40 }

const LEVEL_TONE: Record<string, string> = {
  DEBUG: 'var(--color-on-surface-low)',
  INFO: 'var(--color-info)',
  WARNING: 'var(--color-warn)',
  ERROR: 'var(--color-danger)',
}

const MAX_ENTRIES = 2000  // client-side cap so a long session can't grow unbounded

/** Settings → Diagnostics: a live tail of the backend log stream (SSE, with
 *  ring-buffer replay on connect) plus the runtime log-level control. The only
 *  in-app window into gateway logs — previously this needed terminal access. */
export function DiagnosticsPanel() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [paused, setPaused] = useState(false)
  const [filter, setFilter] = useState('')
  const [minLevel, setMinLevel] = useState<Level>('DEBUG')
  const [level, setLevel] = useState<string>('')      // backend logger level
  const [levelBusy, setLevelBusy] = useState(false)
  const [connected, setConnected] = useState(false)
  const [autoscroll, setAutoscroll] = useState(true)

  const pausedRef = useRef(paused)
  pausedRef.current = paused
  const keyRef = useRef(0)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Load the current backend logger level once.
  useEffect(() => { api.logLevel().then(setLevel).catch(() => {}) }, [])

  // Subscribe to the live log SSE (ring-buffer replay + live tail). While paused,
  // frames are dropped (not buffered) — resuming shows the live tail, not a backlog.
  useEffect(() => {
    let es: EventSource | null = null
    try { es = new EventSource(api.logsUrl(300)) } catch { return }
    es.onopen = () => setConnected(true)
    es.onmessage = (e) => {
      if (pausedRef.current) return
      try {
        const d = JSON.parse(e.data) as { level?: string; msg?: string }
        if (typeof d.msg !== 'string') return
        const entry: LogEntry = { level: (d.level || 'INFO').toUpperCase(), msg: d.msg, key: keyRef.current++ }
        setEntries((prev) => {
          const next = prev.length >= MAX_ENTRIES ? prev.slice(prev.length - MAX_ENTRIES + 1) : prev
          return [...next, entry]
        })
      } catch { /* ignore malformed frame */ }
    }
    es.onerror = () => setConnected(false)
    return () => { es?.close() }
  }, [])

  // Autoscroll to the newest entry when enabled + not paused.
  useEffect(() => {
    if (autoscroll && !paused && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries, autoscroll, paused])

  const q = filter.trim().toLowerCase()
  const visible = useMemo(
    () => entries.filter((e) => RANK[e.level] >= RANK[minLevel] && (q === '' || e.msg.toLowerCase().includes(q))),
    [entries, minLevel, q],
  )

  const changeLevel = async (l: Level) => {
    if (levelBusy) return
    setLevelBusy(true)
    try { const r = await api.setLogLevel(l); setLevel(r.level) } catch { /* leave prior */ }
    finally { setLevelBusy(false) }
  }

  return (
    <div>
      {/* Every other settings sub-route opens with `PanelHeader` — its title is the page's `h1`. These two
          panels started straight at their first `<section>`, so `#/settings/design` measured **h1s=0** and
          its outline began at `h2: Color scheme` with nothing above it, while all 26 siblings had exactly
          one. The PanelHeader-as-h1 change gave every panel that USES it an h1; it could not reach a panel that never rendered one. */}
      <PanelHeader
        title="Diagnostics"
        hint="A live tail of the gateway's own log, plus the backend log level. Nothing here changes your data — the level is the only setting it writes, and it persists across restarts."
      />

      {/* ── Runtime log level ── */}
      {/* `Section` like the other 23 panels: this page and Design were the pair still rendering
          section titles at `text-[1.0625rem]`, 17px beside a sibling page's 15px. */}
      <Section title="Backend log level" hint="Change how verbose the gateway logs are, live. Persists across restarts.">
        <Surface tone="container" radius="lg" className="px-l py-m">
          <div className="flex items-center gap-s">
            <div className="inline-flex rounded-pill bg-surface-container p-1">
              {LEVELS.map((l) => {
                const on = level === l
                return (
                  <button key={l} onClick={() => changeLevel(l)} disabled={levelBusy}
                    className="rounded-pill px-m h-8 text-[0.8125rem] transition-colors disabled:opacity-60"
                    style={on ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
                    {l}
                  </button>
                )
              })}
            </div>
            {level && <span className="text-on-surface-low text-[0.75rem]">Current: <strong className="text-on-surface-var">{level}</strong></span>}
          </div>
        </Surface>
      </Section>

      {/* ── Live log stream ── */}
      <Section
        title="Live logs"
        hint={
          <span className="flex items-center gap-1.5">
            <span className="inline-block size-1.5 rounded-pill" style={{ background: connected ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }} />
            {connected ? 'Streaming' : 'Connecting…'} · {visible.length} shown{entries.length !== visible.length ? ` of ${entries.length}` : ''}
          </span>
        }
        right={
          <div className="flex items-center gap-s">
            {/* min-level floor for the VIEW (distinct from the backend level) */}
            <div className="inline-flex rounded-pill bg-surface-container p-1">
              {LEVELS.map((l) => {
                const on = minLevel === l
                return (
                  <button key={l} onClick={() => setMinLevel(l)} title={`Show ${l} and above`}
                    className="rounded-pill px-2.5 h-7 text-[0.75rem] transition-colors"
                    style={on ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
                    {l}
                  </button>
                )
              })}
            </div>
            <button onClick={() => setAutoscroll((v) => !v)} title={autoscroll ? 'Autoscroll on' : 'Autoscroll off'}
              className="inline-flex items-center justify-center size-8 rounded-lg transition-colors"
              style={autoscroll ? accentChip : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
              <ArrowDownToLine size={15} />
            </button>
            <button onClick={() => setPaused((v) => !v)} title={paused ? 'Resume' : 'Pause'}
              className="inline-flex items-center justify-center size-8 rounded-lg bg-surface-high text-on-surface-var transition-colors hover:bg-surface-highest">
              {paused ? <Play size={15} /> : <Pause size={15} />}
            </button>
            <button onClick={() => setEntries([])} title="Clear"
              className="inline-flex items-center justify-center size-8 rounded-lg bg-surface-high text-on-surface-var transition-colors hover:text-danger">
              <Trash2 size={15} />
            </button>
          </div>
        }
      >

        {/* search within the tail */}
        <div className="mb-s">
          <SearchField value={filter} onChange={setFilter} size="md" placeholder="Filter log lines…"
            ariaLabel="Filter log lines" />
        </div>

        <Surface tone="container" radius="lg" className="p-0 overflow-hidden">
          {/* The log lines are plain text, so this region has NO focusable descendant: a
              keyboard user could not scroll it at all (WCAG 2.1.1; axe
              scrollable-region-focusable, serious). Measured 1743px of output hidden below
              the fold. A tab stop hands scrolling to the browser; role+label keep it
              announced as a named container. Same resolution as the kanban columns, the
              shell denylist and the inbox procedure. */}
          <div ref={scrollRef} className="max-h-[60vh] min-h-[240px] overflow-y-auto p-3 font-mono text-[0.75rem] leading-relaxed"
            tabIndex={0} role="group" aria-label="Log output"
            style={{ fontFamily: '"JetBrains Mono", ui-monospace, monospace' }}>
            {visible.length === 0 ? (
              <div className="py-8 text-center text-on-surface-low text-[0.8125rem]" style={{ fontFamily: 'var(--font-sans)' }}>
                {paused ? 'Paused — resume to see live logs.' : entries.length === 0 ? 'Waiting for log entries…' : 'No lines match the current filter.'}
              </div>
            ) : (
              visible.map((e) => (
                <div key={e.key} className="whitespace-pre-wrap break-words border-b border-outline-variant/20 py-0.5">
                  <span style={withWeight({ color: LEVEL_TONE[e.level] ?? 'var(--color-on-surface-low)' }, 600)}>{e.level.padEnd(7)}</span>
                  <span className="text-on-surface-var"> {e.msg}</span>
                </div>
              ))
            )}
          </div>
        </Surface>
      </Section>
    </div>
  )
}
