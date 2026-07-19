import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Plus, Minus, Maximize2 } from 'lucide-react'
import { api, type MemoryGraphData } from '../../lib/api'

/** Memory graph — the auto-linked node graph of the whole memory store (facts +
 *  their relations), laid out radially in SVG (no graph-lib dependency; mirrors
 *  the KnowledgeGraph technique but over the memory {label,group}/{from,to} shape).
 *  Wheel/pinch zoom + drag-to-pan via a <g> transform; nodes tinted by group.
 *
 *  Two modes:
 *   • Standalone (no props) — fetches its own graph, hover-to-highlight, read-only.
 *   • Studio (props given) — the parent (MemoryStudio) owns the fetched data + the
 *     selection, so the graph, list, and inspector are views onto ONE object set.
 *     `focusRef` drives an Obsidian-style LOCAL-GRAPH focus: the node with that ref +
 *     its `hopDepth`-hop neighbourhood stay lit; everything else dims. `onSelectRef`
 *     fires when a node is clicked (→ the parent selects that memory). */
export function MemoryGraph({ data, focusRef, hopDepth = 1, onSelectRef, boxHeight }: {
  data?: MemoryGraphData | null
  focusRef?: string | null
  hopDepth?: number
  onSelectRef?: (ref: string) => void
  boxHeight?: number
} = {}) {
  const controlled = data !== undefined  // Studio passes data (even null while loading)
  const [selfGraph, setSelfGraph] = useState<MemoryGraphData | null>(null)
  const graph = controlled ? (data ?? null) : selfGraph
  const [hover, setHover] = useState<string | null>(null)
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 })
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)
  const boxRef = useRef<HTMLDivElement | null>(null)
  // Size the canvas to the space remaining below its own top edge (standalone), or
  // use the parent-provided height (Studio pane). Always fits within the viewport.
  const [measuredH, setMeasuredH] = useState(420)
  const boxH = boxHeight ?? measuredH
  useEffect(() => {
    if (boxHeight != null) return  // parent owns the height
    const measure = () => {
      const el = boxRef.current
      if (!el) return
      const top = el.getBoundingClientRect().top
      setMeasuredH(Math.max(280, window.innerHeight - top - 24))  // 24px breathing room
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [graph, boxHeight])

  useEffect(() => {
    if (controlled) return  // parent owns the fetch
    let alive = true
    api.memoryGraph().then((d) => { if (alive) setSelfGraph(d) }).catch(() => { if (alive) setSelfGraph({ nodes: [], edges: [] }) })
    return () => { alive = false }
  }, [controlled])

  const W = 1000, H = 1000, cx = W / 2, cy = H / 2
  const pos = useMemo(() => {
    const m = new Map<string, { x: number; y: number }>()
    const nodes = graph?.nodes ?? []
    const n = nodes.length
    nodes.forEach((node, i) => {
      const ring = i < 1 ? 0 : i < 9 ? 1 : i < 25 ? 2 : i < 60 ? 3 : 4
      const r = ring === 0 ? 0 : ring === 1 ? 130 : ring === 2 ? 250 : ring === 3 ? 370 : 470
      const ringStart = ring === 0 ? 0 : ring === 1 ? 1 : ring === 2 ? 9 : ring === 3 ? 25 : 60
      const ringCount = ring === 0 ? 1 : ring === 1 ? Math.min(8, n - 1) : ring === 2 ? Math.min(16, n - 9) : ring === 3 ? Math.min(35, n - 25) : n - 60
      const idx = i - ringStart
      const a = (idx / Math.max(1, ringCount)) * Math.PI * 2 - Math.PI / 2
      m.set(node.id, { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r })
    })
    return m
  }, [graph])

  // The id of the node currently focused (ref → id), and its N-hop neighbourhood —
  // the lit set for local-graph focus. Empty focus ⇒ everything lit (global view).
  const focusId = useMemo(() => {
    if (!focusRef || !graph) return null
    return graph.nodes.find((n) => n.ref === focusRef)?.id ?? null
  }, [focusRef, graph])
  const litSet = useMemo(() => {
    if (!focusId || !graph) return null  // null = no focus → all lit
    const adj = new Map<string, string[]>()
    for (const e of graph.edges) {
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
  }, [focusId, graph, hopDepth])

  // Group → hue so related facts read as a cluster. Deterministic per group string.
  const groupColor = (group?: string) => {
    if (!group) return 'var(--color-outline)'
    let h = 0
    for (let i = 0; i < group.length; i++) h = (h * 31 + group.charCodeAt(i)) % 360
    return `hsl(${h} 55% 60%)`
  }

  const zoomBy = (factor: number) => setView((v) => ({ ...v, scale: Math.min(6, Math.max(0.3, v.scale * factor)) }))
  const reset = () => setView({ scale: 1, x: 0, y: 0 })

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
    const k = (W / (svgRef.current?.clientWidth || W)) / view.scale
    setView((v) => ({ ...v, x: drag.current!.vx + (e.clientX - drag.current!.x) * k, y: drag.current!.vy + (e.clientY - drag.current!.y) * k }))
  }
  const endDrag = () => { drag.current = null }

  if (!graph) return <div ref={boxRef} className="grid place-items-center text-on-surface-low" style={{ height: boxH }}><Loader2 size={20} className="animate-spin" /></div>
  if (graph.nodes.length === 0) return <div ref={boxRef} className="grid place-items-center text-on-surface-low text-[0.875rem]" style={{ height: boxH }}>No memory graph yet — facts and their links appear here as memory grows.</div>

  const degree = new Map<string, number>()
  for (const e of graph.edges) { degree.set(e.from, (degree.get(e.from) ?? 0) + 1); degree.set(e.to, (degree.get(e.to) ?? 0) + 1) }
  const transform = `translate(${cx} ${cy}) scale(${view.scale}) translate(${-cx + view.x} ${-cy + view.y})`
  const labelAll = view.scale >= (graph.nodes.length > 40 ? 2.0 : 1.4)
  const groups = [...new Set(graph.nodes.map((n) => n.group).filter(Boolean))] as string[]
  // A node/edge is "lit" when there's no focus, or it's in the focus neighbourhood.
  const nodeLit = (id: string) => !litSet || litSet.has(id)
  const edgeLit = (e: { from: string; to: string }) => !litSet || (litSet.has(e.from) && litSet.has(e.to))

  return (
    <div ref={boxRef} className="relative w-full overflow-hidden" style={{ height: boxH }}>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet"
        className="h-full w-full touch-none select-none" style={{ cursor: drag.current ? 'grabbing' : 'grab' }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerLeave={endDrag}>
        <g transform={transform} style={{ transition: drag.current ? 'none' : 'transform 200ms ease-out' }}>
          {graph.edges.map((e, i) => {
            const a = pos.get(e.from), b = pos.get(e.to)
            if (!a || !b) return null
            const active = hover === e.from || hover === e.to
            const lit = edgeLit(e)
            return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={active ? 'var(--color-primary)' : 'var(--color-outline-variant)'} strokeWidth={active ? 1.6 : 0.6} strokeOpacity={!lit ? 0.05 : hover && !active ? 0.15 : 0.5} />
          })}
          {graph.nodes.map((n) => {
            const p = pos.get(n.id); if (!p) return null
            const deg = degree.get(n.id) ?? 0
            const r = Math.min(15, 5 + deg * 1.5)
            const active = hover === n.id
            const isFocus = n.id === focusId
            const lit = nodeLit(n.id)
            const dim = (hover && !active && !isFocus) || !lit
            const clickable = !!onSelectRef && !!n.ref
            return (
              <g key={n.id} transform={`translate(${p.x},${p.y})`}
                onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
                onClick={() => { if (clickable) onSelectRef!(n.ref!) }}
                style={{ cursor: clickable ? 'pointer' : 'default' }} opacity={dim ? (litSet && !lit ? 0.12 : 0.4) : 1}>
                <circle r={isFocus ? r + 2 : r} fill={groupColor(n.group)} fillOpacity={0.85} stroke={isFocus || active ? 'var(--color-primary)' : 'var(--color-outline-variant)'} strokeWidth={isFocus ? 3 : active ? 2.5 : 1}>
                  <title>{n.title || n.label}{n.group ? ` (${n.group})` : ''}</title>
                </circle>
                {(active || isFocus || deg > 2 || labelAll) && <text y={-r - 4} textAnchor="middle" className="fill-on-surface" style={{ fontSize: 10 }}>{n.label}</text>}
              </g>
            )
          })}
        </g>
      </svg>

      {/* group legend */}
      {groups.length > 0 && (
        <div className="absolute left-3 top-3 flex max-w-[45%] flex-wrap gap-1.5 rounded-lg bg-surface-high/80 p-1.5 backdrop-blur">
          {groups.slice(0, 8).map((g) => (
            <span key={g} className="inline-flex items-center gap-1 text-on-surface-low text-[0.68rem]">
              <span className="size-2 rounded-pill" style={{ background: groupColor(g) }} />{g}
            </span>
          ))}
        </div>
      )}

      <div className="absolute bottom-3 right-3 flex flex-col gap-1 rounded-lg bg-surface-high/90 p-1 backdrop-blur">
        <button type="button" onClick={() => zoomBy(1.25)} title="Zoom in" className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Plus size={15} /></button>
        <button type="button" onClick={() => zoomBy(1 / 1.25)} title="Zoom out" className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Minus size={15} /></button>
        <button type="button" onClick={reset} title="Reset view" className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Maximize2 size={14} /></button>
      </div>
      <div className="absolute bottom-3 left-3 rounded-pill bg-surface-high/80 px-2 py-0.5 text-on-surface-low text-[0.7rem] tabular-nums backdrop-blur">
        {graph.nodes.length} facts · {graph.edges.length} links · {Math.round(view.scale * 100)}%
      </div>
    </div>
  )
}
