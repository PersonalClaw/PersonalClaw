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

/** Find-in-<surface> — a compact bar docked under a surface's header. Case-insensitive
 *  substring search over whatever the host hands it; count ("3/17"), Enter/↓ next,
 *  Shift+Enter/↑ prev, Esc closes, focus returns to whatever opened it.
 *
 *  ── What it knows about the thing it searches: nothing (KL-16) ──────────────────
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
export function FindBar<T>({ items, segmentsOf, nodeOf, scrollRef, label, onClose }: {
  items: readonly T[]
  segmentsOf: (item: T) => string[]
  nodeOf: (item: T, index: number) => HTMLElement | null | undefined
  scrollRef: React.RefObject<HTMLElement | null>
  label: string
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [debounced, setDebounced] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const isMobile = useIsMobile()

  // Focus return (CC-6). The bar autofocuses its input, so closing it used to drop
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

  function go(delta: number) {
    if (!matchIndices.length) return
    const next = (active + delta + matchIndices.length) % matchIndices.length
    setActive(next)
    const i = matchIndices[next]
    nodeOf(items[i], i)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

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
      className={`sticky top-2 z-30 flex items-center gap-1 rounded-pill border border-outline-variant/60 bg-surface/95 pl-1 pr-2 h-10 shadow-md backdrop-blur-md ${
        isMobile ? 'mx-l w-auto' : 'ml-auto mr-l w-fit'}`}
      role="search"
      // Escape is bound on the CONTAINER, not only on the input (CC-6). Tabbing to
      // Previous/Next/Close left Escape dead — the one key a user presses to get out of
      // a transient bar did nothing from three of its four tab stops.
      onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); onClose() } }}>
      <SearchField variant="inline" value={query} onChange={setQuery} inputRef={inputRef} autoFocus
        placeholder={label} ariaLabel={label} inlineIconSize={14}
        onKeyDown={(e) => {
          // ↑/↓ cycle too (the S2 design text names them); without them the arrow keys
          // moved the text caret and the bar looked stuck on match 1.
          if (e.key === 'Enter') { e.preventDefault(); go(e.shiftKey ? -1 : 1) }
          else if (e.key === 'ArrowDown') { e.preventDefault(); go(1) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); go(-1) }
        }} />
      {/* Two regions, one truth. The visible `3/17` is aria-hidden so the terse glyph is
          not what a screen reader reads out; the sr-only sibling carries the worded form
          ("Match 3 of 17" / "No matches"). It is mounted from the first render with empty
          content — a live region that appears WITH its text is not reliably announced. */}
      <span aria-hidden="true"
        className="min-w-[3.2rem] select-none text-center text-on-surface-low text-[0.75rem] tabular-nums">
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
