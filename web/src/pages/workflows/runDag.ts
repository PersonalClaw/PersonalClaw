import type { WorkflowContinuation, WorkflowNodeState } from '../../lib/api'
import type { DagEdge, DagNode, DagNodeState } from '../tasks/DagView'
import { buildTree } from './nodeTree'

/** Laying a run's node instances out as a DAG (TASKS-SOPS §7 R6).
 *
 *  `DagView` takes pre-computed geometry — it draws, it does not lay out. This module is that
 *  layout, kept PURE and separate from the view for the reason the rest of this directory is:
 *  the interesting behaviour is which node lands where, in what state, with which verbs enabled,
 *  and none of that needs a rendered SVG to assert.
 *
 *  Row ORDER comes from `buildTree`, the same helper the list view uses, so the DAG and the list
 *  agree about which node comes first. Column DEPTH is derived here from ancestry among the placed
 *  nodes rather than from `TreeRow.depth` — measured, that field counts `.children[...]` segments,
 *  which is right for the list's indentation (a top-level step is not indented under the root
 *  container) and wrong for columns, where it would draw a parent on top of its own children. */

/** Geometry constants. Tuned so a 20-node run fits a laptop viewport without scrolling
 *  horizontally, which is the shape that actually occurs (`deep-research` expands to 21). */
export const NODE_W = 168
export const NODE_H = 44
export const COL_GAP = 56
export const ROW_GAP = 16

/** Vertical room the gate overlay needs. `DagView` draws its Approve/Deny buttons in a
 *  `foreignObject` at `y + h` with height 34; the canvas has to be tall enough to show them. */
export const GATE_OVERLAY_H = 40

/** The engine's 14 instance states, mapped onto DagView's 6 visual states.
 *
 *  A LOSSY-BY-DESIGN mapping, and the losses are deliberate: `degraded` and `no_change` are
 *  successes (the engine's `SUCCESS_STATES` includes both), so painting them as errors would tell
 *  the user work failed when it did not. `scope_violation` and `escalated` are errors, because
 *  both mean the run stopped needing a human to look. Anything unrecognized is `todo` — the state
 *  that claims the least. */
const STATE_MAP: Record<string, DagNodeState> = {
  pending: 'todo',
  ready: 'todo',
  running: 'active',
  waiting: 'awaiting',
  done: 'done',
  degraded: 'done',
  no_change: 'done',
  skipped: 'todo',
  discarded: 'todo',
  cancelled: 'todo',
  blocked: 'blocked',
  failed: 'error',
  scope_violation: 'error',
  escalated: 'error',
}

export function dagState(state: string): DagNodeState {
  return STATE_MAP[state] ?? 'todo'
}

/** Whether a node instance is a gate WAITING on a human, given the run's live continuations.
 *
 *  Asks the continuation list rather than inferring from the state alone: `waiting` also covers a
 *  `wait` node parked on the CLOCK, and offering Approve/Deny on one would ask the user to answer
 *  something nobody asked them. A gate with no continuation is not answerable either — the token
 *  is what a resolution needs.
 */
export function isAwaitingHuman(
  node: Pick<WorkflowNodeState, 'instance_path' | 'state'>,
  continuations: Array<Pick<WorkflowContinuation, 'instance_path'> & { expired?: boolean }>,
): boolean {
  if (node.state !== 'waiting') return false
  return (continuations ?? []).some((c) => c.instance_path === node.instance_path && !c.expired)
}

export interface RunDagLayout {
  nodes: DagNode[]
  edges: DagEdge[]
  width: number
  height: number
}

/** Lay a run's nodes out left-to-right by tree DEPTH, top-to-bottom in path order.
 *
 *  Containment depth as the x-axis rather than a topological rank: a run's readable structure is
 *  its nesting (this parallel's children, that loop's body), not its dependency order. A
 *  topological layout would also need the spec, which the projection does not carry.
 *
 *  Rows are assigned per COLUMN, so a wide fan-out grows downward instead of overlapping. An empty
 *  node list yields a zero-size layout rather than a 1x1 canvas: `DagView` renders an SVG of the
 *  size it is given, and a stray 1px box in an empty run reads as a rendering bug.
 */
export function layoutRunDag(
  nodes: WorkflowNodeState[],
  options: {
    continuations?: Array<Pick<WorkflowContinuation, 'instance_path'> & { expired?: boolean }>
    label?: (node: WorkflowNodeState) => string
  } = {},
): RunDagLayout {
  const rows = buildTree(nodes)
  if (rows.length === 0) return { nodes: [], edges: [], width: 0, height: 0 }

  const continuations = options.continuations ?? []
  const nextRow = new Map<number, number>()
  const placed = new Map<string, { x: number; y: number; depth: number }>()
  const out: DagNode[] = []

  // Depth comes from the PATH's own nesting, normalized so the shallowest row sits in column 0.
  //
  // Measured against a live run (S61j), and both obvious alternatives are wrong:
  //   * `TreeRow.depth` counts `.children[...]` segments and reports 0 for BOTH `root` and
  //     `root.children[0]` — right for the list's indentation (a top-level step is not indented
  //     under the root container), wrong for columns, where a parent would land on its own child.
  //   * counting PLACED ancestors collapses everything to column 0 on a real run, because the
  //     projection contains no container rows at all: a live gated run projected
  //     `root.children[0]`, `root.children[1].children[0..1]` and `root.children[2]` — no `root`,
  //     no `root.children[1]`. Every node had no placed ancestor, so the graph drew one column and
  //     zero edges. That is the defect this comment exists to prevent a "simplification" from
  //     reintroducing.
  // Normalizing means a run whose shallowest node is two levels deep still starts at the left edge
  // rather than leaving two empty columns.
  const rawDepth = new Map<string, number>()
  for (const row of rows) rawDepth.set(row.node.instance_path, nestingOf(row.node.instance_path))
  const minDepth = Math.min(...[...rawDepth.values()])
  const depthOf = new Map<string, number>()

  for (const row of rows) {
    const depth = (rawDepth.get(row.node.instance_path) ?? 0) - minDepth
    depthOf.set(row.node.instance_path, depth)
    const slot = nextRow.get(depth) ?? 0
    nextRow.set(depth, slot + 1)
    const x = depth * (NODE_W + COL_GAP)
    const y = slot * (NODE_H + ROW_GAP)
    placed.set(row.node.instance_path, { x, y, depth })
    const awaiting = isAwaitingHuman(row.node, continuations)
    out.push({
      id: row.node.instance_path,
      x,
      y,
      w: NODE_W,
      h: NODE_H,
      // An awaiting gate is `awaiting` regardless of what the state map says, because that is the
      // one state a user can ACT on and it must not be flattened into the generic `blocked` look.
      state: awaiting ? 'awaiting' : dagState(row.node.state),
      content: options.label ? options.label(row.node) : row.node.node_id,
    })
  }

  // Containment edges. The parent is the nearest PLACED ancestor — which on a real run is usually
  // not the textual parent, because the projection omits container rows entirely (measured: a gated
  // run projected four leaves and zero containers). Falling back to the nearest ancestor by path
  // PREFIX means a nested leaf still links to the step that contains it, so the graph shows the
  // run's shape instead of four disconnected boxes.
  const edges: DagEdge[] = []
  for (const row of rows) {
    const parentPath = parentOf(row.node.instance_path, placed)
    if (!parentPath) continue
    const from = placed.get(parentPath)
    const to = placed.get(row.node.instance_path)
    if (!from || !to) continue
    edges.push({
      id: `${parentPath}->${row.node.instance_path}`,
      from: parentPath,
      to: row.node.instance_path,
      x1: from.x + NODE_W,
      y1: from.y + NODE_H / 2,
      x2: to.x,
      y2: to.y + NODE_H / 2,
      // Particles flow only into work that is actually running — an animated edge into a finished
      // node reads as progress that is not happening.
      active: row.node.state === 'running',
    })
  }

  const maxDepth = Math.max(...[...depthOf.values()])
  const maxSlot = Math.max(...[...nextRow.values()])
  // Room for the gate overlay. `DagView` draws Approve/Deny in a `foreignObject` at `y + h`, so a
  // height computed from the node boxes alone CLIPS it — measured on a live gated run: the buttons
  // rendered into the SVG and were invisible below its edge, which looks exactly like the seam
  // still being unwired.
  const overlay = out.some((n) => n.state === 'awaiting') ? GATE_OVERLAY_H : 0
  return {
    nodes: out,
    edges,
    width: (maxDepth + 1) * NODE_W + maxDepth * COL_GAP,
    height: maxSlot * NODE_H + Math.max(0, maxSlot - 1) * ROW_GAP + overlay,
  }
}

/** How deeply a path nests, by counting its container segments.
 *
 *  Counts `[` rather than `.`: the separators mix (`root.children[1].children[0]`), and a bracket
 *  appears exactly once per container level in every shape the engine emits — `children[n]`,
 *  `body[n]`, `cases[label]`.
 */
function nestingOf(path: string): number {
  let count = 0
  for (const ch of path) if (ch === '[') count += 1
  return count
}

/** The nearest ancestor of `path` that is actually placed.
 *
 *  NEAREST, not the immediate textual parent: a projection can omit an intermediate container (it
 *  has not started yet), and linking to the textual parent would drop the edge entirely — leaving a
 *  child floating with no visible connection to the run it belongs to.
 */
function parentOf(path: string, placed: Map<string, unknown>): string {
  // First: a placed node that is a strict path PREFIX of this one. That is what links
  // `root.children[1].children[0]` to nothing (its container is unprojected) but links a loop body
  // item to the loop's own row when the loop IS projected. Longest prefix wins — the nearest
  // ancestor, not the outermost.
  let best = ''
  for (const candidatePath of placed.keys()) {
    if (candidatePath === path) continue
    if (!path.startsWith(candidatePath)) continue
    if (candidatePath.length > best.length) best = candidatePath
  }
  if (best) return best

  let candidate = path
  for (;;) {
    const cut = Math.max(candidate.lastIndexOf('.'), candidate.lastIndexOf('['))
    if (cut <= 0) return ''
    candidate = candidate.slice(0, cut)
    // `root.children[2]` → strip the bracket segment to reach `root.children` → then `root`.
    if (placed.has(candidate)) return candidate
    if (!candidate.includes('.') && !candidate.includes('[')) return ''
  }
}
