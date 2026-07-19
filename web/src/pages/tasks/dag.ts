import type { TaskItem } from '../../lib/api'

/** Shared DAG helpers over the task's typed `dependencies` (BLOCKS edges).
 *
 *  Edge convention: a task's BLOCKS prerequisites must finish before it can
 *  start (edge points prerequisite → dependent). The server is authoritative on
 *  acyclicity (it rejects cycles on write); the client still guards optimistically
 *  via `wouldCycle` so the editor never offers a cycle-forming choice. */

export type DepMap = Map<string, string[]>

/** The BLOCKS-prerequisite task ids of a task (the status-gating set). */
export function prereqIds(t: TaskItem): string[] {
  return (t.dependencies ?? [])
    .filter((d) => (d.dependency_type ?? 'BLOCKS') === 'BLOCKS')
    .map((d) => d.depends_on_task_id ?? '')
    .filter(Boolean) as string[]
}

export function depMap(tasks: TaskItem[]): DepMap {
  const ids = new Set(tasks.map((t) => t.id))
  const m = new Map<string, string[]>()
  for (const t of tasks) m.set(t.id, prereqIds(t).filter((d) => ids.has(d) && d !== t.id))
  return m
}

/** Does `from` reach `target` following depends_on edges? (i.e. is `target` a
 *  prerequisite of `from`, directly or transitively). */
export function reaches(m: DepMap, from: string, target: string): boolean {
  const seen = new Set<string>()
  const stack = [from]
  while (stack.length) {
    const n = stack.pop()!
    if (n === target) return true
    if (seen.has(n)) continue
    seen.add(n)
    for (const d of m.get(n) ?? []) stack.push(d)
  }
  return false
}

/** Adding `candidate` as a prerequisite of `taskId` creates a cycle iff
 *  `candidate` already (transitively) depends on `taskId`. */
export function wouldCycle(m: DepMap, taskId: string, candidate: string): boolean {
  if (taskId === candidate) return true
  return reaches(m, candidate, taskId)
}

/** Detect every node that participates in a cycle (defensive — the editor
 *  prevents new cycles, but legacy/agent-written data may contain them). */
export function cyclicNodes(m: DepMap): Set<string> {
  const WHITE = 0, GRAY = 1, BLACK = 2
  const color = new Map<string, number>()
  const bad = new Set<string>()
  const onStack: string[] = []
  const visit = (n: string) => {
    color.set(n, GRAY); onStack.push(n)
    for (const d of m.get(n) ?? []) {
      if (color.get(d) === GRAY) { // back-edge → cycle; mark the stack slice
        const i = onStack.indexOf(d)
        for (let k = i; k < onStack.length; k++) bad.add(onStack[k])
        bad.add(d)
      } else if ((color.get(d) ?? WHITE) === WHITE) visit(d)
    }
    onStack.pop(); color.set(n, BLACK)
  }
  for (const n of m.keys()) if ((color.get(n) ?? WHITE) === WHITE) visit(n)
  return bad
}

export interface LayeredNode { id: string; layer: number; row: number }

/** Longest-path layering: layer(n) = longest prerequisite chain behind n.
 *  Robust to cycles (a back-edge simply doesn't extend the layer). Returns the
 *  per-node layer/row plus the layer count and max rows for sizing. */
export function layeredLayout(m: DepMap): { nodes: Map<string, LayeredNode>; layers: number; maxRows: number } {
  const layer = new Map<string, number>()
  const computing = new Set<string>()
  const depthOf = (n: string): number => {
    if (layer.has(n)) return layer.get(n)!
    if (computing.has(n)) return 0 // cycle guard
    computing.add(n)
    let best = 0
    for (const d of m.get(n) ?? []) best = Math.max(best, depthOf(d) + 1)
    computing.delete(n)
    layer.set(n, best)
    return best
  }
  for (const n of m.keys()) depthOf(n)

  // assign rows within each layer (stable by insertion order)
  const rows = new Map<number, number>()
  const nodes = new Map<string, LayeredNode>()
  for (const id of m.keys()) {
    const l = layer.get(id) ?? 0
    const r = rows.get(l) ?? 0
    rows.set(l, r + 1)
    nodes.set(id, { id, layer: l, row: r })
  }
  const layers = Math.max(0, ...[...layer.values()]) + 1
  const maxRows = Math.max(1, ...[...rows.values()])
  return { nodes, layers, maxRows }
}
