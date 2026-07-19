import { useEffect, useMemo, useRef, useState } from 'react'
import { Pause, Play, Trash2, Search, X, ArrowDownToLine } from 'lucide-react'
import { Surface } from '../../ui/Surface'
import { api } from '../../lib/api'

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
    <div className="flex flex-col gap-l">
      {/* ── Runtime log level ── */}
      <section>
        <h2 className="text-on-surface text-[1rem] mb-1" style={{ fontVariationSettings: '"wght" 600' }}>Backend log level</h2>
        <p className="text-on-surface-low text-[0.8125rem] mb-m">
          Change how verbose the gateway logs are, live. Persists across restarts.
        </p>
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
            {level && <span className="text-on-surface-low text-[0.78rem]">Current: <strong className="text-on-surface-var">{level}</strong></span>}
          </div>
        </Surface>
      </section>

      {/* ── Live log stream ── */}
      <section>
        <div className="flex items-center justify-between mb-m gap-s flex-wrap">
          <div>
            <h2 className="text-on-surface text-[1rem]" style={{ fontVariationSettings: '"wght" 600' }}>Live logs</h2>
            <p className="text-on-surface-low text-[0.8125rem] mt-0.5 flex items-center gap-1.5">
              <span className="inline-block size-1.5 rounded-pill" style={{ background: connected ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }} />
              {connected ? 'Streaming' : 'Connecting…'} · {visible.length} shown{entries.length !== visible.length ? ` of ${entries.length}` : ''}
            </p>
          </div>
          <div className="flex items-center gap-s">
            {/* min-level floor for the VIEW (distinct from the backend level) */}
            <div className="inline-flex rounded-pill bg-surface-container p-1">
              {LEVELS.map((l) => {
                const on = minLevel === l
                return (
                  <button key={l} onClick={() => setMinLevel(l)} title={`Show ${l} and above`}
                    className="rounded-pill px-2.5 h-7 text-[0.72rem] transition-colors"
                    style={on ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
                    {l}
                  </button>
                )
              })}
            </div>
            <button onClick={() => setAutoscroll((v) => !v)} title={autoscroll ? 'Autoscroll on' : 'Autoscroll off'}
              className="inline-flex items-center justify-center size-8 rounded-lg transition-colors"
              style={autoscroll ? { background: 'color-mix(in srgb, var(--color-primary) 18%, transparent)', color: 'var(--color-primary)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
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
        </div>

        {/* search within the tail */}
        <div className="relative mb-s">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-low" />
          <input value={filter} onChange={(e) => setFilter(e.target.value)} type="search" placeholder="Filter log lines…"
            className="h-9 w-full rounded-lg bg-surface-high pl-9 pr-9 text-[0.85rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          {filter && (
            <button type="button" onClick={() => setFilter('')} aria-label="Clear filter"
              className="absolute right-2.5 top-1/2 inline-flex size-6 -translate-y-1/2 items-center justify-center rounded-full text-on-surface-low hover:bg-surface-highest hover:text-on-surface"><X size={14} /></button>
          )}
        </div>

        <Surface tone="container" radius="lg" className="p-0 overflow-hidden">
          <div ref={scrollRef} className="max-h-[60vh] min-h-[240px] overflow-y-auto p-3 font-mono text-[0.75rem] leading-relaxed"
            style={{ fontFamily: '"JetBrains Mono", ui-monospace, monospace' }}>
            {visible.length === 0 ? (
              <div className="py-8 text-center text-on-surface-low text-[0.8rem]" style={{ fontFamily: 'var(--font-sans)' }}>
                {paused ? 'Paused — resume to see live logs.' : entries.length === 0 ? 'Waiting for log entries…' : 'No lines match the current filter.'}
              </div>
            ) : (
              visible.map((e) => (
                <div key={e.key} className="whitespace-pre-wrap break-words border-b border-outline-variant/20 py-0.5">
                  <span style={{ color: LEVEL_TONE[e.level] ?? 'var(--color-on-surface-low)', fontVariationSettings: '"wght" 600' }}>{e.level.padEnd(7)}</span>
                  <span className="text-on-surface-var"> {e.msg}</span>
                </div>
              ))
            )}
          </div>
        </Surface>
      </section>
    </div>
  )
}
