/** Text anchoring for reading-view highlights (KNOWLEDGE-LIBRARY T3.1).
 *
 *  A highlight has to survive a reload, and the reader renders markdown — so a
 *  character offset into the item's SOURCE is useless: the transform moves every
 *  index, and a heading marker or a link target contributes source characters that
 *  never reach the page. Anchoring is therefore done against the RENDERED text:
 *
 *    quote      the exact selected string, taken from the flattened article text
 *               (not `Selection.toString()`, which normalizes whitespace differently
 *               than a text-node concatenation does — the two disagree across block
 *               boundaries, and an anchor that cannot find its own quote is inert).
 *    occurrence which instance of that string it is, 0-based. Without it a second
 *               highlight of a repeated sentence would re-mark the first one.
 *
 *  Both are computed by {@link anchorFromSelection} and consumed by
 *  {@link markAnchors}, which is the inverse: same flattening, same match rule.
 *  If an edit removes the passage the anchor simply stops resolving — the row still
 *  lists on the item, it just stops painting. That is the designed degradation, not
 *  a gap: a highlight is a note about a passage, and the note outlives the passage.
 */

/** Class applied to the wrapper element painted around a resolved anchor. */
export const MARK_CLASS = 'kl-highlight'

/** Attribute carrying the owning annotation id, so a click on the paint can find
 *  the row it belongs to and the un-mark pass can remove one anchor's marks. */
export const MARK_ID_ATTR = 'data-annotation-id'

export interface ReadingAnchor {
  id: string
  quote: string
  occurrence: number
}

interface FlatText {
  /** Every text node under the root, in document order. */
  nodes: Text[]
  /** `starts[i]` is the index in `text` at which `nodes[i]` begins. */
  starts: number[]
  /** The concatenation of every text node's data. */
  text: string
}

/** Walk the root's text nodes, skipping anything already painted as a mark (so a
 *  second pass can't nest marks) and anything not rendered as prose. */
function flatten(root: HTMLElement): FlatText {
  const nodes: Text[] = []
  const starts: number[] = []
  let text = ''
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node: Node) {
      const parent = (node as Text).parentElement
      if (!parent) return NodeFilter.FILTER_REJECT
      const tag = parent.tagName
      if (tag === 'SCRIPT' || tag === 'STYLE') return NodeFilter.FILTER_REJECT
      return NodeFilter.FILTER_ACCEPT
    },
  })
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n as Text
    nodes.push(t)
    starts.push(text.length)
    text += t.data
  }
  return { nodes, starts, text }
}

/** Absolute index in the flattened text for a (container, offset) DOM position.
 *  Returns -1 when the position is not inside the flattened run. */
function absoluteIndex(flat: FlatText, container: Node, offset: number): number {
  if (container.nodeType === Node.TEXT_NODE) {
    const i = flat.nodes.indexOf(container as Text)
    return i === -1 ? -1 : flat.starts[i] + offset
  }
  // An element container means the boundary sits BETWEEN children (a selection that
  // ends at a paragraph edge). Resolve it to the first text node at or after that
  // child position, which is where the flattened run continues.
  const child = container.childNodes[offset]
  if (!child) {
    // Past the last child — the boundary is the end of this element's whole run.
    const inside = flat.nodes.filter((n) => container.contains(n))
    if (!inside.length) return -1
    const last = inside[inside.length - 1]
    return flat.starts[flat.nodes.indexOf(last)] + last.data.length
  }
  const next = flat.nodes.find((n) => n === child || child.contains(n))
  return next ? flat.starts[flat.nodes.indexOf(next)] : -1
}

/** Count how many times `quote` starts strictly before `before` in `text`. That
 *  count IS the 0-based occurrence index of the match at `before`. */
function occurrenceBefore(text: string, quote: string, before: number): number {
  let n = 0
  for (let i = text.indexOf(quote); i !== -1 && i < before; i = text.indexOf(quote, i + 1)) n += 1
  return n
}

/** Index of the `occurrence`-th match of `quote`, or -1 when there are fewer. */
function nthIndexOf(text: string, quote: string, occurrence: number): number {
  let i = text.indexOf(quote)
  for (let n = 0; i !== -1 && n < occurrence; n += 1) i = text.indexOf(quote, i + 1)
  return i
}

/** Derive a persistable anchor from the user's current selection inside `root`.
 *  Returns null when the selection is empty, collapsed, or reaches outside `root`
 *  — a highlight that spans out of the article has no meaning on the article. */
export function anchorFromSelection(
  root: HTMLElement,
  selection: Selection | null,
): { quote: string; occurrence: number } | null {
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null
  const range = selection.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) return null
  const flat = flatten(root)
  if (!flat.nodes.length) return null
  const start = absoluteIndex(flat, range.startContainer, range.startOffset)
  const end = absoluteIndex(flat, range.endContainer, range.endOffset)
  if (start < 0 || end < 0 || end <= start) return null
  const quote = flat.text.slice(start, end).trim()
  if (!quote) return null
  // Re-locate the TRIMMED quote: trimming may have moved the start, and the stored
  // occurrence must be the occurrence of what is stored, not of what was dragged.
  const at = flat.text.indexOf(quote, start)
  if (at === -1) return null
  return { quote, occurrence: occurrenceBefore(flat.text, quote, at) }
}

/** Remove every mark this module painted, restoring the original text nodes.
 *  `normalize()` re-joins the split neighbours so a re-mark pass sees one run
 *  again — without it, repeated paint/clear cycles would shard the paragraph and
 *  eventually make a quote unfindable within any single node. */
export function clearMarks(root: HTMLElement): void {
  const marks = Array.from(root.querySelectorAll(`.${MARK_CLASS}`))
  for (const mark of marks) {
    const parent = mark.parentNode
    if (!parent) continue
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark)
    parent.removeChild(mark)
  }
  if (marks.length) root.normalize()
}

/** Paint every anchor that still resolves. Returns the ids that did NOT resolve,
 *  so the caller can say so rather than silently showing nothing. */
export function markAnchors(root: HTMLElement, anchors: ReadingAnchor[]): string[] {
  clearMarks(root)
  const unresolved: string[] = []
  for (const anchor of anchors) {
    // Re-flatten per anchor: the previous anchor's marks split text nodes, so a
    // cached flattening would hold stale node references.
    const flat = flatten(root)
    const at = nthIndexOf(flat.text, anchor.quote, anchor.occurrence)
    if (at === -1 || !anchor.quote) {
      unresolved.push(anchor.id)
      continue
    }
    paint(flat, at, at + anchor.quote.length, anchor.id)
  }
  return unresolved
}

/** Wrap [start, end) of the flattened text in one mark element per text node it
 *  crosses. Per-node rather than one element for the whole range because a range
 *  spanning a `<strong>` or a paragraph break has no single valid wrapper. */
function paint(flat: FlatText, start: number, end: number, id: string): void {
  for (let i = flat.nodes.length - 1; i >= 0; i -= 1) {
    const node = flat.nodes[i]
    const nodeStart = flat.starts[i]
    const nodeEnd = nodeStart + node.data.length
    const from = Math.max(start, nodeStart)
    const to = Math.min(end, nodeEnd)
    if (to <= from) continue
    let target = node
    if (to < nodeEnd) target.splitText(to - nodeStart)
    if (from > nodeStart) target = target.splitText(from - nodeStart)
    const mark = node.ownerDocument.createElement('mark')
    mark.className = MARK_CLASS
    mark.setAttribute(MARK_ID_ATTR, id)
    target.parentNode?.insertBefore(mark, target)
    mark.appendChild(target)
  }
}

/** How far through a scrolling article the reader is, as a fraction in 0..1.
 *  A document shorter than its viewport is fully read by definition — reporting 0
 *  there would leave the indicator stuck at empty on exactly the articles a reader
 *  finishes fastest. */
export function scrollProgress(el: {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
}): number {
  const scrollable = el.scrollHeight - el.clientHeight
  if (scrollable <= 0) return 1
  return Math.max(0, Math.min(1, el.scrollTop / scrollable))
}
