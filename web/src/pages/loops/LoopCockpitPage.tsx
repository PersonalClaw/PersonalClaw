import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowLeft, Pause, Play, Square, X, Check, MessageSquarePlus,
  Play as Start, Trash2, HelpCircle, Search, ChevronRight, CornerDownRight,
  Maximize2, PanelRight, ScrollText, Download, FileText, Bot, Cpu, BarChart3, ExternalLink, ListChecks, Link2, AlertTriangle, Copy,
  FolderKanban, FolderOpen, Clock, ShieldCheck,
} from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { Spark } from '../../ui/Spark'
import { Markdown } from '../../ui/Markdown'
import { SidePanel } from '../../ui/SidePanel'
import { Modal } from '../../ui/Modal'
import { thinkingGlow } from '../../design/gradients'
import { spring, springs, messageEnter } from '../../design/motion'
import { ContentSurface } from '../../ui/content/ContentSurface'
import { resolveContentType } from '../../ui/content/contentTypes'
import { api, type GoalLoop, type LoopFinding, type LoopNudge, type LoopVerdict, type Artifact, type TaskItem } from '../../lib/api'
import { peekCache, writeCache } from '../../lib/useCachedData'
import { downloadText, safeFilename } from '../../lib/download'
import { useRunStream } from './useRunStream'
import { loopToGoalLoop } from './goalAdapter'
import { RunPhaseTrail } from './RunPhaseTrail'
import { foldReducer, emptyRunFlags, type RunFlags } from './runFold'
import { activePhaseIndex, phaseMinCycles, phaseForCycle } from './loopPhases'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useQueryFlag, type RouteProps } from '../../app/useQueryState'

/** Decode the `?sel=` Details-rail drill-down ref. */
function parseSel(raw?: string): { kind: 'log' } | { kind: 'roi' } | { kind: 'cycle'; cycle: number } | null {
  if (!raw) return null
  if (raw === 'log') return { kind: 'log' }
  if (raw === 'roi') return { kind: 'roi' }
  if (raw.startsWith('cycle-')) { const n = Number(raw.slice(6)); if (!Number.isNaN(n)) return { kind: 'cycle', cycle: n } }
  return null
}

const ACTIVE = ['running', 'paused', 'stagnant', 'needs_input']

// Friendly labels for the metadata pills — the API stores raw tokens
// (open_ended, balanced, …) but the UI should never surface the underscore form.
const GOAL_TYPE_LABEL: Record<string, string> = { verifiable: 'Verifiable', open_ended: 'Open-ended', monitor: 'Monitor' }
const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s)

// The granularity dial → marginal-value threshold (mirrors loops/granularity.py).
// 'forever' has no threshold (never self-stops on value).
const DIAL_THRESHOLD: Record<string, number | null> = {
  quick: 3.0, balanced: 2.0, exhaustive: 1.0, forever: null,
}


/** The signature ROI graph (§10.3): the judge's marginal-value per cycle as a
 *  bar chart — one labeled column per cycle, a 0–5 y-axis, and a dashed line at
 *  the granularity threshold. Bars below the line (diminishing returns) are
 *  muted; a run of them is what trips the auto-stop. Open-ended loops only. */
/** One ROI bar: its true cycle number + the judge's marginal value. */
interface RoiPoint { cycle: number; score: number }

function RoiRail({ points, granularity }: { points: RoiPoint[]; granularity: string }) {
  if (!points.length) return null
  const threshold = DIAL_THRESHOLD[granularity] ?? null
  const max = 5
  const H = 96  // chart body height (px)
  const recent = points.slice(-40)
  return (
    <div className="flex gap-s">
      {/* y-axis ticks */}
      <div className="relative w-5 shrink-0 text-on-surface-low text-[0.6rem] tabular-nums" style={{ height: H }}>
        <span className="absolute right-0 -translate-y-1/2" style={{ top: 0 }}>5</span>
        <span className="absolute right-0 -translate-y-1/2" style={{ top: H / 2 }}>2.5</span>
        <span className="absolute right-0 -translate-y-1/2" style={{ top: H }}>0</span>
      </div>
      {/* plot area */}
      <div className="relative flex-1 min-w-0">
        <div className="relative" style={{ height: H }}>
          {/* threshold reference line */}
          {threshold != null && (
            <div className="absolute left-0 right-0 border-t border-dashed" title={`Stop threshold (${granularity})`}
              style={{ top: H * (1 - threshold / max), borderColor: 'color-mix(in srgb, var(--color-primary) 55%, transparent)' }}>
              <span className="absolute right-0 -top-3.5 text-[0.6rem] tabular-nums text-primary">thr {threshold.toFixed(0)}</span>
            </div>
          )}
          {/* bars — one column per scored cycle, labeled by its true cycle #.
              Each bar is keyed by its cycle so a score update SPRINGS the height
              in place (gentle settle) instead of re-mounting — the sanctioned
              "spring on the value" for a low-frequency (per-cycle) data series. */}
          <div className="absolute inset-0 flex items-end gap-[3px]">
            {recent.map((p) => {
              const below = threshold != null && p.score < threshold
              return (
                <motion.div key={p.cycle} className="flex-1 min-w-[5px] rounded-t-[2px]" title={`cycle ${p.cycle}: ${p.score.toFixed(1)}`}
                  initial={false}
                  animate={{ height: `${Math.max(3, (p.score / max) * 100)}%`, opacity: below ? 0.45 : 1 }}
                  transition={springs.gentle}
                  style={{ background: below ? 'var(--color-on-surface-low)' : 'var(--color-primary)' }} />
              )
            })}
          </div>
        </div>
        {/* x-axis cycle labels (thinned when many) */}
        <div className="flex gap-[3px] mt-1">
          {recent.map((p, i) => {
            const show = recent.length <= 16 || i === 0 || i === recent.length - 1 || p.cycle % 5 === 0
            return <span key={p.cycle} className="flex-1 min-w-[5px] text-center text-on-surface-low text-[0.6rem] tabular-nums">{show ? p.cycle : ''}</span>
          })}
        </div>
      </div>
    </div>
  )
}

/** Strip a verbose model id to its readable tail (e.g.
 *  "Bedrock:global.anthropic.claude-opus-4-8" → "claude-opus-4-8"). */
function shortModel(m: string): string {
  const afterColon = m.includes(':') ? m.split(':').pop()! : m
  const parts = afterColon.split('.')
  return (parts.pop() || afterColon).replace(/\[.*\]$/, '')
}

/** The role-phased execution plan as a left-to-right pill trail — the single
 *  shared renderer used by both the PROMPT card and the header status line so
 *  they stay byte-identical. Each phase pill shows: a full green fill once the
 *  phase is DONE (execution moved past it, or the loop completed); one green
 *  check per completed cycle in the phase; and — on the active phase while
 *  running — an animating spinner for the in-flight cycle. Upcoming phases are
 *  name-only. `compact` trims padding/arrows for the dense header. */
/** A compact metadata pill for the prompt card header. */
function MetaPill({ icon, text, tone, title }: { icon?: React.ReactNode; text: string; tone?: 'primary'; title?: string }) {
  return (
    <span title={title}
      className="inline-flex items-center gap-1 rounded-pill px-2 h-5 text-[0.7rem] max-w-[14rem] truncate"
      style={tone === 'primary'
        ? { background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', color: 'var(--color-primary)' }
        : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>
      {icon}<span className="truncate">{text}</span>
    </span>
  )
}

function fmt(sec: number): string {
  if (!sec || sec < 1) return '0s'
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60)
  if (h) return `${h}h ${m}m`
  return m ? `${m}m ${s}s` : `${s}s`
}

/** Coerce a worker-authored finding field to a renderable string. Workers
 *  sometimes emit an object/array where a string is expected (e.g. summary as a
 *  map of PR-ids → notes); rendering that object directly crashes React (#31).
 *  This flattens any shape to safe display text. */
function asText(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return v.map(asText).filter(Boolean).join(', ')
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${k}: ${asText(val)}`).join(' · ')
  }
  return String(v)
}

export function LoopCockpitPage({ id, onBack, onDeleted, onOpenArtifact, onOpenTask, onOpenProject, query, setQuery }: { id: string; onBack: () => void; onDeleted?: () => void; onOpenArtifact?: (slug: string) => void; onOpenTask?: (taskId: string) => void; onOpenProject?: (projectId: string) => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const q = query
  const sq = setQuery
  const [c, setC] = useState<GoalLoop | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [report, setReport] = useState('')
  const [log, setLog] = useState('')
  // Outputs: the loop's artifacts (the general outcome channel) + linked Tasks.
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [tasks, setTasks] = useState<TaskItem[]>([])
  // Right rail + its drill-down are URL-backed (?details=1, ?sel=log|roi|cycle-N)
  // so a refresh / shared link reopens the same Details view.
  const railOpen = q.details === '1'
  const setRailOpen = (v: boolean) => sq(v ? { details: '1' } : { details: null, sel: null })
  const selected = parseSel(q.sel)
  const setSelected = (s: { kind: 'log' } | { kind: 'roi' } | { kind: 'cycle'; cycle: number } | null) =>
    sq({ sel: !s ? null : s.kind === 'cycle' ? `cycle-${s.cycle}` : s.kind })
  // View surfaces are URL-backed (push, Back closes; refresh/deep-link reopens):
  // ?nudge=1 (the nudge drawer), ?report=1 (report modal), ?prompt=1 (prompt-card
  // expander). The nudge COMPOSE DRAFT (nudgeText) stays local — a composer draft has
  // no shareable meaning (canonical §3), so only the drawer's open-state is in the URL.
  const [nudgeOpen, setNudgeOpen] = useQueryFlag(q, sq, 'nudge')
  const [reportOpen, setReportOpen] = useQueryFlag(q, sq, 'report')
  const [promptOpen, setPromptOpen] = useQueryFlag(q, sq, 'prompt')
  const [nudgeText, setNudgeText] = useState('')
  const [nudgeSending, setNudgeSending] = useState(false)
  const [nudgeError, setNudgeError] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  // Stop is terminal (no resume from 'stopped' — see ACTION_SOURCE_STATES), so it
  // gets the same two-click arm as delete rather than firing on a stray click.
  const [confirmStop, setConfirmStop] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  // Set by Escape so commitRename (fired by the unmount blur) skips the save.
  const cancelRename = useRef(false)
  // Guards the double commitRename (Enter → unmount → onBlur) from a redundant PUT.
  const renameInFlight = useRef(false)
  // The done-ness judge failed on a recent cycle (G3) — surfaced so the user
  // knows quality assessment is degraded, not silently never-completing.
  // Transient lifecycle flags folded through the SHARED, unit-tested foldReducer
  // (P16) — one place computes judge-degraded/gate/stall from lifecycle events, so
  // this cockpit can't drift from the reducer's contract. This cockpit only surfaces
  // judgeDegraded today (gate/stall are the code cockpit's), read off the folded state.
  const [runFlags, setRunFlags] = useState<RunFlags>(emptyRunFlags)
  const judgeDegraded = runFlags.judgeDegraded
  const [linkCopied, setLinkCopied] = useState(false)
  // The containing Project's name — for a clickable pill linking back to the Project,
  // mirroring the Code cockpit (C397). Resolve project_id (explicit user scope) OR
  // tasks_project_id (the auto-provisioned backing project a project-less loop gets):
  // a loop launched without choosing a project still lives under a project via
  // tasks_project_id, so the chip must show it — gating on project_id alone hid it.
  const [projName, setProjName] = useState('')
  const projId = c?.project_id || c?.tasks_project_id || ''
  useEffect(() => {
    if (!projId) { setProjName(''); return }
    let alive = true
    api.project(projId).then((pr) => { if (alive) setProjName(pr.name) }).catch(() => {})
    return () => { alive = false }
  }, [projId])
  const [statusText, setStatusText] = useState('')
  const [activity, setActivity] = useState<{ kind: string; label: string; detail?: string }[]>([])
  const [now, setNow] = useState(() => 0) // seconds; ticks while active
  // The hidden worker session key the engine broadcasts chat_status/tool_call
  // under (loops.manager.session_key) — NO 'dashboard:' prefix. The old value
  // had one, so worker events never matched and the live activity stream was
  // always empty.
  const workerKey = `loop-${id}`

  // Initial load + a SLOW fallback poll. Live updates ride the per-loop SSE
  // (useLoopStream below) per the realtime doctrine; the slow poll only
  // backstops a dropped stream — it's not the primary update path anymore.
  const loadReport = useRef<() => void>(() => {})
  const everLoaded = useRef(false)
  useEffect(() => {
    let alive = true
    everLoaded.current = false
    const loadOutputs = () => {
      api.uLoopReport(id).then((rep) => { if (alive && rep) { setReport(rep.report || ''); setLog(rep.log || '') } }).catch(() => {})
      // Outcomes: every artifact tagged for this loop.
      api.artifacts({ tag: `loop:${id}` }).then((a) => { if (alive) setArtifacts(a) }).catch(() => {})
    }
    // Linked Tasks live in the loop's own task-list (under the "Goal Loops"
    // project), NOT under project=<loop id> — so fetch them by the authoritative
    // linked_task_ids the loop stores, not a project filter (which returned none).
    const loadTasks = (ids: string[]) => {
      if (!ids.length) { setTasks([]); return }
      Promise.all(ids.map((tid) => api.task(tid).catch(() => null)))
        .then((ts) => { if (alive) setTasks(ts.filter(Boolean) as TaskItem[]) }).catch(() => {})
    }
    let t = 0
    const TERMINAL = ['complete', 'stopped', 'failed']
    const load = async () => {
      const raw = await api.uLoop(id).catch(() => null)
      const gl = raw ? loopToGoalLoop(raw) : null
      if (!alive) return
      if (gl) {
        everLoaded.current = true; setC(gl); setNotFound(false); writeCache(`loop:${id}`, gl); loadOutputs(); loadTasks(gl.linked_task_ids ?? [])
        // A TERMINAL loop never changes again — stop the 30s fallback poll so a finished
        // cockpit tab doesn't spam GET /api/loops/<id> forever. SSE still delivers the
        // (rare) post-terminal event; reopening / an action re-pulls fresh. Mirrors the
        // Code cockpit gating its poll off non-running. (Pre-launch + active keep polling
        // — those legitimately transition.)
        if (t && TERMINAL.includes(gl.status)) { clearInterval(t); t = 0 }
      }
      // A null before we've ever loaded means the id is unknown/deleted —
      // surface a not-found instead of spinning forever. Once loaded, a null
      // is just a transient poll blip and is ignored (SSE keeps `c` live).
      else if (!everLoaded.current) {
        setNotFound(true)
        // The loop is permanently gone (never loaded → deleted/bad id). Stop the
        // fallback poll so a stale cockpit tab doesn't spam GET /api/loops/<id>
        // → 404 every 30s forever.
        if (t) { clearInterval(t); t = 0 }
      }
    }
    loadReport.current = loadOutputs
    load()
    t = window.setInterval(load, 30_000)  // fallback only; SSE drives live
    return () => { alive = false; if (t) clearInterval(t) }
  }, [id])

  // Instant-paint seed: re-opening a loop should show its last snapshot
  // immediately instead of a cold spinner. This PEEKS the shared cache (which
  // load() above warms via writeCache) — a synchronous read, NO fetch — so the
  // first paint is instant on a same-session re-nav while load() revalidates. We
  // deliberately do NOT use useCachedData here: it always fires its own fetch,
  // which duplicated load()'s GET /api/loops/<id> on every mount (3 identical
  // mount requests → now 1 authoritative + SSE). In-memory only: a hard reload
  // has an empty cache, so load()'s fetch owns the first paint then (correct —
  // never a stale cross-reload snapshot).
  useEffect(() => {
    if (c === null) {
      const seed = peekCache<GoalLoop>(`loop:${id}`)
      if (seed) setC(seed)
    }
  }, [id, c])

  // Per-loop SSE: snapshot keeps `c` live; any lifecycle event refetches the
  // report/findings (which the stream signals but doesn't carry in full).
  // Don't open (or keep) the per-loop SSE once the loop is confirmed gone —
  // EventSource would auto-retry a 404 /stream forever on a stale tab.
  useRunStream(id, !notFound, {
    onSnapshot: (l) => setC(loopToGoalLoop(l)),
    onLifecycle: (event, data) => {
      // Fold each lifecycle event through the shared reducer (judge_error → degraded,
      // cycle_verdict → clears it, + gate/stall for kinds that emit them). One tested
      // contract instead of a per-cockpit switch.
      setRunFlags((f) => foldReducer(f, event, data))
      loadReport.current()
    },
  })

  // 1s clock for live elapsed labels (only while a loop is active)
  useEffect(() => {
    setNow(Date.now() / 1000)
    if (!c || !ACTIVE.includes(c.status)) return
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(t)
  }, [c?.status])

  // Disarm the delete confirmation if the user doesn't follow through quickly.
  // (Must run unconditionally — above the not-found/loading early return.)
  useEffect(() => {
    if (!confirmDelete) return
    const t = window.setTimeout(() => setConfirmDelete(false), 4000)
    return () => clearTimeout(t)
  }, [confirmDelete])
  useEffect(() => {
    if (!confirmStop) return
    const t = window.setTimeout(() => setConfirmStop(false), 4000)
    return () => clearTimeout(t)
  }, [confirmStop])

  const onWs = useCallback((m: WsMessage) => {
    if (m.data?.session !== workerKey) return
    if (m.type === 'chat_status') { const s = String(m.data.status ?? ''); setStatusText(s); setActivity((a) => [...a, { kind: 'status', label: s }].slice(-40)) }
    else if (m.type === 'tool_call') setActivity((a) => [...a, { kind: 'tool', label: String(m.data.tool ?? 'tool'), detail: String(m.data.purpose ?? m.data.input_preview ?? '') }].slice(-40))
    // The runner broadcasts turn milestones (session created, context injected,
    // "Thinking…") as activity_event — the main progress signal for native runs
    // that interleave long thinking gaps between tool calls. Surface them so a
    // working cycle reads as alive, not a frozen "working" with an empty stream.
    else if (m.type === 'activity_event') {
      const text = String(m.data.text ?? '')
      if (text) {
        if (String(m.data.kind ?? '') === 'status') setStatusText(text)
        setActivity((a) => [...a, { kind: 'status', label: text }].slice(-40))
      }
    }
  }, [workerKey])
  useChatSocket(onWs)

  if (!c) {
    if (notFound) return (
      <div className="flex h-full flex-col items-center justify-center gap-m px-l text-center">
        <div className="text-on-surface text-[1rem]" style={{ fontVariationSettings: '"wght" 500' }}>Loop not found</div>
        <p className="max-w-md text-on-surface-low text-[0.875rem]">This loop doesn’t exist — it may have been deleted, or the link is out of date.</p>
        <Button size="sm" onClick={onBack}><ArrowLeft size={15} /> Back to loops</Button>
      </div>
    )
    return <div className="flex h-full items-center justify-center text-on-surface-low">Loading…</div>
  }
  const active = ACTIVE.includes(c.status)
  const running = c.status === 'running'
  const findings = [...(c.findings ?? [])].sort((a, b) => b.cycle - a.cycle)
  // The judge's third-party verdict per cycle (open-ended loops only). A cycle can
  // carry MORE THAN ONE verdict — e.g. a real scored verdict plus a final/degraded
  // one whose marginal_value/quality_score are null. Prefer the SCORED verdict so
  // the rail shows the real number (and the renderers never see null scores).
  const verdictByCycle = new Map<number, LoopVerdict>()
  for (const v of c.verdicts ?? []) {
    if (typeof v.cycle !== 'number') continue
    const prev = verdictByCycle.get(v.cycle)
    const scored = typeof v.marginal_value === 'number'
    const prevScored = typeof prev?.marginal_value === 'number'
    // Keep the first scored verdict; only overwrite an unscored one (or fill an empty slot).
    if (prev == null || (scored && !prevScored)) verdictByCycle.set(v.cycle, v)
  }
  // ROI bars keyed to their TRUE cycle number. The judge is best-effort, so a
  // cycle whose judge failed has no verdict — the flat marginal_scores array
  // can't express that gap and would mislabel every later bar. Prefer the
  // cycle-keyed verdicts; fall back to positional scores only when no verdict
  // carries a marginal_value (older loops).
  const roiPoints: RoiPoint[] = (c.verdicts ?? []).some((v) => typeof v.marginal_value === 'number')
    ? (c.verdicts ?? [])
        .filter((v) => typeof v.cycle === 'number' && typeof v.marginal_value === 'number')
        .map((v) => ({ cycle: v.cycle as number, score: v.marginal_value as number }))
        .sort((a, b) => a.cycle - b.cycle)
    : (c.marginal_scores ?? []).map((s, i) => ({ cycle: i + 1, score: s }))
  const { byCycle, pending } = groupNudges(c.nudges ?? [])
  const W = 'calc(var(--content-width) + 340px)'

  // ── time math ──
  const cycleTs = (c.findings ?? []).map((f) => f.ts).filter((t): t is number => typeof t === 'number').sort((a, b) => a - b)
  // total loop elapsed = running time banked from prior stretches + the current
  // running stretch (only while actually running). Excludes paused intervals,
  // and survives the started_at reset on each resume. Single source: the store
  // backfills elapsed_seconds for any pre-accounting loop, so there's no legacy
  // raw-span fallback here.
  const banked = c.elapsed_seconds ?? 0
  const curStretch = running && c.started_at ? Math.max(0, now - c.started_at) : 0
  const totalElapsed = banked + curStretch
  // current cycle elapsed: since the last finding landed (or since start for cycle 1)
  const lastTs = cycleTs.at(-1) ?? c.started_at ?? now
  const curCycleElapsed = active ? Math.max(0, now - lastTs) : 0

  async function act(a: 'start' | 'pause' | 'resume' | 'stop') {
    const next = await api.uLoopAction(id, a).catch(() => null); if (next) setC(loopToGoalLoop(next))
  }
  async function sendNudge() {
    const t = nudgeText.trim(); if (!t || nudgeSending) return
    setNudgeSending(true); setNudgeError(false)
    try {
      await api.uLoopNudge(id, t)
      // Only clear + close on success — a failed nudge keeps the text so the
      // user can retry instead of silently losing their guidance.
      setNudgeText(''); setNudgeOpen(false)
    } catch {
      setNudgeError(true)
    } finally {
      setNudgeSending(false)
    }
  }
  // Delete is destructive + irreversible (drops findings, deliverable, history),
  // so require a confirming second click instead of firing on the first.
  async function del() {
    if (!confirmDelete) { setConfirmDelete(true); return }
    await api.deleteULoop(id).catch(() => {})
    onDeleted ? onDeleted() : onBack()
  }
  function copyLink() {
    const url = `${location.origin}/#/loops/${id}`
    navigator.clipboard?.writeText(url).then(() => { setLinkCopied(true); setTimeout(() => setLinkCopied(false), 1500) }).catch(() => {})
  }
  // Rename works in ANY state (the name is metadata; the rest of the spec freezes
  // once running). updateLoop with a name-only body routes to the rename path.
  function startRename() { cancelRename.current = false; setTitleDraft(c?.name || ''); setEditingTitle(true) }
  function abortRename() { cancelRename.current = true; setEditingTitle(false) }
  async function commitRename() {
    setEditingTitle(false)
    // Escape sets this so the blur that fires as the input unmounts doesn't
    // save the very edit the user just tried to discard.
    if (cancelRename.current) { cancelRename.current = false; return }
    // Enter sets editingTitle=false → the input unmounts → onBlur fires commitRename
    // AGAIN. The 2nd call still sees the old c.name (setC is post-await), so the
    // name-unchanged guard misses it → a redundant PUT + double SSE. Guard re-entry.
    if (renameInFlight.current) return
    const name = titleDraft.trim()
    if (!name || name === (c?.name || '')) return
    renameInFlight.current = true
    try {
      const updated = await api.updateULoop(id, { name }).catch(() => null)
      if (updated) setC(loopToGoalLoop(updated))
    } finally { renameInFlight.current = false }
  }

  // Role-phased plan (planner/quorum): the phase the upcoming/current cycle is
  // in, so the cockpit can show "Phase 2/4 · news" and highlight it in the list.
  const execPlan = (c.execution_plan ?? []) as Record<string, unknown>[]
  const activePhase = execPlan.length ? activePhaseIndex(c.total_cycles, execPlan) : -1

  // Header status line (status dot/spinner + cycle + elapsed + rubric score).
  const statusLine = (
    <span className="inline-flex items-center gap-s text-on-surface-var text-[0.8125rem] truncate">
      {running ? (
        <span className="relative inline-flex items-center justify-center size-4">
          <motion.span aria-hidden className="absolute inset-[-7px] rounded-pill" style={{ background: thinkingGlow() }}
            animate={{ opacity: [0.4, 0.9, 0.4] }} transition={{ duration: 3.2, ease: 'easeInOut', repeat: Infinity }} />
          <Spark size={13} />
        </span>
      ) : (
        <span className="size-1.5 rounded-pill" style={{ background: c.status === 'failed' ? 'var(--color-danger)' : c.status === 'complete' ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
      )}
      {running ? (statusText || 'Working') : statusLabel(c.status)}
    </span>
  )

  // ── Dedicated status bar (item 14) — a full-width row BELOW the header carrying
  // the live execution status: phase trail (or cycle counter), elapsed, the
  // containing project + bound workspace, and the run metadata pills hoisted out
  // of the prompt card. The title sub-line keeps only the status dot + label; this
  // bar is the single place to read "where the loop is right now". ──
  const wsDir = (c as { workspace_dir?: string }).workspace_dir || ''
  const cycleLabel = (() => {
    const shown = running ? c.total_cycles + 1 : c.total_cycles
    return c.max_cycles === 0 ? `cycle ${shown} · ongoing` : `cycle ${shown}/${c.max_cycles}`
  })()
  const statusBar = (
    <div className="shrink-0 flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-outline-variant/30 px-2xl py-1.5"
      style={{ background: 'var(--color-surface-container)' }}>
      {/* progress — phase trail for planned loops, else the cycle counter */}
      {execPlan.length > 0
        ? <RunPhaseTrail plan={execPlan} activePhase={activePhase} active={active} complete={c.status === 'complete'} findings={findings} compact />
        : <span className="text-on-surface-var text-[0.75rem] tabular-nums">{cycleLabel}</span>}
      {c.started_at != null && <MetaPill icon={<Clock size={11} />} text={fmt(totalElapsed)} title="Elapsed (running time)" />}
      <span className="flex-1" />
      {/* scope: containing project (clickable) + bound workspace */}
      {projId && projName && (onOpenProject
        ? <button type="button" onClick={() => onOpenProject(projId)} title={`Project: ${projName} — open`}
            className="inline-flex items-center gap-1 rounded-pill px-2 h-5 text-[0.7rem] max-w-[14rem] hover:brightness-110"
            style={{ background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', color: 'var(--color-primary)' }}>
            <FolderKanban size={11} className="shrink-0" /><span className="truncate">{projName}</span>
          </button>
        : <MetaPill icon={<FolderKanban size={11} />} text={projName} tone="primary" title="Project" />)}
      {wsDir && <MetaPill icon={<FolderOpen size={11} />} text={wsDir.split('/').pop() || wsDir} title={`Workspace: ${wsDir}`} />}
      <MetaPill icon={<Bot size={11} />} text={c.agent || 'default'} title="Worker agent" />
      {c.model && <MetaPill icon={<Cpu size={11} />} text={shortModel(c.model)} title={c.model} />}
      <MetaPill text={c.attended ? 'Attended' : 'Unattended'} title="Mode" />
      {(c as { kind?: string }).kind === 'goal' && <>
        <MetaPill text={GOAL_TYPE_LABEL[c.goal_type] ?? c.goal_type} tone="primary" title="Goal type" />
        <MetaPill text={cap(c.granularity)} title="Granularity" />
      </>}
    </div>
  )

  return (
    <div className="flex h-full flex-col">
      {/* Header — back + generated title + status + action buttons. The Details
          rail toggle stays on the right (kept where it was per design). The rail
          docks BELOW this bar, so keep the corner padding (the shell corner still
          floats over the header — without this the actions slide under it). */}
      <TopBar
        keepCornerPadding
        left={
          <div className="flex items-center gap-s min-w-0">
            <IconButton icon={ArrowLeft} label="Back to loops" size={40} onClick={onBack} />
            <div className="min-w-0 flex flex-col">
              {editingTitle ? (
                <input autoFocus value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)}
                  onBlur={commitRename} onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); else if (e.key === 'Escape') abortRename() }}
                  className="min-w-[16rem] h-7 rounded-md bg-surface-high px-2 text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
              ) : (
                <button type="button" onClick={startRename} title="Rename loop"
                  className="truncate text-on-surface text-[0.9375rem] leading-tight text-left hover:text-on-surface-var" style={{ fontVariationSettings: '"wght" 600' }}>
                  {c.name || c.goal}
                </button>
              )}
              {statusLine}
            </div>
            {/* copy a shareable link to this loop — next to the title, like chat. */}
            <IconButton icon={linkCopied ? Check : Link2} label={linkCopied ? 'Link copied' : 'Copy loop link'} size={32} onClick={copyLink} />
          </div>
        }
        right={
          // 4-tier cluster: run-controls are primary (last to shed labels), Details is
          // a default toggle, Delete is low-priority danger so the cluster's own
          // overflow menu absorbs it first as the header tightens. Header tenet:
          // the side-panel opener (Details) is the RIGHTMOST control; Delete sits
          // LEFTMOST of the right-edge group; everything else in between.
          <HeaderActions className="max-w-[60vw]">
            {!active && <HeaderControl icon={Trash2} label={confirmDelete ? 'Confirm delete?' : 'Delete'} danger priority="low" onClick={del} />}
            {c.status === 'ready' && <HeaderControl icon={Start} label="Start" variant="primary" priority="primary" onClick={() => act('start')} />}
            {running && <HeaderControl icon={Pause} label="Pause" variant="secondary" priority="primary" onClick={() => act('pause')} />}
            {['paused', 'stagnant', 'needs_input', 'failed'].includes(c.status) && <HeaderControl icon={Play} label="Resume" variant="primary" priority="primary" onClick={() => act('resume')} />}
            {active && <HeaderControl icon={Square} label={confirmStop ? 'Stop for good?' : 'Stop'} variant={confirmStop ? 'danger' : 'secondary'} onClick={() => { if (!confirmStop) { setConfirmStop(true); return } setConfirmStop(false); act('stop') }} />}
            {active && <HeaderControl icon={MessageSquarePlus} label="Nudge" variant="secondary" onClick={() => setNudgeOpen(!nudgeOpen)} />}
            <HeaderControl icon={PanelRight} label="Details" active={railOpen} onClick={() => { setRailOpen(!railOpen); setSelected(null) }} />
          </HeaderActions>
        }
      />

      {/* Dedicated execution status bar (item 14) — directly below the header. */}
      {statusBar}

      <div className="flex-1 min-h-0 flex">
       {/* main column (body); narrows when the Details rail opens */}
       <div className="flex-1 min-w-0 overflow-hidden flex flex-col">
        <div className="shrink-0 px-2xl pt-m pb-m flex flex-col gap-m" style={{ marginInline: 'auto', width: '100%', maxWidth: W }}>
          {/* Prompt bar (item 14) — a second, expandable status bar: COLLAPSED shows
              the first line of the prompt; EXPANDED reveals the full goal markdown +
              sub-goals. Run metadata now lives in the status bar above, so this row is
              purely the prompt. */}
          <div className="rounded-lg bg-surface-container/60 px-l py-m">
            <button type="button" onClick={() => setPromptOpen(!promptOpen)} className="flex items-center gap-s text-left w-full min-w-0">
              <ChevronRight size={14} className={`shrink-0 text-on-surface-low transition-transform ${promptOpen ? 'rotate-90' : ''}`} />
              <span className="shrink-0 text-on-surface-low text-[0.7rem] uppercase tracking-wide">Prompt</span>
              {/* first line of the prompt, shown only while collapsed */}
              {!promptOpen && (
                <span className="min-w-0 flex-1 truncate text-on-surface-var text-[0.875rem]">
                  {(c.goal || '').split('\n').map((l) => l.trim()).find(Boolean) || '—'}
                </span>
              )}
            </button>
            {promptOpen && <div className="mt-2" />}
            {promptOpen
              ? <div className="max-h-[40vh] overflow-y-auto text-on-surface text-[0.9375rem]">
                  <Markdown>{c.goal}</Markdown>
                  {c.success_criteria && <p className="mt-2 text-on-surface-low text-[0.8125rem]"><span className="text-on-surface-var">Done when:</span> {c.success_criteria}</p>}
                  {/* Sub-goals live inside the expanded prompt — they're part of
                      how the goal is decomposed, not a separate concern. */}
                  {c.sub_goals.length > 0 && (
                    <div className="mt-l">
                      <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">Sub-goals · {c.sub_goals.length}</div>
                      <ul className="flex flex-col gap-1.5">
                        {c.sub_goals.map((s, i) => (
                          <li key={i} className="flex items-start gap-s text-on-surface-var text-[0.875rem]">
                            <span className="mt-1.5 size-1 shrink-0 rounded-pill bg-primary" />{asText(s)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {/* Multi-agent roster — the personas driving the loop. Hidden on
                      the cockpit before this; surfaced so the configuration stays
                      visible once the loop is running, mirroring the Plan Review. */}
                  {c.execution === 'multi_agent' && (c.roster?.length ?? 0) > 0 && (
                    <div className="mt-l">
                      <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">
                        Roster · {c.roster!.length}{c.strategy_id ? ` · ${cap(c.strategy_id.replace(/_/g, ' '))}` : ''}
                      </div>
                      <div className="flex flex-col gap-1.5">
                        {c.roster!.map((m, i) => (
                          <div key={i} className="flex flex-col gap-0.5 rounded-lg bg-surface-container px-m py-2">
                            <span className="text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>{m.role}</span>
                            {m.persona && <span className="text-on-surface-var text-[0.8125rem]">{m.persona}</span>}
                            {m.role_hint && <span className="text-on-surface-low text-[0.75rem] mt-0.5">↳ {m.role_hint}</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              : <div className="pl-[22px] text-on-surface-var text-[0.875rem] line-clamp-2 break-words"><Markdown>{c.goal}</Markdown></div>}
          </div>

          {c.error_message && (() => {
            // A COMPLETE loop's note is informational (the non-genuine budget stop:
            // "cycle budget reached" — an expected end, NOT an error), so render it
            // neutral. failed/stagnant/etc. are real problems → danger treatment.
            const info = c.status === 'complete'
            const tone = info ? 'var(--color-on-surface-low)' : 'var(--color-danger)'
            const Icon = info ? FileText : AlertTriangle
            return (
            <motion.div variants={messageEnter} initial="initial" animate="animate" className="rounded-md px-m py-2.5 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${tone} 12%, transparent)`, color: tone }}>
              <div className="flex items-center gap-1.5 mb-1" style={{ fontVariationSettings: '"wght" 500' }}>
                <Icon size={14} className="shrink-0" /> {c.status === 'failed' ? 'This loop stopped on an error' : info ? 'Completed on its cycle budget' : 'Last cycle hit an error'}
              </div>
              <p className="break-words opacity-90">{c.error_message}</p>
              {['failed', 'stagnant'].includes(c.status) && <p className="mt-1.5 opacity-75">Fix the underlying cause, then <span style={{ fontVariationSettings: '"wght" 500' }}>Resume</span> to continue from where it left off.</p>}
            </motion.div>
          )})()}
          {judgeDegraded && running && (
            <div className="rounded-md px-m py-2 text-[0.8125rem] flex items-center gap-2" style={{ background: 'color-mix(in srgb, var(--color-warning) 12%, transparent)', color: 'var(--color-warning)' }}>
              <AlertTriangle size={14} className="shrink-0" /> Done-ness check was unavailable on a recent cycle — the loop keeps running on its cycle budget. It’ll resume quality assessment automatically.
            </div>
          )}
          {c.status === 'needs_input' && c.pending_question && (
            <div className="rounded-md px-m py-2.5 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
              <div className="flex items-center gap-1.5 text-info mb-1" style={{ fontVariationSettings: '"wght" 500' }}><HelpCircle size={14} /> The agent needs your input</div>
              <div className="text-on-surface">{c.pending_question}</div>
              {/* Offer the answer inline — the question IS the call to action, so
                  open the nudge composer right here instead of just naming it. */}
              {!nudgeOpen && (
                <Button size="sm" className="mt-2" onClick={() => setNudgeOpen(true)}>
                  <MessageSquarePlus size={14} /> Answer & resume
                </Button>
              )}
            </div>
          )}

          <AnimatePresence>
            {nudgeOpen && (
              <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                <div className="rounded-lg bg-surface-container p-m">
                  <textarea autoFocus value={nudgeText} onChange={(e) => { setNudgeText(e.target.value); if (nudgeError) setNudgeError(false) }}
                    onKeyDown={(e) => {
                      // ⌘/Ctrl+Enter sends (matches every other composer); Esc cancels.
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendNudge() }
                      else if (e.key === 'Escape') { e.preventDefault(); setNudgeOpen(false); setNudgeText(''); setNudgeError(false) }
                    }}
                    placeholder="Guide the next cycle — focus an angle, or answer the agent's question."
                    className="w-full bg-transparent outline-none text-on-surface placeholder:text-on-surface-low text-[0.875rem] min-h-[60px] resize-y" />
                  {nudgeError && (
                    <p role="alert" className="mt-1 text-[0.8125rem]" style={{ color: 'var(--color-error)' }}>Couldn’t send the nudge — your text is kept, try again.</p>
                  )}
                  <div className="flex items-center justify-end gap-s mt-2">
                    <span className="mr-auto text-on-surface-low text-[0.7rem]">⌘↵ to send · Esc to cancel</span>
                    <Button variant="ghost" size="sm" onClick={() => { setNudgeOpen(false); setNudgeText(''); setNudgeError(false) }}>Cancel</Button>
                    <Button size="sm" onClick={sendNudge} disabled={!nudgeText.trim() || nudgeSending}>{nudgeSending ? 'Sending…' : 'Send nudge'}</Button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* ── body: shell-like — the page/sections don't scroll; only the Outputs
            panel's inner content does. Outputs fills the remaining height and
            scrolls internally. Progress signal lives in the header status line +
            the Details rail's per-cycle ROI, not a standalone hero. ── */}
        <div className="flex-1 min-h-0 px-2xl pb-2xl flex flex-col gap-l" style={{ marginInline: 'auto', width: '100%', maxWidth: W }}>
          {/* Outputs — artifact-driven, the general outcome channel. Shows every
              artifact the loop produced (a report, N reports, an insight, a check
              summary) + the linked Tasks + the raw log. Fills remaining height;
              its inner content scrolls. */}
          <OutputsPanel
            loop={c} artifacts={artifacts} tasks={tasks} report={report} active={active}
            onOpenArtifact={onOpenArtifact}
            onOpenTask={onOpenTask}
            onExpandReport={() => setReportOpen(true)}
            onDownloadReport={() => downloadText(`${safeFilename(c.name || c.goal, c.id)}-deliverable.md`, report, 'text/markdown;charset=utf-8')}
          />
        </div>
       </div>

       {/* ── Details rail: primary list (Findings Log + cycles) → detail in-place ── */}
       <AnimatePresence>
         {railOpen && (
           <SidePanel
             key="details"
             storeKey="loop-rail-w"
             fillHeight
             icon={selected
               ? <button type="button" onClick={() => setSelected(null)} aria-label="Back to list" className="inline-flex items-center justify-center size-6 rounded-md text-on-surface-var hover:bg-surface-high transition-colors"><ArrowLeft size={16} /></button>
               : <PanelRight size={18} />}
             title={selected == null ? 'Details' : selected.kind === 'log' ? 'Findings Log' : selected.kind === 'roi' ? 'Returns per cycle' : `Cycle ${selected.cycle}`}
             onClose={() => { setRailOpen(false); setSelected(null) }}
           >
             {selected == null ? (
               // ── primary list: Findings Log first, then cycles (newest first) ──
               <div className="flex flex-col gap-1">
                 <RailRow
                   icon={<ScrollText size={15} />}
                   label="Findings Log"
                   hint={`${findings.length} ${findings.length === 1 ? 'cycle' : 'cycles'}`}
                   onClick={() => setSelected({ kind: 'log' })}
                 />
                 {c.goal_type === 'open_ended' && (c.marginal_scores?.length ?? 0) > 0 && (
                   <RailRow
                     icon={<BarChart3 size={15} />}
                     label="Returns per cycle"
                     hint={`▲${c.marginal_scores!.at(-1)!.toFixed(1)}`}
                     onClick={() => setSelected({ kind: 'roi' })}
                   />
                 )}
                 {pending.length > 0 && (
                   <div className="rounded-lg px-m py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-info) 8%, transparent)', border: '1px dashed color-mix(in srgb, var(--color-info) 30%, transparent)' }}>
                     <div className="flex items-center gap-1.5 text-info text-[0.7rem] uppercase tracking-wide mb-1"><MessageSquarePlus size={12} /> nudge queued — applies next cycle</div>
                     {pending.map((n, i) => <p key={i} className="text-on-surface-var">{n.text}</p>)}
                   </div>
                 )}
                 {(() => {
                   const cycleNode = (f: LoopFinding, idx: number) => {
                     const sorted = cycleTs
                     const ti = sorted.indexOf(f.ts ?? -1)
                     const prev = ti > 0 ? sorted[ti - 1] : c.started_at ?? undefined
                     const dur = f.ts != null && prev != null ? f.ts - prev : undefined
                     return <CycleNode key={f.cycle} f={f} verdict={verdictByCycle.get(f.cycle)} dur={dur} hasNudge={!!byCycle.get(f.cycle)?.length} onClick={() => setSelected({ kind: 'cycle', cycle: f.cycle })} delay={idx * 0.02} />
                   }
                   // The in-progress cycle indicator (pulsing for running). For a
                   // plan it renders INSIDE the active phase group (not as a
                   // separate box); for a flat loop it sits above the cycle list.
                   const liveCycle = active ? (
                     <div className="rounded-lg bg-surface-container px-m py-2">
                       <div className="flex items-center gap-s">
                         <span className="relative inline-flex items-center justify-center size-5 shrink-0">
                           {running && <motion.span aria-hidden className="absolute inset-[-6px] rounded-pill" style={{ background: thinkingGlow() }} animate={{ opacity: [0.3, 0.7, 0.3] }} transition={{ duration: 3, repeat: Infinity }} />}
                           <span className={running ? '' : 'text-on-surface-low'}><Spark size={13} /></span>
                         </span>
                         <span className="flex-1 truncate text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 500' }}>Cycle {c.total_cycles + 1} · {running ? (statusText || 'working') : statusLabel(c.status).toLowerCase()}</span>
                         {running && <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{fmt(curCycleElapsed)}</span>}
                       </div>
                       {running && activity.length > 0 && <LiveSubsteps activity={activity} />}
                     </div>
                   ) : null
                   // No plan → flat list (legacy): live cycle on top, then findings.
                   if (execPlan.length === 0) {
                     // Distinguish "genuinely nothing ran" from "cycles ran but this
                     // loop carries no per-cycle findings" (older loops persisted
                     // total_cycles + marginal_scores but not finding objects) — else a
                     // completed 3-cycle loop misleadingly reads "No cycles completed yet"
                     // while its header, Change Log, and ROI graph all show 3 cycles.
                     const emptyNote = findings.length === 0 && !active
                       ? (c.total_cycles > 0
                           ? `${c.total_cycles} ${c.total_cycles === 1 ? 'cycle' : 'cycles'} ran — no per-cycle detail recorded for this loop.`
                           : 'No cycles completed yet.')
                       : null
                     return (<>
                       {liveCycle}
                       {emptyNote && <p className="text-on-surface-low text-[0.8125rem] px-m py-1">{emptyNote}</p>}
                       {findings.map((f, idx) => cycleNode(f, idx))}
                     </>)
                   }
                   // With a plan → one group per phase, REVERSE-chronological (the
                   // latest/active phase first). Cycles always show under their
                   // phase; only the phase detail is behind expansion. The live
                   // in-progress cycle renders inside the active phase group.
                   return execPlan.map((_ph, pi) => pi).reverse().map((pi) => (
                     <PhaseGroup key={pi} phase={execPlan[pi]} index={pi} active={pi === activePhase}
                       minCycles={phaseMinCycles(execPlan[pi])}
                       cycles={findings.filter((f) => phaseForCycle(f.cycle, execPlan) === pi)}
                       renderCycle={cycleNode} liveCycle={pi === activePhase ? liveCycle : null} />
                   ))
                 })()}
               </div>
             ) : (
               // ── drilled-in detail: full rail height (Back returns to list) ──
               selected.kind === 'roi'
                 ? (<div className="flex flex-col gap-m">
                     <p className="text-on-surface-var text-[0.875rem]">The judge's marginal value per cycle, against your <span className="text-on-surface">{c.granularity}</span> stop threshold. A run of bars below the line is what trips the auto-stop.</p>
                     <div className="rounded-lg bg-surface-container px-m py-l">
                       <RoiRail points={roiPoints} granularity={c.granularity} />
                     </div>
                   </div>)
                 : selected.kind === 'log'
                 ? (log
                   ? (<div className="flex flex-col gap-m">
                       <div className="flex justify-end">
                         <button type="button" onClick={() => downloadText(`${safeFilename(c.name || c.goal, c.id)}-findings.md`, log, 'text/markdown;charset=utf-8')}
                           className="inline-flex items-center gap-1 rounded-md px-2 h-7 text-[0.75rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface"><Download size={13} /> Download</button>
                       </div>
                       <Markdown>{log}</Markdown>
                     </div>)
                   : <p className="text-on-surface-low text-[0.875rem]">No findings logged yet — the cumulative trail appears here as cycles complete.</p>)
                 : (() => {
                     const f = findings.find((x) => x.cycle === selected.cycle)
                     if (!f) return <p className="text-on-surface-low text-[0.875rem]">Cycle not found.</p>
                     return <CycleDetail f={f} verdict={verdictByCycle.get(f.cycle)} nudges={byCycle.get(f.cycle) ?? []} activity={running && c.total_cycles === f.cycle ? activity : []} />
                   })()
             )}
           </SidePanel>
         )}
       </AnimatePresence>
      </div>

      {/* report — expand to a full reading modal */}
      <AnimatePresence>
        {reportOpen && (
          <Modal title="Report" icon={<Spark size={18} animated={false} />} onClose={() => setReportOpen(false)}>
            <Markdown>{report}</Markdown>
          </Modal>
        )}
      </AnimatePresence>
    </div>
  )
}

function statusLabel(s: string) {
  return ({ complete: 'Completed', failed: 'Failed', stopped: 'Stopped', paused: 'Paused', stagnant: 'Stagnant', needs_input: 'Needs input', ready: 'Ready', draft: 'Draft' } as Record<string, string>)[s] ?? s
}

/** Outputs — the loop's outcomes in a tabbed shell (the general outcome channel).
 *  Each output entity gets its own tab: every artifact the worker saved, the
 *  canonical deliverable doc (when the goal type has one), and the linked Tasks.
 *  The tab strip + per-tab actions (download/expand) live in a FIXED header so
 *  they stay visible while the tab's content scrolls below. */
type OutputTab =
  | { id: string; kind: 'artifact'; label: string; artifact: Artifact }
  | { id: 'deliverable'; kind: 'deliverable'; label: string }
  | { id: 'tasks'; kind: 'tasks'; label: string }

function OutputsPanel({ loop, artifacts, tasks, report, active, onOpenArtifact, onOpenTask, onExpandReport, onDownloadReport }: {
  loop: GoalLoop; artifacts: Artifact[]; tasks: TaskItem[]; report: string; active: boolean
  onOpenArtifact?: (slug: string) => void
  onOpenTask?: (taskId: string) => void
  onExpandReport: () => void; onDownloadReport: () => void
}) {
  // The engine auto-saves the canonical deliverable as a `*-deliverable`
  // artifact AND it's served as the dedicated Deliverable tab — same content.
  // When the Deliverable tab is present, drop that duplicate artifact so the
  // document isn't shown twice in the strip.
  const artifactTabs = (report ? artifacts.filter((a) => !a.slug.endsWith('-deliverable')) : artifacts)
    .map((a): OutputTab => ({ id: `a:${a.slug}`, kind: 'artifact', label: a.name, artifact: a }))
  // Deliverable FIRST — it's the loop's canonical output, so it's the default tab
  // (landing on a sibling findings-report artifact buried the main result). Then
  // other artifacts, then Tasks.
  const tabs: OutputTab[] = [
    ...(report ? [{ id: 'deliverable', kind: 'deliverable', label: 'Deliverable' } as OutputTab] : []),
    ...artifactTabs,
    ...(tasks.length ? [{ id: 'tasks', kind: 'tasks', label: `Tasks · ${tasks.length}` } as OutputTab] : []),
  ]
  const [activeId, setActiveId] = useState<string>('')
  const [copiedReport, setCopiedReport] = useState(false)
  const copyReport = () => {
    navigator.clipboard?.writeText(report).then(() => { setCopiedReport(true); setTimeout(() => setCopiedReport(false), 1500) }).catch(() => {})
  }
  // Keep the active tab valid as outputs stream in (default to the first).
  const current = tabs.find((t) => t.id === activeId) ?? tabs[0]
  useEffect(() => {
    if (tabs.length && !tabs.some((t) => t.id === activeId)) setActiveId(tabs[0].id)
  }, [tabs.map((t) => t.id).join(','), activeId])

  return (
    // Shell-like: the panel fills the body's remaining height. The tab strip +
    // actions are a FIXED header; only the active tab's content scrolls.
    <div className="flex-1 min-h-0 rounded-xl bg-surface-container/50 flex flex-col overflow-hidden">
      <div className="shrink-0 px-l pt-m flex items-center gap-l border-b border-outline-variant/40">
        {tabs.length > 0 ? (
          <>
            {/* tab strip — contiguous tabs sitting ON the bottom border, the
                active one carrying a primary underline (reads as real tabs) */}
            <div className="flex-1 min-w-0 flex items-end gap-1 overflow-x-auto -mb-px">
              {tabs.map((t) => {
                const on = current?.id === t.id
                const Icon = t.kind === 'tasks' ? ListChecks : t.kind === 'deliverable' ? Spark : FileText
                return (
                  <button key={t.id} type="button" onClick={() => setActiveId(t.id)} role="tab" aria-selected={on}
                    className={`shrink-0 inline-flex items-center gap-1.5 px-m h-9 text-[0.8125rem] max-w-[14rem] border-b-2 transition-colors ${on ? 'border-primary text-on-surface' : 'border-transparent text-on-surface-low hover:text-on-surface-var'}`}
                    style={on ? { fontVariationSettings: '"wght" 600' } : undefined}
                    title={t.label}>
                    <Icon size={13} className="shrink-0" /><span className="truncate">{t.label}</span>
                  </button>
                )
              })}
            </div>
            {/* per-tab actions — pinned in the header so they survive scroll */}
            {current?.kind === 'deliverable' && (
              <div className="shrink-0 flex items-center gap-1">
                <IconButton icon={copiedReport ? Check : Copy} label={copiedReport ? 'Copied' : 'Copy deliverable'} size={28} onClick={copyReport} />
                <IconButton icon={Download} label="Download deliverable" size={28} onClick={onDownloadReport} />
                <IconButton icon={Maximize2} label="Expand deliverable" size={28} onClick={onExpandReport} />
              </div>
            )}
            {current?.kind === 'artifact' && onOpenArtifact && (
              <IconButton icon={ExternalLink} label="Open in Artifacts" size={28} onClick={() => onOpenArtifact(current.artifact.slug)} />
            )}
          </>
        ) : (
          <span className="h-9 inline-flex items-center text-on-surface-low text-[0.7rem] uppercase tracking-wide">Outputs</span>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-l py-l">
        {!current ? (
          <p className="text-on-surface-low text-[0.875rem]">
            {active
              ? (loop.goal_type === 'verifiable'
                  ? 'This goal produces a passing check, not a document — outcomes will appear as the worker saves them.'
                  : (loop.total_cycles > 0
                      ? 'No outputs saved yet — they’ll appear here as the loop produces them.'
                      : 'Working on the first cycle… outputs appear here as the loop produces them.'))
              : 'No outputs yet.'}
          </p>
        ) : current.kind === 'deliverable' ? (
          <DeliverableDoc report={report} loop={loop} />
        ) : current.kind === 'tasks' ? (
          <div className="flex flex-col gap-1.5">
            {tasks.map((t) => {
              const done = t.status === 'done'
              const box = (
                <span className="shrink-0 inline-flex size-4 items-center justify-center rounded-sm border" style={{ borderColor: done ? 'var(--color-ok)' : 'var(--color-outline-variant)', background: done ? 'var(--color-ok)' : 'transparent' }}>{done && <Check size={11} className="text-on-primary" />}</span>
              )
              const label = <span className={`flex-1 min-w-0 truncate ${done ? 'text-on-surface-low line-through' : 'text-on-surface'}`}>{t.title}</span>
              const status = <span className="shrink-0 text-on-surface-low text-[0.7rem]">{t.status}</span>
              // When a task-open handler is wired, each row is a button that
              // deep-links to that task on the Tasks page (read its detail, edit,
              // see deps) — parity with the artifact tab's "Open" affordance.
              return onOpenTask ? (
                <button key={t.id} type="button" onClick={() => onOpenTask(t.id)}
                  className="group flex w-full items-center gap-s rounded-md px-2 py-1 -mx-2 text-left text-[0.875rem] hover:bg-surface-2 transition-colors"
                  title="Open task">
                  {box}{label}{status}
                  <ChevronRight size={14} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 transition-opacity" />
                </button>
              ) : (
                <div key={t.id} className="flex items-center gap-s text-[0.875rem]">
                  {box}{label}{status}
                </div>
              )
            })}
          </div>
        ) : (
          <ArtifactTab artifact={current.artifact} onOpen={onOpenArtifact} />
        )}
      </div>
    </div>
  )
}

/** A document reading surface: an elevated, padded "page" so long-form output
 *  reads as a finished document sitting on the panel rather than raw markdown
 *  flush against the container. Optional eyebrow line carries source metadata. */
function DocSurface({ eyebrow, children }: { eyebrow?: React.ReactNode; children: string }) {
  return (
    <div className="rounded-xl bg-surface px-2xl py-xl ring-1 ring-outline-variant/30" style={{ boxShadow: 'var(--shadow-composer)' }}>
      {eyebrow && <div className="mb-l flex items-center gap-s text-on-surface-low text-[0.7rem] uppercase tracking-wide border-b border-outline-variant/30 pb-m">{eyebrow}</div>}
      <Markdown>{children}</Markdown>
    </div>
  )
}

/** The canonical deliverable, rendered as a document page. */
function DeliverableDoc({ report, loop }: { report: string; loop: GoalLoop }) {
  // The goal_type suffix applies only to the goal kind (general/design have none —
  // the adapter defaults it, which would dangle a spurious "· Open-ended" on them).
  const isGoal = (loop as { kind?: string }).kind === 'goal'
  return (
    <DocSurface eyebrow={<><span className="text-primary inline-flex"><Spark size={12} /></span> Deliverable{isGoal ? ` · ${GOAL_TYPE_LABEL[loop.goal_type] ?? loop.goal_type}` : ''}</>}>
      {report}
    </DocSurface>
  )
}

/** One artifact's tab body: its metadata + a live render of its content (fetched
 *  on demand), as a document page with a click-through to the full Artifacts page. */
function ArtifactTab({ artifact, onOpen }: { artifact: Artifact; onOpen?: (slug: string) => void }) {
  const [content, setContent] = useState<string | null>(artifact.content ?? null)
  useEffect(() => {
    if (content != null) return
    let alive = true
    api.artifact(artifact.slug).then((full) => { if (alive) setContent(full.content ?? '') }).catch(() => { if (alive) setContent('') })
    return () => { alive = false }
  }, [artifact.slug])
  const ctype = useMemo(() => resolveContentType({ kind: artifact.kind }), [artifact.kind])

  const eyebrow = (
    <>
      <FileText size={12} className="text-primary" />
      <span className="normal-case tracking-normal text-[0.75rem]">{artifact.kind}{artifact.version > 1 ? ` · v${artifact.version}` : ''}</span>
      {onOpen && <button type="button" onClick={() => onOpen(artifact.slug)} className="ml-auto inline-flex items-center gap-1 normal-case tracking-normal text-primary hover:underline">Open in Artifacts <ExternalLink size={12} /></button>}
    </>
  )
  if (content == null) return <DocSurface eyebrow={eyebrow}>{'Loading…'}</DocSurface>
  if (!content.trim()) return (
    <DocSurface eyebrow={eyebrow}>{'_This artifact has no inline content — open it in Artifacts to view._'}</DocSurface>
  )
  // markdown → the deliberate prose reading surface; everything else → the shared
  // render engine (read-only), so HTML/SVG/React/json render exactly as on the
  // Artifacts page (same sanitizer, same sandbox) — no parallel kind dispatch here.
  if (ctype.id === 'markdown' || ctype.id === 'text') return <DocSurface eyebrow={eyebrow}>{content}</DocSurface>
  return (
    <div className="rounded-xl overflow-hidden bg-surface ring-1 ring-outline-variant/30" style={{ boxShadow: 'var(--shadow-composer)' }}>
      <div className="flex items-center gap-s bg-surface px-l py-2 text-on-surface-low text-[0.7rem] uppercase tracking-wide border-b border-outline-variant/30">{eyebrow}</div>
      <div className="h-[60vh]"><ContentSurface type={ctype} content={content} title={artifact.name} docId={artifact.slug} readOnly /></div>
    </div>
  )
}

function LiveSubsteps({ activity }: { activity: { kind: string; label: string; detail?: string }[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-1.5 pl-7">
      <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-1.5 text-[0.75rem] text-on-surface-low hover:text-on-surface">
        <Search size={12} /> {activity.length} steps <ChevronRight size={12} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <ul className="mt-1 flex flex-col gap-1">
          {activity.slice(-12).map((e, i) => (
            <li key={i} className="flex items-start gap-s text-[0.75rem] text-on-surface-low">
              <span className="mt-1.5 size-1 shrink-0 rounded-pill" style={{ background: e.kind === 'tool' ? 'var(--color-secondary)' : 'var(--color-on-surface-low)' }} />
              <span className="truncate">{e.label}{e.detail ? ` · ${e.detail}` : ''}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** A row in the Details rail primary list — drills into its detail on click. */
function RailRow({ icon, label, hint, onClick }: {
  icon: React.ReactNode; label: string; hint?: string; onClick: () => void
}) {
  return (
    <button type="button" onClick={onClick}
      className="group w-full text-left rounded-lg px-m py-2.5 flex items-center gap-s hover:bg-surface-high transition-colors">
      <span className="shrink-0 text-primary">{icon}</span>
      <span className="flex-1 truncate text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 500' }}>{label}</span>
      {hint && <span className="shrink-0 text-on-surface-low text-[0.7rem]">{hint}</span>}
      <ChevronRight size={15} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 transition-opacity" />
    </button>
  )
}

/** A planned-phase group in the Details rail: a header (number/role/active/cycle
 *  count) with that phase's cycles ALWAYS shown beneath (newest first), and the
 *  live in-progress cycle for the active phase. Expanding the header reveals the
 *  phase DETAIL only — target, exit, agent, skills, workflows. Auto-expanded for
 *  the active phase. */
function PhaseGroup({ phase, index, active, minCycles, cycles, renderCycle, liveCycle }: {
  phase: Record<string, unknown>; index: number; active: boolean; minCycles: number
  cycles: LoopFinding[]; renderCycle: (f: LoopFinding, idx: number) => React.ReactNode
  liveCycle?: React.ReactNode
}) {
  const [open, setOpen] = useState(active)
  const role = String(phase.role || '').trim()
  const target = String(phase.target || '').trim()
  const exit = String(phase.phase_exit || '').trim()
  const agent = String(phase.agent_name || '').trim()
  const skills = (phase.skill_ids as string[]) || []
  const wfs = (phase.workflow_ids as string[]) || []
  // Cycles newest-first within the phase (mirrors the latest-first phase order).
  const orderedCycles = [...cycles].reverse()
  return (
    <div className={`rounded-lg ${active ? 'ring-1 ring-primary/40' : ''}`} style={{ background: 'color-mix(in srgb, var(--color-surface-container) 55%, transparent)' }}>
      <button type="button" onClick={() => setOpen((v) => !v)} className="w-full flex items-center gap-s px-m py-2 text-left">
        <ChevronRight size={13} className={`shrink-0 text-on-surface-low transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="shrink-0 inline-flex size-5 items-center justify-center rounded-pill bg-surface-high text-on-surface-low text-[0.65rem] tabular-nums">{index + 1}</span>
        {/* role + the agent definition backing it this phase (always visible). */}
        <span className="flex-1 min-w-0 flex flex-col">
          <span className="truncate text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>{role || `Phase ${index + 1}`}</span>
          <span className="truncate text-on-surface-low text-[0.65rem]">{agent ? <><Bot size={9} className="inline -mt-0.5 mr-0.5" />{agent}</> : 'default worker'}</span>
        </span>
        {active && <span className="shrink-0 self-start text-primary text-[0.6rem] uppercase tracking-wide">● active</span>}
        {/* Count: "done/min" while still inside the minimum; once the phase has
            met (or exceeded) its minimum, show just the cycle count so it never
            reads as a broken fraction like "4/1". */}
        <span className="shrink-0 text-on-surface-low text-[0.7rem] tabular-nums" title={`${cycles.length} cycle${cycles.length !== 1 ? 's' : ''} run · minimum ${minCycles}`}>
          {cycles.length >= minCycles ? `${cycles.length} ${cycles.length === 1 ? 'cycle' : 'cycles'}` : `${cycles.length}/${minCycles}`}
        </span>
      </button>
      {/* phase DETAIL — only behind expansion */}
      {open && (
        <div className="px-m pb-2 pl-[42px] flex flex-col gap-1">
          {target && <span className="text-on-surface-var text-[0.8125rem]">{target}</span>}
          {exit && <span className="text-on-surface-low text-[0.7rem]">↳ advances when: {exit}</span>}
          {/* agent shows in the header; here we detail the loaded capabilities. */}
          {(skills.length || wfs.length) ? (
            <div className="flex flex-wrap items-center gap-1 mt-0.5 text-[0.65rem]">
              {skills.map((s) => <span key={s} className="inline-flex items-center rounded-pill px-1.5 h-5 bg-surface-high text-on-surface-low" title="Skill loaded this phase">{s}</span>)}
              {wfs.map((w) => <span key={w} className="inline-flex items-center rounded-pill px-1.5 h-5 bg-surface-high text-on-surface-low" title="Workflow loaded this phase">{w}</span>)}
            </div>
          ) : <span className="text-on-surface-low text-[0.65rem]">baseline capabilities only</span>}
        </div>
      )}
      {/* cycles ALWAYS shown under the phase (live first, then newest→oldest) */}
      <div className="px-m pb-2 flex flex-col gap-1">
        {liveCycle}
        {orderedCycles.map((f, i) => renderCycle(f, i))}
        {!liveCycle && orderedCycles.length === 0 && <p className="text-on-surface-low text-[0.7rem]">Not started.</p>}
      </div>
    </div>
  )
}

function CycleNode({ f, verdict, dur, hasNudge, onClick, delay }: { f: LoopFinding; verdict?: LoopVerdict; dur?: number; hasNudge: boolean; onClick: () => void; delay: number }) {
  return (
    <motion.button initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialFast, delay }} onClick={onClick}
      className="group w-full text-left rounded-lg bg-surface-container px-m py-m hover:bg-surface-high transition-colors">
      <div className="flex items-center gap-s">
        <span className="shrink-0 inline-flex items-center justify-center size-5 rounded-pill text-[0.7rem] tabular-nums" style={{ background: 'color-mix(in srgb, var(--color-primary) 20%, transparent)', color: 'var(--color-on-surface)' }}>{f.cycle}</span>
        <span className="flex-1 truncate text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 500' }}>{asText(f.key_insight) || asText(f.summary) || `Cycle ${f.cycle}`}</span>
        {hasNudge && <MessageSquarePlus size={13} className="text-info shrink-0" />}
        {typeof verdict?.marginal_value === 'number' && <span className="shrink-0 text-on-surface-low text-[0.7rem] tabular-nums" title="judge's marginal value (return this cycle)">▲{verdict.marginal_value.toFixed(1)}</span>}
        <ChevronRight size={15} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>
      {dur != null && dur > 0 && <div className="mt-1 pl-7 text-on-surface-low text-[0.7rem] tabular-nums">took {fmt(dur)}</div>}
    </motion.button>
  )
}

/** Body content for one cycle — rendered inside the reusable SidePanel (docked
 *  or full-width). Lists the cycle's actual evidence + the judge's third-party
 *  verdict (never a worker self-verdict). */
function CycleDetail({ f, verdict, nudges, activity }: { f: LoopFinding; verdict?: LoopVerdict; nudges: LoopNudge[]; activity: { kind: string; label: string; detail?: string }[] }) {
  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap gap-s">
        {typeof verdict?.marginal_value === 'number' && typeof verdict?.quality_score === 'number' && <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${verdict.done ? 'var(--color-ok)' : 'var(--color-primary)'} 18%, transparent)`, color: verdict.done ? 'var(--color-ok)' : 'var(--color-primary)' }}>{verdict.done ? <Check size={13} /> : null} judge ▲{verdict.marginal_value.toFixed(1)} · ★{verdict.quality_score.toFixed(1)}</span>}
        {verdict?.adversarial && <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem] bg-surface-high text-on-surface-var" title="A second, skeptical judge independently cross-checked this verdict (adversarial review)."><ShieldCheck size={13} className="text-on-surface-low" /> cross-checked</span>}
        {f.metric && typeof f.metric.value === 'number' && <span className="inline-flex items-center rounded-pill px-m h-7 text-[0.8125rem] bg-surface-high text-on-surface-var">{asText(f.metric.name) || 'metric'}: {f.metric.value}</span>}
        {typeof f.new_findings_count === 'number' && <span className="inline-flex items-center rounded-pill px-m h-7 text-[0.8125rem] bg-surface-high text-on-surface-var">{f.new_findings_count} new this cycle</span>}
      </div>
      {f.key_insight && <Section label="Key insight"><p className="text-on-surface text-[0.9375rem] leading-relaxed">{asText(f.key_insight)}</p></Section>}
      {f.summary && <Section label="Summary"><p className="text-on-surface-var text-[0.875rem] leading-relaxed">{asText(f.summary)}</p></Section>}
      {f.evidence && <Section label="Evidence"><p className="text-on-surface-var text-[0.875rem] leading-relaxed whitespace-pre-wrap">{asText(f.evidence)}</p></Section>}
      {f.files_touched?.length ? (
        <Section label="Files written">
          <div className="flex flex-col gap-1.5">
            {f.files_touched.map((s, i) => (
              <div key={`f${i}`} className="flex items-start gap-s text-on-surface text-[0.8125rem]">
                <FileText size={13} className="text-on-surface-low shrink-0 mt-0.5" />
                <span className="font-mono text-[0.75rem] break-words">{asText(s)}</span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}
      {verdict?.done_reason && <Section label="Judge verdict"><p className="text-on-surface-var text-[0.8125rem]">{asText(verdict.done_reason)}{typeof verdict.band_used === 'number' && <span className="text-on-surface-low"> · returns band {verdict.band_used.toFixed(1)}</span>}</p></Section>}
      {(f.sources_checked?.length || f.sources_empty?.length) ? (
        <Section label="Sources">
          <div className="flex flex-col gap-1.5">
            {f.sources_checked?.map((s, i) => <div key={`c${i}`} className="flex items-start gap-s text-on-surface text-[0.8125rem]"><Check size={13} className="text-ok shrink-0 mt-0.5" /><span className="font-mono text-[0.75rem] break-words">{asText(s)}</span></div>)}
            {f.sources_empty?.map((s, i) => <div key={`e${i}`} className="flex items-start gap-s text-on-surface-low text-[0.8125rem]"><X size={13} className="shrink-0 mt-0.5" /><span className="font-mono text-[0.75rem] break-words">{asText(s)} <span className="opacity-60">(empty)</span></span></div>)}
          </div>
        </Section>
      ) : null}
      {nudges.length > 0 && (
        <Section label={`Nudges applied (${nudges.length})`}>
          <div className="flex flex-col gap-s">
            {nudges.map((n, i) => (
              <div key={i} className="flex gap-s">
                <CornerDownRight size={14} className="text-info shrink-0 mt-1" />
                <div className="flex-1 rounded-md px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-info) 8%, transparent)' }}>
                  <div className="text-info text-[0.7rem] uppercase tracking-wide mb-1">sent cycle {n.sent_at_cycle} · applied cycle {n.applied_cycle}</div>
                  <p className="text-on-surface text-[0.875rem] leading-relaxed whitespace-pre-wrap">{n.text}</p>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}
      {activity.length > 0 && (
        <Section label="Live activity">
          <div className="flex flex-col gap-1.5">
            {activity.slice(-20).map((e, i) => (
              <div key={i} className="flex items-start gap-s text-[0.8125rem]">
                <span className="mt-1.5 size-1 shrink-0 rounded-pill" style={{ background: e.kind === 'tool' ? 'var(--color-secondary)' : 'var(--color-on-surface-low)' }} />
                <span className="text-on-surface-var"><span className="text-on-surface">{e.label}</span>{e.detail ? ` · ${e.detail}` : ''}</span>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}

function groupNudges(nudges: LoopNudge[]) {
  const byCycle = new Map<number, LoopNudge[]>(); const pending: LoopNudge[] = []
  for (const n of nudges) {
    if (n.applied_cycle == null) pending.push(n)
    else { const arr = byCycle.get(n.applied_cycle) ?? []; arr.push(n); byCycle.set(n.applied_cycle, arr) }
  }
  return { byCycle, pending }
}
