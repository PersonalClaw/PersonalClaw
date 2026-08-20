import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import {
  KnowledgeGraph,
  hasProjection,
  projectPositions,
  ringPositions,
  nodeRadius,
  placeLabels,
  labelRect,
  weightSpan,
  weightFraction,
  weightStroke,
  weightWidth,
} from './KnowledgeGraph'

// ── What the entity canvas draws, and what it refuses to draw ─────────────────────────────────
//
// Three things were wrong at once, and each one was invisible because the view "worked":
//
//   1. LAYOUT MEANT NOTHING. Nodes went onto concentric rings by their index in the payload array —
//      and that array is built from a Python `set`, so the centre of the graph was whichever node
//      hashed first. Distance between two dots carried no information, and the same library drew
//      differently on a reload.
//   2. WEIGHT WAS ON THE WIRE AND ON NO CHANNEL. `entity_relations.weight` shipped in every edge and
//      every line was painted at one fixed colour and one fixed width.
//   3. LABELS WERE GATED ON `deg > 2 || scale >= 1.3`. Two entities one degree apart drew their names
//      on top of each other, and zooming past the threshold drew ALL of them on top of each other.
//
// This file asserts the replacements behaviourally: positions come from the payload, weight moves two
// independent channels, and a name is drawn only if it measured a clear slot. Every exclusion here is
// paired with a positive control in the same test, because a canvas that rendered nothing passes an
// exclusion for free.

const PAD = 60, SPAN = 880 // the projection's inset and usable world span — see projectPositions

type Node = { id: string; name?: string; x?: number; y?: number; degree?: number }
type Edge = { source: string; target: string; weight?: number }

function mockGraph(nodes: Node[], edges: Edge[] = []) {
  globalThis.fetch = vi.fn(async () => ({ json: async () => ({ nodes, edges }) })) as never
}
const at = (container: HTMLElement, id: string) =>
  container.querySelector(`[data-entity-id="${id}"]`)?.getAttribute('transform')
const groups = (container: HTMLElement) => container.querySelectorAll('[data-entity-id]')

async function draw(nodes: Node[], edges: Edge[] = []) {
  mockGraph(nodes, edges)
  const { container } = render(<KnowledgeGraph />)
  await waitFor(() => expect(groups(container).length).toBe(nodes.length))
  return container
}

// The projection's declared output is [-1,1] on both axes with the ORIGIN AT THE CENTROID
// (`knowledge/projection.py`: "both coordinates in [-1.0, 1.0] … the origin is the centroid of the
// placed items"), so these four are the corners of the real domain.
const CORNERS: Node[] = [
  { id: 'nw', name: 'NW', x: -1, y: -1 },
  { id: 'ne', name: 'NE', x: 1, y: -1 },
  { id: 'sw', name: 'SW', x: -1, y: 1 },
  { id: 'se', name: 'SE', x: 1, y: 1 },
]

const original = globalThis.fetch
afterEach(() => { globalThis.fetch = original; vi.restoreAllMocks() })

describe('the canvas draws where the projection says, not where the array index says', () => {
  it('puts each entity at its own normalized coordinate', async () => {
    const container = await draw(CORNERS)
    // The whole domain, mapped into the inset world box. Hand-checkable: 60 + n × 880.
    expect(at(container, 'nw')).toBe(`translate(${PAD},${PAD})`)
    expect(at(container, 'ne')).toBe(`translate(${PAD + SPAN},${PAD})`)
    expect(at(container, 'sw')).toBe(`translate(${PAD},${PAD + SPAN})`)
    expect(at(container, 'se')).toBe(`translate(${PAD + SPAN},${PAD + SPAN})`)
    // The discriminator against the layout this replaces: rings put the FIRST node dead centre and
    // the rest on a radius, regardless of what the payload said.
    const drawn = [...groups(container)].map((g) => g.getAttribute('transform'))
    expect(drawn, 'nothing landed on the ring centre').not.toContain('translate(500,500)')
  })

  it('puts the projection origin at the centre of the canvas, where the centroid belongs', () => {
    const pos = projectPositions([{ id: 'o', x: 0, y: 0 }, { id: 'e', x: 1, y: 0 }])
    expect(pos.get('o')).toEqual({ x: PAD + SPAN / 2, y: PAD + SPAN / 2 })
    expect(pos.get('e')).toEqual({ x: PAD + SPAN, y: PAD + SPAN / 2 })
    // And a coordinate outside the declared domain clamps into the box rather than escaping it.
    const wild = projectPositions([{ id: 'w', x: 4, y: -9 }])
    expect(wild.get('w')).toEqual({ x: PAD + SPAN, y: PAD })
  })

  it('sizes an entity by its FULL degree, not by the relations that survived thinning', async () => {
    // The payload thins weak edges, so a hub can arrive with one line drawn and twenty relations.
    const container = await draw(
      [
        { id: 'hub', name: 'Hub', x: -0.5, y: 0, degree: 20 },
        { id: 'pair', name: 'Pair', x: 0.5, y: 0, degree: 2 },
      ],
      [{ source: 'pair', target: 'hub' }],
    )
    const radius = (id: string) => container.querySelector(`[data-entity-id="${id}"] circle`)?.getAttribute('r')
    expect(radius('hub'), 'twenty relations, capped').toBe('16')
    expect(radius('pair')).toBe('9')
    // Vacuity: counting the ONE drawn line instead would have made the hub the smallest mark there is.
    expect(nodeRadius(1)).toBe(7.5)
  })

  it('adding an entity does not move the others — the map is fixed, not fitted', () => {
    const one = projectPositions([{ id: 'a', x: 0.25, y: 0.75 }])
    const many = projectPositions([
      { id: 'a', x: 0.25, y: 0.75 },
      { id: 'b', x: 0.9, y: 0.1 },
      { id: 'c', x: 0.02, y: 0.4 },
    ])
    expect(many.get('a')).toEqual(one.get('a'))
  })
})

describe('a payload without positions still draws a graph', () => {
  it('falls back to rings rather than stacking every entity on one point', async () => {
    // An older gateway, or the projection unavailable: no x/y anywhere.
    const bare = CORNERS.map(({ id, name }) => ({ id, name }))
    const container = await draw(bare)
    const drawn = [...groups(container)].map((g) => g.getAttribute('transform'))
    expect(drawn.length, 'every node is still drawn').toBe(4)
    expect(new Set(drawn).size, 'at four distinct places, not one').toBe(4)
    // The ring signature: first node centred, the rest on the inner radius of 160.
    expect(at(container, 'nw')).toBe('translate(500,500)')
    expect(at(container, 'ne')).toBe('translate(500,340)')
    expect(hasProjection(bare)).toBe(false)
  })

  it('honours a partial payload, and parks the unplaceable at the centroid', () => {
    // The projection answers the ORIGIN for a node it cannot place, and the origin is the centroid —
    // so an unplaceable entity sits in the middle of its neighbours, not in a corner.
    const nodes = [{ id: 'placed', x: 1, y: 1 }, { id: 'origin', x: 0, y: 0 }, { id: 'absent' }]
    expect(hasProjection(nodes), 'one placed node is a projection').toBe(true)
    const pos = projectPositions(nodes)
    expect(pos.get('placed')).toEqual({ x: PAD + SPAN, y: PAD + SPAN })
    expect(pos.get('origin')).toEqual({ x: PAD + SPAN / 2, y: PAD + SPAN / 2 })
    expect(pos.get('absent')).toEqual({ x: PAD + SPAN / 2, y: PAD + SPAN / 2 })
    // And the fallback is a different layout, not the same one under another name.
    expect(ringPositions(nodes).get('placed')).toEqual({ x: 500, y: 500 })
  })

  it('treats an all-origin projection as no projection, rather than piling the library on one point', async () => {
    // A degenerate projection — no embeddings, or fewer than two distinct vectors — returns the origin
    // for EVERY node. Honouring that literally draws one dot for a whole library.
    const flat = CORNERS.map(({ id, name }) => ({ id, name, x: 0, y: 0 }))
    expect(hasProjection(flat), 'the origin is not a placement').toBe(false)
    expect(new Set([...projectPositions(flat).values()].map((p) => `${p.x},${p.y}`)).size)
      .toBe(1) // what honouring it would look like
    const container = await draw(flat)
    const drawn = [...groups(container)].map((g) => g.getAttribute('transform'))
    expect(new Set(drawn).size, 'four visible entities instead').toBe(4)
    // Vacuity: ONE non-origin coordinate is enough to make it a projection again.
    expect(hasProjection([...flat.slice(1), { id: 'nw', x: 0, y: -0.4 }])).toBe(true)
  })
})

describe('relation weight is encoded on two channels, colour and width', () => {
  // Weights an eighth apart, so nothing here can pass on rounding.
  const WEIGHTED: [Node[], Edge[]] = [
    [{ id: 'a', name: 'A', x: 0.1, y: 0.1 }, { id: 'b', name: 'B', x: 0.5, y: 0.5 }, { id: 'c', name: 'C', x: 0.9, y: 0.9 }],
    [{ source: 'a', target: 'b', weight: 0.2 }, { source: 'b', target: 'c', weight: 4.1 }, { source: 'a', target: 'c', weight: 8 }],
  ]
  const lines = (container: HTMLElement) => [...container.querySelectorAll('line')]

  it('a light and a heavy relation differ on COLOUR, on its own', async () => {
    const container = await draw(...WEIGHTED)
    const [low, mid, high] = lines(container).map((l) => l.getAttribute('stroke'))
    expect(lines(container).length, 'all three relations drew').toBe(3)
    expect(low).not.toBe(high)
    expect(mid).not.toBe(low)
    expect(mid).not.toBe(high)
    // The lightest relation paints the measured contrast floor EXACTLY — a 0% mix is the second
    // colour — so the ramp can only add contrast, never spend it.
    expect(low).toBe('color-mix(in srgb, var(--color-on-surface) 0%, var(--color-on-surface-low))')
    expect(high).toBe('color-mix(in srgb, var(--color-on-surface) 70%, var(--color-on-surface-low))')
    expect(low, 'no raw colour reaches the canvas').toContain('var(--color-')
  })

  it('and they differ on WIDTH, on its own', async () => {
    const container = await draw(...WEIGHTED)
    const widths = lines(container).map((l) => Number(l.getAttribute('stroke-width')))
    expect(widths).toEqual([1, 1.7, 2.4])
    // Monotone, and the resting floor is still a whole painted pixel.
    expect(widths[0]).toBeLessThan(widths[1])
    expect(widths[1]).toBeLessThan(widths[2])
    expect(Math.min(...widths)).toBeGreaterThanOrEqual(1)
  })

  it('keeps the width painted at any viewport — the ramp is in screen pixels', async () => {
    const container = await draw(...WEIGHTED)
    for (const l of lines(container)) expect(l.getAttribute('vector-effect')).toBe('non-scaling-stroke')
  })

  it('still hands the accent to hover, so weight cannot swallow the interaction state', async () => {
    const container = await draw(...WEIGHTED)
    fireEvent.mouseEnter(container.querySelector('[data-entity-id="a"]')!)
    await waitFor(() => {
      const strokes = lines(container).map((l) => l.getAttribute('stroke'))
      expect(strokes.filter((s) => s === 'var(--color-primary)').length, "a's two relations").toBe(2)
      // Positive control: the relation that does not touch `a` keeps its weight colour.
      expect(strokes.some((s) => s?.startsWith('color-mix'))).toBe(true)
    })
    // Weight survives on the other channel while active.
    expect(lines(container).map((l) => Number(l.getAttribute('stroke-width')))).toEqual([1.6, 1.7, 3])
  })

  it('encodes nothing when there is nothing to encode, rather than inventing a ramp', async () => {
    const flat: Edge[] = [{ source: 'a', target: 'b', weight: 1 }, { source: 'b', target: 'c', weight: 1 }]
    const container = await draw(WEIGHTED[0], flat)
    const drawn = lines(container)
    expect(drawn.length, 'the vacuity floor: both relations drew').toBe(2)
    expect(drawn[0].getAttribute('stroke')).toBe(drawn[1].getAttribute('stroke'))
    expect(drawn[0].getAttribute('stroke-width')).toBe('1')
    expect(weightSpan(flat)).toEqual({ lo: 0, hi: 0 })
    // A missing weight is the lightest, not a crash and not the heaviest.
    expect(weightFraction(undefined, { lo: 1, hi: 9 })).toBe(0)
    expect(weightFraction(null, { lo: 1, hi: 9 })).toBe(0)
  })

  it('the two channels are independent functions of the same fraction', () => {
    expect(weightStroke(0)).not.toBe(weightStroke(1))
    expect(weightWidth(0, false)).not.toBe(weightWidth(1, false))
    expect(weightFraction(5, { lo: 0, hi: 10 })).toBe(0.5)
    expect(weightFraction(50, { lo: 0, hi: 10 }), 'clamped, not extrapolated').toBe(1)
  })
})

describe('labels are placed by measurement, and the larger entity wins a contested slot', () => {
  // A hub with six far-flung relations, and an unconnected entity sitting almost on top of it. Both
  // want a label in the same place; only one can have it.
  const HUB = 'Hubbergraph Entity Alpha'
  const LONE = 'Lonesome Entity Beta'
  const SPOKES = [
    { id: 's1', name: 'Spoke One', x: 0.05, y: 0.05 },
    { id: 's2', name: 'Spoke Two', x: 0.95, y: 0.05 },
    { id: 's3', name: 'Spoke Tre', x: 0.05, y: 0.95 },
    { id: 's4', name: 'Spoke Fur', x: 0.95, y: 0.95 },
    { id: 's5', name: 'Spoke Fiv', x: 0.5, y: 0.05 },
    { id: 's6', name: 'Spoke Six', x: 0.5, y: 0.95 },
  ]
  const CROWDED: [Node[], Edge[]] = [
    [{ id: 'h', name: HUB, x: 0.5, y: 0.5 }, { id: 'l', name: LONE, x: 0.52, y: 0.5 }, ...SPOKES],
    SPOKES.map((s) => ({ source: 'h', target: s.id })),
  ]

  it('drops the smaller entity\'s name and keeps the bigger one\'s', async () => {
    const container = await draw(...CROWDED)
    expect(groups(container).length, 'the vacuity floor: the canvas drew all eight entities').toBe(8)
    expect(screen.getByText(HUB), 'the bigger entity keeps its name').toBeTruthy()
    expect(screen.queryByText(LONE), 'the smaller one loses the contested slot').toBeNull()
    // And the filter is selective, not a blanket off-switch: every uncontested name is drawn.
    for (const s of SPOKES) expect(screen.getByText(s.name)).toBeTruthy()
  })

  it('is decided by node size, not by payload order', () => {
    // The same two candidates, small one first and large one first: the large one wins either way.
    const big = { id: 'big', name: 'Wide Name Here', r: 15, x: 500, y: 500 }
    const small = { id: 'small', name: 'Wide Name Here', r: 6, x: 512, y: 500 }
    expect([...placeLabels([small, big], 10)]).toEqual(['big'])
    expect([...placeLabels([big, small], 10)]).toEqual(['big'])
    // Vacuity: moved apart, BOTH are placed — the rule is collision, not a cap of one.
    expect(placeLabels([small, { ...big, x: 100 }], 10).size).toBe(2)
  })

  it('measures the box from the text, so a longer name collides where a shorter one would not', () => {
    const box = labelRect('12345678', 10, 100, 100)
    expect(box.x2 - box.x1).toBeCloseTo(8 * 10 * 0.55)
    expect(labelRect('1234', 10, 100, 100).x2 - labelRect('1234', 10, 100, 100).x1)
      .toBeLessThan(box.x2 - box.x1)
    // Sits ABOVE the node, clear of its outline.
    expect(box.y2).toBe(100 - 10 - 4)
  })

  it('draws nothing below the rendered-size floor, and the entities are still there', async () => {
    const container = await draw(...CROWDED)
    expect(screen.getByText(HUB), 'labelled before zooming out').toBeTruthy()
    const out = screen.getByRole('button', { name: 'Zoom out' })
    // One step is 0.8 → a 8px face, still readable. The floor is not just "any zoom out".
    fireEvent.click(out)
    await waitFor(() => expect(screen.getByText(HUB)).toBeTruthy())
    // The second step is 0.64 → 6.4px, which is noise. Nothing is labelled.
    fireEvent.click(out)
    await waitFor(() => expect(screen.queryByText(HUB)).toBeNull())
    for (const s of SPOKES) expect(screen.queryByText(s.name)).toBeNull()
    // 🔑 The exclusion above is only meaningful because the graph is still drawn: the entities, their
    // relations and the zoom control are all present at 64%.
    expect(groups(container).length).toBe(8)
    expect(container.querySelectorAll('line').length).toBe(6)
    expect(screen.getByText('64%')).toBeTruthy()
    // A hover still names the one entity under the pointer — the floor governs the ambient set.
    fireEvent.mouseEnter(container.querySelector('[data-entity-id="h"]')!)
    await waitFor(() => expect(screen.getByText(HUB)).toBeTruthy())
  })

  it('keeps the shared zoom control, not a second inlined one', async () => {
    const container = await draw(...CROWDED)
    for (const name of ['Zoom in', 'Zoom out', 'Reset view']) {
      expect(screen.getAllByRole('button', { name }).length, `${name} is mounted once`).toBe(1)
    }
    expect(container.querySelectorAll('button').length, 'three controls, no fourth').toBe(3)
  })
})
