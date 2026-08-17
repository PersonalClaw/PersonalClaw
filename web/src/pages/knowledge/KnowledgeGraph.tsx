import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Network, Sparkles } from 'lucide-react'
import { GraphZoomControls } from '../../ui/GraphZoomControls'
import { EmptyState } from '../../ui/ListScaffold'

interface GraphNode { id: string; name?: string; type?: string }
interface GraphEdge { source: string; target: string; type?: string }

/** Entity graph — top entities laid out radially in SVG (no d3 dependency). Fills
 *  the available width/height; supports wheel/pinch zoom and drag-to-pan via an SVG
 *  viewBox transform, plus zoom buttons and a reset-to-fit control. Hover highlights
 *  a node + its edges; click selects it (opens the entity in the sidebar). */
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
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    let alive = true
    fetch('/api/knowledge/graph?limit=120', { headers: { 'X-Session-Key': 'dashboard:ui' } })
      .then((r) => r.json()).then((d) => { if (alive) setGraph(d) }).catch(() => { if (alive) setGraph({ nodes: [], edges: [] }) })
    return () => { alive = false }
  }, [])

  // World coordinate space (the layout canvas); the viewBox shows a window into it.
  const W = 1000, H = 1000, cx = W / 2, cy = H / 2
  const pos = useMemo(() => {
    const m = new Map<string, { x: number; y: number }>()
    const nodes = graph?.nodes ?? []
    const n = nodes.length
    nodes.forEach((node, i) => {
      // Concentric rings; spread across more rings as the graph grows.
      const ring = i < 1 ? 0 : i < 9 ? 1 : i < 25 ? 2 : 3
      const r = ring === 0 ? 0 : ring === 1 ? 160 : ring === 2 ? 300 : 440
      const ringStart = ring === 0 ? 0 : ring === 1 ? 1 : ring === 2 ? 9 : 25
      const ringCount = ring === 0 ? 1 : ring === 1 ? Math.min(8, n - 1) : ring === 2 ? Math.min(16, n - 9) : n - 25
      const idx = i - ringStart
      const a = (idx / Math.max(1, ringCount)) * Math.PI * 2 - Math.PI / 2
      m.set(node.id, { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r })
    })
    return m
  }, [graph])

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

  const degree = new Map<string, number>()
  for (const e of graph.edges) { degree.set(e.source, (degree.get(e.source) ?? 0) + 1); degree.set(e.target, (degree.get(e.target) ?? 0) + 1) }

  // Zoom/pan applied as a <g> transform (not a viewBox window) so it can CSS-transition
  // smoothly. During a drag we suppress the transition for 1:1 cursor tracking; wheel/
  // button zoom animates. Map: center → scale → recenter + pan.
  const transform = `translate(${cx} ${cy}) scale(${view.scale}) translate(${-cx + view.x} ${-cy + view.y})`

  // Reveal every entity's label once zoomed in enough (sparse graphs label sooner).
  const labelAll = view.scale >= (graph.nodes.length > 40 ? 1.8 : 1.3)

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
          // Resting relation: `--color-on-surface-low` at 0.7 measures 3.92:1 (dark) / 3.86:1
          // (light) against the canvas — `--color-outline-variant` at 0.5 measured 1.35:1 / 1.07:1.
          // Width 1 rather than 0.6 because a sub-pixel stroke lands as partial pixel coverage, so
          // it cannot reach the ratio its colour promises. 0.15 stays for the DIMMED case: that is
          // deliberate de-emphasis while the pointer is on another node, not the resting state.
          return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={active ? 'var(--color-primary)' : 'var(--color-on-surface-low)'} strokeWidth={active ? 1.6 : 1} strokeOpacity={hover && !active ? 0.15 : 0.7} />
        })}
        {graph.nodes.map((n) => {
          const p = pos.get(n.id); if (!p) return null
          const deg = degree.get(n.id) ?? 0
          const r = Math.min(16, 6 + deg * 1.5)
          const selected = selectedId != null && (n.name === selectedId || n.id === selectedId)
          const active = hover === n.id || selected
          return (
            <g key={n.id} transform={`translate(${p.x},${p.y})`}
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
              <circle r={r} fill={selected ? 'var(--color-primary)' : 'color-mix(in srgb, var(--color-primary) 30%, var(--color-surface))'} stroke={active ? 'var(--color-primary)' : 'var(--color-on-surface-low)'} strokeWidth={active ? 2.5 : 1} />
              {(active || deg > 2 || labelAll) && <text y={-r - 4} textAnchor="middle" className="fill-on-surface" style={{ fontSize: 10 }}>{n.name}</text>}
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
