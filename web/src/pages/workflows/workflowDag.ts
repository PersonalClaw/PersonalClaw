import type { WorkflowItem } from '../../lib/api'

/** Workflow composition graph over step `ref`s (a step that references another
 *  workflow). Used to keep references acyclic — a workflow must not, directly or
 *  transitively, reference itself. The backend will be authoritative; the editor
 *  enforces it client-side
 *  so the user can't author a cycle in the first place. */

/** Direct workflow ids referenced by a workflow's steps. */
export function refsOf(w: WorkflowItem): string[] {
  return (w.steps ?? []).map((s) => s.ref).filter((r): r is string => !!r)
}

/** Build id → referenced-ids adjacency from the full workflow list, optionally
 *  overriding one workflow's refs with a pending draft set. */
export function refMap(all: WorkflowItem[], overrideId?: string, overrideRefs?: string[]): Map<string, string[]> {
  const m = new Map<string, string[]>()
  for (const w of all) m.set(w.id, overrideId && w.id === overrideId ? (overrideRefs ?? []) : refsOf(w))
  if (overrideId && overrideRefs && !m.has(overrideId)) m.set(overrideId, overrideRefs)
  return m
}

/** Does `from` reach `target` following ref edges (transitively)? */
export function reaches(m: Map<string, string[]>, from: string, target: string): boolean {
  const seen = new Set<string>()
  const stack = [from]
  while (stack.length) {
    const n = stack.pop()!
    if (n === target) return true
    if (seen.has(n)) continue
    seen.add(n)
    for (const r of m.get(n) ?? []) stack.push(r)
  }
  return false
}

/** Adding `candidate` as a ref of `selfId` creates a cycle iff candidate is
 *  selfId, or candidate already (transitively) references selfId. */
export function wouldCycle(m: Map<string, string[]>, selfId: string | undefined, candidate: string): boolean {
  if (!selfId) return candidate === selfId
  if (candidate === selfId) return true
  return reaches(m, candidate, selfId)
}
