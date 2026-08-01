import type { WorkflowNodeState } from '../../lib/api'
import { nodeDepth } from './workflowMeta'

/** Grouping a flat node list into collapsible containers (WF2 Slice 10b).
 *
 *  The run view renders one row per node INSTANCE, which is right until a spec fans out: the
 *  `deep-research` template expands to 21 rows, and 18 of them are one skipped subgraph. A flat
 *  list of those is unreadable — the three rows that matter are buried in the ones that did not
 *  run.
 *
 *  So a container's descendants collapse into it, and the collapsed row reports what its subtree
 *  did. The rule for what starts collapsed is the interesting part, and it is not "everything":
 *  collapsing a running subtree would hide the only thing the user came to look at.
 *
 *  Kept as a pure module so the grouping rules are unit-testable — which matters because the
 *  interesting behaviour is entirely about WHICH rows a user sees, and that is exactly what a
 *  rendered-component test asserts badly. */

/** A node row plus its place in the tree. */
export interface TreeRow {
  node: WorkflowNodeState
  depth: number
  /** Instance paths of everything under this node (empty for a leaf). */
  descendants: string[]
  /** True when this row has descendants AND they are worth collapsing. */
  collapsible: boolean
}

/** A collapsed group's one-line summary, e.g. "18 skipped" or "3 done, 1 running". */
export interface SubtreeSummary {
  total: number
  byState: Record<string, number>
  /** The dominant state, for the collapsed row's status chip. */
  dominant: string
}

const TERMINAL = new Set([
  'done', 'degraded', 'failed', 'skipped', 'no_change', 'scope_violation',
  'discarded', 'escalated', 'blocked', 'cancelled',
])

/** Build the tree rows from a flat, path-sorted node list.
 *
 *  Parenthood is derived from the instance PATH rather than from the spec, because the path is what
 *  the projection carries — asking the FE to re-parse the spec tree to render its own rows would
 *  put two sources of truth on screen. */
export function buildTree(nodes: WorkflowNodeState[]): TreeRow[] {
  const sorted = [...nodes].sort((a, b) => a.instance_path.localeCompare(b.instance_path))
  return sorted.map((node) => {
    const prefix = `${node.instance_path}.`
    const descendants = sorted
      .filter((n) => n.instance_path !== node.instance_path && n.instance_path.startsWith(prefix))
      .map((n) => n.instance_path)
    return {
      node,
      depth: nodeDepth(node.instance_path),
      descendants,
      // A container with ONE child is not worth a disclosure control: collapsing it saves a row and
      // costs a click, which is a bad trade.
      collapsible: descendants.length > 1,
    }
  })
}

/** Summarize what a subtree did, for the collapsed row.
 *
 *  Counted by state rather than reduced to a percentage: "18 skipped" tells a user the branch was
 *  not taken, and "17 done, 1 failed" tells them exactly where to look. A progress bar would say
 *  neither. */
export function summarize(paths: string[], nodes: WorkflowNodeState[]): SubtreeSummary {
  const set = new Set(paths)
  const members = nodes.filter((n) => set.has(n.instance_path))
  const byState: Record<string, number> = {}
  for (const m of members) byState[m.state] = (byState[m.state] ?? 0) + 1
  // The dominant state is the most common one, with a tie broken toward the most ALARMING — a
  // subtree that is half done and half failed should not present itself as done.
  const rank = (s: string) =>
    s === 'failed' || s === 'scope_violation' || s === 'blocked' ? 0
      : s === 'escalated' ? 1
      : s === 'running' || s === 'waiting' ? 2
      : s === 'degraded' ? 3
      : 4
  const dominant = Object.entries(byState)
    .sort((a, b) => b[1] - a[1] || rank(a[0]) - rank(b[0]))[0]?.[0] ?? ''
  return { total: members.length, byState, dominant }
}

/** Which containers start collapsed.
 *
 *  A subtree collapses only when EVERY member reached a terminal state AND none of them failed:
 *
 *  * an unfinished subtree holds the live work, which is the only thing a user watching a run
 *    actually wants on screen;
 *  * a failed one holds the thing they need to read, and hiding a failure behind a disclosure
 *    control is how a run "silently" fails from the user's point of view.
 *
 *  So in practice this collapses exactly the noise: completed fan-outs and untaken branches. */
export function initialCollapsed(rows: TreeRow[], nodes: WorkflowNodeState[]): Set<string> {
  const out = new Set<string>()
  for (const row of rows) {
    if (!row.collapsible) continue
    const summary = summarize(row.descendants, nodes)
    const members = nodes.filter((n) => new Set(row.descendants).has(n.instance_path))
    const allTerminal = members.every((m) => TERMINAL.has(m.state))
    const anyBad = members.some(
      (m) => m.state === 'failed' || m.state === 'scope_violation' || m.state === 'blocked',
    )
    if (allTerminal && !anyBad && summary.total > 1) out.add(row.node.instance_path)
  }
  return out
}

/** The rows actually rendered, given a collapsed set.
 *
 *  A node is hidden when ANY ancestor is collapsed — not just its immediate parent. Checking only
 *  the parent would leave a grandchild visible under a collapsed grandparent, which reads as a
 *  rendering bug. */
export function visibleRows(rows: TreeRow[], collapsed: Set<string>): TreeRow[] {
  if (collapsed.size === 0) return rows
  return rows.filter((row) => {
    for (const path of collapsed) {
      if (row.node.instance_path.startsWith(`${path}.`)) return false
    }
    return true
  })
}

/** A collapsed group's label, e.g. "18 skipped" or "3 done · 1 failed".
 *
 *  Ordered most-alarming-first so the part a user needs to see is not at the end of the line. */
export function summaryLabel(summary: SubtreeSummary): string {
  const order = ['failed', 'scope_violation', 'blocked', 'escalated', 'running', 'waiting', 'degraded']
  const entries = Object.entries(summary.byState).sort((a, b) => {
    const ia = order.indexOf(a[0])
    const ib = order.indexOf(b[0])
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
  })
  return entries.map(([state, n]) => `${n} ${state.replace(/_/g, ' ')}`).join(' · ')
}
