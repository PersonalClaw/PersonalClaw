import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { Highlighter, PanelRight, Search, X } from 'lucide-react'
import { FindBar } from '../../ui/FindBar'
import { Button } from '../../ui/Button'
import { TextArea } from '../../ui/forms'
import { Markdown } from '../../ui/Markdown'
import { ProgressRing } from '../../ui/ProgressRing'
import { SelectionPill } from '../../ui/SelectionPill'
import { IconButton } from '../../ui/IconButton'
import { InlineError } from '../../ui/InlineError'
import { PROSE_MEASURE_CLASS } from '../../design/measure'
import { prefersReducedMotion } from '../../design/motion'
import { api, type KnowledgeAnnotation, type KnowledgeItem } from '../../lib/api'
import { anchorFromSelection, clearMarks, markAnchors, scrollProgress } from './readingAnchors'
import { getReadingPosition, setReadingPosition } from './readingPosition'
import { parseOutline, type OutlineEntry } from './readingOutline'
import { DocumentOutline } from './DocumentOutline'
import { RestructureControl } from './RestructureControl'

/** Words per minute used for the "N min read" estimate. The common editorial figure for
 *  adult prose; it is a rough orientation cue, not a measurement, and being off by 20%
 *  costs a reader nothing while having no estimate at all costs them the decision of
 *  whether to start now. */
const WPM = 220

/** The reader-pane width at which the insight rail can sit BESIDE the article instead of
 *  under it, as a container-query threshold rather than a viewport breakpoint.
 *
 *  🔑 The number is the sum of what the two columns need, not a device size: the article's
 *  measure is 35rem plus `px-l` either side, and the rail's own content (an entity chip row,
 *  a related-item title) stops being readable under about 17rem. 35 + 3 + 17 + a 1rem gap
 *  lands at 56rem, and 58rem leaves the article column the rem or two of slack that keeps
 *  the measure centred rather than pinned.
 *
 *  🪤 It is spelled literally in the class strings below (`@min-[58rem]:…`) and CANNOT be
 *  interpolated from this constant — Tailwind generates utilities by scanning source text,
 *  so a computed class name produces no CSS at all. This constant is the documentation of
 *  that literal; change both together. */
export const RAIL_SPLIT_WIDTH = '58rem'

/** How far down the scroller a heading has to have travelled before the reader counts as
 *  being inside its section, as a fraction of the scroller's height.
 *
 *  Not zero: a heading level with the very top of the pane is the one you are about to read,
 *  and switching the outline's active row only once a heading has scrolled off the top would
 *  leave the row a whole screen behind the prose. A quarter down is the band a reader's eye
 *  is actually in. */
const READING_LINE = 0.25

/** The heading elements the renderer actually produced, in document order. Scoped to the
 *  article body, which is why the reader's own `<h2>` item title (a SIBLING of `articleRef`)
 *  is correctly not one of them. */
function articleHeadings(article: HTMLElement): HTMLElement[] {
  return Array.from(article.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6'))
}

/** Which heading the reader is currently under: the LAST one whose top has passed the
 *  reading line, measured by rect against the scroller's own box.
 *
 *  Rects rather than an `IntersectionObserver`: the question is "which section am I in",
 *  which is a comparison between positions, and thresholds answer a different question
 *  ("how much of this heading is visible") that goes ambiguous the moment two headings are
 *  on screen at once or none is. `null` means the reader is above the first heading — in the
 *  body's preamble — which is a real state and not a missing answer. */
export function activeHeadingIndex(scroller: HTMLElement, headings: HTMLElement[]): number | null {
  if (headings.length === 0) return null
  const box = scroller.getBoundingClientRect()
  const line = box.top + box.height * READING_LINE
  let current: number | null = null
  headings.forEach((h, i) => { if (h.getBoundingClientRect().top <= line) current = i })
  return current
}

/** The article's top-level prose blocks — the find bar's scroll units.
 *
 *  A BLOCK rather than a section, because ↑/↓ should land a reader on the paragraph holding
 *  match 3 of 17, not on the heading of the section that happens to contain it. It is also
 *  the unit that makes `segmentsOf`'s contract true for free: the bar's docstring asks that
 *  "a match never spans two segments", and one block is one segment.
 *
 *  🪤 NOT `article.children`. `ui/Markdown` wraps its whole output in a single `<div>`, so the
 *  article ref's own child list is that wrapper — a walk over it finds ONE block containing
 *  the entire body, which counts every match as the same stop. The blocks are the wrapper's
 *  children: reached through a heading's parent when the body has headings, and through the
 *  lone wrapper child when it does not. */
export function articleBlocks(article: HTMLElement): HTMLElement[] {
  const heading = article.querySelector<HTMLElement>('h1, h2, h3, h4, h5, h6')
  const parent = heading?.parentElement ?? (article.firstElementChild as HTMLElement | null) ?? article
  return (Array.from(parent.children) as HTMLElement[]).filter((el) => (el.textContent ?? '').trim())
}

/** Map an outline ENTRY to the heading element it names, or `null` when that cannot be done
 *  safely.
 *
 *  🔑 BY DOCUMENT ORDER, BECAUSE AN OFFSET IS NOT A DOM COORDINATE. `OutlineEntry.offset` is
 *  a character index into the markdown SOURCE and deliberately never a position in the
 *  rendered tree — `readingAnchors`'s docstring is the long form ("the transform moves every
 *  index"), and `readingOutline`'s explains why the offset is still the right KEY. The one
 *  thing the source and the DOM can agree on without either re-deriving the other's text is
 *  order: the nth entry is the nth `h1`–`h6` in the article. So the entry is located by its
 *  INDEX in `entries`, and the offset is used only to find that index.
 *
 *  🪤 THE DEGRADE, AND WHY IT IS A NO-OP RATHER THAN A BEST EFFORT. That correspondence is an
 *  assumption about the renderer, not a guarantee: `parseOutline` skips setext headings,
 *  headings inside a blockquote or list item, and raw HTML `<h2>` — all of which DO render as
 *  heading elements — so a body using any of them has more heading nodes than entries, and
 *  every index after the extra one slips. When the counts disagree we cannot know which side
 *  gained or where, so there is no repair: index n is simply not this entry's heading, and
 *  scrolling a reader into the wrong section is worse than not moving them at all. */
export function headingForEntry(article: HTMLElement, entries: OutlineEntry[], offset: number): HTMLElement | null {
  const headings = articleHeadings(article)
  if (headings.length !== entries.length) return null
  const i = entries.findIndex((e) => e.offset === offset)
  return i < 0 ? null : headings[i] ?? null
}

/** Collapse a heading or title to the form worth comparing: no markdown marker, no case,
 *  no punctuation, single spaces. */
function normalizeHeading(s: string): string {
  return s.replace(/^#+\s*/, '').replace(/[^\p{L}\p{N}\s]/gu, '').trim().replace(/\s+/g, ' ').toLowerCase()
}

/** True when the body's FIRST heading already says the item's title.
 *
 *  Very common for a saved article — the fetched body opens with the headline the item is
 *  titled after — and left alone the reader printed the title twice, one line apart, at
 *  almost the same size. Found by opening a real article in a browser, not by a test. */
export function bodyOpensWithTitle(content: string, title: string): boolean {
  const heading = content.split('\n').find((line) => line.trim())?.trim() ?? ''
  if (!/^#{1,6}\s/.test(heading)) return false
  const t = normalizeHeading(title)
  return !!t && normalizeHeading(heading) === t
}

/** The reading view: the item's body at the editorial type scale, a scroll-progress
 *  indicator, and a select→highlight affordance whose result persists on the item.
 *
 *  Type scale: the `reading` class, which is the SAME editorial scale `.doc` gives the
 *  `document` content type (design/tokens.css). ui/Markdown pins every prose element at
 *  CHAT density — 0.9375rem body on muted ink — which is metadata sizing, and is exactly
 *  what makes the default detail view a data view rather than a reading view.
 *
 *  Highlights: `annotations` come from the owning page (so the More-details panel lists
 *  the same rows and a delete there re-paints here), and are re-anchored into the
 *  rendered prose by readingAnchors. An anchor whose passage no longer exists stops
 *  painting and is reported — it does not disappear.
 *
 *  Insight rail (KL-16): opening the reader used to cost the reader the More-details dock.
 *  It no longer does — `insightRail` rides beside the article when the reader PANE is wide
 *  enough for two columns, and folds under a disclosure when it is not.
 */
export function ReadingView({
  item, annotations, onAnnotationsChanged, insightRail, onRestructured,
}: {
  item: KnowledgeItem
  annotations: KnowledgeAnnotation[]
  /** Re-read the item's highlights after a write. */
  onAnnotationsChanged: () => void
  /** The dock's attention sections (highlights / entities / related items), supplied by the
   *  owning page. Absent when the item has none of the three — an empty column beside the
   *  article is worse than no column, and only the page can know. */
  insightRail?: React.ReactNode
  /** Re-read the ITEM after a structural restructure (KL-19). Separate from
   *  `onAnnotationsChanged` because a split or a retitle changes the body and the title, not
   *  just the highlight rows — reloading only the highlights would leave the reader looking at
   *  the pre-split article with the post-split marks on it. */
  onRestructured?: () => void
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const articleRef = useRef<HTMLDivElement | null>(null)
  const pillRef = useRef<HTMLButtonElement | null>(null)
  const composerRef = useRef<HTMLDivElement | null>(null)
  const [progress, setProgress] = useState(0)
  // The selection the user has finished making inside the article: the quote + which
  // occurrence of it, plus content-relative coords for the floating pill.
  const [pending, setPending] = useState<{ quote: string; occurrence: number; x: number; y: number } | null>(null)
  const [composing, setComposing] = useState<{ quote: string; occurrence: number; x: number; y: number } | null>(null)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const [unresolved, setUnresolved] = useState<string[]>([])
  // The insight rail's disclosure state, which only DECIDES anything in a narrow reader
  // pane: above the container-query threshold the rail is beside the article regardless,
  // so this flag is the fold-out for the one-column case.
  const [railOpen, setRailOpen] = useState(false)
  // The outline row the reader is currently inside, as an OFFSET (the entry key), not an
  // index — the outline panel identifies its rows by offset so that two `## Setup` sections
  // stay two rows. `null` is a real answer: the reader is above the first heading.
  const [activeOffset, setActiveOffset] = useState<number | null>(null)
  const [findOpen, setFindOpen] = useState(false)
  // The article's blocks as TEXT, snapshotted from the rendered prose after it commits.
  //
  // 🔑 FROM THE DOM AND NOT FROM `content`. The find bar's painter walks the scroll
  // container's text NODES, so a counter fed from the markdown source would count matches
  // the paint cannot show and miss ones it does: the source carries `#`, `**` and link
  // syntax the reader never sees. Both halves have to read the same text, and only one of
  // the two is the text the reader is looking at.
  const [blockText, setBlockText] = useState<string[]>([])

  const content = item.content || ''
  const minutes = item.word_count ? Math.max(1, Math.round(item.word_count / WPM)) : 0
  const titleIsInBody = bodyOpensWithTitle(content, item.title || item.url_title || '')
  const outline = useMemo(() => parseOutline(content), [content])
  // Whether the outline panel will render anything. It drops rows with no text (`##` alone is
  // a legal empty heading with nothing to put in a button), so "there are entries" is not the
  // same question as "there is a panel" — and an empty rail beside the article is the thing
  // the `insightRail` prop already goes out of its way to avoid.
  const hasOutline = outline.some((e) => e.text)
  const railHasContent = hasOutline || !!insightRail
  // Named for what is actually in it. A rail that promised an outline on a body with no
  // headings, or insights on an item with none, would be a control that reveals less than
  // its own name — so the name is composed from the two parts that are present.
  const railName = [hasOutline ? 'Outline' : '', insightRail ? 'Insights' : ''].filter(Boolean).join(' & ')

  // ── scroll progress ──────────────────────────────────────────────────────
  // rAF-coalesced: a scroll fires far faster than a paint, and setting state per event
  // would re-render the whole article body on every one of them.
  useEffect(() => {
    const root = scrollRef.current
    if (!root) return
    let frame = 0
    const read = () => {
      frame = 0
      setProgress(scrollProgress(root))
    }
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(read) }
    read()
    root.addEventListener('scroll', onScroll, { passive: true })
    return () => { root.removeEventListener('scroll', onScroll); if (frame) cancelAnimationFrame(frame) }
  }, [content])

  // ── resume where you left off (KL-8) ─────────────────────────────────────
  // The other half of the progress ring: KL-7 reported the fraction, this persists it, and
  // the library home's continue-reading shelf is what reads it back.
  //
  // 🔑 THE RESTORE GATES THE WRITE. `read()` above fires 0 on mount, and `setReadingPosition`
  // treats a fraction under 2% as "not started" and DELETES the entry — so persisting before
  // the restore lands would erase the position it is about to resume to. `restored` flips only
  // after the scroll is placed (or after we know there is nothing to place).
  const restored = useRef(false)
  useEffect(() => {
    restored.current = false
    const root = scrollRef.current
    if (!root) return
    const saved = getReadingPosition(item.id)
    if (!saved) { restored.current = true; return }
    // One frame later: the article body commits in this same paint, so scrollHeight is not
    // final until after it. A zero span (an article shorter than the pane) is not a failure —
    // there is simply nowhere to scroll, and the reader is already at their position.
    const frame = requestAnimationFrame(() => {
      const span = root.scrollHeight - root.clientHeight
      if (span > 0) root.scrollTop = saved.pct * span
      setProgress(scrollProgress(root))
      restored.current = true
    })
    return () => cancelAnimationFrame(frame)
  }, [item.id, content])

  useEffect(() => {
    if (!restored.current) return
    // Debounced off `progress` (itself rAF-coalesced), so a flick through a long article
    // writes once when it settles rather than on every frame.
    const t = setTimeout(() => setReadingPosition(item.id, progress), 400)
    return () => clearTimeout(t)
  }, [item.id, progress])

  // Opening the reader and actually starting to read is what "reading" MEANS, so the shelf's
  // membership comes from the act, not from remembering to press a button. Once per mount, and
  // only from 'unread': a finished article re-opened stays 'read' rather than being demoted
  // back onto the continue-reading shelf. Finishing is NOT auto-detected — scrolling to the
  // bottom to check a reference is not the same as being done, so the shelf's own "Mark read"
  // action (and the detail page's read-state cycle) stay the way an item leaves the shelf.
  //
  // The window is 2%–98%, the same one `readingPosition` calls "resumable", and BOTH ends are
  // load-bearing: `scrollProgress` reports 1 when there is nothing to scroll, so an article that
  // fits the pane (or one already scrolled to the end) would otherwise be filed as in-progress
  // the instant it opened and sit on the shelf until the reader dismissed it by hand.
  const markedReading = useRef(false)
  useEffect(() => { markedReading.current = false }, [item.id])
  useEffect(() => {
    if (markedReading.current || progress < 0.02 || progress >= 0.98) return
    if ((item.read_state || 'unread') !== 'unread') return
    markedReading.current = true
    api.setKnowledgeReadState(item.id, 'reading').catch(() => { markedReading.current = false })
  }, [item.id, item.read_state, progress])

  // ── find-in-article ──────────────────────────────────────────────────────
  // The block snapshot, re-taken whenever the body changes. In an effect rather than a memo
  // because on the first render for a new body `articleRef` still holds the PREVIOUS DOM (or
  // nothing at all), and a memo reading it there would snapshot the wrong article.
  useEffect(() => {
    const article = articleRef.current
    setBlockText(article ? articleBlocks(article).map((el) => el.textContent ?? '') : [])
  }, [content])

  // Each block is its own segment: `articleBlocks` already guarantees a match cannot span
  // two, which is what the bar asks a host for. Stable references both, so a keystroke in
  // the find field does not re-scan the article.
  const findSegments = useCallback((block: string) => [block], [])
  // A GETTER, resolved at scroll time, as `ui/FindBar` documents: the article element is
  // memoised on `content` but its nodes are re-created whenever it is, and a node captured
  // earlier can outlive the DOM it came from.
  const findNode = useCallback((_block: string, i: number) => {
    const article = articleRef.current
    return article ? articleBlocks(article)[i] : null
  }, [])

  // Cmd/Ctrl+F opens it, the same binding and the same escape hatch chat uses: a second
  // press (or Esc) closes and falls through to the browser's native find. Bound while the
  // reader is mounted, which is exactly while there is an article to search.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'f' || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return
      const t = e.target as HTMLElement | null
      // Don't hijack a different field — except our own find input, where a repeat ⌘F
      // should toggle the bar shut rather than no-op.
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
        && !t.closest('[role="search"]')) return
      e.preventDefault()
      setFindOpen((o) => !o)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // ── the outline's active row: a rect-based scroll spy ────────────────────
  // rAF-coalesced like the progress reader above, and here the coalescing is not just
  // politeness: this callback does a LAYOUT READ PER HEADING, so running it once per scroll
  // event would force a reflow dozens of times a frame on a long article.
  useEffect(() => {
    const root = scrollRef.current
    const article = articleRef.current
    if (!root || !article || outline.length === 0) return
    let frame = 0
    const read = () => {
      frame = 0
      const headings = articleHeadings(article)
      // The same count check `headingForEntry` makes, for the same reason: with the counts
      // disagreeing, index n is not entry n's heading, so highlighting a row would light up
      // the WRONG section — a confidently wrong answer where no answer is available.
      if (headings.length !== outline.length) { setActiveOffset(null); return }
      const i = activeHeadingIndex(root, headings)
      setActiveOffset(i === null ? null : outline[i].offset)
    }
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(read) }
    read()
    root.addEventListener('scroll', onScroll, { passive: true })
    return () => { root.removeEventListener('scroll', onScroll); if (frame) cancelAnimationFrame(frame) }
  }, [content, outline])

  // ── selection detection ──────────────────────────────────────────────────
  // Two triggers, for two input methods: `mouseup` is the drag, `selectionchange` is the
  // keyboard (shift+arrows) and touch path. Both are needed — a mouseup-only layer is
  // invisible to a keyboard user, and this affordance is not mouse-only.
  useEffect(() => {
    const article = articleRef.current
    const root = scrollRef.current
    if (!article || !root) return
    const inOwnUi = (t: EventTarget | null) =>
      (pillRef.current && t instanceof Node && pillRef.current.contains(t)) ||
      (composerRef.current && t instanceof Node && composerRef.current.contains(t))

    const recompute = () => {
      const selection = article.ownerDocument.getSelection()
      const anchor = anchorFromSelection(article, selection)
      if (!anchor || !selection || selection.rangeCount === 0) { setPending(null); return }
      const rect = selection.getRangeAt(0).getBoundingClientRect()
      const box = root.getBoundingClientRect()
      // The pill is absolutely positioned inside the SCROLLING root, so its coordinates
      // are content-relative. Without scrollTop/scrollLeft it drifts away from the
      // passage the moment the article scrolls.
      setPending({
        ...anchor,
        x: rect.left - box.left + root.scrollLeft + rect.width / 2,
        y: rect.top - box.top + root.scrollTop - 8,
      })
    }

    let frame = 0
    const onSelectionChange = () => {
      if (frame) return
      frame = requestAnimationFrame(() => { frame = 0; recompute() })
    }
    const onUp = (e: MouseEvent) => { if (!inOwnUi(e.target)) recompute() }
    const onDown = (e: MouseEvent) => { if (!inOwnUi(e.target)) setPending(null) }
    document.addEventListener('mouseup', onUp)
    document.addEventListener('selectionchange', onSelectionChange)
    root.addEventListener('mousedown', onDown)
    return () => {
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('selectionchange', onSelectionChange)
      root.removeEventListener('mousedown', onDown)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [content])

  // ── paint the saved highlights ───────────────────────────────────────────
  // `useLayoutEffect` so the marks land in the same frame the prose does — a paint pass
  // one frame late reads as the article flashing unmarked first.
  //
  // Mutating DOM inside a React subtree is only safe because `article` below is a
  // MEMOIZED element over `content`: React re-renders it when the body text changes and
  // at no other time, so nothing reconciles over the split text nodes in between. The
  // cleanup restores the original nodes before any such re-render.
  useLayoutEffect(() => {
    const article = articleRef.current
    if (!article) return
    setUnresolved(markAnchors(article, annotations))
    return () => clearMarks(article)
  }, [annotations, content])

  const article = useMemo(
    () => <Markdown className="reading">{content}</Markdown>,
    [content],
  )

  const openComposer = useCallback(() => {
    if (!pending) return
    setComposing(pending)
    setNote('')
    setPending(null)
    articleRef.current?.ownerDocument.getSelection()?.removeAllRanges()
  }, [pending])

  const closeComposer = useCallback(() => { setComposing(null); setNote('') }, [])

  /** Jump the article to an outline row's section. Silent when the entry cannot be mapped to
   *  a heading — see `headingForEntry` on why a wrong destination is worse than none. */
  const selectOutlineEntry = useCallback((entry: OutlineEntry) => {
    const article = articleRef.current
    if (!article) return
    // `scrollIntoView` optional-called: it is this app's 13-site idiom for "bring this row
    // into view", and jsdom implements none of it, so a caller's render test must not crash.
    headingForEntry(article, outline, entry.offset)?.scrollIntoView?.({
      block: 'start',
      behavior: prefersReducedMotion() ? 'auto' : 'smooth',
    })
  }, [outline])

  async function save() {
    if (!composing) return
    setSaving(true)
    setErr('')
    try {
      await api.createKnowledgeAnnotation(item.id, {
        quote: composing.quote,
        occurrence: composing.occurrence,
        note: note.trim() || undefined,
      })
      setComposing(null)
      setNote('')
      onAnnotationsChanged()
    } catch {
      setErr('Could not save that highlight.')
    } finally {
      setSaving(false)
    }
  }

  const pct = Math.round(progress * 100)
  const highlightHint = pending
    ? undefined
    : 'Select a passage in the article first'

  return (
    /* ── `@container`: the reader PANE is the query context, not the viewport ─────────
       The split below has to answer "does the article still have room beside a rail",
       and the viewport cannot answer it. This pane sits inside the nav rail and, when
       the user has it open, inside the "More details" SidePanel as well — both of which
       take width from the reader while the viewport stays exactly as wide as it was. A
       `lg:` breakpoint would therefore read "wide" on a 1440px window whose reader pane
       had been squeezed to 40rem, and would put the rail beside an article that no
       longer fits one. Declaring the pane a container makes the query measure the only
       width that matters: this element's. */
    <div className="@container flex h-full min-h-0 flex-col gap-m">
      {err && <InlineError onDismiss={() => setErr('')}>{err}</InlineError>}

      {/* Reading rail: how far through, how long it is, and the keyboard-reachable
          highlight action. The floating pill below is the pointer shortcut for the same
          verb — it is a convenience, never the only route to it. */}
      <div className="flex shrink-0 items-center gap-s">
        <ProgressRing pct={progress} tone="var(--color-primary)" size={22} label={`Reading progress: ${pct}%`} />
        <span className="text-on-surface-low" data-type="label-s">
          {pct}% read{minutes ? ` · ${minutes} min` : ''}
          {annotations.length ? ` · ${annotations.length} highlight${annotations.length === 1 ? '' : 's'}` : ''}
        </span>
        <div className="ml-auto flex items-center gap-s">
          {/* Find has a pointer route as well as ⌘F: a keyboard-only affordance is one the
              reader has to already know about, and nothing on this surface would have said
              it existed. */}
          <Button size="sm" variant="ghost" ariaExpanded={findOpen}
            onClick={() => setFindOpen((o) => !o)}>
            <Search size={14} /> Find
          </Button>
          <Button size="sm" variant="ghost" onClick={openComposer}
            disabled={!pending} disabledReason={highlightHint}>
            <Highlighter size={14} /> Highlight selection
          </Button>
          {/* KL-19 — the structural editing verbs, HERE rather than only on a management screen.
              A reader notices that one note is really three while reading it, and the selection
              this rail already tracks for Highlight is the same passage Extract needs, so the two
              verbs share one gesture. `onRestructured` reloads the item because every verb here
              changes the body, the title or the highlights under the reader. */}
          <RestructureControl item={item} selection={pending?.quote}
            onDone={() => { onRestructured?.(); onAnnotationsChanged() }} />
          {/* The narrow-pane fold-out for the rail. Wrapped rather than classed directly so
              the container variant lands on a plain div instead of racing Button's own
              display utility. Hidden — not merely redundant — above the threshold, where
              the rail is already on screen and a toggle for it would promise a fold that
              does not happen. */}
          {railHasContent && (
            <div className="@min-[58rem]:hidden">
              <Button size="sm" variant="ghost" ariaExpanded={railOpen}
                onClick={() => setRailOpen((v) => !v)}>
                <PanelRight size={14} /> {railName}
              </Button>
            </div>
          )}
        </div>
      </div>

      {unresolved.length > 0 && (
        <p className="shrink-0 text-on-surface-low" data-type="body-s">
          {unresolved.length} highlight{unresolved.length === 1 ? '' : 's'} no longer match the text and
          {unresolved.length === 1 ? ' is' : ' are'} listed under More details rather than marked here.
        </p>
      )}

      {/* ── The split: article, and the insight rail beside it or under it ──────────────
          `flex-col` is the base and `@min-[58rem]:flex-row` the wide case, so the rail's
          POSITION and the article's width are one decision made by the same container
          query. Everything responsive here is a `@min-[…]` variant: a viewport `md:`/`lg:`
          prefix anywhere on this axis would silently reintroduce the bug the container
          query exists to fix. */}
      <div className="flex min-h-0 flex-1 flex-col gap-m @min-[58rem]:flex-row">
        {/* `relative` establishes the positioning context the pill/composer anchor into
            (their coordinates are content-relative to THIS scroller). The trio
            tabIndex/role/aria-label makes a long article keyboard-scrollable and named. */}
        <div ref={scrollRef} tabIndex={0} role="group" aria-label="Article body"
          className="relative min-h-0 flex-1 overflow-y-auto rounded-lg border border-outline-variant/40 bg-surface-container">
          <AnimatePresence>
            {/* `ui/FindBar` knows nothing about what it searches; the reader supplies the
                article's own blocks as the scroll units, their rendered text as the
                searchable segments, and the block element to bring into view. It docks
                `sticky` inside THIS scroller, which is also the container its highlight
                painter walks — the same element on both sides, so the count and the paint
                can never be describing different text. */}
            {findOpen && (
              <FindBar items={blockText} segmentsOf={findSegments} nodeOf={findNode}
                scrollRef={scrollRef} label="Find in article" onClose={() => setFindOpen(false)} />
            )}
          </AnimatePresence>
          {/* The prose measure, now the app's ONE reading line-length rather than this
              surface's own number: `design/measure.ts` carries the measurement that produced
              it and the two other consumers (the document preview, the standalone HTML
              export) that used to each guess separately. This reader is where the
              measurement was taken; the token is where it lives. */}
          <div className={`mx-auto ${PROSE_MEASURE_CLASS} px-l py-xl`}>
            {/* Not a PageTitle: the page's h1 is the item title in the TopBar, and a second
                one would give the route two. This is the article's own opening line under it —
                and it is SUPPRESSED when the body already opens with that same headline, which
                is the common case for a saved article. */}
            {!titleIsInBody && (
              <h2 className="mb-xl text-on-surface" data-type="display-s">{item.title || item.url_title || 'Untitled'}</h2>
            )}
            <div ref={articleRef}>{article}</div>
          </div>

          {pending && (
            <SelectionPill ref={pillRef} icon={Highlighter} label="Highlight"
              x={pending.x} y={pending.y} onPress={openComposer} />
          )}

          {composing && (
            <div ref={composerRef}
              onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); closeComposer() } }}
              className="absolute z-40 w-[min(24rem,80vw)] -translate-x-1/2 rounded-xl bg-surface-highest p-m shadow-xl ring-1 ring-outline-variant/50"
              style={{ left: composing.x, top: composing.y }}>
              <div className="mb-2 max-h-16 overflow-y-auto rounded-md bg-surface-low px-2 py-1.5 text-on-surface-var italic line-clamp-3" data-type="body-s">
                “{composing.quote}”
              </div>
              <TextArea value={note} onChange={setNote} rows={3} size="sm" autoFocus
                ariaLabel="Note on this passage (optional)"
                placeholder="Why this matters… (optional)" />
              <div className="mt-2 flex justify-end gap-s">
                <Button size="sm" variant="ghost" onClick={closeComposer}>Cancel</Button>
                <Button size="sm" onClick={save} loading={saving}>Keep highlight</Button>
              </div>
            </div>
          )}
        </div>

        {/* The rail. ONE instance in the DOM either way — the container query moves it and
            the disclosure reveals it, so the outline, highlights, entities and related items
            a reader follows a lead from are never a second copy of anything (the insight
            sections are literally the dock's own components — see `ReaderInsights`).
            `@min-[58rem]:flex` overrides the collapsed `hidden` above the threshold, which
            is why the fold-out state is deliberately NOT allowed to gate the render: at
            wide widths the rail is unconditional, and a `{railOpen && …}` here would hide
            it in exactly the pane that has room for it.
            It scrolls, so it takes the article scroller's tab stop + name convention; as an
            `<aside>` it is also a landmark a screen-reader user can jump to by name. */}
        {railHasContent && (
          <aside tabIndex={0} aria-label={`Article ${railName.toLowerCase()}`}
            className={`${railOpen ? 'flex' : 'hidden'} max-h-[16rem] min-h-0 shrink-0 flex-col gap-l overflow-y-auto rounded-lg border border-outline-variant/40 bg-surface-container p-m @min-[58rem]:flex @min-[58rem]:max-h-none @min-[58rem]:w-[19rem]`}>
            {/* The outline leads: it is how a reader MOVES through the article, where the
                sections below are what they leave it for. `activeOffset` is this reader's
                rect-based scroll spy; the panel only keeps its own list scrolled to it. */}
            <DocumentOutline entries={outline} activeOffset={activeOffset} onSelect={selectOutlineEntry} />
            {insightRail}
          </aside>
        )}
      </div>
    </div>
  )
}

/** The item's highlights, listed. Rendered in the More-details panel so a highlight is
 *  visible on the item whether or not the reader is open — the reading view paints them
 *  in the prose, this says what they are and lets one go. */
export function AnnotationList({ annotations, onDelete }: {
  annotations: KnowledgeAnnotation[]
  onDelete: (id: string) => void
}) {
  return (
    <div className="flex flex-col gap-1.5">
      {annotations.map((a) => (
        <div key={a.id} className="rounded-md bg-surface-container px-m py-2">
          <div className="flex items-start gap-2">
            <blockquote className="min-w-0 flex-1 text-on-surface-var italic" data-type="body-s">“{a.quote}”</blockquote>
            {/* Named with the passage, not just the verb: every row's control would
                otherwise announce the same three words with nothing to choose between. */}
            <IconButton icon={X} size={26} iconSize={14} onClick={() => onDelete(a.id)}
              label={`Remove highlight: ${a.quote.slice(0, 40)}`} title="Remove highlight" />
          </div>
          {a.note && <p className="mt-1 text-on-surface" data-type="body-s">{a.note}</p>}
        </div>
      ))}
    </div>
  )
}
