import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion, useReducedMotion } from 'framer-motion'
import { ArrowLeft, ArrowRight, Check, X, type LucideIcon } from 'lucide-react'
import { Button } from './Button'
import { IconButton } from './IconButton'
import { useFocusTrap } from './useFocusTrap'
import { ease, instant, spring } from '../design/motion'

/** One stop on a spotlight tour. */
export interface SpotlightStep {
  /** Stable id — also what the overlay reports as `data-tour-step`. */
  id: string
  /** The value of the `data-tour="…"` attribute on the element to spotlight. */
  anchor: string
  icon: LucideIcon
  title: string
  body: string
}

/** Gap between the anchor's box and the ring drawn around it. */
const RING_PAD = 6
/** Gap between the ring and the step card. */
const CARD_GAP = 12
const CARD_WIDTH = 320
/** Rough card height, used only to decide above-vs-below placement. */
const CARD_HEIGHT_HINT = 190
/** Keep the card off the viewport edge. */
const EDGE = 12
/** The anchor for a step arrives LATER than the step does: the host navigates, the
 *  route's chunk loads, then the page mounts. So the element is polled for rather
 *  than read once — bounded, because a stop whose surface never mounts must degrade
 *  to a centred card instead of hanging. */
const POLL_MS = 60
const POLL_TRIES = 40

interface Box { top: number; left: number; width: number; height: number }

/** A spotlight ("coach mark") tour over the REAL, mounted UI: the page dims except
 *  for a ring around one element, and a card beside it says what that element is.
 *
 *  It is a modal dialog and behaves like one, because the page underneath is dimmed:
 *  focus is trapped in the card and re-taken on every step (a navigating tour lands on
 *  surfaces that autofocus their own fields — Settings' search does — and without this
 *  the trap would be left holding nothing). Escape exits from any step — but only while the
 *  tour holds focus, so a layer opened ABOVE it (the Cmd+K palette rides `--z-toast`, above
 *  this overlay's `--z-modal`) closes on the first press instead of the tour underneath. A click anywhere
 *  outside the card exits too: the whole overlay sits on ONE transparent shield, so
 *  that click ends the tour instead of silently actioning a control the dim layer was
 *  covering. Nothing in the tour navigates or writes on its own — the host owns both.
 *
 *  Reduced motion is honoured as an ABSENCE, not a slower animation: no pulsing halo
 *  around the ring, no card transition between stops, and the anchor is jumped into
 *  view rather than smooth-scrolled.
 *
 *  A stop whose anchor is missing (or scrolled entirely out of the viewport — the
 *  mobile nav drawer parks off-screen) keeps its card, centred, with no ring: the copy
 *  still teaches, and the overlay reports `data-tour-anchored="false"` so a test can
 *  tell a resolved stop from a degraded one. */
export function SpotlightTour({ steps, index, label, onIndex, onExit }: {
  steps: SpotlightStep[]
  /** Which stop is showing (0-based). The host owns it, so Back/Next stay one source. */
  index: number
  /** The tour's name, e.g. "PersonalClaw tour" — it opens the dialog's accessible name. */
  label: string
  onIndex: (next: number) => void
  onExit: () => void
}) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  const reduce = useReducedMotion()
  const step = steps[index]
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null)
  const [box, setBox] = useState<Box | null>(null)

  // Escape exits from any stop, consumed so one press closes one layer — this app's
  // single-layer convention (ui/Popover documents it).
  //
  // 🔴 BUT ONLY WHEN THE TOUR HOLDS FOCUS, and that guard is the whole point. The original
  // reasoning here was "the tour is the topmost layer", which is false: this overlay rides
  // `--z-modal` and `app/CommandPalette` rides `--z-toast` (higher), so Cmd+K opens a palette ABOVE the tour
  // and takes focus — by design, because guidance never gates. The tour binds on `document`
  // and the palette on `window`, and `document` fires FIRST in the bubble path, so consuming
  // unconditionally swallowed the key before the focused layer ever saw it. Measured: with the
  // palette open and focused, one Escape closed the TOUR and left the palette up and focused;
  // a second closed the palette. Without the tour, that same press closes the palette.
  //
  // Deferring when focus sits in another layer restores the ordering with no layer registry:
  // whoever has focus owns the key. Focus on `<body>` still counts as ours — the trap takes
  // focus on mount and re-takes it on every stop, so an unfocused document means nothing else
  // has claimed it, and the tour must stay dismissable.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const active = document.activeElement
      const ours = !active || active === document.body || !!trapRef.current?.contains(active)
      if (!ours) return
      e.stopPropagation()
      onExit()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onExit, trapRef])

  // Resolve this stop's anchor. `find` re-runs on a bounded schedule because the host
  // may have just navigated: the surface is code-split, so the element does not exist
  // in the frame the step changed.
  useEffect(() => {
    setAnchorEl(null)
    let alive = true
    let tries = 0
    let timer = 0
    const find = () => {
      if (!alive) return
      const el = document.querySelector<HTMLElement>(`[data-tour="${step.anchor}"]`)
      if (el) { setAnchorEl(el); return }
      if (++tries > POLL_TRIES) return
      timer = window.setTimeout(find, POLL_MS)
    }
    find()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [step.anchor])

  // Measure it, and keep measuring: the ring is drawn in viewport coordinates, so a
  // resize, a scroll, or the anchor's own growth all move it.
  useEffect(() => {
    if (!anchorEl) { setBox(null); return }
    if (typeof anchorEl.scrollIntoView === 'function') {
      anchorEl.scrollIntoView({ block: 'center', inline: 'nearest', behavior: reduce ? 'auto' : 'smooth' })
    }
    const measure = () => {
      const r = anchorEl.getBoundingClientRect()
      // Zero-area (never laid out) or wholly off-screen (the parked mobile drawer) are
      // both "nothing to point at" — the card centres itself instead of ringing a box
      // the user cannot see.
      const onScreen = r.width > 0 && r.height > 0
        && r.bottom > 0 && r.right > 0
        && r.top < window.innerHeight && r.left < window.innerWidth
      setBox(onScreen ? { top: r.top, left: r.left, width: r.width, height: r.height } : null)
    }
    measure()
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null
    ro?.observe(anchorEl)
    return () => {
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
      ro?.disconnect()
    }
  }, [anchorEl, reduce])

  // Re-take focus on every stop. The card is the dialog; focusing its container (rather
  // than a control inside it) re-announces the step's name + body on each advance.
  //
  // Keyed on `anchorEl` as well as the stop, and that is the load-bearing half: the host
  // navigates when the stop changes, so the new surface mounts AFTERWARDS — and Settings'
  // bento autofocuses its search field on mount. Refocusing only on `index` would hand
  // focus to the page behind the dim while the markup still claimed `aria-modal`. Resolving
  // the anchor is the latest signal that the surface has arrived, so it is the right moment
  // to take focus back.
  useEffect(() => { trapRef.current?.focus() }, [index, anchorEl, trapRef])

  const ring = box && ringFor(box)
  const last = index === steps.length - 1
  const Icon = step.icon
  const titleId = `tour-title-${step.id}`
  const bodyId = `tour-body-${step.id}`

  return createPortal(
    <div className="fixed inset-0 z-[var(--z-modal)]">
      {/* ONE shield under everything: a click outside the card ends the tour rather
          than reaching a control the dim layer is covering. Escape is its keyboard twin. */}
      <div data-tour-shield className="absolute inset-0" onClick={onExit} aria-hidden />

      {/* The dim. Four bands around the ring so the anchor stays legible; one full
          sheet when there is nothing to point at. All decorative and click-through —
          the shield above owns the pointer. */}
      {ring ? (
        <div aria-hidden className="pointer-events-none">
          <Dim style={{ top: 0, left: 0, right: 0, height: ring.top }} reduce={reduce} />
          <Dim style={{ top: ring.top + ring.height, left: 0, right: 0, bottom: 0 }} reduce={reduce} />
          <Dim style={{ top: ring.top, left: 0, width: ring.left, height: ring.height }} reduce={reduce} />
          <Dim style={{ top: ring.top, left: ring.left + ring.width, right: 0, height: ring.height }} reduce={reduce} />
          <div className="fixed rounded-lg" style={{ ...ring, outline: '2px solid var(--color-primary)' }} />
          {/* The pulsing halo is the tour's one piece of ambient motion, so under
              reduced motion it is ABSENT rather than slowed. The static ring above
              stays either way — the spotlight never depends on movement to read. */}
          {!reduce && (
            <motion.div data-tour-halo className="fixed rounded-lg"
              style={{ ...ring, outline: '2px solid var(--color-primary)' }}
              animate={{ opacity: [0.55, 0, 0.55], scale: [1, 1.05, 1] }}
              transition={{ duration: 2.4, repeat: Infinity, ease: ease.emphasized }} />
          )}
        </div>
      ) : (
        <Dim style={{ inset: 0 }} reduce={reduce} />
      )}

      <motion.div ref={trapRef} role="dialog" aria-modal="true" tabIndex={-1}
        aria-label={`${label} — step ${index + 1} of ${steps.length}: ${step.title}`}
        aria-describedby={bodyId}
        data-tour-step={step.id}
        data-tour-anchored={anchorEl ? 'true' : 'false'}
        className="squircle fixed flex flex-col gap-m bg-surface p-l shadow-sheet"
        style={cardPosition(ring)}
        initial={reduce ? false : { opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={reduce ? instant : spring.spatialFast}>
        <div className="flex items-start gap-s">
          <span className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-lg"
            style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
            <Icon size={15} className="text-primary" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <p id={titleId} data-type="title-s" className="text-on-surface">{step.title}</p>
            <p id={bodyId} className="mt-1 text-on-surface-low text-[0.8125rem]">{step.body}</p>
          </div>
          <IconButton icon={X} label="End the tour" onClick={onExit} size={30} iconSize={15}
            className="shrink-0 text-on-surface-low" />
        </div>
        <div className="flex items-center justify-between gap-s">
          <span className="text-on-surface-low text-[0.75rem] tabular-nums">
            Step {index + 1} of {steps.length}
          </span>
          <div className="flex shrink-0 items-center gap-1.5">
            {index > 0 && (
              <Button variant="ghost" size="sm" onClick={() => onIndex(index - 1)}>
                <ArrowLeft size={15} /> Back
              </Button>
            )}
            <Button variant="primary" size="sm" onClick={last ? onExit : () => onIndex(index + 1)}>
              {last ? <>Done <Check size={15} /></> : <>Next <ArrowRight size={15} /></>}
            </Button>
          </div>
        </div>
      </motion.div>
    </div>,
    document.body,
  )
}

/** The ring drawn around an anchor: its box plus a little breathing room, CLAMPED to the
 *  viewport.
 *
 *  The clamp is what keeps the outline closed. Full-bleed anchors are ordinary here — the
 *  inbox queue column spans the whole content area — so the padded box runs past the edge
 *  and its right-hand stroke lands off-screen. Measured on the inbox stop before this: a
 *  three-sided ring that read as an unfinished box. Clamping also keeps the four dim bands
 *  non-negative, since they are derived from the same rectangle. */
function ringFor(box: Box): Box {
  const top = Math.max(0, box.top - RING_PAD)
  const left = Math.max(0, box.left - RING_PAD)
  return {
    top,
    left,
    width: Math.min(box.width + RING_PAD * 2, window.innerWidth - left),
    height: Math.min(box.height + RING_PAD * 2, window.innerHeight - top),
  }
}

/** One dim panel — decorative and click-through, so the shield underneath owns the
 *  pointer. Fades in unless the user asked for less motion, in which case it is simply
 *  there. */
function Dim({ style, reduce }: { style: React.CSSProperties; reduce: boolean | null }) {
  return (
    <motion.div aria-hidden className="pointer-events-none fixed bg-canvas/70" style={style}
      initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }}
      transition={reduce ? instant : spring.effects} />
  )
}

/** Where the card sits. Under the ring, then over it, then BESIDE it — and only centred
 *  when there is no ring at all.
 *
 *  The beside case is not a nicety: the nav rail and the settings grid are as tall as the
 *  viewport, so "under, else over, else clamp to the top" put the card ON TOP of the very
 *  thing it was pointing at. Measured on the rail stop before this — the card covered the
 *  wordmark and four of the six rail rows it was describing. */
function cardPosition(ring: Box | null): React.CSSProperties {
  const centred: React.CSSProperties = {
    top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: CARD_WIDTH,
  }
  if (!ring) return centred
  const vw = window.innerWidth
  const vh = window.innerHeight
  const clamp = (v: number, max: number) => Math.min(Math.max(EDGE, v), Math.max(EDGE, max))
  const centredOn = (v: number, span: number, extent: number, limit: number) =>
    clamp(v + span / 2 - extent / 2, limit - extent - EDGE)

  const below = ring.top + ring.height + CARD_GAP
  if (below + CARD_HEIGHT_HINT < vh - EDGE) {
    return { top: below, left: centredOn(ring.left, ring.width, CARD_WIDTH, vw), width: CARD_WIDTH }
  }
  const above = ring.top - CARD_GAP - CARD_HEIGHT_HINT
  if (above > EDGE) {
    return { top: above, left: centredOn(ring.left, ring.width, CARD_WIDTH, vw), width: CARD_WIDTH }
  }
  const y = centredOn(ring.top, ring.height, CARD_HEIGHT_HINT, vh)
  const right = ring.left + ring.width + CARD_GAP
  if (right + CARD_WIDTH < vw - EDGE) return { top: y, left: right, width: CARD_WIDTH }
  const left = ring.left - CARD_GAP - CARD_WIDTH
  if (left > EDGE) return { top: y, left, width: CARD_WIDTH }
  return centred
}
