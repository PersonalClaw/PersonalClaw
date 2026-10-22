import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { SessionMapEntry } from './sessionMap'
import { SESSION_MAP_MIN_MARKS } from './sessionMap'
import { SessionMapCard, sessionMapMarkName } from './SessionMapCard'
import { currentMarkRange, useVisibleTurns } from './sessionMapRegion'
import { MARK_MAX_PX, markLength } from './sessionMapGeometry'
import { bindingLabel, useKeyBinding } from '../../app/keymap'

/** SESSION MAP RAIL — the markers, their preview, and the ways to move through them (#4776).
 *
 *  A vertical rail that indexes the transcript: ONE MARKER PER USER MESSAGE (`sessionMapEntries`),
 *  stacked on a constant pitch in the transcript's left gutter, fixed while the transcript scrolls.
 *
 *  ── THE DESIGN ─────────────────────────────────────────────────────────────────
 *
 *   1. A MARKER IS A USER MESSAGE. Assistant replies and their tool calls are not markers: a reply
 *      is too long to show, and its opening and what it did are on the question's preview.
 *   2. AT REST A MARKER IS SHORT, and an exchange on screen rests longer and in the accent, so the
 *      rail is a spatial overview of where the reader is, told by length as well as by colour.
 *      The lengths live in `sessionMapGeometry.ts`.
 *   3. THE TARGET GROWS AND ITS NEIGHBOURS FOLLOW. The marker the pointer, the keyboard cursor or
 *      a scrub is on grows to the full length in a brighter tone, and the three on either side
 *      grow by 0.7, 0.4 and 0.2 of the way: a sweep down the rail reads as a wave around the
 *      pointer rather than one line twitching. Only the PAINTED line changes length; every row is
 *      the same 32 x 24 target on the same pitch, so nothing a pointer aims at ever moves.
 *   4. A BOOKMARK IS A DOT at the end of its marker, a shape rather than a colour.
 *   5. ONE PREVIEW, BESIDE THE TARGET. Pointing (after a short intent delay), arrowing and
 *      scrubbing all show the same preview surface next to the marker they are on. There is one
 *      of it for the whole rail, so two can never be open at once.
 *   6. A CLICK JUMPS SMOOTHLY; PRESS AND DRAG SCRUBS. Dragging from one marker onto the next
 *      captures the pointer, and every marker the drag crosses jumps the transcript at once, with
 *      the preview following. Letting go leaves the transcript where the drag ended and fires no
 *      second jump.
 *   7. ⌥↑ / ⌥↓ JUMP BETWEEN PROMPTS FROM ANYWHERE (the keymap's `prev-message` / `next-message`),
 *      without tabbing into the rail first.
 *
 *  ── NO ANIMATION WORK AT REST ───────────────────────────────────────────────────────────────
 *
 *  A marker's length is a CSS `transform` with a short CSS transition, and nothing here schedules
 *  an animation frame: at rest, in a hidden document or under reduced motion (where the global
 *  reduced-motion rule in `tokens.css` collapses every transition) the rail does no work at all.
 *  `SessionMapRail.quiescence.test.tsx` counts the frames to keep it so.
 *
 *  ── THE TWO TONES ───────────────────────────────────────────────────────────────────────────
 *
 *  `design/schemeContrast.test.ts` parses the `const tone = isCurrent ? … : …` and `const lit = …`
 *  declarations below and measures them in all 12 schemes × both modes: both resting tones clear
 *  SC 1.4.11's 3:1 on the canvas, and each lit tone out-contrasts its resting one. The rest tone is
 *  `--color-map-rest`, a neutral minted for this rail as far in lightness from coral as 3:1 on the
 *  canvas allows. `aria-current` carries "on screen" to assistive tech.
 *
 *  ── THE CURRENT REGION IS WHAT IS ON SCREEN ─────────────────────────────────────────────────
 *
 *  `useVisibleTurns`' `IntersectionObserver` watches EVERY turn an entry owns — the question and
 *  each turn of its answer — and `currentMarkRange` resolves an on-screen turn to the question that
 *  opened its exchange (`sessionMapRegion.ts`). So while a long answer is being read, its question
 *  stays lit after the question itself has scrolled away.
 *
 *  ── THE KEYBOARD ────────────────────────────────────────────────────────────────────────────
 *
 *  The rail is ONE tab stop with a roving `tabIndex`, starting on the current region. The arrow
 *  keys move it, `Home`/`End` go to the ends and `PageUp`/`PageDown` step by `PAGE_STEP`;
 *  `Enter`/`Space` jump (`onJumpTo`, `ChatPage`'s `jumpToTurn`, the one scroll implementation).
 *
 *  🪤 PASSIVE FOCUS DOES NOT OPEN THE PREVIEW: the rail sits between the transcript and the
 *  composer, so a Tab on the way past must not flash one. A cursor key does — it arms the
 *  one-shot `reveal` immediately before it moves focus, and the receiving marker's `onFocus`
 *  consumes it. The preview a key opened is the next stop after the marker in the tab order (it is
 *  rendered inside the rail, after the list), so Tab reaches its bookmark control and Shift+Tab
 *  comes back; Escape closes it and leaves focus on the marker; focus leaving the rail closes it.
 *
 *  🔴 THE CURSOR SCROLLS THE RAIL BEFORE IT FOCUSES, because the rail has a scroller of its own:
 *  `revealInRail` runs first and `focus({ preventScroll: true })` raises no scroll of its own.
 *
 *  ── HIT TARGET, FOCUS RING, NAVRAIL CLEARANCE ───────────────────────────────────────────────
 *
 *  · THE PRESSABLE AREA IS THE WHOLE ROW, the rows are flush, and a row is 32×24 — WCAG 2.2 SC
 *    2.5.8's minimum, whatever length the line inside it is painted.
 *  · 🔴 THE ROW HAS TO CLEAR `NavRail`'s BAND, which is why this rail carries a fixed `ml-4`: the
 *    rail mounts flush against the shell's content-column edge, where `ui/NavRail`'s resize
 *    splitter wears a `.hit-24-x` band plus `z-10`. FIXED `ml-4`, NOT the t-shirt `ml-l`, whose
 *    compact densities drop under the 14px the clearance needs.
 *  · NO `outline-none`: the marker takes the global `:focus-visible` ring as well as growing.
 *
 *  ── THE RAIL HAS NO RETURN-TO-NEWEST OF ITS OWN, ON PURPOSE ─────────────────────────────────
 *
 *  "Back to the newest message" is `SessionMapReturnLatest`, the transcript's circular control, and
 *  `SessionMapReturnLatest.test.tsx` keeps it the only one app-wide.
 */

/** Preview width. A preview line wants roughly 45-55 characters to be worth reading. */
const CARD_WIDTH = 300

/** The gap between the rail and its preview, in px. */
const CARD_GAP = 8

/** Pointer-intent delays. The OPEN delay stops a pointer merely CROSSING the rail on its way to the
 *  transcript from opening a preview; once one is open, moving to another marker moves it at once.
 *  The CLOSE delay is the bridge that lets the pointer cross the gap and land on the preview. */
const OPEN_DELAY_MS = 150
const CLOSE_DELAY_MS = 120

/** `PageUp`/`PageDown` step, in markers — one per user message. */
const PAGE_STEP = 5

/** How long a ⌥↑/⌥↓ jump stays the base of the next one, in ms. A smooth jump takes a moment to
 *  bring its exchange on screen, and a second press inside that moment steps on from the message
 *  the first one went to rather than from where the transcript happened to be mid-scroll. */
const CHORD_MEMORY_MS = 1500

/** The tallest the marker list grows before it scrolls itself: the reference's cap. */
const RAIL_CAP = 'min(70vh, 40rem)'

/** The rail fades its own top and bottom edge so an overflowing marker list ends in a gradient
 *  rather than a hard cut. `black` rather than a hex because a mask reads only alpha. */
const EDGE_FADE =
  'linear-gradient(to bottom, transparent, black var(--spacing-xl),'
  + ' black calc(100% - var(--spacing-xl)), transparent)'

/** Keep the cursor's marker inside the rail's OWN scroller, clear of the edge fade. Rect-based;
 *  jsdom reports zeroes for every rect, which makes this a no-op there rather than a wrong scroll. */
function revealInRail(list: HTMLElement | null, mark: HTMLElement | null) {
  if (!list || !mark) return
  const l = list.getBoundingClientRect()
  const m = mark.getBoundingClientRect()
  const fade = parseFloat(getComputedStyle(list).paddingTop) || 0
  if (m.top < l.top + fade) list.scrollTop -= l.top + fade - m.top
  else if (m.bottom > l.bottom - fade) list.scrollTop += m.bottom - (l.bottom - fade)
}

/** Who opened the preview: it closes the way it was opened. */
type OpenedBy = 'pointer' | 'keyboard' | 'scrub'

export interface SessionMapRailProps {
  /** The ordered entries from `sessionMapEntries` — one marker per user message. */
  entries: SessionMapEntry[]
  /** `ChatPage`'s live `turnNodes` registry, keyed by the same coordinate an entry carries. Read
   *  only to observe which turns are on screen. */
  turnNodes: ReadonlyMap<number, Element>
  /** The transcript scroll container (`ChatPage`'s `scrollRef`) — the observer root. */
  scrollRef: { current: Element | null }
  /** `ChatPage`'s `jumpToTurn`: smooth for a click, a key or a chord, instant while scrubbing. */
  onJumpTo: (visibleIndex: number, how?: 'smooth' | 'instant') => void
  /** Bookmark a message, or take its bookmark off. Absent: the preview offers no control. */
  onToggleBookmark?: (entry: SessionMapEntry) => void
  /** The newest question's reply is still on its way (its preview holds a placeholder). */
  replyPending?: boolean
}

export function SessionMapRail(props: SessionMapRailProps) {
  // Self-suppression: below `SESSION_MAP_MIN_MARKS` the conversation needs no map. Guarded out
  // here rather than inside the body so the early return sits before any hook.
  if (props.entries.length < SESSION_MAP_MIN_MARKS) return null
  return <SessionMapRailBody {...props} />
}

function SessionMapRailBody({ entries, turnNodes, scrollRef, onJumpTo, onToggleBookmark, replyPending = false }: SessionMapRailProps) {
  const visibleTurns = useVisibleTurns(turnNodes, scrollRef, entries.flatMap((e) => e.coords))
  const [regionStart, regionEnd] = currentMarkRange(entries, visibleTurns)
  /** The roving cursor, or `null` while it has never been moved. */
  const [cursor, setCursor] = useState<number | null>(null)
  /** The ONE marker the pointer, the keyboard or a scrub is on, or `null`: the wave's centre. */
  const [target, setTarget] = useState<number | null>(null)
  /** The previewed marker and who opened its preview, or `null` while none is shown. */
  const [opened, setOpened] = useState<{ index: number; by: OpenedBy } | null>(null)
  /** What the rail says out loud. */
  const [announcement, setAnnouncement] = useState('')
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const listRef = useRef<HTMLDivElement>(null)
  const navRef = useRef<HTMLElement>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const hintId = useId()
  /** One-shot "this focus came from a cursor key" — set by `move`, consumed by `onFocus`. */
  const reveal = useRef(false)
  /** The one pending pointer intent (open or close). */
  const timer = useRef<number | null>(null)
  /** The marker the pointer is over, read by the open intent when it fires. */
  const hovered = useRef<number | null>(null)
  /** The marker holding keyboard focus, or `null`. */
  const focused = useRef<number | null>(null)
  /** A primary press on a marker: where it started, and whether it became a scrub. */
  const press = useRef<{ pointerId: number; from: number; at: number; scrubbing: boolean } | null>(null)
  /** Set by a scrub: the click its release produces is not a jump. */
  const swallowClick = useRef(false)
  /** The last ⌥↑/⌥↓ jump, the next one's base while it is fresh. */
  const lastChord = useRef<{ index: number; at: number } | null>(null)

  const lastIndex = entries.length - 1
  // The single tab stop. Before the first cursor key it is the current region's first marker.
  const tabStop = cursor === null ? Math.max(0, regionStart) : Math.min(cursor, lastIndex)
  // Entries can shrink under the rail (a rewind), so an index past the end shows nothing.
  const shown = opened && opened.index <= lastIndex ? opened : null
  const waveAt = target !== null && target <= lastIndex ? target : null

  const cancel = () => {
    if (timer.current !== null) { window.clearTimeout(timer.current); timer.current = null }
  }
  const after = (ms: number, fn: () => void) => {
    cancel()
    timer.current = window.setTimeout(() => { timer.current = null; fn() }, ms)
  }
  useEffect(() => () => { if (timer.current !== null) window.clearTimeout(timer.current) }, [])

  const jump = (index: number, how: 'smooth' | 'instant' = 'smooth', say = true) => {
    onJumpTo(entries[index].visibleIndex, how)
    // The marker's own name already says the cursor's position on every move, so this says only
    // what a screen-reader user cannot otherwise tell: that the TRANSCRIPT moved.
    if (say) setAnnouncement(`Jumped to message ${index + 1} of ${entries.length}`)
  }

  const move = (to: number) => {
    const next = Math.max(0, Math.min(lastIndex, to))
    setCursor(next)
    const el = buttons.current[next]
    if (!el) return
    revealInRail(listRef.current, el)
    reveal.current = true
    el.focus({ preventScroll: true })
    // Cleared unconditionally: when the cursor was already on `next`, no focus fired to consume it.
    reveal.current = false
  }

  // ── The preview's place: beside its marker, centred on it, inside the rail's box ──────────
  const place = () => {
    const card = cardRef.current
    const nav = navRef.current
    const mark = shown ? buttons.current[shown.index] : null
    if (!card || !nav || !mark) return
    const n = nav.getBoundingClientRect()
    const m = mark.getBoundingClientRect()
    const h = card.offsetHeight
    const top = m.top + m.height / 2 - n.top - h / 2
    card.style.top = `${Math.max(0, Math.min(top, Math.max(0, n.height - h)))}px`
  }
  // A layout effect, so the preview is placed before it is painted, never at the top for a frame.
  useLayoutEffect(place)
  useEffect(() => {
    if (!shown) return
    // The rail's own scroll and a resize move the marker under a preview that stays open; the
    // place is written to the preview's style directly, so neither renders the rail.
    const list = listRef.current
    list?.addEventListener('scroll', place, { passive: true })
    window.addEventListener('resize', place)
    return () => { list?.removeEventListener('scroll', place); window.removeEventListener('resize', place) }
  })

  // ── The pointer ─────────────────────────────────────────────────────────────────────────
  const indexOf = (el: EventTarget | null): number | null => {
    const mark = (el as Element | null)?.closest?.('[data-session-mark]')
    const i = mark ? Number(mark.getAttribute('data-index')) : NaN
    return Number.isInteger(i) ? i : null
  }
  /** The marker at a height, for a captured scrub whose events all land on the list. */
  const indexAtY = (y: number): number => {
    const rows = buttons.current
    for (let i = 0; i <= lastIndex; i++) {
      const r = rows[i]?.getBoundingClientRect()
      if (r && y < r.bottom) return i
    }
    return lastIndex
  }
  const scrubTo = (index: number) => {
    setTarget(index)
    setOpened((o) => (o && o.index === index && o.by === 'scrub' ? o : { index, by: 'scrub' }))
    revealInRail(listRef.current, buttons.current[index] ?? null)
    // Immediate while dragging, so the transcript is under the finger's intent as it moves.
    jump(index, 'instant', false)
  }

  const onPointerDown = (e: React.PointerEvent) => {
    swallowClick.current = false
    if (e.button !== 0 || e.pointerType === 'touch') return
    const i = indexOf(e.target)
    if (i === null) return
    press.current = { pointerId: e.pointerId, from: i, at: i, scrubbing: false }
  }
  const onPointerMove = (e: React.PointerEvent) => {
    const p = press.current
    if (p?.scrubbing) {
      const i = indexAtY(e.clientY)
      if (i !== p.at) { p.at = i; scrubTo(i) }
      return
    }
    const i = indexOf(e.target)
    if (i === null) return
    if (p && (e.buttons & 1) && i !== p.from) {
      // The press left its own marker for another: from here on it is a scrub.
      p.scrubbing = true
      p.at = i
      swallowClick.current = true
      cancel()
      listRef.current?.setPointerCapture?.(e.pointerId)
      scrubTo(i)
      return
    }
    if (hovered.current === i) return
    hovered.current = i
    cancel()
    setTarget(i)
    if (shown) setOpened({ index: i, by: 'pointer' })
    else after(OPEN_DELAY_MS, () => { if (hovered.current !== null) setOpened({ index: hovered.current, by: 'pointer' }) })
  }
  const endPress = (e: React.PointerEvent) => {
    const p = press.current
    press.current = null
    if (!p?.scrubbing) return
    if (listRef.current?.hasPointerCapture?.(e.pointerId)) listRef.current.releasePointerCapture(e.pointerId)
    // The keyboard carries on from where the scrub ended.
    setCursor(p.at)
    setOpened({ index: p.at, by: 'pointer' })
    // Let go away from the rail: the preview goes as it does when the pointer leaves.
    const n = navRef.current?.getBoundingClientRect()
    if (n && (e.clientX < n.left || e.clientX > n.right || e.clientY < n.top || e.clientY > n.bottom)) {
      after(CLOSE_DELAY_MS, closeFromPointer)
    }
  }

  const closeFromPointer = () => {
    hovered.current = null
    setOpened((o) => (o && o.by !== 'keyboard' ? null : o))
    setTarget(focused.current)
  }

  // ── The global chord ────────────────────────────────────────────────────────────────────
  const chord = (dir: -1 | 1) => {
    const now = Date.now()
    const recent = lastChord.current && now - lastChord.current.at < CHORD_MEMORY_MS ? lastChord.current.index : null
    let next: number
    if (recent !== null) next = recent + dir
    else if (regionEnd < regionStart) next = dir < 0 ? lastIndex : 0
    else if (dir < 0) {
      // Reading an answer whose question has scrolled away: ⌥↑ goes to that question first.
      const question = entries[regionStart].visibleIndex
      next = visibleTurns.includes(question) ? regionStart - 1 : regionStart
    } else next = regionEnd + 1
    next = Math.max(0, Math.min(lastIndex, next))
    lastChord.current = { index: next, at: now }
    setCursor(next)
    revealInRail(listRef.current, buttons.current[next] ?? null)
    jump(next)
  }
  useKeyBinding('prev-message', () => chord(-1))
  useKeyBinding('next-message', () => chord(1))

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      if (!shown) return
      // One layer: the preview closes, and nothing behind it hears the key.
      e.stopPropagation()
      e.preventDefault()
      const inCard = cardRef.current?.contains(document.activeElement)
      setOpened(null)
      if (inCard) buttons.current[shown.index]?.focus({ preventScroll: true })
      return
    }
    // The preview's own controls take their own keys.
    if (cardRef.current?.contains(e.target as Node)) return
    switch (e.key) {
      case 'ArrowUp': move(tabStop - 1); break
      case 'ArrowDown': move(tabStop + 1); break
      case 'Home': move(0); break
      case 'End': move(lastIndex); break
      case 'PageUp': move(tabStop - PAGE_STEP); break
      case 'PageDown': move(tabStop + PAGE_STEP); break
      case 'Enter': case ' ': jump(tabStop); break
      default: return
    }
    // Only for the keys the rail HANDLED: the arrows must not scroll the transcript out from under
    // the cursor, and cancelling `Enter`/`Space` stops the button's own activation firing a
    // SECOND jump through `onClick`.
    e.preventDefault()
  }

  // The on-screen tone and the rest tone — `design/schemeContrast.test.ts` parses THIS declaration
  // and measures both in 12 schemes × 2 modes, so keep it in this one shape.
  const toneOf = (isCurrent: boolean) => {
    const tone = isCurrent ? 'var(--color-primary)' : 'var(--color-map-rest)'
    return tone
  }
  // The target brightens: the accent's higher-contrast shade on screen, the strongest ink off it.
  const litOf = (isCurrent: boolean) => {
    const lit = isCurrent ? 'var(--color-primary-emphasis)' : 'var(--color-on-surface)'
    return lit
  }

  return (
    <nav
      ref={navRef}
      aria-label="Session map"
      aria-describedby={hintId}
      onKeyDown={onKeyDown}
      onPointerEnter={cancel}
      onPointerLeave={() => { if (!press.current?.scrubbing) after(CLOSE_DELAY_MS, closeFromPointer) }}
      // Focus leaving the rail entirely — not moving between its own markers or into the preview —
      // silences the live region and closes a preview the keyboard opened.
      onBlur={(e) => {
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
        focused.current = null
        setAnnouncement('')
        setOpened((o) => (o?.by === 'keyboard' ? null : o))
        setTarget(hovered.current)
      }}
      // `ml-4` keeps this rail's pressable band off `ui/NavRail`'s splitter band. `items-center`:
      // the marker list sits in the middle of the gutter rather than stretching.
      className="relative ml-4 flex h-full w-8 shrink-0 items-center"
    >
      <span id={hintId} className="sr-only">
        One mark per message you sent. Point at or arrow to a mark to preview it; click it or press
        Enter to jump to it, or press and drag along the marks to scrub through the conversation.
        Option or Alt with the up and down arrows moves between your messages from anywhere.
      </span>
      {/* The marker list — a constant-pitch column capped at `RAIL_CAP` that SCROLLS ITSELF once it
          outgrows it. `overscroll-contain` keeps a wheel gesture at the rail's end from chaining
          into the transcript. 🔴 `py-xl` IS THE FADE DISTANCE: a list that fits shows every marker
          at full strength, and one that overflows fades the markers it is cutting off.
          `select-none`: a scrub is a drag, and it must not select the page's text. */}
      <div
        ref={listRef}
        data-session-map-list
        className="flex w-full select-none flex-col overflow-y-auto overscroll-contain py-xl"
        style={{ maxHeight: RAIL_CAP, maskImage: EDGE_FADE, WebkitMaskImage: EDGE_FADE }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPress}
        onPointerCancel={endPress}
        onClickCapture={(e) => {
          if (!swallowClick.current) return
          swallowClick.current = false
          e.preventDefault()
          e.stopPropagation()
        }}
      >
        {entries.map((entry, i) => {
          const isCurrent = i >= regionStart && i <= regionEnd
          const len = markLength(i, waveAt, isCurrent)
          return (
            <button
              key={entry.markIndex}
              type="button"
              ref={(el) => { buttons.current[i] = el }}
              data-session-mark
              data-index={i}
              data-current={isCurrent || undefined}
              data-target={waveAt === i || undefined}
              data-bookmarked={entry.bookmarked || undefined}
              aria-current={isCurrent ? 'true' : undefined}
              tabIndex={i === tabStop ? 0 : -1}
              aria-label={sessionMapMarkName(entries, i)}
              onClick={() => jump(i)}
              onFocus={() => {
                focused.current = i
                cancel()
                setTarget(i)
                const wanted = reveal.current
                reveal.current = false
                if (wanted) setOpened({ index: i, by: 'keyboard' })
              }}
              // THE TARGET, AND THE PITCH — one declaration for both: `h-6` × `w-full` is 24×32,
              // `shrink-0` stops the column shrinking a row instead of scrolling, and the line grows
              // rightward from the rail's inner edge.
              className="relative flex h-6 w-full shrink-0 items-center justify-start"
              // `color`, NOT `background`: the painted thing is the LINE inside the row, which
              // inherits it through `currentColor`.
              style={{ color: waveAt === i ? litOf(isCurrent) : toneOf(isCurrent) }}
            >
              {/* THE MARKER: a `MARK_MAX_PX` line, 2px tall, scaled from its left edge to its painted
                  length. A CSS transform on a short CSS transition — no frame is scheduled for it. */}
              <span
                aria-hidden
                data-session-map-mark-line
                className="block h-0.5 shrink-0 origin-left rounded-pill transition-transform duration-(--duration-short) ease-out motion-reduce:transition-none"
                style={{ width: MARK_MAX_PX, background: 'currentColor', transform: `scaleX(${len / MARK_MAX_PX})` }}
              />
              {/* THE BOOKMARK: a dot just past the end of the line, which it follows as the line
                  grows. A shape, so a bookmark is told without its colour. */}
              {entry.bookmarked ? (
                <span
                  aria-hidden
                  data-session-map-bookmark
                  className="absolute left-0 top-1/2 size-1.5 rounded-full transition-transform duration-(--duration-short) ease-out motion-reduce:transition-none"
                  style={{ background: 'currentColor', transform: `translate(${len + 2}px, -50%)` }}
                />
              ) : null}
            </button>
          )
        })}
      </div>
      {/* THE PREVIEW — one for the rail, beside its target, inside the rail's box so it is the next
          tab stop after the marker that revealed it. */}
      {shown ? (
        <div
          ref={cardRef}
          data-session-map-preview
          data-opened-by={shown.by}
          role="group"
          aria-label={`Preview of message ${shown.index + 1} of ${entries.length}`}
          className="glass absolute left-full z-[var(--z-menu)] rounded-lgi px-m py-s"
          style={{ width: CARD_WIDTH, marginLeft: CARD_GAP, top: 0 }}
        >
          <SessionMapCard entry={entries[shown.index]} replyPending={replyPending && shown.index === lastIndex}
            onToggleBookmark={onToggleBookmark}
            keys={`${bindingLabel('prev-message')} ${bindingLabel('next-message')} your previous and next message, from anywhere`} />
        </div>
      ) : null}
      {/* Announces the keyboard's own jumps (and ⌥↑/⌥↓'s); a hover and a scrub stay silent. */}
      <span data-session-map-live role="status" aria-live="polite" className="sr-only">{announcement}</span>
    </nav>
  )
}
