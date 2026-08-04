import { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ChevronUp, ChevronDown, X } from 'lucide-react'
import { spring } from '../../design/motion'
import { SearchField } from '../../ui/SearchField'
import { IconButton } from '../../ui/IconButton'
import { findInText, findMatches, type FindMatch } from './findMatches'
import type { ChatTurn } from './chatTypes'

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
      className="sticky top-2 z-30 ml-auto mr-l flex w-fit items-center gap-1 rounded-pill border border-outline-variant/60 bg-surface/95 pl-1 pr-2 h-10 shadow-md backdrop-blur-md"
      role="search">
      <SearchField variant="inline" value={query} onChange={setQuery} inputRef={inputRef} autoFocus
        placeholder="Find in conversation" ariaLabel="Find in conversation" inlineIconSize={14}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { e.preventDefault(); onClose() }
          else if (e.key === 'Enter') { e.preventDefault(); go(e.shiftKey ? -1 : 1) }
        }} />
      <span className="min-w-[3.2rem] select-none text-center text-on-surface-low text-[0.75rem] tabular-nums" aria-live="polite">
        {debounced.trim() ? (matchTurns.length ? `${active + 1}/${matchTurns.length}` : '0/0') : ''}
      </span>
      <IconButton icon={ChevronUp} label="Previous match" onClick={() => go(-1)} disabled={!matchTurns.length} size={28} iconSize={14} />
      <IconButton icon={ChevronDown} label="Next match" onClick={() => go(1)} disabled={!matchTurns.length} size={28} iconSize={14} />
      <IconButton icon={X} label="Close find" onClick={onClose} size={28} iconSize={14} />
    </motion.div>
  )
}
