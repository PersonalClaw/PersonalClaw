import { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ChevronUp, ChevronDown, X } from 'lucide-react'
import { spring } from '../../design/motion'
import { SearchField } from '../../ui/SearchField'
import { IconButton } from '../../ui/IconButton'
import { useIsMobile } from '../../app/useIsMobile'
import { findInText, findMatches, type FindMatch } from './findMatches'
import type { ChatTurn } from './chatTypes'

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

/** Find-in-conversation (CHAT-CRAFT S2) — a compact bar docked under the chat
 *  header. Case-insensitive substring search over the hydrated turns; count
 *  ("3/17"), Enter/↓ next, Shift+Enter/↑ prev, Esc closes. The active match's
 *  turn is scrolled into view via the host's turnNodes map; ALL occurrences are
 *  painted with the CSS Custom Highlight API (a range-walk over the scroll
 *  container's text nodes) so markdown is never re-parsed (K44-class safety) —
 *  where the API is absent the bar still cycles + scrolls, just without paint.
 *
 *  Highlighting is intentionally decoupled from the pure `findMatches` coordinates:
 *  the scanner drives the count + per-turn scroll target (robust, testable), while
 *  the painter walks the live DOM text nodes (the only safe way to overlay rendered
 *  markdown without touching its structure). */
export function FindBar({ turns, scrollRef, turnNodes, onClose }: {
  turns: ChatTurn[]
  scrollRef: React.RefObject<HTMLDivElement | null>
  turnNodes: React.MutableRefObject<Map<number, HTMLDivElement>>
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [debounced, setDebounced] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const isMobile = useIsMobile()

  // Focus return (CC-6). The bar autofocuses its input, so closing it used to drop
  // focus on <body> — a keyboard user's next Tab restarted from the top of the page,
  // which is how a transient surface makes the whole transcript feel unreachable.
  // Remember whatever had focus when we mounted and hand it back on unmount.
  const restoreRef = useRef<HTMLElement | null>(
    typeof document === 'undefined' ? null : (document.activeElement as HTMLElement | null))
  useEffect(() => {
    const prev = restoreRef.current
    return () => {
      // Guard both ways: the element may have unmounted under us, and it may not be
      // focusable any more (a disabled button) — .focus() is then a silent no-op.
      if (prev && prev.isConnected && typeof prev.focus === 'function') prev.focus()
    }
  }, [])

  // Debounce the scan 150ms so typing in a long session stays smooth.
  useEffect(() => {
    const h = window.setTimeout(() => setDebounced(query), 150)
    return () => window.clearTimeout(h)
  }, [query])

  const matches = useMemo<FindMatch[]>(() => findMatches(turns, debounced), [turns, debounced])
  // Distinct turns that contain a match, in order — the scroll target set.
  const matchTurns = useMemo(() => {
    const seen = new Set<number>(); const out: number[] = []
    for (const m of matches) { if (!seen.has(m.turnIndex)) { seen.add(m.turnIndex); out.push(m.turnIndex) } }
    return out
  }, [matches])

  useEffect(() => { setActive(0) }, [debounced])

  // Paint highlights with the CSS Custom Highlight API (feature-detected). We walk
  // the scroll container's text nodes and add a Range per occurrence. Cleared + rebuilt
  // whenever the query or turns change; fully removed on unmount.
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
      // Same offsets as the scanner, by construction — indexing the original text,
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
  }, [debounced, turns, scrollRef])

  function go(delta: number) {
    if (!matchTurns.length) return
    const next = (active + delta + matchTurns.length) % matchTurns.length
    setActive(next)
    turnNodes.current.get(matchTurns[next])?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
      transition={spring.spatialFast}
      // Mobile (≤768px, `useIsMobile`) DOCKS the bar: it spans the transcript's gutter
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
        placeholder="Find in conversation" ariaLabel="Find in conversation" inlineIconSize={14}
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
        {debounced.trim() ? (matchTurns.length ? `${active + 1}/${matchTurns.length}` : '0/0') : ''}
      </span>
      <div role="status" aria-live="polite" className="sr-only">
        {findAnnouncement(debounced, active, matchTurns.length)}
      </div>
      {/* The counter beside these announces the count, but the BUTTONS said nothing — a keyboard
          user lands on them (this primitive keeps its tab stop) and hears only "Previous match". */}
      <IconButton icon={ChevronUp} label="Previous match" onClick={() => go(-1)} disabled={!matchTurns.length}
        disabledReason={debounced.trim() ? 'Nothing matches this search yet' : 'Type something to search for'}
        size={28} iconSize={14} />
      <IconButton icon={ChevronDown} label="Next match" onClick={() => go(1)} disabled={!matchTurns.length}
        disabledReason={debounced.trim() ? 'Nothing matches this search yet' : 'Type something to search for'}
        size={28} iconSize={14} />
      <IconButton icon={X} label="Close find" onClick={onClose} size={28} iconSize={14} />
    </motion.div>
  )
}
