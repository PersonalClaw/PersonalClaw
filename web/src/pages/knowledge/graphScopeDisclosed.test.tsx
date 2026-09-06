import { describe, expect, it, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { KnowledgeGraph } from './KnowledgeGraph'

// ── The canvas says how much of the library it is drawing (issue 808) ─────────────────────────────
//
// Measured on a seeded home of 6 items / 154 entities / 1,540 relations, driven through a real
// gateway and a real browser:
//
//                          BEFORE                      AFTER
//   header chips           154 entities, 1540 relations (unchanged — it states the LIBRARY)
//   payload                154 nodes, 768 edges, thinning.edges_total 1540, edges_kept 768
//   drawn                  154 node groups, 768 lines  (unchanged — the cap is not the defect)
//   canvas caption         "100%"                      "154 entities · 768 of 1,540 relations · 100%"
//
// 🪤 THE ISSUE'S OWN NUMBERS WERE STALE, which is why the entity half is pinned as hard as the
// relation half. It reports a cap at "the top 120 entities by degree"; that cap is gone — the old
// `?limit=` node cap was replaced by edge thinning, and 154 of 154 entities ship and draw. The
// assertions below hold BOTH halves, so a re-introduced node cap cannot slip in under a caption that
// still claims every entity.

type Node = { id: string; name?: string; x?: number; y?: number; degree?: number }
type Edge = { source: string; target: string }
type Thinning = { edges_total?: number | null; edges_kept?: number | null }

function draw(nodes: Node[], edges: Edge[], thinning?: Thinning) {
  globalThis.fetch = vi.fn(async () => ({ json: async () => ({ nodes, edges, ...(thinning ? { thinning } : {}) }) })) as never
  return render(<KnowledgeGraph />)
}
const nodesOf = (n: number): Node[] =>
  Array.from({ length: n }, (_, i) => ({ id: `e${i}`, name: `Entity ${i}`, x: (i % 7) / 7, y: (i % 5) / 5, degree: 3 }))
const edgesOf = (n: number, count: number): Edge[] =>
  Array.from({ length: count }, (_, i) => ({ source: `e${i % n}`, target: `e${(i + 3) % n}` }))

async function scope(container: HTMLElement) {
  await waitFor(() => expect(container.querySelector('[data-graph-scope]')).toBeTruthy())
  return {
    // The gap between segments is flex `gap-1.5`, not whitespace in the text, so `textContent`
    // concatenates them; re-spacing the separators reads the caption the way the pill paints it.
    caption: container.querySelector('[data-graph-scope]')!.textContent!.replace(/·/g, ' · ').replace(/\s+/g, ' ').trim(),
    title: container.querySelector('[data-graph-scope]')!.getAttribute('title') ?? '',
    nodes: container.querySelectorAll('[data-entity-id]').length,
    lines: container.querySelectorAll('line').length,
  }
}

describe('the entity canvas states its own scope', () => {
  it('names the relations it left out, and its numbers match what it drew', async () => {
    const { container } = draw(nodesOf(154), edgesOf(154, 768), { edges_total: 1540, edges_kept: 768 })
    const s = await scope(container)
    expect(s.caption).toBe(`154 entities · 768 of ${(1540).toLocaleString()} relations · 100%`)
    // The property the issue is about: no number on screen may exceed what the view honours.
    expect(s.lines, 'the caption counts the lines that exist').toBe(768)
    expect(s.nodes, 'and every entity in the payload is drawn').toBe(154)
  })

  it('explains WHY, and where the rest can be read', async () => {
    const { container } = draw(nodesOf(20), edgesOf(20, 30), { edges_total: 120, edges_kept: 30 })
    const s = await scope(container)
    expect(s.title).toMatch(/strongest relations/)
    // The residue is reachable — the entity sidebar's "Connected to" reads the uncapped
    // /related endpoint — so the caption points at it rather than being a dead end.
    expect(s.title).toMatch(/Click an entity to see all of its relations/)
  })

  it('drops the caveat when nothing was thinned', async () => {
    const { container } = draw(nodesOf(9), edgesOf(9, 12), { edges_total: 12, edges_kept: 12 })
    const s = await scope(container)
    expect(s.caption).toBe('9 entities · 12 relations · 100%')
    expect(s.title).toMatch(/Every entity and relation/)
  })

  it('reads an older gateway as complete rather than as an unknown residue', async () => {
    // No `thinning` block at all. Inventing a caveat there would put a permanent "some are
    // missing" on a payload that may well be whole.
    const { container } = draw(nodesOf(5), edgesOf(5, 6))
    expect((await scope(container)).caption).toBe('5 entities · 6 relations · 100%')
  })

  it('counts the RENDER, not the server\'s own tally', async () => {
    // `edges_kept` is the server's count of what it sent. If the two ever disagree — a filter added
    // in this component, a payload built by an older gateway — the caption must describe the
    // picture, because that is what the reader is comparing it against.
    const { container } = draw(nodesOf(10), edgesOf(10, 7), { edges_total: 90, edges_kept: 44 })
    const s = await scope(container)
    expect(s.caption).toBe('10 entities · 7 of 90 relations · 100%')
    expect(s.lines).toBe(7)
  })

  it('agrees in the singular', async () => {
    const { container } = draw(nodesOf(1), [{ source: 'e0', target: 'e0' }], { edges_total: 1, edges_kept: 1 })
    expect((await scope(container)).caption).toBe('1 entity · 1 relation · 100%')
  })

  it('keeps the zoom readout it always had', async () => {
    const { container } = draw(nodesOf(4), edgesOf(4, 4), { edges_total: 4, edges_kept: 4 })
    expect((await scope(container)).caption).toMatch(/· 100%$/)
  })
})
