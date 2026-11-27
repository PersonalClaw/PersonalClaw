import { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ChevronUp, ChevronDown, X } from 'lucide-react'
import { spring } from '../design/motion'
import { SearchField } from './SearchField'
import { IconButton } from './IconButton'
import { useFocusReturn } from './useFocusReturn'
import { useIsMobile } from '../app/useIsMobile'
import { findInText, matchingIndices } from './findText'

/** What the sr-only region says for a given scan state — worded, not the bare `3/17`
 *  the sighted counter shows. Exported so the announcement is asserted as a VALUE
 *  rather than by scraping the rendered bar.
 *
 *  🪤 The noun trap from `ui/ListControls.tsx`'s `ResultAnnouncement`: that primitive
 *  builds "No matching ${noun}", which with the honest noun here ("matches") reads
 *  "No matching matches". Find also has a second axis `ResultAnnouncement` has no
 *  concept of — WHICH match you are on — and cycling with ↑/↓ must re-announce even
 *  though the total did not change. So this is a sibling wording, not a second
 *  implementation of list filtering. */
export function findAnnouncement(query: string, active: number, total: number): string {
  if (!query.trim()) return ''
  if (!total) return 'No matches'
  return `Match ${active + 1} of ${total}`
}

/** Scroll *root* so the first occurrence of *query* inside *node* sits in the middle of it: true
 *  when it did. False, and nothing scrolled, where there is nothing to measure (no occurrence, no
 *  layout, as under a test DOM) or *root* does not scroll, so the caller scrolls the item instead. */
function scrollToOccurrence(node: HTMLElement, query: string, root: HTMLElement | null): boolean {
  if (!root || root.scrollHeight <= root.clientHeight || !query.trim()) return false
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT)
  let text: Node | null
  while ((text = walker.nextNode())) {
    const value = text.nodeValue ?? ''
    const [first] = findInText(value, query)
    if (!first) continue
    const range = document.createRange()
    try {
      range.setStart(text, Math.min(first.start, value.length))
      range.setEnd(text, Math.min(first.end, value.length))
    } catch { return false }
    if (typeof range.getBoundingClientRect !== 'function') return false
    const at = range.getBoundingClientRect()
    if (!at.height && !at.top) return false
    const box = root.getBoundingClientRect()
    root.scrollTo({ top: root.scrollTop + (at.top - box.top) - (root.clientHeight - at.height) / 2, behavior: 'smooth' })
    return true
  }
  return false
}

/** Find-in-<surface> — a compact bar docked under a surface's header. Case-insensitive
 *  substring search over whatever the host hands it; count ("3/17"), Enter/↓ next,
 *  Shift+Enter/↑ prev, Esc closes, focus returns to whatever opened it.
 *
 *  ── What it knows about the thing it searches: nothing ──────────────────────────
 *  Born in `pages/chat` over `ChatTurn[]` and a `Map<turnIndex, HTMLDivElement>`, it
 *  is now driven by three surface-neutral inputs, because the reader hands it article
 *  sections, not turns:
 *
 *    items       the ordered things a match can live in — the SCROLL UNITS, whatever
 *                they are (chat turns, article sections, log lines).
 *    segmentsOf  item → its searchable strings. A match never spans two segments, so
 *                a caller keeps a heading and its body apart rather than joining them
 *                with a space and inventing matches across the seam.
 *    nodeOf      item → the element to bring into view. A GETTER, not a node: hosts
 *                keep their nodes in a ref-held Map that mutates as rows mount, so
 *                resolving at scroll time is the only correct read.
 *
 *  The counter counts ITEMS that contain a match, not occurrences — "3/17" is the
 *  third of seventeen turns/sections you can jump between, which is what ↑/↓ move
 *  through. (Inherited from the chat bar, deliberately: the alternative counts stops
 *  the arrows cannot make.)
 *
 *  ── Two halves, one offset source ───────────────────────────────────────────────
 *  The active item is scrolled into view via `nodeOf`; ALL occurrences are painted
 *  with the CSS Custom Highlight API (a range-walk over the scroll container's text
 *  nodes) so rendered markdown is never re-parsed (K44-class safety) — where the API
 *  is absent the bar still cycles + scrolls, just without paint. Counter and painter
 *  both fold through `ui/findText`, which is what keeps their offsets identical
 *  (#546: two derivations of "the same" offsets drifted, and `Range.setEnd` threw).
 *
 *  Paints under the `pc-find` highlight name, styled once in `design/tokens.css`. */
export function FindBar<T>({ items, segmentsOf, nodeOf, revealOf, scrollRef, label, initialQuery, onClose }: {
  items: readonly T[]
  segmentsOf: (item: T) => string[]
  nodeOf: (item: T, index: number) => HTMLElement | null | undefined
  /** Open whatever hides *item*'s text (a folded long message), called as a step lands on it,
   *  before its match is scrolled to. Optional: a surface with nothing folded leaves it out. */
  revealOf?: (item: T, index: number) => void
  scrollRef: React.RefObject<HTMLElement | null>
  label: string
  initialQuery?: string
  onClose: () => void
}) {
  // Read ONCE at mount (like a defaultValue): a caller that opens the bar over a known
  // term — a chat-history search result deep-linked here — pre-seeds it, and everyone
  // else opens empty. Later changes to the prop do not reset a bar the user is already
  // typing in; a caller that needs a fresh seed remounts the bar (a keyed instance).
  const [query, setQuery] = useState(initialQuery ?? '')
  const [active, setActive] = useState(0)
  const [debounced, setDebounced] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const isMobile = useIsMobile()

  // Focus return. The bar autofocuses its input, so closing it used to drop
  // focus on <body> — a keyboard user's next Tab restarted from the top of the page,
  // which is how a transient surface makes the whole surface feel unreachable. The
  // shared hook captures during RENDER (before our own autoFocus lands) and refuses to
  // "restore" into the closing bar; the chat bar hand-rolled that, one directory away
  // from the hook.
  const focusReturnRef = useFocusReturn<HTMLDivElement>()

  // Debounce the scan 150ms so typing over a long surface stays smooth.
  useEffect(() => {
    const h = window.setTimeout(() => setDebounced(query), 150)
    return () => window.clearTimeout(h)
  }, [query])

  // The scroll-target set AND the counter's total. Memoised on the host's own inputs
  // (`items` and `segmentsOf` are stable references in a well-behaved caller), so an
  // unrelated re-render of the host does not re-scan the surface.
  const matchIndices = useMemo(
    () => matchingIndices(items, segmentsOf, debounced), [items, segmentsOf, debounced])

  useEffect(() => { setActive(0) }, [debounced])

  // Paint highlights with the CSS Custom Highlight API (feature-detected). We walk
  // the scroll container's text nodes and add a Range per occurrence. Cleared + rebuilt
  // whenever the query or the items change; fully removed on unmount.
  useEffect(() => {
    const CSSns = (window as unknown as { CSS?: { highlights?: Map<string, unknown> } }).CSS
    const HighlightCtor = (window as unknown as { Highlight?: new (...r: Range[]) => unknown }).Highlight
    if (!CSSns?.highlights || !HighlightCtor) return
    const root = scrollRef.current
    const clear = () => { CSSns.highlights!.delete('pc-find') }
    if (!root || !debounced.trim()) { clear(); return clear }
    const ranges: Range[] = []
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    let node: Node | null
    while ((node = walker.nextNode())) {
      const text = node.nodeValue ?? ''
      // Same offsets as the counter, by construction — indexing the original text,
      // not a case-folded copy whose length can differ (#546).
      for (const m of findInText(text, debounced)) {
        // Belt-and-braces: clamp, and never let one pathological node abort the
        // whole loop — a throw here used to leave the page with zero highlights.
        const start = Math.min(m.start, text.length)
        const end = Math.min(m.end, text.length)
        if (end <= start) continue
        try {
          const r = document.createRange()
          r.setStart(node, start); r.setEnd(node, end)
          ranges.push(r)
        } catch { /* skip this occurrence; keep painting the rest */ }
      }
    }
    CSSns.highlights!.set('pc-find', new HighlightCtor(...ranges))
    return clear
  }, [debounced, items, scrollRef])

  // The stop a step landed on, scrolled to once the host's reveal of it has rendered (`revealOf`
  // opens a fold, which moves what is below it). `step` makes every step a change, the same stop
  // twice included.
  const [landing, setLanding] = useState<{ index: number; step: number } | null>(null)
  function go(delta: number) {
    if (!matchIndices.length) return
    const next = (active + delta + matchIndices.length) % matchIndices.length
    setActive(next)
    const i = matchIndices[next]
    revealOf?.(items[i], i)
    setLanding((l) => ({ index: i, step: (l?.step ?? 0) + 1 }))
  }
  useEffect(() => {
    if (!landing) return
    const node = nodeOf(items[landing.index], landing.index)
    if (!node) return
    // The match itself, centred, where the page can measure it; the stop's whole item otherwise.
    if (!scrollToOccurrence(node, debounced, scrollRef.current)) node.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [landing]) // eslint-disable-line react-hooks/exhaustive-deps -- once per step; reads the rest at fire time

  // Seeded open: a bar created WITH an initialQuery brings its FIRST match into view on
  // its own — the same scroll pressing ↓ once gives (`go(0)`), so there is no second
  // scroll path. One-shot and gated on the seed: a bar opened empty (⌘F, the reader)
  // never auto-scrolls, so it keeps its type-then-cycle behavior unchanged. It fires only
  // once the seed has debounced through AND a match actually exists — so a deep-link into a
  // transcript that is still loading scrolls when the matching turn mounts, not to nothing.
  const seeded = useRef(!!initialQuery?.trim())
  useEffect(() => {
    if (!seeded.current) return
    if (debounced.trim() !== (initialQuery ?? '').trim()) return
    if (!matchIndices.length) return
    seeded.current = false
    go(0)
  }, [debounced, matchIndices]) // eslint-disable-line react-hooks/exhaustive-deps -- one-shot; reads go at fire time

  return (
    <motion.div
      ref={focusReturnRef}
      initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
      transition={spring.spatialFast}
      // Mobile (≤768px, `useIsMobile`) DOCKS the bar: it spans the column's gutter
      // instead of sitting as a right-aligned `w-fit` pill.
      //
      // MEASURED, in Chrome, both layouts at both widths — the honest numbers, because
      // the first version of this comment claimed a break at 390px that is not there:
      //   390px (iPhone 12-15)  w-fit → bar 344px, fits (left 30, right 374), input 147px
      //                         docked → bar 358px, input 161px          ← polish, +14px
      //   320px (iPhone SE 1)   w-fit → bar 344px, OVERFLOWS (left 0, right 344 > 320)
      //                         docked → bar 288px, fits (left 16), input 91px  ← the fix
      // So `w-fit` has a ~344px intrinsic floor (field + counter + three 28px buttons)
      // that cannot shrink; below roughly 360px the pill hangs off the edge and the left
      // gutter is eaten. Docked, the row shrinks with the column instead.
      className={`sticky top-2 z-30 flex items-center gap-xs rounded-pill border border-outline-variant/60 bg-surface/95 pl-xs pr-s h-10 shadow-md backdrop-blur-md ${
        isMobile ? 'mx-l w-auto' : 'ml-auto mr-l w-fit'}`}
      role="search"
      // Escape is bound on the CONTAINER, not only on the input. Tabbing to
      // Previous/Next/Close left Escape dead — the one key a user presses to get out of
      // a transient bar did nothing from three of its four tab stops.
      //
      // 🔑 AND IT STOPS PROPAGATION, or one press dismisses TWO layers. This bar is transient and
      // holds focus, while the surfaces hosting it also carry window-level Escape handlers that
      // dismiss something bigger — `pages/chat/ChatFilePanel` closes the open file that way, and it
      // is one of 26 modules binding a global Escape. `preventDefault` does NOT stop the native
      // event, so the press kept bubbling: open a file from chat, ⌘F, Escape — and the bar AND the
      // file panel both went, leaving the reader to re-open a file they never closed.
      // `stopPropagation` on the synthetic event stops the native one too, and React's listener
      // sits at the root container, BELOW `window` in the bubble path, so the outer handler never
      // runs. Whoever has focus owns the key — the doctrine `ui/tourEscapeLayer.test.tsx` states.
      onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose() } }}>
      <SearchField variant="inline" value={query} onChange={setQuery} inputRef={inputRef} autoFocus
        placeholder={label} ariaLabel={label} inlineIconSize={14}
        onKeyDown={(e) => {
          // ↑/↓ cycle too (the design text names them); without them the arrow keys
          // moved the text caret and the bar looked stuck on match 1.
          if (e.key === 'Enter') { e.preventDefault(); go(e.shiftKey ? -1 : 1) }
          else if (e.key === 'ArrowDown') { e.preventDefault(); go(1) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); go(-1) }
        }} />
      {/* Two regions, one truth. The visible `3/17` is aria-hidden so the terse glyph is
          not what a screen reader reads out; the sr-only sibling carries the worded form
          ("Match 3 of 17" / "No matches"). It is mounted from the first render with empty
          content — a live region that appears WITH its text is not reliably announced. */}
      <span aria-hidden="true" data-type="caption"
        className="min-w-[3.2rem] select-none text-center text-on-surface-low tabular-nums">
        {debounced.trim() ? (matchIndices.length ? `${active + 1}/${matchIndices.length}` : '0/0') : ''}
      </span>
      <div role="status" aria-live="polite" className="sr-only">
        {findAnnouncement(debounced, active, matchIndices.length)}
      </div>
      {/* The counter beside these announces the count, but the BUTTONS said nothing — a keyboard
          user lands on them (this primitive keeps its tab stop) and hears only "Previous match". */}
      <IconButton icon={ChevronUp} label="Previous match" onClick={() => go(-1)} disabled={!matchIndices.length}
        disabledReason={debounced.trim() ? 'Nothing matches this search yet' : 'Type something to search for'}
        size={28} iconSize={14} />
      <IconButton icon={ChevronDown} label="Next match" onClick={() => go(1)} disabled={!matchIndices.length}
        disabledReason={debounced.trim() ? 'Nothing matches this search yet' : 'Type something to search for'}
        size={28} iconSize={14} />
      <IconButton icon={X} label="Close find" onClick={onClose} size={28} iconSize={14} />
    </motion.div>
  )
}
