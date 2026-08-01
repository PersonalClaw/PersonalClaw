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
  complete: { label: 'Complete', icon: CircleCheck, tone: 'text-success' },
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

export function fmtElapsed(secs: number | undefined): string {
  if (!secs || secs <= 0) return ''
  if (secs < 60) return `${Math.round(secs)}s`
  const m = Math.floor(secs / 60)
  if (m < 60) return `${m}m ${Math.round(secs % 60)}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}
