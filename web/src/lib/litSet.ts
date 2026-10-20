/** The lit-set BFS — the ONE hop-depth neighbourhood expansion in the app.
 *
 *  Extracted from `pages/settings/MemoryGraph.tsx`, whose `litSet` memo has owned this
 *  traversal since the Memory Studio's Obsidian-style local-graph focus landed. It moved here
 *  when a SECOND canvas needed the same answer: the knowledge ego view
 *  (`pages/knowledge/KnowledgeEgoGraph.tsx`) narrows its payload to the focused document's
 *  N-hop neighbourhood, and an ego view that re-derived "which nodes are within N hops" would
 *  be a second implementation of the only interesting thing either canvas computes — two
 *  answers to one question, free to drift apart the first time either is tuned.
 *
 *  Both callers pass the SAME undirected `{from,to}` edge shape, so nothing about the traversal
 *  is memory- or knowledge-specific; only the adapter that reaches this shape is.
 *
 *  Contract, unchanged from the memo it replaces:
 *   • `focusId` empty/null ⇒ `null`, meaning "no focus, everything is lit (global view)". A
 *     caller must treat `null` as "do not dim / do not narrow", NOT as "nothing is lit".
 *   • edges are undirected — an edge lights its neighbour from either end.
 *   • `hopDepth` is floored at 1, so a 0 or negative depth still returns the direct neighbours
 *     rather than the bare focus (the control that drives it offers 1..3).
 *   • the focus itself is always in the returned set. */
export interface LitEdge {
  from: string
  to: string
}

export function litNeighbourhood(
  edges: readonly LitEdge[],
  focusId: string | null | undefined,
  hopDepth: number,
): Set<string> | null {
  if (!focusId) return null // null = no focus → all lit
  const adj = new Map<string, string[]>()
  for (const e of edges) {
    ;(adj.get(e.from) ?? adj.set(e.from, []).get(e.from)!).push(e.to)
    ;(adj.get(e.to) ?? adj.set(e.to, []).get(e.to)!).push(e.from)
  }
  const lit = new Set<string>([focusId])
  let frontier = [focusId]
  for (let hop = 0; hop < Math.max(1, hopDepth); hop++) {
    const next: string[] = []
    for (const id of frontier) for (const nb of adj.get(id) ?? []) if (!lit.has(nb)) { lit.add(nb); next.push(nb) }
    frontier = next
  }
  return lit
}
