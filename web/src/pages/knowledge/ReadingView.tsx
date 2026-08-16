import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Highlighter, X } from 'lucide-react'
import { Button } from '../../ui/Button'
import { TextArea } from '../../ui/forms'
import { Markdown } from '../../ui/Markdown'
import { ProgressRing } from '../../ui/ProgressRing'
import { SelectionPill } from '../../ui/SelectionPill'
import { IconButton } from '../../ui/IconButton'
import { InlineError } from '../../ui/InlineError'
import { api, type KnowledgeAnnotation, type KnowledgeItem } from '../../lib/api'
import { anchorFromSelection, clearMarks, markAnchors, scrollProgress } from './readingAnchors'

/** Words per minute used for the "N min read" estimate. The common editorial figure for
 *  adult prose; it is a rough orientation cue, not a measurement, and being off by 20%
 *  costs a reader nothing while having no estimate at all costs them the decision of
 *  whether to start now. */
const WPM = 220

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
 */
export function ReadingView({ item, annotations, onAnnotationsChanged }: {
  item: KnowledgeItem
  annotations: KnowledgeAnnotation[]
  /** Re-read the item's highlights after a write. */
  onAnnotationsChanged: () => void
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

  const content = item.content || ''
  const minutes = item.word_count ? Math.max(1, Math.round(item.word_count / WPM)) : 0
  const titleIsInBody = bodyOpensWithTitle(content, item.title || item.url_title || '')

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
    <div className="flex h-full min-h-0 flex-col gap-m">
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
        <div className="ml-auto">
          <Button size="sm" variant="ghost" onClick={openComposer}
            disabled={!pending} disabledReason={highlightHint}>
            <Highlighter size={14} /> Highlight selection
          </Button>
        </div>
      </div>

      {unresolved.length > 0 && (
        <p className="shrink-0 text-on-surface-low" data-type="body-s">
          {unresolved.length} highlight{unresolved.length === 1 ? '' : 's'} no longer match the text and
          {unresolved.length === 1 ? ' is' : ' are'} listed under More details rather than marked here.
        </p>
      )}

      {/* `relative` establishes the positioning context the pill/composer anchor into
          (their coordinates are content-relative to THIS scroller). The trio
          tabIndex/role/aria-label makes a long article keyboard-scrollable and named. */}
      <div ref={scrollRef} tabIndex={0} role="group" aria-label="Article body"
        className="relative min-h-0 flex-1 overflow-y-auto rounded-lg border border-outline-variant/40 bg-surface-container">
        {/* The measure, set by MEASUREMENT rather than by the `ch` unit. The document
            reader (ui/content/renderers.tsx) caps at 72ch, which sounds like 72 characters
            and is not: `ch` is the advance of "0", and in this font that is 0.66em, so 72ch
            resolved to 758px and a measured 101 CHARACTERS on a full line — well past the
            45-90 band a reader can return-sweep without losing their place. 35rem measures
            ~75. Stated in rem so the number means what it says. */}
        <div className="mx-auto max-w-[35rem] px-l py-xl">
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
