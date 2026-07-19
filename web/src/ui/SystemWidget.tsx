import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Cpu, MemoryStick, HardDrive, Activity, Zap, Network, Boxes, ShieldCheck, Server, Bot, X, Loader2, RotateCw, DownloadCloud, AlertTriangle } from 'lucide-react'
import { api, type SystemInfo, type AuthStatus, type SpawnedAgent } from '../lib/api'
import { useVisiblePoll } from '../lib/useVisiblePoll'
import { spring, stagger, listItemEnter } from '../design/motion'

/** Live system + auth health — the app shell's top-right corner dot.
 *  - Collapsed (resting): a single connectivity dot. GREEN + pulsing = gateway
 *    live/connected; ORANGE = connecting / status unknown; RED = disconnected /
 *    not found. Status is driven live by the /api/system poll succeeding vs
 *    failing.
 *  - Click the dot → the full card popover (CPU/mem/disk/GPU bars, network,
 *    processes, agent activity, and auth merged in) — unchanged. Click outside
 *    to close. Polls /api/system + /api/auth-status.
 *  Deliberately omits Ollama-specific status (vendor leakage). */
type ConnStatus = 'connected' | 'connecting' | 'disconnected'

export function SystemWidget() {
  const [sys, setSys] = useState<SystemInfo | null>(null)
  const [auth, setAuth] = useState<AuthStatus | null>(null)
  const [status, setStatus] = useState<ConnStatus>('connecting')
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const cardRef = useRef<HTMLDivElement>(null)

  // Anchor the (portaled) card to the trigger, opening DOWN + LEFT from the
  // top-right corner (right-aligned to the trigger's right edge).
  const openCard = () => {
    const r = triggerRef.current?.getBoundingClientRect()
    if (r) setPos({ top: r.bottom + 8, right: Math.max(8, window.innerWidth - r.right) })
    setOpen((v) => !v)
  }

  // A successful /api/system poll = the gateway answered → connected. A failure
  // (network error, gateway down/restarting) → disconnected. This is the live
  // connectivity signal the dot reflects; auth is secondary detail for the card.
  useVisiblePoll(() => {
    api.system().then((s) => { setSys(s); setStatus('connected') }).catch(() => setStatus('disconnected'))
    api.authStatus().then(setAuth).catch(() => {})
  }, 5000)

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (ref.current?.contains(t) || cardRef.current?.contains(t)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const cpu = sys?.cpu_pct ?? 0
  const memPct = sys ? (sys.mem_used_gb / sys.mem_total_gb) * 100 : 0

  // Dot color = gateway CONNECTIVITY (not CPU/mem pressure). Green pulses outward
  // while live; orange while first connecting / unknown; red when the poll fails.
  const dotColor = status === 'connected'
    ? 'var(--color-success)'
    : status === 'connecting'
      ? 'var(--color-warning)'
      : 'var(--color-error)'
  const statusLabel = status === 'connected' ? 'Gateway connected' : status === 'connecting' ? 'Connecting to gateway…' : 'Gateway disconnected'

  return (
    <div ref={ref} className="relative grid place-items-center">
      <button ref={triggerRef} type="button" onClick={openCard}
        aria-label={`System status — ${statusLabel}`} title={statusLabel}
        className="grid size-7 place-items-center rounded-pill transition-colors"
        style={open ? { background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)' } : undefined}>
        <span className="relative grid size-3 place-items-center">
          {/* solid core */}
          <span className="size-2 rounded-full" style={{ background: dotColor }} />
          {/* outward pulse ring — only while connected (a live "breathing" beacon).
              Pulse cadence is user-configurable (Design → Motion → "Status dot
              pulse") via the status-pulse animation's --status-pulse-speed. */}
          {status === 'connected' && (
            <span className="status-pulse absolute inline-flex size-2 rounded-full" style={{ background: dotColor }} />
          )}
        </span>
      </button>

      {createPortal(
        <AnimatePresence>
          {open && pos && (
            <motion.div ref={cardRef}
              initial={{ opacity: 0, y: -8, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -8, scale: 0.98 }}
              transition={spring.spatialFast}
              // Portaled to <body> with FIXED coords anchored to the trigger, so
              // no overflow/stacking context can clip it. Opens DOWN + LEFT from
              // the top-right shell corner, right-aligned to the dot.
              className="fixed z-[60] w-72 rounded-2xl border border-outline/40 bg-surface-container p-4"
              style={{ top: pos.top, right: pos.right, borderRadius: 'var(--radius-xl)', boxShadow: 'var(--shadow-lift)' }}>
            {sys ? (
              // Connected: the full system card — unchanged (identical expanded view).
              <>
                <div className="mb-3 flex items-center gap-2">
                  <Server size={14} className="text-on-surface-low" />
                  <span className="flex-1 truncate text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 600' }}>{sys.hostname}</span>
                  <span className="text-on-surface-low text-[0.7rem]">{sys.os.split(' ')[0]} · {sys.arch?.split(' ')[0]}</span>
                </div>

                <Bar icon={Cpu} label="CPU" pct={cpu} detail={`${sys.cpu_count} cores · load ${sys.load_1m.toFixed(1)}`} />
                <Bar icon={MemoryStick} label="Memory" pct={memPct} detail={`${sys.mem_used_gb.toFixed(1)} / ${sys.mem_total_gb.toFixed(0)} GB`} />
                {sys.disk_total_gb != null && sys.disk_free_gb != null && (
                  <Bar icon={HardDrive} label="Disk" pct={((sys.disk_total_gb - sys.disk_free_gb) / sys.disk_total_gb) * 100} detail={`${sys.disk_free_gb.toFixed(0)} GB free`} />
                )}

                <div className="mt-3 flex flex-col gap-1.5 text-[0.7rem]">
                  {sys.gpu_present && sys.gpu_model && <Kv icon={Zap} label="GPU" value={sys.gpu_model} />}
                  {(sys.net_rx_kbs != null || sys.net_tx_kbs != null) && <Kv icon={Network} label="Network" value={`↓${fmtKbs(sys.net_rx_kbs)}  ↑${fmtKbs(sys.net_tx_kbs)}`} />}
                  <Kv icon={Boxes} label="Processes" value={`${sys.child_processes ?? 0} child · ${sys.mcp_total ?? 0} MCP`} />
                  <Kv icon={Activity} label="This process"
                    value={`${sys.proc_mem_mb.toFixed(0)} MB${sys.proc_cpu_pct != null ? ` · ${sys.proc_cpu_pct.toFixed(1)}% CPU` : ''}${sys.thread_count != null ? ` · ${sys.thread_count} thr` : ''}`} />
                </div>
              </>
            ) : (
              // No system data yet (connecting) or the gateway dropped (disconnected):
              // still give the click a useful result — the connectivity status itself.
              <div className="flex items-center gap-2">
                <span className="size-2 shrink-0 rounded-full" style={{ background: dotColor }} />
                <span className="text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 600' }}>{statusLabel}</span>
              </div>
            )}

            <RunningAgents open={open} />

            <RestartControls onFired={() => setOpen(false)} />

            {auth && (
              <div className="mt-3 flex items-center gap-2 border-t border-outline-variant/30 pt-2.5 text-[0.7rem]">
                <ShieldCheck size={13} style={{ color: auth.valid ? 'var(--color-success)' : 'var(--color-error)' }} className="shrink-0" />
                <span className="text-on-surface-var">{authLabel(auth)}</span>
                {auth.minutes_remaining != null && <span className="ml-auto text-on-surface-low tabular-nums">{fmtMins(auth.minutes_remaining)}</span>}
              </div>
            )}
          </motion.div>
        )}
        </AnimatePresence>,
        document.body,
      )}
    </div>
  )
}

/** Background subagents monitor — the fleet of agents spawned by crons, goal
 *  loops, Slack, or an agent's own subagent_run (distinct from the per-chat
 *  subagent cards). Lazily loads /api/spawn when the card opens + polls while it's
 *  open; running agents can be cancelled, and the finished list cleared. */
function RunningAgents({ open }: { open: boolean }) {
  const [agents, setAgents] = useState<SpawnedAgent[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const load = () => { api.spawnedAgents().then(setAgents).catch(() => setAgents([])) }
  // Load on open; poll every 4s while open so a running agent's completion shows.
  useEffect(() => {
    if (!open) return
    load()
    const t = window.setInterval(load, 4000)
    return () => clearInterval(t)
  }, [open])
  if (!agents || agents.length === 0) return null
  const running = agents.filter((a) => !a.done)
  const doneCount = agents.length - running.length
  const cancel = async (id: string) => { setBusy(id); try { await api.cancelSpawnedAgent(id) } catch { /* */ } setBusy(null); load() }
  const clear = async () => { setBusy('__clear'); try { await api.clearSpawnedAgents() } catch { /* */ } setBusy(null); load() }
  return (
    <div className="mt-3 border-t border-outline-variant/30 pt-2.5">
      <div className="mb-1.5 flex items-center gap-1.5 text-[0.7rem]">
        <Bot size={12} className="text-on-surface-low" />
        <span className="text-on-surface-var" style={{ fontVariationSettings: '"wght" 600' }}>Background agents</span>
        <span className="ml-auto text-on-surface-low">{running.length} running · {doneCount} done</span>
      </div>
      {/* the live subagent fleet — rows stagger in and animate out as background
          agents spawn/finish, so the monitor reads as a living list */}
      <motion.div className="flex flex-col gap-1" variants={{ animate: { transition: stagger(0.04) } }} initial="initial" animate="animate">
        <AnimatePresence initial={false}>
          {running.map((a) => (
            <motion.div key={a.id} layout variants={listItemEnter}
              exit={{ opacity: 0, height: 0, marginTop: 0, transition: spring.spatialFast }}
              className="flex items-center gap-1.5 rounded-md bg-surface-high px-2 py-1 text-[0.68rem]">
              <Loader2 size={10} className="shrink-0 animate-spin text-primary" />
              <span className="min-w-0 flex-1 truncate text-on-surface-var" title={a.task}>{firstLine(a.task)}</span>
              {a.parent && <span className="shrink-0 text-on-surface-low/70">{a.parent.replace('cron:', '⏱')}</span>}
              <button type="button" onClick={() => cancel(a.id)} disabled={busy === a.id} aria-label="Cancel agent"
                className="shrink-0 rounded p-0.5 text-on-surface-low hover:text-danger disabled:opacity-50"><X size={11} /></button>
            </motion.div>
          ))}
        </AnimatePresence>
        {running.length === 0 && <div className="px-2 py-1 text-on-surface-low text-[0.68rem] italic">No agents running now.</div>}
      </motion.div>
      {doneCount > 0 && (
        <button type="button" onClick={clear} disabled={busy === '__clear'}
          className="mt-1.5 text-on-surface-low text-[0.65rem] hover:text-on-surface disabled:opacity-50">Clear {doneCount} finished</button>
      )}
    </div>
  )
}

/** Gateway lifecycle controls — Restart (apply committed backend changes, no
 *  git pull) + Update & Restart (git pull + rebuild FE + restart, via the
 *  existing /api/update flow). Both are gated by a warn-if-active confirm that
 *  surfaces the count of running agents + live sessions a restart would
 *  interrupt. On confirm the request fires and the confirm UI resets — the
 *  shell-level UpdateProgressOverlay owns ALL progress feedback (both paths
 *  push `update_progress` events: restart-only gets the simplified view, a
 *  real update the 4-step stepper). No inline busy state here: it could never
 *  clear on success (the gateway re-execs and this component unmounts), so it
 *  read as permanently stuck. A pipeline-START failure (e.g. 409 dirty tree)
 *  never pushes any progress event, so the overlay can't surface it — that
 *  error is toasted at this call site instead. */
function RestartControls({ onFired }: { onFired?: () => void }) {
  // pending = which action awaits confirm ('restart' | 'update')
  const [pending, setPending] = useState<'restart' | 'update' | null>(null)
  const [active, setActive] = useState<{ running_agents: number; sessions: number } | null>(null)

  const ask = async (which: 'restart' | 'update') => {
    setPending(which)
    // Best-effort active-work probe for the warning; a failure just omits the count.
    try { setActive(await api.restartProbe()) } catch { setActive(null) }
  }
  const cancel = () => { setPending(null); setActive(null) }
  const confirm = async () => {
    if (!pending) return
    const which = pending
    // Reset the confirm UI + close the popover immediately — the overlay takes
    // over from here (one progress surface, not a popover competing with it).
    setPending(null); setActive(null)
    onFired?.()
    try {
      if (which === 'update') await api.applyUpdate()
      else await api.restartGateway()
    } catch (e) {
      // A rejected START (409 dirty tree / no PROJECT_DIR / already-in-progress)
      // pushes NO update_progress events — the overlay never opens. Surface the
      // backend's error text so the user isn't left staring at nothing.
      // (A dropped connection mid-restart rejects too, but by then the overlay
      // is already showing "restarting", so a stray toast is harmless.)
      const msg = e instanceof Error && e.message ? e.message : 'Request failed'
      window.dispatchEvent(new CustomEvent('ne:toast', {
        detail: { level: 'error', message: which === 'update' ? `Update blocked: ${msg}` : `Restart failed: ${msg}` },
      }))
    }
  }

  const n = (active?.running_agents ?? 0)
  const warn = n > 0
  return (
    <div className="mt-3 border-t border-outline-variant/30 pt-2.5">
      {!pending ? (
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => ask('restart')}
            className="flex items-center gap-1.5 rounded-lg bg-surface-high px-2 py-1 text-on-surface-var text-[0.7rem] hover:bg-surface-highest hover:text-on-surface"
            style={{ borderRadius: 'var(--radius-md)' }}>
            <RotateCw size={12} /> Restart
          </button>
          <button type="button" onClick={() => ask('update')}
            className="flex items-center gap-1.5 rounded-lg bg-surface-high px-2 py-1 text-on-surface-var text-[0.7rem] hover:bg-surface-highest hover:text-on-surface"
            style={{ borderRadius: 'var(--radius-md)' }}>
            <DownloadCloud size={12} /> Update &amp; Restart
          </button>
        </div>
      ) : (
        <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialFast}>
          <div className="flex items-start gap-1.5 text-[0.7rem] text-on-surface-var">
            {warn && <AlertTriangle size={13} className="mt-0.5 shrink-0" style={{ color: 'var(--color-warning)' }} />}
            <span>
              {pending === 'update'
                ? 'Pull latest, rebuild, and restart the gateway?'
                : 'Restart the gateway to apply changes?'}
              {warn && (
                <> This interrupts <span style={{ fontVariationSettings: '"wght" 650' }}>
                  {n} running agent{n === 1 ? '' : 's'}</span>{active && active.sessions > 0 ? ` and ${active.sessions} live session${active.sessions === 1 ? '' : 's'}` : ''}.</>
              )}
            </span>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <button type="button" onClick={confirm}
              className="rounded-lg px-2 py-1 text-[0.7rem] text-on-primary"
              style={{ background: warn ? 'var(--color-warning)' : 'var(--color-primary)', borderRadius: 'var(--radius-md)' }}>
              {pending === 'update' ? 'Update & Restart' : 'Restart'}
            </button>
            <button type="button" onClick={cancel}
              className="rounded-lg px-2 py-1 text-on-surface-low text-[0.7rem] hover:text-on-surface">Cancel</button>
          </div>
        </motion.div>
      )}
    </div>
  )
}

/** First non-empty line of a task prompt, for a compact one-line label. */
function firstLine(task: string): string {
  const line = (task || '').split('\n').map((l) => l.trim()).find((l) => l && !l.startsWith('[')) || task || 'agent'
  return line.length > 60 ? line.slice(0, 60) + '…' : line
}

function Bar({ icon: Icon, label, pct, detail }: { icon: typeof Cpu; label: string; pct: number; detail: string }) {
  const p = Math.min(100, Math.max(0, pct))
  const tone = p > 90 ? 'var(--color-error)' : p > 70 ? 'var(--color-warning)' : 'var(--color-primary)'
  return (
    <div className="mb-2">
      <div className="flex items-center gap-1.5 text-[0.7rem]">
        <Icon size={11} className="text-on-surface-low" />
        <span className="text-on-surface-var">{label}</span>
        <span className="ml-auto text-on-surface-low tabular-nums">{Math.round(p)}%</span>
      </div>
      <div className="mt-1 flex items-center gap-2">
        <div className="h-1 flex-1 overflow-hidden rounded-pill bg-surface-high">
          <motion.div className="h-full rounded-pill" style={{ background: tone }} animate={{ width: `${p}%` }} transition={spring.spatialSlow} />
        </div>
      </div>
      <div className="mt-0.5 text-on-surface-low text-[0.65rem]">{detail}</div>
    </div>
  )
}

function Kv({ icon: Icon, label, value }: { icon: typeof Cpu; label: string; value: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <Icon size={11} className="shrink-0 text-on-surface-low" />
      <span className="shrink-0 text-on-surface-low">{label}</span>
      <span className="ml-auto truncate text-on-surface-var" title={value}>{value}</span>
    </div>
  )
}

function fmtKbs(kbs?: number): string {
  if (kbs == null) return '0'
  if (kbs >= 1024) return `${(kbs / 1024).toFixed(1)}MB/s`
  return `${Math.round(kbs)}KB/s`
}
function fmtMins(m: number): string {
  if (m >= 1440) return `${Math.floor(m / 1440)}d left`
  if (m >= 60) return `${Math.floor(m / 60)}h ${Math.round(m % 60)}m left`
  return `${Math.round(m)}m left`
}
function authLabel(a: AuthStatus): string {
  const mode = a.mode === 'local_token' ? 'Local token' : a.mode === 'oauth2' ? 'OAuth2' : a.mode
  return a.oauth2_issuer ? `${mode} · ${a.oauth2_issuer}` : `${mode} · ${a.bind_host}`
}
