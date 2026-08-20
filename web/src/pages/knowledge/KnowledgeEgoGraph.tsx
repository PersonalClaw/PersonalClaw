import { useMemo, useState } from 'react'
import { MemoryGraph } from '../settings/MemoryGraph'
import { litNeighbourhood } from '../../lib/litSet'
import { Segmented } from '../../ui/Segmented'
import { Select } from '../../ui/forms'
import { TextLink } from '../../ui/TextLink'
import type { MemoryGraphData } from '../../lib/api'

/** The wire shape of `GET /api/knowledge/graph` — nodes carry a display `name` and an entity
 *  `type`, edges a `type` (relation) and a `weight`. Declared here rather than imported because
 *  the graph route has no `api` client method yet (`KnowledgeGraph.tsx` raw-fetches it); the
 *  mount hands the payload in, so this component never fetches and stays trivially testable. */
export interface KnowledgeGraphNode {
  id: string
  name?: string
  type?: string
}
export interface KnowledgeGraphEdge {
  source: string
  target: string
  type?: string
  weight?: number
}
export interface KnowledgeGraphPayload {
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
}

/** Hop depths the focus control offers — the same 1..3 the Memory Studio's "Focus · hops"
 *  control offers, because it is the same question about the same traversal. */
const HOP_DEPTHS = [1, 2, 3]

/** Knowledge ego view — ONE document's neighbourhood, expanded by hop depth.
 *
 *  🔑 THIS IS AN ADAPTER, NOT A SECOND CANVAS. `pages/settings/MemoryGraph.tsx` is already a
 *  generic `{label,group}`/`{from,to}` graph canvas parameterised by noun, height and empty hint
 *  — it is only *filed* under settings. So the knowledge payload is mapped into that shape and
 *  handed over, which is why the radial layout, the group legend, the node counter, the
 *  hover/focus treatment and the shared `GraphZoomControls` cluster are all INHERITED here
 *  rather than re-implemented. The neighbourhood itself comes from `lib/litSet.ts` — the same
 *  lit-set BFS that canvas uses for its own local-graph focus.
 *
 *  🔑 WHY THE PAYLOAD IS NARROWED RATHER THAN DIMMED. In the Studio the lit set DIMS the rest of
 *  the graph to 12%, which is right for a full-pane canvas you are exploring. This view rides an
 *  item's reading rail, where drawing the entire library at 12% opacity would be noise around a
 *  picture you cannot read. So the lit set selects the SUBGRAPH: out-of-range nodes are absent,
 *  not faint, and raising the depth genuinely expands what is on screen. Same traversal, one
 *  implementation, two presentations.
 *
 *  Vocabulary is deliberately borrowed from the Memory Studio's canvas, not re-invented:
 *  "Focus · hops" for the depth control, `Filter links by type` / "Any link type" for the edge
 *  filter, "What links here" for the drawer, and "↺ show all" for the escape to the full graph. */
export function KnowledgeEgoGraph({
  data,
  focusId,
  boxHeight,
  onSelect,
  onShowAll,
  defaultHopDepth = 1,
}: {
  /** The graph payload. Owned by the mount so this component never fetches — the reading rail
   *  already loads the item's context, and a self-fetching rail widget would be a second reader
   *  of a route whose shape is still moving. */
  data: KnowledgeGraphPayload
  /** The node id the view is centred on — the document (or entity) whose rail this is. */
  focusId: string
  /** Canvas height in px. Omitted, the canvas self-measures to the space below its own top edge
   *  (the standalone behaviour); a rail should pass its pane height. */
  boxHeight?: number
  /** A neighbour was clicked. The parent decides what that means (navigate to the item), so this
   *  view holds no navigation state of its own and re-roots simply by being handed a new
   *  `focusId`. */
  onSelect?: (id: string) => void
  /** Leave the focused view for the full graph. Rendered as "↺ show all" — the Studio's own words
   *  for the same intent — and omitted entirely when unwired, rather than shipping a dead link. */
  onShowAll?: () => void
  /** Where the depth control STARTS. The depth itself stays user-owned from then on — the clause
   *  asks for expansion by hop depth, which a fixed depth cannot provide however it is chosen. */
  defaultHopDepth?: number
}) {
  const [hopDepth, setHopDepth] = useState(defaultHopDepth)
  const [linkType, setLinkType] = useState('')

  // Same construction as the Studio's `linkTypeOptions`, over this payload's relation types:
  // an "any" sentinel first, then the present types sorted, underscores read as spaces.
  const linkTypeOptions = useMemo(() => {
    const seen = new Set<string>()
    for (const e of data.edges) if (e.type) seen.add(e.type)
    return [
      { value: '', label: 'Any link type' },
      ...[...seen].sort().map((t) => ({ value: t, label: t.replace(/_/g, ' ') })),
    ]
  }, [data])

  // Edges surviving the filter, already in the canvas's undirected `{from,to}` shape so the
  // traversal and the drawing agree by construction.
  const edges = useMemo(
    () =>
      data.edges
        .filter((e) => !linkType || e.type === linkType)
        .map((e) => ({ from: e.source, to: e.target, type: e.type })),
    [data, linkType],
  )

  // THE lit set. Not a local BFS — `lib/litSet.ts`, the one MemoryGraph runs too.
  const lit = useMemo(() => litNeighbourhood(edges, focusId, hopDepth), [edges, focusId, hopDepth])

  const canvas: MemoryGraphData = useMemo(() => {
    const keep = lit ?? new Set<string>()
    return {
      nodes: data.nodes
        .filter((n) => keep.has(n.id))
        .map((n) => ({
          id: n.id,
          label: n.name || n.id,
          // The entity type becomes the canvas's `group`, which drives the legend AND the node
          // hue — so colour-by-group is colour-by-kind, not a second clustering the picture
          // invented. The canvas appends the group to the tooltip itself, so `title` must NOT
          // repeat it (that read "Analytical Engine — project (project)").
          group: n.type || undefined,
          title: n.name || n.id,
          // `ref` is the canvas's stable handle onto the source object; here the node id IS
          // that handle, so focus and selection round-trip without a second key space.
          ref: n.id,
        })),
      edges: edges.filter((e) => keep.has(e.from) && keep.has(e.to)),
    }
  }, [data, edges, lit])

  const focusNode = useMemo(() => data.nodes.find((n) => n.id === focusId) ?? null, [data, focusId])
  /** Whether the document has ANY link in the unfiltered payload. This is what separates "the
   *  graph pass has not reached this document" from "you filtered or narrowed its links away" —
   *  two states that look identical (an empty drawer) and want opposite advice. */
  const hasAnyLink = useMemo(
    () => data.edges.some((e) => e.source === focusId || e.target === focusId),
    [data, focusId],
  )

  /** The drawer's rows: every node in the neighbourhood except the focus, with the relation
   *  type(s) that put it there. One pass over the kept edges — no second traversal. */
  const neighbours = useMemo(() => {
    const keep = lit ?? new Set<string>()
    const types = new Map<string, Set<string>>()
    for (const e of edges) {
      if (!keep.has(e.from) || !keep.has(e.to)) continue
      for (const side of [e.from, e.to]) {
        if (side === focusId) continue
        const bucket = types.get(side) ?? types.set(side, new Set<string>()).get(side)!
        bucket.add(e.type || 'link')
      }
    }
    return canvas.nodes
      .filter((n) => n.id !== focusId)
      .map((n) => ({ id: n.id, label: n.label, types: [...(types.get(n.id) ?? ['link'])].sort() }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [canvas, edges, focusId, lit])

  // `role="group"`, not a bare named `<section>`: ARIA discards a name on a generic element, so the
  // label would look present in the markup and announce nothing (`design/ariaProhibitedAttr`).
  // `group` over `region` because this is a labelled cluster of controls inside a rail, not a
  // landmark a user should find in landmark navigation.
  return (
    <section
      role="group"
      aria-label="Focused neighbourhood"
      className="flex min-w-0 flex-col gap-2"
    >
      <div className="relative min-w-0 overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container/40">
        <MemoryGraph
          data={canvas}
          focusRef={focusId}
          hopDepth={hopDepth}
          onSelectRef={onSelect}
          boxHeight={boxHeight}
          nodeNoun="item"
          // The focus is always in its own neighbourhood, so a 0-node canvas has exactly ONE
          // cause: the focus is not in the payload. The "it has links but none in range" state is
          // a canvas with one node, and the drawer below is what speaks to it.
          //
          // 🪤 Neither of this view's two empty sentences promises a SCHEDULE ("… once the graph
          // pass has run"), even though the pass is real and cadenced (KL-13 on KL-14's host).
          // `settings/promisedMechanismsExist.test.ts` censuses "Nothing … yet" copy that also
          // promises an automatic future and requires each one traced to its mechanism in that
          // test's own VERIFIED list. Naming the cause instead of the timetable is both truer
          // (the view cannot tell "not indexed" from "nothing scored close enough") and outside
          // that census, so the copy states what is missing, not when it will arrive.
          emptyHint="This document is not in the graph — the graph covers items that have been indexed."
        />
        {/* The edge filter sits in the band between the canvas's own four occupied corners
            (legend top-left, focus control top-right, counter bottom-left, zoom bottom-right) —
            the same slot, and the same reason, as the Studio canvas's filter strip. */}
        <div className="absolute bottom-11 left-3 right-14 flex flex-nowrap items-center gap-1.5 overflow-x-auto pb-1">
          <div className="w-[8.5rem] shrink-0">
            <Select
              value={linkType}
              onChange={setLinkType}
              options={linkTypeOptions}
              ariaLabel="Filter links by type"
            />
          </div>
        </div>
        {/* The depth control. The Studio hand-rolled three bare toggles here; this is the
            canonical exclusive-choice primitive instead, which carries the tablist semantics and
            the selected state the hand-rolled version conveyed with colour alone. */}
        <div className="absolute right-3 top-3 flex items-center gap-2 rounded-pill bg-surface-high/90 px-2 py-1 text-[0.75rem] backdrop-blur">
          <span className="text-on-surface-low">Focus · hops</span>
          <Segmented
            size="sm"
            ariaLabel="Focus · hops"
            value={String(hopDepth)}
            onChange={(k) => setHopDepth(Number(k))}
            options={HOP_DEPTHS.map((d) => ({ key: String(d), label: String(d) }))}
          />
          {onShowAll && (
            <TextLink size="xs" ink="emphasis" onClick={onShowAll}>
              ↺ show all
            </TextLink>
          )}
        </div>
      </div>

      {/* ── DRAWER ──
          The canvas dims and un-dims; only text says WHICH documents came into range, and in a
          rail this narrow the labels on the picture are the first thing to go. So the
          neighbourhood is also listed, in the Studio drawer's own shape: an uppercase
          "What links here" header over relation-chip rows. */}
      <div className="min-w-0">
        <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">What links here</div>
        {focusNode && (
          <div className="mb-1 flex flex-wrap items-baseline gap-x-2 text-[0.75rem]">
            <span className="min-w-0 break-words text-on-surface">{focusNode.name || focusNode.id}</span>
            {focusNode.type && <span className="whitespace-nowrap text-on-surface-low">{focusNode.type}</span>}
          </div>
        )}
        {neighbours.length === 0 ? (
          <div className="mt-2 text-on-surface-low text-[0.75rem] italic">
            {hasAnyLink
              ? `Nothing links here within ${hopDepth} hop${hopDepth === 1 ? '' : 's'} — widen the hops, or clear the link filter.`
              : 'No links yet — either nothing scored close enough, or the graph has not covered this document.'}
          </div>
        ) : (
          <div className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2">
            {neighbours.map((n) => (
              <div key={n.id} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[0.75rem]">
                {n.types.map((t) => (
                  <span
                    key={t}
                    className="whitespace-nowrap rounded bg-surface-high px-1.5 py-0.5 uppercase tracking-wide text-on-surface-low"
                  >
                    {t.replace(/_/g, ' ')}
                  </span>
                ))}
                <span className="min-w-0 break-words text-on-surface-var">{n.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
