/** Pure, kind-agnostic fold of a run (Loop) + its lifecycle events into ONE
 *  view-model — the shared source of truth for every run surface (Loop Cockpit,
 *  Code Cockpit, in-chat SdlcProgressCard, Plan Review). No React, no fetch: it's
 *  a pure function of (snapshot, transient-flags), so the "which events must be
 *  handled" contract is UNIT-TESTABLE instead of a hand-maintained comment spread
 *  across three inline switches (P16).
 *
 *  Split of responsibilities:
 *   • `foldRunSnapshot(loop)` — everything derivable from the persisted snapshot
 *     (phases/steps, progress, parked, scores, marginals). Reruns on every snapshot.
 *   • `foldReducer(flags, event, data)` — folds a TRANSIENT lifecycle event into the
 *     small set of not-persisted flags (gate failure, stall, judge degraded). These
 *     are the states the old inline cockpit folds tracked in component state.
 *   • `foldRun(loop, flags)` — merges the two into the final RunViewModel.
 *
 *  Parity is the whole point: each branch below reproduces a hard-won fix from the
 *  inline folds it replaces (keyed by the comment tags), and runFold.test.ts locks
 *  each one as a test case. */

import { activePhaseIndex, phaseForCycle, type Phase } from './loopPhases'

export type RunStepState = 'done' | 'active' | 'todo'
export interface RunStep { label: string; state: RunStepState; key: string }

export interface GateFailure { label: string; command: string; output: string }
export interface StallInfo { stage: string; title: string; findings: number }

/** The transient, NOT-persisted flags a run's lifecycle events toggle. Mirrors the
 *  ad-hoc component state the inline cockpit folds kept (gateFail/stalled/judgeDegraded). */
export interface RunFlags {
  gate: GateFailure | null
  stall: StallInfo | null
  judgeDegraded: boolean
  /** Set when a `deleted` event arrives — the run is gone; callers flip to not-found. */
  deleted: boolean
}

export const emptyRunFlags = (): RunFlags => ({ gate: null, stall: null, judgeDegraded: false, deleted: false })

export interface RunViewModel {
  id: string
  kind: string
  /** true for a phase-planned kind (code/design/general); false for a goal loop. */
  phased: boolean
  status: string
  /** Parked = sitting AT its stage, not progressing — color warn (matches the cockpit). */
  parked: boolean
  totalCycles: number
  maxCycles: number
  phaseDone: number
  phaseTotal: number
  /** "N/M stages" for phased kinds, the goal-type label for a goal loop, else "". */
  progressLabel: string
  steps: RunStep[]
  activePhase: number
  bestScore: number | null
  lastScore: number | null
  marginals: number[]
  elapsedSeconds: number
  gate: GateFailure | null
  stall: StallInfo | null
  judgeDegraded: boolean
}

// A run is "parked" (stopped at its active stage, not progressing) in these statuses;
// the SdlcProgressCard + cockpit color it warn. `status` is the EFFECTIVE status.
const PARKED = new Set(['blocked', 'needs_input', 'stagnant', 'failed', 'stopped', 'ended_early'])

const GOAL_TYPE_LABEL: Record<string, string> = {
  open_ended: 'Open-ended', verifiable: 'Verifiable', monitor: 'Monitoring',
}

/** The minimal shape foldRun reads off a Loop — a structural subset so the fold is
 *  testable without constructing a full Loop (and decoupled from api.ts churn). */
export interface RunSnapshot {
  id: string
  kind: string
  status: string
  total_cycles?: number
  max_cycles?: number
  plan?: Phase[]
  phase_status?: Record<string, string>
  kind_config?: Record<string, unknown>
  elapsed_seconds?: number
  best_score?: number
  last_score?: number
  marginal_scores?: number[]
}

/** The snapshot-derived half of the view-model (everything foldRunSnapshot returns —
 *  the full RunViewModel minus the transient lifecycle flags). Components that only
 *  render persisted progress (e.g. RunProgress) accept THIS, so they don't demand a
 *  gate/stall/judgeDegraded the caller may not have folded yet. */
export type RunSnapshotViewModel = Omit<RunViewModel, keyof RunFlags>

/** Derive everything the persisted snapshot determines (no transient flags). */
export function foldRunSnapshot(loop: RunSnapshot): RunSnapshotViewModel {
  const kind = loop.kind
  const phased = kind !== 'goal'
  const kc = (loop.kind_config || {}) as Record<string, unknown>
  const totalCycles = loop.total_cycles ?? 0
  const plan = loop.plan || []

  let phaseDone = 0, phaseTotal = 0, progressLabel = ''
  const steps: RunStep[] = []
  if (phased) {
    // code/design/general: real per-phase done-state from plan[] + phase_status{}.
    const ss = loop.phase_status || {}
    phaseTotal = plan.length
    phaseDone = Object.values(ss).filter((s) => s === 'done').length
    progressLabel = plan.length ? `${phaseDone}/${phaseTotal} stages` : ''
    for (const s of plan) {
      // Key EXACTLY as the backend's phase_key: `stage.trim() || title.trim()`. The old
      // nullish `s.stage ?? s.title` kept an EMPTY-string stage (deliberately emitted for
      // a titled-but-stageless row) → skey '' → phase_status miss → stage stuck 'todo'.
      const skey = (String((s as Record<string, unknown>).stage ?? '').trim()
        || String((s as Record<string, unknown>).title ?? '').trim())
      const st = ss[skey]
      steps.push({
        key: skey,
        label: String((s as Record<string, unknown>).title || (s as Record<string, unknown>).stage || ''),
        state: st === 'done' ? 'done' : (st === 'active' || st === 'running') ? 'active' : 'todo',
      })
    }
  } else {
    // goal: no per-step done-state (advances by cycles) → show the goal TYPE as progress,
    // list sub-goals as todo rows.
    progressLabel = GOAL_TYPE_LABEL[String(kc.goal_type ?? '')] ?? ''
    const subs = Array.isArray(kc.sub_goals) ? (kc.sub_goals as string[]) : []
    for (const g of subs) steps.push({ key: g, label: g, state: 'todo' })
  }

  // best/last rubric score rides kind_config on the unified Loop (not a top-level field);
  // read from kc first, fall back to a top-level field if present.
  const scoreSrc = { ...kc, ...(loop as unknown as Record<string, unknown>) }
  const bestScore = typeof scoreSrc.best_score === 'number' ? scoreSrc.best_score : null
  const lastScore = typeof scoreSrc.last_score === 'number' ? scoreSrc.last_score : null
  const marginals = Array.isArray(loop.marginal_scores) ? loop.marginal_scores.slice(-16) : []

  // The active phase index (cumulative min_cycles windows) — same helper the cockpit +
  // goals-list peek share, so the fold agrees with them.
  const activePhase = phased ? activePhaseIndex(totalCycles, plan) : phaseForCycle(totalCycles, plan)

  return {
    id: loop.id,
    kind,
    phased,
    status: loop.status,
    parked: PARKED.has(loop.status),
    totalCycles,
    maxCycles: loop.max_cycles ?? 0,
    phaseDone,
    phaseTotal,
    progressLabel,
    steps,
    activePhase,
    bestScore,
    lastScore,
    marginals,
    elapsedSeconds: loop.elapsed_seconds ?? 0,
  }
}

/** Fold ONE transient lifecycle event into the flag set. Pure: returns a new RunFlags
 *  (never mutates). Reproduces the inline cockpit fold's parity fixes:
 *   • gate_check ok:false → record the failure; ok:true → CLEAR it (a gate that re-ran
 *     and passed clears a stale banner even with no stage_advance).
 *   • stage_stalled → record the stall; any FORWARD event clears it — EXCEPT `blocked`,
 *     which keeps the stall (the stall is the REASON for the block; its one-shot event
 *     won't re-fire, so clearing it would leave a contextless "blocked").
 *   • judge_error → degraded; cycle_verdict → clears it.
 *   • deleted → mark gone. */
export function foldReducer(flags: RunFlags, event: string, data?: unknown): RunFlags {
  const d = (data ?? {}) as Record<string, unknown>
  switch (event) {
    case 'deleted':
      return { ...flags, deleted: true }
    case 'gate_check': {
      if (d.ok === false) {
        return { ...flags, gate: { label: String(d.label || 'check'), command: String(d.command || ''), output: String(d.output || '') } }
      }
      return { ...flags, gate: null } // re-ran + passed → clear a stale failure banner
    }
    case 'stage_stalled':
      return { ...flags, stall: { stage: String(d.stage || ''), title: String(d.title || d.stage || 'this stage'), findings: Number(d.findings || 0) } }
    case 'judge_error':
      return { ...flags, judgeDegraded: true }
    case 'cycle_verdict':
      return { ...flags, judgeDegraded: false, stall: null, gate: null }
    case 'blocked':
      // Keep the stall (it's the reason for the block); clear a stale gate banner.
      return { ...flags, gate: null }
    // Any OTHER lifecycle event is forward progress → clear the transient stall + gate.
    // (new_finding / stage_advance / rolled_back / complete / resume / …)
    default:
      return { ...flags, stall: null, gate: null }
  }
}

/** Merge the snapshot derivation with the transient flags into the final view-model. */
export function foldRun(loop: RunSnapshot, flags: RunFlags = emptyRunFlags()): RunViewModel {
  return {
    ...foldRunSnapshot(loop),
    gate: flags.gate,
    stall: flags.stall,
    judgeDegraded: flags.judgeDegraded,
  }
}
