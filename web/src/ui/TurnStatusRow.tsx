import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { bindingLabel, matchBinding } from '../app/keymap'
import { prefersReducedMotion } from '../design/motion'
import { WORK_PHASE_LOOKS, phaseLabel, type WorkPhase } from '../lib/workPhase'
import { AgentOrb } from './AgentOrb'
import { Button } from './Button'
import { cx } from './cx'

// ── The running turn's live status, in one row at the conversation's bottom ─────────────────────
//
// A turn can run to hundreds of rows while the transcript follows its end, so what the turn is
// doing now lives where the reader is: one row pinned just above the composer, outside the
// transcript's scroll, shown whenever a turn runs and gone when it ends (`web/REDESIGN.md`, "Live
// state sits at the bottom"). It holds, in order:
//
// - the agent orb in the turn's own phase (`docs/design/agent-orb.md`);
// - what the turn is doing, in the gateway's own words for the phase (the tool by name), else the
//   phase's word: the row's one polite live region, said once per change;
// - while it works, a word that turns over (`STATUS_WORDS`), decoration only and never said;
// - the time since the turn began;
// - the tokens the runtime reported for the turn, in and out, and nothing while it has reported
//   none: never an estimate;
// - Stop, which Esc presses too while nothing else has taken the key.
//
// It shows `SHOW_DELAY_MS` after the turn starts, so a fast reply never flashes it. Under reduced
// motion no word turns over and the orb draws one static frame.

/** PersonalClaw's own words for a turn at work, one at a time, in this order. */
export const STATUS_WORDS = [
  'Working', 'Reading', 'Weighing', 'Checking', 'Tracing', 'Sorting', 'Comparing', 'Gathering', 'Shaping', 'Drafting',
] as const

/** How long each word stays. */
export const WORD_MS = 2400
/** How long after the turn starts the row appears. */
export const SHOW_DELAY_MS = 150

export interface TurnUsage { input: number; output: number }

/** The time since the turn began: `12s`, `4m 05s`, `1h 02m`. */
export function elapsedWords(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}

/** What the turn is doing, in words: the gateway's line, else the phase's word. */
function stateWords(phase: WorkPhase, line: string): string {
  const said = line.trim()
  if (said) return said
  return phase === 'tool' ? 'Running a tool' : phaseLabel(phase)
}

/** Whether a key pressed on *el* is typed into a field. */
function typesText(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  if (el.isContentEditable || el.closest('[contenteditable="true"],[role="textbox"]')) return true
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
}

const onMotionChange = (cb: () => void) => {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
  const q = window.matchMedia('(prefers-reduced-motion: reduce)')
  q.addEventListener?.('change', cb)
  return () => q.removeEventListener?.('change', cb)
}

export function TurnStatusRow({ running, phase, line, startedAt, usage, onStop, stopShortcut = true, onShown, className }: {
  /** Whether a turn runs: the row shows a moment after it starts and goes when it ends. */
  running: boolean
  /** The turn's phase, `null` until the gateway has said one (waiting to start). */
  phase: WorkPhase | null
  /** The gateway's line for the phase: the tool by name while one runs. */
  line: string
  /** When the turn began (epoch ms), or `null` for when the row first saw it running. */
  startedAt: number | null
  /** The tokens the runtime reported for the turn so far, or `null` while it has reported none. */
  usage: TurnUsage | null
  /** Stop the turn. */
  onStop: () => void
  /** Whether Esc presses Stop while nothing else has taken the key: off where another control
   *  owns Esc. */
  stopShortcut?: boolean
  /** Called when the row appears, so a transcript that follows its end can keep its end in view
   *  above the row's height. */
  onShown?: () => void
  className?: string
}) {
  const [shown, setShown] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const [turn, setTurn] = useState(0)
  const seenAt = useRef<number | null>(null)
  const reduced = useSyncExternalStore(onMotionChange, prefersReducedMotion, () => false)
  const shows = phase ?? 'queued'
  const works = WORK_PHASE_LOOKS[shows].family === 'active'
  const stop = useRef(onStop)
  stop.current = onStop

  useEffect(() => {
    if (!running) { setShown(false); seenAt.current = null; return }
    seenAt.current ??= Date.now()
    const t = window.setTimeout(() => setShown(true), SHOW_DELAY_MS)
    return () => window.clearTimeout(t)
  }, [running])

  const shownCb = useRef(onShown)
  shownCb.current = onShown
  // In the commit that shows it, before the browser paints: from a passive effect, which ran after
  // the paint, the row could appear over the transcript's end for a frame before the transcript
  // made room for it (the history peek's head did the same, #5619's and #5651's web-unit red).
  useLayoutEffect(() => { if (shown) shownCb.current?.() }, [shown])

  // The clock, once a second while it shows.
  useEffect(() => {
    if (!shown) return
    setNow(Date.now())
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [shown])

  // The word, while it works and motion is welcome.
  const turns = shown && works && !reduced
  useEffect(() => {
    if (!turns) return
    const t = window.setInterval(() => setTurn((n) => n + 1), WORD_MS)
    return () => window.clearInterval(t)
  }, [turns])

  // Esc presses Stop, unless a dialog is open, a control has taken the key, or it was typed into a
  // field of its own (a rename): the composer, marked `data-turn-stop-scope`, is the one field it
  // stops from.
  useEffect(() => {
    if (!shown || !stopShortcut) return
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.repeat || !matchBinding(e, 'stop-turn')) return
      if (document.querySelector('[aria-modal=true]')) return
      if (typesText(e.target) && !(e.target as HTMLElement).closest('[data-turn-stop-scope]')) return
      e.preventDefault()
      stop.current()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [shown, stopShortcut])

  if (!running || !shown) return null
  const said = stateWords(shows, line)
  const began = startedAt ?? seenAt.current ?? now
  const reported = usage && (usage.input > 0 || usage.output > 0) ? usage : null
  return (
    <div data-turn-status="" data-phase={shows}
      className={cx('flex min-w-0 items-center gap-s rounded-pill border border-outline-variant bg-surface px-m py-xs', className)}>
      <AgentOrb phase={shows} size={20} />
      <span data-turn-state="" data-type="body-s" className="min-w-0 truncate text-on-surface">{said}</span>
      {turns ? (
        <span data-turn-word="" aria-hidden="true" data-type="body-s" className="shrink-0 text-on-surface-var">
          {STATUS_WORDS[turn % STATUS_WORDS.length]}
        </span>
      ) : null}
      <span data-turn-elapsed="" data-type="label-s" className="shrink-0 tabular-nums text-on-surface-var">{elapsedWords(now - began)}</span>
      {reported ? (
        <span data-turn-tokens="" data-type="label-s" title="Tokens the model reported for this turn" className="shrink-0 tabular-nums text-on-surface-var">
          {reported.input.toLocaleString('en-US')} in · {reported.output.toLocaleString('en-US')} out
        </span>
      ) : null}
      <span className="ml-auto shrink-0">
        <Button variant="secondary" size="sm" ariaLabel="Stop this turn" title={stopShortcut ? `Stop this turn (${bindingLabel('stop-turn')})` : 'Stop this turn'}
          onClick={onStop} className="max-md:min-h-11 max-md:min-w-11">
          Stop{stopShortcut ? <kbd aria-hidden="true" data-type="label-s" className="ml-xs text-on-surface-var">{bindingLabel('stop-turn')}</kbd> : null}
        </Button>
      </span>
      {/* The row's one live region: what the turn is doing, said once each time it changes. */}
      <span role="status" aria-live="polite" className="sr-only">{said}</span>
    </div>
  )
}
