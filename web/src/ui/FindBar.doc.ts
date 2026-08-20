import type { UiDoc } from './uiDoc'

// Doc object for FindBar — the one find-in-<surface> bar. Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// FindBar.tsx at build time.
const doc: UiDoc = {
  name: 'FindBar',
  keywords: ['find', 'search', 'find in page', 'highlight', 'match', 'next', 'previous', 'cmd+f', 'ctrl+f', 'transcript', 'reader'],
  description:
    'Find-in-<surface>: a sticky pill docked under a surface\'s header that searches what is already in memory, counts the stops ("3/17"), cycles them with Enter/↓ and Shift+Enter/↑, and paints every occurrence with the CSS Custom Highlight API so rendered markdown is never re-parsed. Escape closes from every one of its four tab stops, and focus returns to whatever opened it. It knows NOTHING about what it searches: the host supplies the ordered items, what text each item exposes, and which node to scroll to — chat drives it with turns, the knowledge reader with article sections. Where the Highlight API is missing the bar still counts, cycles and scrolls, just without paint.',
  props: [
    { name: 'items', description: 'The ordered things a match can live in — the SCROLL UNITS, whatever they are (chat turns, article sections, log lines). Pass a stable reference: it is a scan dependency, so a fresh array on every host render re-scans the surface on every keystroke.' },
    { name: 'segmentsOf', description: "item → its searchable strings. Also declares where the SEAMS are: a match never spans two segments, so keep a heading apart from its body rather than joining them with a space and inventing a match across the join. Chat passes `findSegments`, which knows that a turn's tool cards are searchable by title and its approval cards are not searchable at all. Stable reference (a module-level function), for the same reason as `items`." },
    { name: 'nodeOf', description: '(item, index) → the element to bring into view when that item becomes the active match. A GETTER, not a node: hosts keep their nodes in a ref-held Map that mutates as rows mount and unmount, so resolving at scroll time is the only correct read. Return null/undefined for an item that is not currently rendered — the bar just skips the scroll.' },
    { name: 'scrollRef', description: 'The scroll container whose text nodes get painted. The bar walks THIS subtree, so it must be the element that actually contains the rendered content, not the page.' },
    { name: 'label', description: 'Names the surface being searched ("Find in conversation", "Find in article"). Used as both the placeholder and the accessible name — required, not defaulted, because a shared primitive that defaults to one caller\'s wording ships that caller\'s vocabulary to every other surface.' },
    { name: 'onClose', description: 'Close the bar. Fired by the ✕ and by Escape from any tab stop. The host owns the open/closed state (and typically the ⌘F/Ctrl+F shortcut that sets it).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Mount it inside the scroll container it searches, wrapped in AnimatePresence so its exit transition plays; it positions itself `sticky top-2` and docks full-width below 768px (a `w-fit` pill has a ~344px intrinsic floor, measured, which hangs off a 320px viewport).' },
    { guidance: true, description: 'Memoise `items` and hoist `segmentsOf` to module scope. Both are scan dependencies: with unstable references the whole surface is re-scanned on every host re-render, which for a streaming transcript is every token.' },
    { guidance: true, description: "Give `label` the surface's own noun. It is what a screen-reader user hears when they land in the field." },
    { guidance: false, description: 'Do not join an item\'s segments into one string to "simplify" `segmentsOf` — a query that straddles the seam then counts as a match the user cannot see highlighted anywhere.' },
    { guidance: false, description: 'Do not hand-roll a second find bar for a new surface, and do not fold match-counting into ListControls\' ResultAnnouncement: find has a second axis (WHICH match you are on) that a result count has no concept of, and the honest noun collides ("No matching matches").' },
    { guidance: false, description: 'Do not scan the DOM to count matches, or re-derive offsets for the painter. Counter and painter both fold through `ui/findText`; two derivations of "the same" offsets is exactly how issue 546 shipped a paint loop that threw on İ (U+0130) and blanked every highlight on the page.' },
  ],
  anatomy: ['sticky pill (role=search, Escape bound on the container)', 'SearchField variant=inline (autofocused, ↑/↓/Enter cycle)', 'aria-hidden glyph counter "3/17"', 'sr-only role=status live region ("Match 3 of 17" / "No matches")', 'Previous / Next IconButtons (gated, each with a disabledReason)', 'Close IconButton'],
}

export default doc
