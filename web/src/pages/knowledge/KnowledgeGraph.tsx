import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Plus, Minus, Maximize2 } from 'lucide-react'

interface GraphNode { id: string; name?: string; type?: string }
interface GraphEdge { source: string; target: string; type?: string }

/** Entity graph — top entities laid out radially in SVG (no d3 dependency). Fills
 *  the available width/height; supports wheel/pinch zoom and drag-to-pan via an SVG
 *  viewBox transform, plus zoom buttons and a reset-to-fit control. Hover highlights
 *  a node + its edges; click selects it (opens the entity in the sidebar). */
export function KnowledgeGraph({ selectedId, onSelect }: {
  selectedId?: string | null
  onSelect?: (name: string) => void
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
  if (graph.nodes.length === 0) return <div className="grid h-full place-items-center text-on-surface-low text-[0.875rem]">No entities extracted yet. Add documents to build the graph.</div>

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
          return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={active ? 'var(--color-primary)' : 'var(--color-outline-variant)'} strokeWidth={active ? 1.6 : 0.6} strokeOpacity={hover && !active ? 0.15 : 0.5} />
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
              <circle r={r} fill={selected ? 'var(--color-primary)' : 'color-mix(in srgb, var(--color-primary) 30%, var(--color-surface))'} stroke={active ? 'var(--color-primary)' : 'var(--color-outline-variant)'} strokeWidth={active ? 2.5 : 1} />
              {(active || deg > 2 || labelAll) && <text y={-r - 4} textAnchor="middle" className="fill-on-surface" style={{ fontSize: 10 }}>{n.name}</text>}
            </g>
          )
        })}
        </g>
      </svg>

      {/* Zoom controls */}
      <div className="absolute bottom-3 right-3 flex flex-col gap-1 rounded-lg bg-surface-high/90 p-1 backdrop-blur">
        <button type="button" onClick={() => zoomBy(1.25)} title="Zoom in" aria-label="Zoom in"
          className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Plus size={15} /></button>
        <button type="button" onClick={() => zoomBy(1 / 1.25)} title="Zoom out" aria-label="Zoom out"
          className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Minus size={15} /></button>
        <button type="button" onClick={reset} title="Reset view" aria-label="Reset view"
          className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Maximize2 size={14} /></button>
      </div>
      <div className="absolute bottom-3 left-3 rounded-pill bg-surface-high/80 px-2 py-0.5 text-on-surface-low text-[0.7rem] tabular-nums backdrop-blur">{Math.round(view.scale * 100)}%</div>
    </div>
  )
}
