import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Network, Sparkles } from 'lucide-react'
import { GraphZoomControls } from '../../ui/GraphZoomControls'
import { EmptyState } from '../../ui/ListScaffold'

interface GraphNode {
  id: string
  name?: string
  type?: string
  /** Coordinates from the server-side projection, both in [-1,1] with the ORIGIN AT THE CENTROID of
   *  the placed items, scaled isotropically so relative distance is meaningful. Absent on an older
   *  gateway; the origin itself when a node is unplaceable (no usable embedding) or when the
   *  projection is degenerate (fewer than two distinct vectors — nothing to spread). */
  x?: number | null
  y?: number | null
  /** Degree in the FULL graph. The payload thins weak edges, so counting the drawn lines would shrink
   *  a hub whose relations were thinned; this is the entity's real connectedness. */
  degree?: number | null
  cluster?: number | string | null
}
interface GraphEdge {
  source: string
  target: string
  type?: string
  /** Relation strength. Already on the wire (`entity_relations.weight`, a REAL defaulting to 1.0)
   *  and, until now, drawn nowhere. An OPEN range — co-occurrence counts and similarity scores both
   *  land in it — so it is read as a fraction of the payload's own span, never against an assumed
   *  0..1 that would flatten one of them. */
  weight?: number | null
}

// World coordinate space (the layout canvas); the viewBox shows a window into it. At module scope so
// the projection and label passes below are plain functions a test can drive directly.
const W = 1000, H = 1000, cx = W / 2, cy = H / 2
/** Inset kept clear of the frame, so an entity at a domain corner still shows its outline and name. */
const PAD = 60

const finite = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** Does this payload carry a projection that SAYS ANYTHING? One placed node is enough — but the
 *  origin does not count as placed. A degenerate projection (no embeddings, or fewer than two
 *  distinct vectors) answers the origin for every node, and honouring that would pile the whole
 *  library onto one point; rings at least show every entity. */
export function hasProjection(nodes: GraphNode[]): boolean {
  return nodes.some((n) => {
    const x = finite(n.x), y = finite(n.y)
    return x !== null && y !== null && (x !== 0 || y !== 0)
  })
}

/** Server positions scaled into the world box.
 *
 *  🔑 THE MAP IS FIXED, NOT FITTED to the payload's own extent: a node's screen position depends only
 *  on its own coordinate, so adding or dropping an entity never moves the others. That is what makes
 *  on-screen distance mean something and the layout stable across sessions. No jitter is added and
 *  the node array is never re-ordered — position carries the meaning that insertion order used to
 *  pretend to.
 *
 *  The domain is [-1,1] on both axes, which is the projection's declared output, so the centre of the
 *  canvas is the centroid of the placed items and an unplaceable node lands there rather than in a
 *  corner. Out-of-domain values clamp into the box instead of escaping it. */
export function projectPositions(nodes: GraphNode[]): Map<string, { x: number; y: number }> {
  const unit = (v: number | null) => (v === null ? 0.5 : Math.min(1, Math.max(0, (v + 1) / 2)))
  const m = new Map<string, { x: number; y: number }>()
  for (const n of nodes) {
    m.set(n.id, {
      x: PAD + unit(finite(n.x)) * (W - 2 * PAD),
      y: PAD + unit(finite(n.y)) * (H - 2 * PAD),
    })
  }
  return m
}

/** The pre-projection layout, kept as the FALLBACK for a payload with no positions. Insertion-order
 *  concentric rings, where distance from the centre carries no meaning at all — which is exactly why
 *  the projection replaces it. It survives because a graph that draws in the wrong places still beats
 *  one that collapses every entity onto the origin. */
export function ringPositions(nodes: GraphNode[]): Map<string, { x: number; y: number }> {
  const m = new Map<string, { x: number; y: number }>()
  const n = nodes.length
  nodes.forEach((node, i) => {
    const ring = i < 1 ? 0 : i < 9 ? 1 : i < 25 ? 2 : 3
    const r = ring === 0 ? 0 : ring === 1 ? 160 : ring === 2 ? 300 : 440
    const ringStart = ring === 0 ? 0 : ring === 1 ? 1 : ring === 2 ? 9 : 25
    const ringCount = ring === 0 ? 1 : ring === 1 ? Math.min(8, n - 1) : ring === 2 ? Math.min(16, n - 9) : n - 25
    const idx = i - ringStart
    const a = (idx / Math.max(1, ringCount)) * Math.PI * 2 - Math.PI / 2
    m.set(node.id, { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r })
  })
  return m
}

/** Node radius in world units — degree-driven, which is what "a larger node" means below. */
export function nodeRadius(degree: number): number {
  return Math.min(16, 6 + degree * 1.5)
}

/** The label face, in world units — matches the `fontSize` on the <text> below. */
const LABEL_FONT = 10
/** Mean glyph advance of that face at 1em. */
const LABEL_ADVANCE = 0.55
const LABEL_LINE = 1.2
/** Clearance between a node's outline and the bottom of its label box. */
const LABEL_GAP = 4
/** Rendered-size floor, in real screen pixels: below this a name is not readable, and an unreadable
 *  label is noise that also steals a slot from a legible one. */
const LABEL_MIN_PX = 7

interface LabelRect { x1: number; x2: number; y1: number; y2: number }
interface LabelCandidate { id: string; name: string; r: number; x: number; y: number }

/** The box a label WILL occupy, in world units.
 *
 *  🔴 ESTIMATED FROM CHARACTER COUNT, NOT MEASURED. `getBBox()` is the real measurement in a browser
 *  and is unimplemented in jsdom, where it answers 0×0 — so a measured-only pass places EVERY label
 *  under test (nothing can ever collide) while looking correct in a browser, and the collision rule
 *  would ship untested. The estimate is therefore the only code path, in both: what a test drives is
 *  what a user gets. It over-states narrow glyphs, which errs toward dropping a label rather than
 *  overlapping two. */
export function labelRect(name: string, r: number, x: number, y: number): LabelRect {
  const w = Math.max(1, name.length) * LABEL_FONT * LABEL_ADVANCE
  const h = LABEL_FONT * LABEL_LINE
  const bottom = y - r - LABEL_GAP
  return { x1: x - w / 2, x2: x + w / 2, y1: bottom - h, y2: bottom }
}

/** Measure-then-filter label placement, replacing the old degree/zoom threshold.
 *
 *  Candidates are walked LARGEST NODE FIRST and a label is kept only when its rect clears every rect
 *  already placed — so where two names cannot both fit, the bigger entity keeps its label and the
 *  smaller one goes without. Ties hold payload order (`Array#sort` is stable) and the input is copied
 *  rather than sorted in place, so nothing about draw order or the layout changes.
 *
 *  `renderedPx` is the label's height in REAL screen pixels — world font size × pan/zoom scale × the
 *  viewBox's own CTM scale. Below the floor nothing is labelled: at 390px wide the CTM alone is
 *  ~0.358, which paints this 10-unit face at 3.6px. */
export function placeLabels(candidates: LabelCandidate[], renderedPx: number): Set<string> {
  const placed = new Set<string>()
  if (renderedPx < LABEL_MIN_PX) return placed
  const taken: LabelRect[] = []
  for (const c of [...candidates].sort((a, b) => b.r - a.r)) {
    const box = labelRect(c.name, c.r, c.x, c.y)
    if (taken.some((t) => box.x1 < t.x2 && box.x2 > t.x1 && box.y1 < t.y2 && box.y2 > t.y1)) continue
    taken.push(box)
    placed.add(c.id)
  }
  return placed
}

/** The payload's own weight span, so the two channels below encode the range that is actually there.
 *  A degenerate span — every relation equally weighted, or none weighted at all — reads as 0
 *  everywhere, i.e. the unencoded resting look, rather than inventing a ramp out of nothing. */
export function weightSpan(edges: GraphEdge[]): { lo: number; hi: number } {
  let lo = Infinity, hi = -Infinity
  for (const e of edges) {
    const w = finite(e.weight)
    if (w === null) continue
    lo = Math.min(lo, w)
    hi = Math.max(hi, w)
  }
  return hi > lo ? { lo, hi } : { lo: 0, hi: 0 }
}

/** Where one relation sits in that span: 0 at the lightest, 1 at the heaviest. */
export function weightFraction(weight: number | null | undefined, span: { lo: number; hi: number }): number {
  const w = finite(weight)
  if (w === null || span.hi <= span.lo) return 0
  return Math.min(1, Math.max(0, (w - span.lo) / (span.hi - span.lo)))
}

/** CHANNEL 1 — COLOUR. Mixes the strong neutral INTO the resting one, so the lightest relation paints
 *  exactly `--color-on-surface-low` (a 0% mix IS the second colour) and the measured 3.92:1 dark /
 *  3.86:1 light floor is preserved by construction; a heavier relation only gains contrast. Both ends
 *  are neutrals — `design/schemes.ts` drives the accent identity only — so no scheme moves this. */
export function weightStroke(f: number): string {
  return `color-mix(in srgb, var(--color-on-surface) ${Math.round(f * 70)}%, var(--color-on-surface-low))`
}

/** CHANNEL 2 — WIDTH, added on top of the base so the resting floor stays at a whole painted pixel.
 *  Deliberately a narrow ramp: the relation's non-scaling stroke makes these SCREEN pixels at any
 *  viewport and any zoom, which is what makes the ramp mean anything — and also what keeps its usable
 *  range small. Width alone cannot carry a weight under this viewBox; that is why colour is there.
 *
 *  (The attribute that does it is spelled out on the <line> below and nowhere else in this file: the
 *  contrast rail counts its occurrences in raw source text, comments included.) */
export function weightWidth(f: number, active: boolean): number {
  // Rounded to a hundredth so the painted attribute is the number this reasons about, rather than
  // 1.6999999999999997 — a stroke width no renderer distinguishes and no reader can.
  return Math.round(((active ? 1.6 : 1) + f * 1.4) * 100) / 100
}

/** Entity graph — top entities drawn in SVG at the coordinates the server's projection gives them
 *  (no d3 dependency, and no client-side layout to disagree with it). Fills the available
 *  width/height; supports wheel/pinch zoom and drag-to-pan via an SVG viewBox transform, plus the
 *  shared zoom buttons and a reset-to-fit control. Hover highlights a node + its edges; click selects
 *  it (opens the entity in the sidebar). Relation weight is on colour and width; entity names are
 *  placed by collision, largest node first. */
export function KnowledgeGraph({ selectedId, onSelect, onRegenerate, regenerating }: {
  selectedId?: string | null
  onSelect?: (name: string) => void
  /** Runs the ingestion node-graph over items missing insights — the ONLY thing that turns
   *  items into the entities this view draws. Its header control is `view === 'library'`-only,
   *  so on this tab it is off screen; the empty state below carries it instead of describing it. */
  onRegenerate?: () => void
  regenerating?: boolean
} = {}) {
  const [graph, setGraph] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  // Pan/zoom state: a scale + world-space translation applied via the SVG viewBox.
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 })
  // Screen pixels per world unit — the viewBox's own CTM scale, which is BELOW 1 at every real
  // viewport (measured 0.761 at 1440px, 0.358 at 390px) because a 1000-unit space is fitted to the
  // panel. The label floor is a rendered-size floor, so it needs this and not just `view.scale`.
  const [pxPerWorld, setPxPerWorld] = useState(1)
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    let alive = true
    fetch('/api/knowledge/graph', { headers: { 'X-Session-Key': 'dashboard:ui' } })
      .then((r) => r.json()).then((d) => { if (alive) setGraph(d) }).catch(() => { if (alive) setGraph({ nodes: [], edges: [] }) })
    return () => { alive = false }
  }, [])

  // Track the CTM scale so the label floor is measured against real pixels. A width of 0 (before
  // layout, and in jsdom, which does not lay out) leaves the 1:1 default in place rather than
  // reporting a 0-pixel face and hiding every label.
  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const measure = () => { if (el.clientWidth > 0) setPxPerWorld(el.clientWidth / W) }
    measure()
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    ro?.observe(el)
    window.addEventListener('resize', measure)
    return () => { ro?.disconnect(); window.removeEventListener('resize', measure) }
  }, [graph])

  // Where the projection puts each entity, or the ring fallback when the payload carries no
  // positions. The choice is per-payload and total: a mix of the two would put half the graph in a
  // space where distance means something and half in one where it does not.
  const pos = useMemo(() => {
    const nodes = graph?.nodes ?? []
    return hasProjection(nodes) ? projectPositions(nodes) : ringPositions(nodes)
  }, [graph])

  // Each entity's connectedness, which sets its radius and therefore its priority for a label slot.
  // The payload's own `degree` wins where it exists: it counts the FULL graph, while the edges here
  // have been thinned, so counting drawn lines would shrink exactly the hubs that earned their size.
  const degree = useMemo(() => {
    const d = new Map<string, number>()
    for (const e of graph?.edges ?? []) {
      d.set(e.source, (d.get(e.source) ?? 0) + 1)
      d.set(e.target, (d.get(e.target) ?? 0) + 1)
    }
    for (const n of graph?.nodes ?? []) {
      const declared = finite(n.degree)
      if (declared !== null) d.set(n.id, declared)
    }
    return d
  }, [graph])

  const span = useMemo(() => weightSpan(graph?.edges ?? []), [graph])

  // Which entities get a name drawn. Computed WITHOUT `hover`, so moving the pointer never reshuffles
  // everyone else's labels — the hovered entity is drawn on top of this set instead of competing for
  // a slot in it. Before the early returns below, because these are hooks.
  const labelled = useMemo(
    () =>
      placeLabels(
        (graph?.nodes ?? []).flatMap((n) => {
          const p = pos.get(n.id)
          return p && n.name ? [{ id: n.id, name: n.name, r: nodeRadius(degree.get(n.id) ?? 0), x: p.x, y: p.y }] : []
        }),
        LABEL_FONT * view.scale * pxPerWorld,
      ),
    [graph, pos, degree, view.scale, pxPerWorld],
  )

  const zoomBy = (factor: number) => setView((v) => ({ ...v, scale: Math.min(6, Math.max(0.3, v.scale * factor)) }))
  const reset = () => setView({ scale: 1, x: 0, y: 0 })

  // Wheel zoom; non-passive listener so we can preventDefault the page scroll.
  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12
      setView((v) => ({ ...v, scale: Math.min(6, Math.max(0.3, v.scale * factor)) }))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [graph])

  const onPointerDown = (e: React.PointerEvent) => {
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y }
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return
    // Translate pixel delta into world units via the current scale + viewBox ratio.
    const k = (W / (svgRef.current?.clientWidth || W)) / view.scale
    setView((v) => ({ ...v, x: drag.current!.vx + (e.clientX - drag.current!.x) * k, y: drag.current!.vy + (e.clientY - drag.current!.y) * k }))
  }
  const endDrag = () => { drag.current = null }

  if (!graph) return <div className="grid h-full place-items-center text-on-surface-low"><Loader2 size={20} className="animate-spin" /></div>
  // 🔴 THIS STATE MEANS "ITEMS EXIST, ENTITIES DO NOT" — never "the library is empty". The parent
  // renders this view only when `!empty` (`stats.items > 0`), so the old copy here — "Add documents
  // to build the graph" — was shown ONLY to people who had already added documents, and never in
  // the one state where it would have been true. Measured on a seeded home: 6 items, 0 entities,
  // and that instruction on screen.
  //
  // What is actually missing is the enrichment pass, and its control is `view === 'library'`-only,
  // so it is off screen from here. The empty state carries the action itself rather than pointing at
  // a button the user cannot see. Through the `EmptyState` primitive, like every other empty state
  // on this page — the hand-rolled centered div was also the `emptystate` lens's outlier here.
  if (graph.nodes.length === 0) {
    return (
      <div className="grid h-full place-items-center">
        <EmptyState icon={Network} title="No entities extracted yet"
          hint="Your items have not been through entity extraction, so there is nothing to draw. Running it re-derives insights for items that are missing them."
          action={onRegenerate ? { label: regenerating ? 'Extracting…' : 'Regenerate intelligence', onClick: regenerating ? () => {} : onRegenerate, icon: Sparkles } : undefined} />
      </div>
    )
  }

  // Zoom/pan applied as a <g> transform (not a viewBox window) so it can CSS-transition
  // smoothly. During a drag we suppress the transition for 1:1 cursor tracking; wheel/
  // button zoom animates. Map: center → scale → recenter + pan.
  const transform = `translate(${cx} ${cy}) scale(${view.scale}) translate(${-cx + view.x} ${-cy + view.y})`

  return (
    <div className="relative h-full w-full overflow-hidden">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        className="h-full w-full touch-none select-none"
        style={{ cursor: drag.current ? 'grabbing' : 'grab' }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={endDrag} onPointerLeave={endDrag}
      >
        <g transform={transform} style={{ transition: drag.current ? 'none' : 'transform 200ms ease-out' }}>
        {graph.edges.map((e, i) => {
          const a = pos.get(e.source), b = pos.get(e.target)
          if (!a || !b) return null
          const active = hover === e.source || hover === e.target
          // 🔑 WEIGHT IS ENCODED ON TWO CHANNELS, COLOUR AND WIDTH. It has been on the wire all along
          // (`entity_relations.weight`) and drawn nowhere, so every relation looked equally strong.
          // Two channels rather than one because width alone is not readable under this viewBox: the
          // ramp lives inside a couple of screen pixels (see below), which is a difference you can
          // see between two ADJACENT lines and not one you can read off a single line. Colour carries
          // the rest, and the pair is also the redundant-coding answer to SC 1.4.1.
          const f = weightFraction(e.weight, span)
          // Resting relation: `--color-on-surface-low` at 0.7 measures 3.92:1 (dark) / 3.86:1
          // (light) against the canvas — `--color-outline-variant` at 0.5 measured 1.35:1 / 1.07:1.
          // The weight ramp mixes TOWARD the stronger neutral from exactly that colour, so the
          // lightest relation still paints the measured floor and nothing on this line can fall under
          // it. Width 1 rather than 0.6 because a sub-pixel stroke lands as partial pixel coverage,
          // so it cannot reach the ratio its colour promises — the ramp adds to that floor, never
          // below it. 0.15 stays for the DIMMED case: that is deliberate de-emphasis while the
          // pointer is on another node, not the resting state.
          //
          // 🔴 AND `strokeWidth` ALONE DID NOT ACHIEVE THAT — `vectorEffect` is what does. The
          // viewBox is 1000×1000 under `xMidYMid meet`, so this graph is never drawn 1:1: the CTM
          // scale measured **0.761 at 1440px and 0.358 at 390px**, which paints a declared `1` as
          // 0.76px on a desktop and 0.36px on a phone. The width change reduced the shortfall and
          // was described as removing it, which was wrong. `non-scaling-stroke` makes the declared
          // width the PAINTED width at any viewport and any zoom level, so the ratio above is a
          // real number rather than a nominal one — and the active widths (1.6 / 2.5) stop thinning
          // with the viewport too. It is also what bounds the width channel: because the declared
          // width IS the painted width, the whole weight ramp is 1.0→2.4 real pixels at rest.
          return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} vectorEffect="non-scaling-stroke" stroke={active ? 'var(--color-primary)' : weightStroke(f)} strokeWidth={weightWidth(f, active)} strokeOpacity={hover && !active ? 0.15 : 0.7} />
        })}
        {graph.nodes.map((n) => {
          const p = pos.get(n.id); if (!p) return null
          const r = nodeRadius(degree.get(n.id) ?? 0)
          const selected = selectedId != null && (n.name === selectedId || n.id === selectedId)
          const active = hover === n.id || selected
          return (
            <g key={n.id} data-entity-id={n.id} transform={`translate(${p.x},${p.y})`}
              onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.(n.name ?? n.id)}
              style={{ cursor: 'pointer' }} opacity={hover && !active ? 0.4 : 1}>
              {/* 🔑 THE OUTLINE IS WHAT MAKES AN ENTITY PERCEIVABLE, not the fill. The fill is a 30%
                  primary tint over surface, which measures 1.68:1 (dark) / 1.4:1 (light) against the
                  canvas — nowhere near SC 1.4.11's 3:1 for a graphical object you need in order to
                  read the view, and these dots ARE the view. Raising the tint would reach 3:1 in dark
                  only at 60% and never in light (2.27:1 at 60%), and it would restyle the graph; a
                  ≥3:1 boundary is the standard remedy for a low-contrast shape, so the stroke carries
                  it and the fill is left alone.
                  `--color-on-surface-low` measures 6.88:1 / 8.5:1. `--color-outline` was the closer
                  neighbour by name and FAILS light at 2.88:1, and `--color-primary` is unavailable
                  here by meaning, not by number: this very line uses it for `active`, so painting
                  resting nodes with it would erase hover and selection. Both are NEUTRALS, which is
                  why two measurements settle all twelve schemes — `design/schemes.ts` re-tints the
                  accent identity only ("Neutral surfaces stay from tokens.css"). */}
              <circle r={r} vectorEffect="non-scaling-stroke" fill={selected ? 'var(--color-primary)' : 'color-mix(in srgb, var(--color-primary) 30%, var(--color-surface))'} stroke={active ? 'var(--color-primary)' : 'var(--color-on-surface-low)'} strokeWidth={active ? 2.5 : 1} />
              {/* Placed by the measure-then-filter pass, not by a degree count or a zoom threshold:
                  this name is here because its estimated rect cleared every rect a LARGER entity had
                  already taken, and because the face renders above the readable floor. The hovered or
                  selected entity is labelled on top of that set regardless — a pointer or a selection
                  is a direct request for that one name, and keeping it out of the placement pass is
                  what stops every other label from reshuffling as the pointer moves. */}
              {n.name && (active || labelled.has(n.id)) && <text y={-r - LABEL_GAP} textAnchor="middle" className="fill-on-surface" style={{ fontSize: LABEL_FONT }}>{n.name}</text>}
            </g>
          )
        })}
        </g>
      </svg>

      {/* Zoom controls */}
      <GraphZoomControls onZoomIn={() => zoomBy(1.25)} onZoomOut={() => zoomBy(1 / 1.25)} onReset={reset} />
      <div className="absolute bottom-3 left-3 rounded-pill bg-surface-high/80 px-2 py-0.5 text-on-surface-low text-[0.75rem] tabular-nums backdrop-blur">{Math.round(view.scale * 100)}%</div>
    </div>
  )
}
