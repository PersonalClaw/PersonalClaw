/** Pure layout of a streamed plan into the shared DAG geometry (WF2UNI-10).
 *
 *  The read-only graph is one of the three synchronized plan-review views. `DagView` draws
 *  ALREADY-POSITIONED nodes — layout stays in the caller (the same split `runDag.ts` uses for
 *  workflow runs) so the interesting behaviour (which step lands in which column, which edges
 *  connect) is unit-testable without a rendered SVG.
 *
 *  A plan step's column is its DEPENDENCY depth: a step with no `depends_on` sits in column 0,
 *  and each step sits one column right of its deepest declared prerequisite. Edges run from each
 *  prerequisite to the step that names it; a plan with no declared deps degrades to a straight
 *  left-to-right chain (each step depends on the previous), which is what a linear plan looks
 *  like and keeps the graph from collapsing into one overlapping column. */

import type { DagEdge, DagNode } from '../tasks/DagView'
import type { PlanDraft, PlanStep } from './planStream'

const NODE_W = 168
const NODE_H = 44
const COL_GAP = 48
const ROW_GAP = 16

export interface PlanDagLayout {
  nodes: DagNode[]
  edges: DagEdge[]
  width: number
  height: number
}

/** Column depth per step id. A step's depth is 1 + the max depth of its (present) declared
 *  prerequisites; a step with none is depth 0. Missing/forward references are ignored (they
 *  cannot deepen a step), and a dependency cycle is broken by capping the walk at the step count
 *  — a planner-authored cycle is a bug we render flat, not one we hang on. */
export function planDepths(steps: PlanStep[]): Map<string, number> {
  const byId = new Map(steps.map((s) => [s.id, s]))
  const depth = new Map<string, number>()
  const computing = new Set<string>()

  const of = (id: string, guard: number): number => {
    if (depth.has(id)) return depth.get(id)!
    const step = byId.get(id)
    if (!step || guard <= 0 || computing.has(id)) return 0
    computing.add(id)
    let d = 0
    for (const dep of step.depends_on ?? []) {
      if (dep !== id && byId.has(dep)) d = Math.max(d, of(dep, guard - 1) + 1)
    }
    computing.delete(id)
    depth.set(id, d)
    return d
  }
  for (const s of steps) of(s.id, steps.length)
  return depth
}

/** Lay the plan out left-to-right by dependency depth, stacking steps that share a column. An
 *  empty plan yields a zero-size layout (a stray 1px box in an empty review reads as a bug). */
export function layoutPlanDag(
  draft: PlanDraft,
  label: (step: PlanStep) => string = (s) => s.label ?? s.id,
): PlanDagLayout {
  const steps = draft.steps
  if (steps.length === 0) return { nodes: [], edges: [], width: 0, height: 0 }

  // With no declared deps at all, fall back to a linear chain so the graph reads as the sequence
  // it is rather than one overlapping column.
  const anyDeps = steps.some((s) => (s.depends_on ?? []).length > 0)
  const linear = !anyDeps
  const depth = linear
    ? new Map(steps.map((s, i) => [s.id, i]))
    : planDepths(steps)

  const rowInCol = new Map<number, number>()
  const placed = new Map<string, { x: number; y: number }>()
  const nodes: DagNode[] = []
  for (const step of steps) {
    const col = depth.get(step.id) ?? 0
    const slot = rowInCol.get(col) ?? 0
    rowInCol.set(col, slot + 1)
    const x = col * (NODE_W + COL_GAP)
    const y = slot * (NODE_H + ROW_GAP)
    placed.set(step.id, { x, y })
    nodes.push({
      id: step.id,
      x, y, w: NODE_W, h: NODE_H,
      // A still-streaming step reads as active (work flowing in); a settled one as todo — the
      // plan-review graph is a preview, so nothing is `done`.
      state: step.pending ? 'active' : 'todo',
      content: label(step),
    })
  }

  const edges: DagEdge[] = []
  const pushEdge = (from: string, to: string) => {
    const a = placed.get(from), b = placed.get(to)
    if (!a || !b) return
    edges.push({
      id: `${from}->${to}`, from, to,
      x1: a.x + NODE_W, y1: a.y + NODE_H / 2,
      x2: b.x, y2: b.y + NODE_H / 2,
    })
  }
  if (linear) {
    for (let i = 1; i < steps.length; i++) pushEdge(steps[i - 1].id, steps[i].id)
  } else {
    const ids = new Set(steps.map((s) => s.id))
    for (const step of steps) {
      for (const dep of step.depends_on ?? []) if (dep !== step.id && ids.has(dep)) pushEdge(dep, step.id)
    }
  }

  const maxCol = Math.max(...[...depth.values()])
  const maxSlot = Math.max(...[...rowInCol.values()])
  return {
    nodes,
    edges,
    width: (maxCol + 1) * NODE_W + maxCol * COL_GAP,
    height: maxSlot * NODE_H + Math.max(0, maxSlot - 1) * ROW_GAP,
  }
}
