import { CircleCheck, CircleDashed, CircleSlash, Clock, Loader2, OctagonAlert, Pause, TriangleAlert, type LucideIcon } from 'lucide-react'
import type { WorkflowRunStatus } from '../../lib/api'

/** Presentation for one run status. Centralized so the list, the run view and any future
 *  card render the SAME icon and tone for a given state — three components each picking
 *  their own colour is how a "failed" run ends up looking calm in one place and alarming
 *  in another. */
export interface StatusLook { label: string; icon: LucideIcon; tone: string; spin?: boolean }

const RUN_LOOK: Record<WorkflowRunStatus, StatusLook> = {
  draft: { label: 'Draft', icon: CircleDashed, tone: 'text-on-surface-low' },
  running: { label: 'Running', icon: Loader2, tone: 'text-on-surface', spin: true },
  paused: { label: 'Paused', icon: Pause, tone: 'text-on-surface-low' },
  // needs_input is the only status a user can ACT on, so it is the only one that gets a
  // warning tone in the list — everything else is informational.
  needs_input: { label: 'Needs you', icon: TriangleAlert, tone: 'text-warning' },
  // "Completed", not "Complete" — the past-tense form the other two status registries already use
  // for this exact wire value (`lib/loopStatus.complete` and `taskMeta`'s terminal `done`). A run
  // that has finished is being DESCRIBED, and "Complete" reads as an adjective (or worse, as the
  // imperative verb TasksListPage uses for its "Complete this task" action), so the same backend
  // `"complete"` was narrated three different ways across three surfaces a user moves between.
  complete: { label: 'Completed', icon: CircleCheck, tone: 'text-success' },
  failed: { label: 'Failed', icon: OctagonAlert, tone: 'text-danger' },
  cancelled: { label: 'Cancelled', icon: CircleSlash, tone: 'text-on-surface-low' },
  escalated: { label: 'Escalated', icon: TriangleAlert, tone: 'text-danger' },
}

export function runLook(status: string): StatusLook {
  return RUN_LOOK[status as WorkflowRunStatus] ?? { label: status || 'Unknown', icon: CircleDashed, tone: 'text-on-surface-low' }
}

/** Presentation for a node-instance state. The engine's outcome vocabulary is wider than
 *  done|failed — `degraded`, `no_change`, `scope_violation`, `blocked` and `escalated` are
 *  first-class — and flattening them in the UI would throw away exactly the distinction
 *  the backend went to the trouble of keeping. */
const NODE_LOOK: Record<string, StatusLook> = {
  pending: { label: 'Pending', icon: CircleDashed, tone: 'text-on-surface-low' },
  ready: { label: 'Ready', icon: CircleDashed, tone: 'text-on-surface-low' },
  running: { label: 'Running', icon: Loader2, tone: 'text-on-surface', spin: true },
  waiting: { label: 'Waiting', icon: Clock, tone: 'text-warning' },
  done: { label: 'Done', icon: CircleCheck, tone: 'text-success' },
  // Degraded is a SUCCESS with a reason — shown as success-adjacent, never as a failure,
  // or a user "fixes" a run that worked.
  degraded: { label: 'Degraded', icon: TriangleAlert, tone: 'text-warning' },
  no_change: { label: 'No change', icon: CircleCheck, tone: 'text-on-surface-low' },
  skipped: { label: 'Skipped', icon: CircleSlash, tone: 'text-on-surface-low' },
  failed: { label: 'Failed', icon: OctagonAlert, tone: 'text-danger' },
  scope_violation: { label: 'Scope violation', icon: OctagonAlert, tone: 'text-danger' },
  blocked: { label: 'Blocked', icon: OctagonAlert, tone: 'text-danger' },
  escalated: { label: 'Escalated', icon: TriangleAlert, tone: 'text-danger' },
  cancelled: { label: 'Cancelled', icon: CircleSlash, tone: 'text-on-surface-low' },
  discarded: { label: 'Discarded', icon: CircleSlash, tone: 'text-on-surface-low' },
}

export function nodeLook(state: string): StatusLook {
  return NODE_LOOK[state] ?? { label: state || 'Unknown', icon: CircleDashed, tone: 'text-on-surface-low' }
}

/** Statuses after which a run will not move on its own. Used to decide whether to hold an
 *  SSE connection open and whether to offer live controls. */
export const TERMINAL_RUN_STATUSES = new Set<string>(['complete', 'failed', 'cancelled', 'escalated'])

export const isTerminal = (status: string) => TERMINAL_RUN_STATUSES.has(status)

/** Statuses BEFORE anything has executed — the run's spec and policy overlay are still
 *  editable (mirrors `workflows/models.py:RUN_PHASES`' PRELAUNCH phase, deliberately a set
 *  rather than a `=== 'draft'` literal so a future prelaunch status inherits every gate that
 *  reads this). The policy-overrides editor shows only here: once launched, the engine's own
 *  whole-row saves would silently revert a live overlay edit (PP-16 seam 4f). */
export const PRELAUNCH_RUN_STATUSES = new Set<string>(['draft'])

export const isPrelaunch = (status: string) => PRELAUNCH_RUN_STATUSES.has(status)

/** Node-INSTANCE states after which a node will not run again without an explicit mutation —
 *  the exact set the `/inspect` endpoint accepts (mirrors `workflows/models.py:TERMINAL_STATES`).
 *  A node in any other state has nothing to reconstruct yet, so the endpoint 409s; the run view
 *  gates the Inspect affordance on this so the click is offered only where it can succeed. */
export const TERMINAL_NODE_STATES = new Set<string>([
  'done', 'degraded', 'failed', 'skipped', 'no_change', 'scope_violation', 'discarded', 'escalated', 'blocked', 'cancelled',
])

export const isNodeTerminal = (state: string) => TERMINAL_NODE_STATES.has(state)

/** Node instances a `foreach` body produces share one node id, so the INSTANCE PATH is the
 *  stable key. Strips the `#i` / `@n` suffix for display without losing which instance a
 *  row is. */
export function nodeLabel(node: { node_id: string; instance_path: string }): string {
  if (node.node_id) {
    const suffix = node.instance_path.match(/[#@]\d+$/)
    return suffix ? `${node.node_id} ${suffix[0]}` : node.node_id
  }
  return node.instance_path
}

/** The per-item progress prefix for a `foreach` row — `[3/12] auth.py` (WF2-R5).
 *
 *  Returns '' for a non-iterated node, so a caller renders nothing rather than an empty
 *  bracket. Twelve identical rows distinguishable only by an index suffix are technically
 *  correct and useless for answering "which item is stuck?" — this is what makes them
 *  distinguishable.
 *
 *  The counter renders 1-BASED: the engine's `item_index` is a 0-based array position, and
 *  "[0/12]" reads as "none done yet" to a human rather than "the first one". */
export function itemProgress(node: {
  item_index?: number; item_total?: number; item_label?: string
}): string {
  const parts: string[] = []
  if (typeof node.item_index === 'number') {
    parts.push(node.item_total
      ? `[${node.item_index + 1}/${node.item_total}]`
      : `[${node.item_index + 1}]`)
  }
  if (node.item_label) parts.push(node.item_label)
  return parts.join(' ')
}

/** Depth of an instance path, for indenting the node list into its tree shape. Counts the
 *  structural separators the engine's path grammar uses (`root.children[0].body`). */
export function nodeDepth(instancePath: string): number {
  return Math.max(0, (instancePath.match(/\.(children\[\d+\]|body|cases\[[^\]]*\]|default)/g) ?? []).length - 1)
}

/** An elapsed time for an INLINE chip, where nothing is a legitimate rendering.
 *
 *  The empty string is the contract, not an oversight: every caller here guards on it to decide
 *  whether the chip appears at all (`{elapsed && <span…>}` in `WorkflowsListPage`,
 *  `run.elapsed_secs ? … : null` in `WorkflowRunDetail`, and the two equivalents in
 *  `WorkflowProgressCard`/`IntrospectPanel`'s timeline). A run that has not started has no elapsed
 *  time to show, and putting "0s" beside its name would read as a finished instant run.
 *
 *  🪤 It is therefore the WRONG formatter for a `<Stat>` cell or a rail cell, which render their
 *  label unconditionally — use `elapsedStat`. */
export function fmtElapsed(secs: number | undefined): string {
  if (!secs || secs <= 0) return ''
  if (secs < 60) return `${Math.round(secs)}s`
  const m = Math.floor(secs / 60)
  if (m < 60) return `${m}m ${Math.round(secs % 60)}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

/** The same measurement for a LABELLED cell — a `<Stat>` or a rail `<dd>`, where the label is
 *  rendered whether or not the value is.
 *
 *  🔴 A measured zero must not render as nothing. Measured on a fresh container: the deterministic
 *  bundled template `knowledge-health` completed in under 10ms, and its run page rendered FOUR
 *  labels with no value at all — `Duration`, `Duration p50`, `Duration p95` (all from
 *  `stats.duration_secs: 0.0`) and the findings rail's `Took` for the step whose
 *  `duration_secs` was exactly `0.0`. The sibling step's `0.007` rounded to `0s` and rendered
 *  fine, so ONE panel showed the same label as `0s` and as blank space for two measurements seven
 *  milliseconds apart. A label with nothing after it reads as a broken panel, and it is also
 *  indistinguishable from `LedgerRailsPanel`'s deliberate em dash, which means "the ledger did not
 *  carry this key" — the opposite claim.
 *
 *  This is the shape `runCostStat` and `runTokensStat` already exist for: a cell needs its own
 *  formatter because the inline chip's "render nothing" is not available to it. The rounding is
 *  `fmtElapsed`'s, unchanged, so the two never disagree about a non-zero figure. */
export function elapsedStat(secs: number): string {
  // A non-finite figure is not a measurement of zero — say nothing was measured rather than
  // asserting an instant. `LedgerRailsPanel.cell` already screens `null`/`undefined`; this covers
  // the `NaN` a division could hand a `<Stat>` directly.
  if (!Number.isFinite(secs)) return 'not recorded'
  return secs > 0 ? fmtElapsed(secs) : '0s'
}

/** "To first output", said at the resolution it was measured at.
 *
 *  `null` is a run no step has produced output for, and the cell must say so: `0 ms` there
 *  claimed output arrived instantly on a run that never produced any. The figure is a difference
 *  of journal timestamps, which are whole seconds, so anything under 1000 means "within the first
 *  second". Printed as `0 ms` for a step that answered in 400 ms, it was false precision too. */
export function firstOutputStat(ms: number | null | undefined, finished: boolean): string {
  if (ms == null || !Number.isFinite(ms)) return finished ? 'no output' : 'none yet'
  if (ms < 1000) return 'under 1s'
  return elapsedStat(ms / 1000)
}
