import { useMemo, useRef, type ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { expr, exprHeavy } from '../../design/motion'
import { accentChip } from '../../design/accent'

/** A reusable, presentational SVG DAG renderer (P17). It draws ALREADY-POSITIONED
 *  nodes + edges — layout stays in the caller (`dag.ts` for tasks) so this component
 *  is graph-agnostic and Tasks / Workflows / (later) loop sub-goals can all share it.
 *
 *  Two expressive behaviors, both gated so they never fight the a11y switch or the
 *  refined tier:
 *   • a GATED node (blocked / awaiting-approval / error) gets a soft PULSING RING in
 *     its state tone — amplitude via `expr()`, static when `useReducedMotion()`.
 *   • an ACTIVE edge (work is flowing into an in-progress head) gets a particle that
 *     travels the bezier — gated by `exprHeavy()` AND reduced-motion (a heavy effect
 *     that DROPS at the refined tier, and only ever on active edges — never all of
 *     them, per the deleted-primitives particle-discipline lesson).
 *
 *  HOVER LINEAGE: hovering a node lights its FULL connected lineage — every
 *  transitive ancestor (prerequisite) and descendant (dependent) plus the edges
 *  between them — and dims everything else. Implemented as direct classList
 *  toggles on the rendered SVG (`.dag-hovering` on the root, `.dag-lit` on the
 *  lineage) so a large graph never re-renders on hover; tokens.css owns the look.
 *  KEYBOARD FOCUS lights the same lineage, so the affordance is not mouse-only.
 *
 *  KEYBOARD CONTRACT (#474). Every clickable node is a real button: `role="button"`,
 *  a tab stop, Enter/Space activation, and an accessible name from `node.label`.
 *  Before this, all three DAG surfaces (the task graph, the workflow run graph and
 *  plan review) rendered each node as a bare `<g onClick>` — no role, no tabIndex,
 *  no key handler, no name — so click was the ONLY way to open anything and a
 *  keyboard or screen-reader user could not reach a single node. `ListScaffold`'s
 *  rows are the in-repo reference for the pattern (the fix #307 landed).
 *
 *  Why the button is an INNER `<g>` rather than the node group itself: an
 *  `awaiting` node renders real Approve/Deny `<button>`s, and nesting those inside
 *  a `role="button"` subtree makes assistive tech present the node as a leaf and
 *  swallow them. So the focusable/operable surface wraps only the node's own box
 *  and content, and the gate buttons stay SIBLINGS of it — reachable in their own
 *  right. The outer group keeps `data-dag-node`, the hover handlers and the
 *  `.dag-node` class the lineage CSS keys off.
 *
 *  Node-level inline Approve/Deny is a declared extension point (`onApprove`/`onDeny`
 *  + the `awaiting` state) but is BACKEND-GATED for tasks: it needs a node-level
 *  approval seam that exists for chat/loop/workflow runs but NOT for tasks. */

export type DagNodeState = 'todo' | 'active' | 'blocked' | 'awaiting' | 'done' | 'error'

export interface DagNode {
  id: string
  x: number; y: number; w: number; h: number
  state: DagNodeState
  radius?: number
  /** Left accent bar color (a token var). Omit for no accent bar. */
  accent?: string
  /** A primary-tone ring (e.g. critical-path marker) drawn UNDER any state ring. */
  ringed?: boolean
  /** The node's ACCESSIBLE NAME — plain text, because `content` is arbitrary JSX
   *  inside a `foreignObject` and gives assistive tech nothing dependable to read.
   *  Every caller supplies it; omit it only for a purely decorative node. */
  label?: string
  content: ReactNode
}

export interface DagEdge {
  id: string
  /** Endpoint NODE ids (tail = prerequisite, head = dependent) — power the hover
   *  lineage highlight. Optional for back-compat: falls back to parsing the
   *  `${from}->${to}` id convention both current callers use. */
  from?: string
  to?: string
  x1: number; y1: number; x2: number; y2: number
  /** Particles flow only when true (work is flowing into an in-progress head). */
  active?: boolean
  /** A cycle/invalid edge — drawn in the danger tone. */
  bad?: boolean
}

// The tone a gated node's pulsing ring uses, by state. Only gated states pulse.
const RING_TONE: Partial<Record<DagNodeState, string>> = {
  blocked: 'var(--color-warn)',
  awaiting: 'var(--color-warn)',
  error: 'var(--color-danger)',
}

export function DagView({
  nodes, edges, width, height, onNodeClick, onApprove, onDeny, className, label,
}: {
  nodes: DagNode[]
  edges: DagEdge[]
  width: number
  height: number
  onNodeClick?: (id: string) => void
  /** Inline node approval — backend-gated; unused by the Tasks caller (no seam). */
  onApprove?: (id: string) => void
  onDeny?: (id: string) => void
  className?: string
  /** The whole graph's accessible name (e.g. "Dependency graph — 27 tasks"). An
   *  unnamed region is announced as nothing useful, so every caller passes one. */
  label?: string
}) {
  const reduce = useReducedMotion()
  const svgRef = useRef<SVGSVGElement>(null)
  const particles = exprHeavy() && !reduce   // heavy effect: drop at refined + reduced-motion
  // Particle traversal time scales inversely with expressiveness (bolder = livelier),
  // clamped to a calm range; a ref-free constant per render is fine (SMIL owns timing).
  const flowDur = 2.4 - expr(1.1, 0.2)

  // ── Hover lineage ──
  // Adjacency for the hover highlight: node → its prerequisite hops (up) and its
  // dependent hops (down), each carrying the edge id so the traversed edges light
  // up with the nodes. Endpoints come from edge.from/to, falling back to the
  // `${from}->${to}` id convention both current callers already use.
  const hops = useMemo(() => {
    const up = new Map<string, { n: string; e: string }[]>()
    const down = new Map<string, { n: string; e: string }[]>()
    for (const ed of edges) {
      let from = ed.from, to = ed.to
      if (!from || !to) {
        const i = ed.id.indexOf('->')
        if (i > 0) { from = ed.id.slice(0, i); to = ed.id.slice(i + 2) }
      }
      if (!from || !to) continue
      const u = up.get(to) ?? []; u.push({ n: from, e: ed.id }); up.set(to, u)
      const d = down.get(from) ?? []; d.push({ n: to, e: ed.id }); down.set(from, d)
    }
    return { up, down }
  }, [edges])

  // Hover applies/removes CSS classes DIRECTLY on the already-rendered SVG DOM
  // (no React state → zero re-render on a large graph); tokens.css owns the
  // dim/emphasis styling keyed off .dag-hovering / .dag-lit.
  const clearLineage = () => {
    const svg = svgRef.current; if (!svg) return
    svg.classList.remove('dag-hovering')
    svg.querySelectorAll('.dag-lit').forEach((el) => el.classList.remove('dag-lit'))
  }
  const showLineage = (id: string) => {
    const svg = svgRef.current; if (!svg) return
    clearLineage()
    // Full transitive closure in BOTH directions (all ancestors + all
    // descendants), collecting every traversed edge. Cycle-safe via `seen`.
    const litNodes = new Set<string>([id])
    const litEdges = new Set<string>()
    for (const dir of [hops.up, hops.down]) {
      const stack = [id]
      const seen = new Set<string>([id])
      while (stack.length) {
        const n = stack.pop()!
        for (const h of dir.get(n) ?? []) {
          litEdges.add(h.e)
          if (!seen.has(h.n)) { seen.add(h.n); litNodes.add(h.n); stack.push(h.n) }
        }
      }
    }
    svg.classList.add('dag-hovering')
    for (const n of litNodes) svg.querySelector(`[data-dag-node="${CSS.escape(n)}"]`)?.classList.add('dag-lit')
    for (const e of litEdges) svg.querySelector(`[data-dag-edge="${CSS.escape(e)}"]`)?.classList.add('dag-lit')
  }

  return (
    // `role="group"` (not `img`) keeps the nodes inside EXPOSED to assistive tech —
    // `role="img"` would collapse the whole graph to its own label and hide all of them.
    <svg ref={svgRef} width={width} height={height} className={className} style={{ width: '100%' }}
      role={label ? 'group' : undefined} aria-label={label}>
      <defs>
        <marker id="dag-arrow" markerWidth="8" markerHeight="8" refX="6.5" refY="4" orient="auto" markerUnits="userSpaceOnUse">
          <path d="M0,0 L8,4 L0,8 Z" fill="var(--color-outline)" />
        </marker>
        <marker id="dag-arrow-bad" markerWidth="8" markerHeight="8" refX="6.5" refY="4" orient="auto" markerUnits="userSpaceOnUse">
          <path d="M0,0 L8,4 L0,8 Z" fill="var(--color-danger)" />
        </marker>
        <marker id="dag-arrow-active" markerWidth="8" markerHeight="8" refX="6.5" refY="4" orient="auto" markerUnits="userSpaceOnUse">
          <path d="M0,0 L8,4 L0,8 Z" fill="var(--color-primary)" />
        </marker>
      </defs>

      {edges.map((e) => {
        const my = (e.y1 + e.y2) / 2
        const d = `M${e.x1},${e.y1} C${e.x1},${my} ${e.x2},${my} ${e.x2},${e.y2}`
        const stroke = e.bad ? 'var(--color-danger)' : e.active ? 'var(--color-primary)' : 'var(--color-outline-variant)'
        const marker = e.bad ? 'dag-arrow-bad' : e.active ? 'dag-arrow-active' : 'dag-arrow'
        return (
          <g key={e.id} data-dag-edge={e.id} className={e.bad ? 'dag-edge dag-edge-bad' : 'dag-edge'}>
            <path d={d} fill="none" stroke={stroke} strokeWidth={e.bad || e.active ? 2 : 1.5}
              markerEnd={`url(#${marker})`} opacity={e.bad ? 0.9 : e.active ? 0.85 : 0.7} />
            {/* Particle rides ONLY active edges, and only in the bold, motion-on tier. */}
            {e.active && particles && (
              <circle r={3} fill="var(--color-primary)">
                <animateMotion dur={`${flowDur}s`} repeatCount="indefinite" path={d} />
              </circle>
            )}
          </g>
        )
      })}

      {nodes.map((n) => {
        const r = n.radius ?? 12
        const ringTone = RING_TONE[n.state]
        const stroke = n.state === 'error' ? 'var(--color-danger)'
          : n.ringed ? 'var(--color-primary)'
          : 'var(--color-outline-variant)'
        const clip = `dag-clip-${n.id}`
        const open = onNodeClick ? () => onNodeClick(n.id) : undefined
        return (
          <g key={n.id} transform={`translate(${n.x},${n.y})`} data-dag-node={n.id}
            className={onNodeClick ? 'dag-node cursor-pointer group' : 'dag-node group'}
            onMouseEnter={() => showLineage(n.id)}
            onMouseLeave={clearLineage}>
            {/* State ring — a soft pulsing outline for gated nodes (blocked/awaiting/
                error). Static when reduced-motion; amplitude via expr() otherwise. */}
            {ringTone && (
              <motion.rect
                x={-2} y={-2} width={n.w + 4} height={n.h + 4} rx={r + 2}
                fill="none" stroke={ringTone} strokeWidth={2}
                initial={false}
                animate={reduce ? { opacity: 0.7 } : { opacity: [0.25, expr(0.85, 0.5), 0.25] }}
                transition={reduce ? undefined : { duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
              />
            )}
            {/* ── The focusable, operable node surface ──
                A tab stop with button semantics when the caller gave us an open
                handler; a named `img` when it did not (plan review draws a
                read-only graph, and an unnamed node reads as nothing at all).
                Space is preventDefault'd so activating a node never also scrolls
                the graph's container. Focus mirrors hover's lineage highlight. */}
            <g
              role={open ? 'button' : n.label ? 'img' : undefined}
              tabIndex={open ? 0 : undefined}
              aria-label={n.label}
              className="dag-node-hit"
              onClick={open}
              onKeyDown={open ? (ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open() }
              } : undefined}
              onFocus={open ? () => showLineage(n.id) : undefined}
              onBlur={open ? clearLineage : undefined}
            >
              {/* Focus ring — hidden until :focus-visible (tokens.css), so a keyboard
                  user can see WHICH node they are on. Drawn outside the state ring so
                  the two never overlap into one ambiguous outline. */}
              {open && (
                <rect className="dag-focus-ring" x={-5} y={-5} width={n.w + 10} height={n.h + 10} rx={r + 5}
                  fill="none" stroke="var(--color-primary)" strokeWidth={2.5} />
              )}
              <clipPath id={clip}><rect width={n.w} height={n.h} rx={r} /></clipPath>
              <rect width={n.w} height={n.h} rx={r} fill="var(--color-surface-container)"
                stroke={stroke} strokeWidth={n.state === 'error' || n.ringed ? 1.5 : 1}
                className="group-hover:brightness-125 transition-all" />
              {n.accent && <rect width={4} height={n.h} fill={n.accent} clipPath={`url(#${clip})`} />}
              {/* aria-hidden: the label above already names the node, and this is
                  arbitrary JSX that would otherwise be re-read as unstructured text. */}
              <foreignObject x={16} y={8} width={n.w - 28} height={n.h - 16} aria-hidden={n.label ? true : undefined}>
                {n.content}
              </foreignObject>
            </g>
            {/* Inline approve/deny for an awaiting-approval node. A SIBLING of the
                focusable surface above, never a child: nested inside `role="button"`
                these two would be announced as part of the node's name instead of as
                the controls they are. */}
            {n.state === 'awaiting' && onApprove && onDeny && (
              <foreignObject x={0} y={n.h} width={n.w} height={34}>
                <div className="flex items-center gap-1.5 pt-1.5">
                  <button type="button" onClick={(ev) => { ev.stopPropagation(); onApprove(n.id) }}
                    data-type="caption" className="inline-flex h-6 items-center gap-1 rounded-pill px-2"
                    style={accentChip}>
                    Approve
                  </button>
                  <button type="button" onClick={(ev) => { ev.stopPropagation(); onDeny(n.id) }}
                    data-type="caption" className="inline-flex h-6 items-center gap-1 rounded-pill px-2"
                    style={{ background: 'color-mix(in srgb, var(--color-danger) 16%, transparent)', color: 'var(--color-danger)' }}>
                    Deny
                  </button>
                </div>
              </foreignObject>
            )}
          </g>
        )
      })}
    </svg>
  )
}
