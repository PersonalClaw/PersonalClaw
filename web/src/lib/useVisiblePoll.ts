import { useEffect, useRef } from 'react'

// ── Idle back-off ────────────────────────────────────────────────────────────────────────────
//
// Measured (day 8): ONE idle Home tab made 428 requests in 3 minutes — eleven pollers, each
// correct on its own and none of them aware that nobody was looking — and every request cost the
// gateway a security-log row. Pausing while HIDDEN was already here; pausing while nobody is
// USING the tab was not. So an interval stretches once the tab has had no input for a while, and
// the first input after that catches up at once, so a returning user never reads stale state.

/** No input for this long and the tab counts as idle. */
export const IDLE_AFTER_MS = 30_000
/** …and after this long, as long idle. */
export const LONG_IDLE_AFTER_MS = 5 * 60_000
const IDLE_FACTOR = 4
const LONG_IDLE_FACTOR = 10
/** The back-off never stretches a poll past this (a poll already slower keeps its own cadence). */
export const MAX_IDLE_INTERVAL_MS = 5 * 60_000

/** The interval a poll uses when the tab has had no input for *idleForMs*. */
export function idleInterval(ms: number, idleForMs: number): number {
  if (idleForMs < IDLE_AFTER_MS) return ms
  const factor = idleForMs < LONG_IDLE_AFTER_MS ? IDLE_FACTOR : LONG_IDLE_FACTOR
  return Math.max(ms, Math.min(ms * factor, MAX_IDLE_INTERVAL_MS))
}

// ONE input tracker for every poll on the page: the time of the last input, and the polls to
// wake when input arrives after an idle stretch.
let lastInput = Date.now()
const wakers = new Set<() => void>()
let tracking = false

const INPUT_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart', 'focus'] as const

function trackInput(): void {
  if (tracking || typeof window === 'undefined') return
  tracking = true
  const onInput = () => {
    const wasIdle = Date.now() - lastInput >= IDLE_AFTER_MS
    lastInput = Date.now()
    if (wasIdle) for (const wake of [...wakers]) wake()
  }
  for (const type of INPUT_EVENTS) window.addEventListener(type, onInput, { capture: true, passive: true })
}

/** Test seam: treat the tab as having just had input (resets idleness for a fresh test). */
export function noteInputForTests(at: number = Date.now()): void {
  lastInput = at
}

export interface VisiblePollOptions {
  /** Stretch the interval while the tab is idle (default). Off only for a LIVE MIRROR the user
   *  watches without touching — a desktop view — where the cadence is the product. */
  idleBackoff?: boolean
  /** Run once on mount (default). Off for a caller that already loads everything on mount, so
   *  the first read is not paid twice. */
  immediate?: boolean
}

/** Run `fn` on mount, then every `ms` — but PAUSE while the tab is hidden, fire once as soon as
 *  it is visible again, and BACK OFF while nobody is using it (see the header). `fn` is held in a
 *  ref so the timer is not torn down on every render when an inline closure is passed.
 *  Pass `ms = null` to disable polling entirely (e.g. only poll while running). */
export function useVisiblePoll(fn: () => void, ms: number | null, opts: VisiblePollOptions = {}) {
  const fnRef = useRef(fn)
  fnRef.current = fn
  const idleBackoff = opts.idleBackoff ?? true
  const immediate = opts.immediate ?? true

  useEffect(() => {
    if (ms === null) return  // polling disabled
    if (idleBackoff) trackInput()
    let timer: number | undefined
    let lastRun = Date.now()
    const clear = () => { if (timer !== undefined) { clearTimeout(timer); timer = undefined } }
    const run = () => { lastRun = Date.now(); fnRef.current() }
    const schedule = () => {
      clear()
      if (document.hidden) return
      const interval = idleBackoff ? idleInterval(ms, Date.now() - lastInput) : ms
      timer = window.setTimeout(tick, Math.max(0, lastRun + interval - Date.now()))
    }
    function tick() {
      if (!document.hidden) run()
      schedule()
    }
    const onVisibility = () => {
      if (document.hidden) { clear(); return }
      run(); schedule()  // catch up immediately, then resume
    }
    // Input after an idle stretch: a poll that is due at the ACTIVE cadence runs now.
    const onWake = () => {
      if (Date.now() - lastRun >= ms) run()
      schedule()
    }
    if (immediate) run()
    schedule()
    document.addEventListener('visibilitychange', onVisibility)
    if (idleBackoff) wakers.add(onWake)
    return () => {
      clear()
      document.removeEventListener('visibilitychange', onVisibility)
      wakers.delete(onWake)
    }
  }, [ms, idleBackoff, immediate])
}
