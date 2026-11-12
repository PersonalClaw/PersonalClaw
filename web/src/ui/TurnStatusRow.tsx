import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { matchBinding } from '../app/keymap'
import { prefersReducedMotion } from '../design/motion'
import { WORK_PHASE_LOOKS, phaseLabel, type WorkPhase } from '../lib/workPhase'
import { AgentOrb } from './AgentOrb'
import { Tooltip } from './Tooltip'
import { cx } from './cx'
import { bloomsFor, fadeMs, useHeldEnding, type TurnEnding } from './turnEnding'

// ── The running turn's live status, one bare line at the end of the conversation ───────────────
//
// What a running turn is doing now is the last thing in the conversation: one bare line, with no
// border, background or pill, under the latest message in the turn column, scrolling with the
// thread like the turn it belongs to, shown whenever a turn runs and, once it ends, for as long as it
// takes to show how it ended (#5867; `web/REDESIGN.md`, "Live state sits at the bottom"). It holds,
// in order:
//
// - the agent orb in the turn's own phase (`docs/design/agent-orb.md`);
// - what the turn is doing, in the gateway's own words for the phase (the tool by name), else the
//   phase's word: the row's one polite live region, said once per change;
// - while it works, a word that turns over (`STATUS_WORDS`), decoration only and never said;
// - the time since the turn began;
// - once its runtime has done nothing for a minute, for how long ("No new activity for 1m"), from
//   the gateway's `last_step_at`, while it works and waits on nothing that says why;
// - the tokens the runtime reported for the turn, in and out, and nothing while it has reported
//   none: never an estimate.
//
// Stopping the turn stays in reach without a scroll: the composer's send button is "Stop this turn"
// while a turn runs, a secondary one sits beside Steer or Queue once a draft is typed, and Esc
// stops it too while nothing else has taken the key, which this row listens for.
//
// It shows `SHOW_DELAY_MS` after the turn starts, so a fast reply never flashes it. Under reduced
// motion no word turns over and the orb draws one static frame.
//
// When the turn's last frame says how it ended, the line keeps showing it: the orb takes the
// ending, done (with its one bloom only when the turn completed) or a failure with its ×, and the
// words its line. The clock stops, the turning word goes, Esc no longer presses Stop, and the live
// region empties, since the transcript's narration says how the turn ended. It holds until the orb
// has settled and `MIN_HOLD_MS` more (`ui/turnEnding.ts`), then fades over the Medium length
// (`fadeMs()`) and goes; under reduced motion
// it goes with no fade. A turn that starts meanwhile takes the line back. A turn that ends with no
// ending said goes at once.

/** PersonalClaw's own words for a turn at work, one at a time, in this order. */
export const STATUS_WORDS = [
  'Working', 'Reading', 'Weighing', 'Checking', 'Tracing', 'Sorting', 'Comparing', 'Gathering', 'Shaping', 'Drafting',
] as const

/** How long each word stays. */
export const WORD_MS = 2400
/** How long after the turn starts the row appears. */
export const SHOW_DELAY_MS = 150
/** How long a working turn's runtime does nothing before the row says it has gone quiet. */
export const STALL_AFTER_MS = 60_000
/** The phases that say what they wait on themselves: a retry when, a local model how many ahead. */
const SAYS_ITS_WAIT: readonly WorkPhase[] = ['retrying', 'waiting_model']

export interface TurnUsage { input: number; output: number }

/** The time since the turn began: `12s`, `4m 05s`, `1h 02m`. */
export function elapsedWords(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}

/** How long a turn has been quiet: `1m`, `14m`, `1h 05m`. */
export function quietWords(ms: number): string {
  const m = Math.max(1, Math.floor(ms / 60_000))
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
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

type Stage = 'hidden' | 'shown' | 'ending' | 'leaving'

export function TurnStatusRow({ running, phase, line, startedAt, lastStepAt = null, usage, onStop, stopShortcut = true, onShown, ending = null, className }: {
  /** Whether a turn runs: the row shows a moment after it starts and, once it ends, goes, or holds its `ending` first. */
  running: boolean
  /** The turn's phase, `null` until the gateway has said one (waiting to start). */
  phase: WorkPhase | null
  /** The gateway's line for the phase: the tool by name while one runs. */
  line: string
  /** When the turn began (epoch ms), or `null` for when the row first saw it running. */
  startedAt: number | null
  /** When its runtime last did anything (epoch ms, the gateway's `last_step_at`), or `null` while it
   *  has taken no step: then it is quiet since it began. */
  lastStepAt?: number | null
  /** The tokens the runtime reported for the turn so far, or `null` while it has reported none. */
  usage: TurnUsage | null
  /** Stop the turn: what Esc does while the row shows (the composer's Stop does the same). */
  onStop: () => void
  /** Whether Esc presses Stop while nothing else has taken the key: off where another control
   *  owns Esc. */
  stopShortcut?: boolean
  /** Called when the row appears, so a transcript that follows its end can keep its end in view
   *  above the row's height. */
  onShown?: () => void
  /** How the turn ended, once its last frame said so: the line shows it until its orb has settled
   *  and `MIN_HOLD_MS` more, then fades and goes. */
  ending?: TurnEnding | null
  className?: string
}) {
  const [stage, setStage] = useState<Stage>('hidden')
  const shown = stage !== 'hidden'
  const ticking = running && shown
  const [now, setNow] = useState(() => Date.now())
  const [turn, setTurn] = useState(0)
  const seenAt = useRef<number | null>(null)
  const reduced = useSyncExternalStore(onMotionChange, prefersReducedMotion, () => false)
  const ends = !running && ending !== null
  const { held, onSettled } = useHeldEnding(running ? null : ending, shown)
  const shows = ends ? ending.phase : phase ?? 'queued'
  const works = !ends && WORK_PHASE_LOOKS[shows].family === 'active'
  const stop = useRef(onStop)
  stop.current = onStop

  // Shown a moment after a turn starts, and at once when one starts while the last one's ending
  // still shows. Once it ends, it goes at once, or holds its ending and then fades.
  useEffect(() => {
    if (running) {
      if (stage === 'ending' || stage === 'leaving') { seenAt.current = Date.now(); setStage('shown'); return }
      if (stage === 'shown') return
      seenAt.current ??= Date.now()
      const t = window.setTimeout(() => setStage('shown'), SHOW_DELAY_MS)
      return () => window.clearTimeout(t)
    }
    // The turn is over: the next one counts from when it is seen.
    seenAt.current = null
    if (!ends) { if (stage !== 'hidden') setStage('hidden'); return }
    if (stage === 'shown') { setStage('ending'); return }
    if (stage === 'ending' && !held) { setStage('leaving'); return }
    if (stage === 'leaving') {
      const t = window.setTimeout(() => setStage('hidden'), reduced ? 0 : fadeMs())
      return () => window.clearTimeout(t)
    }
  }, [running, stage, ends, held, reduced])

  const shownCb = useRef(onShown)
  shownCb.current = onShown
  // In the commit that shows it, before the browser paints: from a passive effect, which ran after
  // the paint, the row could appear over the transcript's end for a frame before the transcript
  // made room for it (the history peek's head did the same, #5619's and #5651's web-unit red).
  useLayoutEffect(() => { if (shown) shownCb.current?.() }, [shown])

  // The clock, once a second while the turn runs; it stops where the turn ended.
  useEffect(() => {
    if (!ticking) return
    setNow(Date.now())
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => { window.clearInterval(t); setNow(Date.now()) }
  }, [ticking])

  // The word, while it works and motion is welcome.
  const turns = ticking && works && !reduced
  useEffect(() => {
    if (!turns) return
    const t = window.setInterval(() => setTurn((n) => n + 1), WORD_MS)
    return () => window.clearInterval(t)
  }, [turns])

  // Esc presses Stop, unless a dialog is open, a control has taken the key, or it was typed into a
  // field of its own (a rename): the composer, marked `data-turn-stop-scope`, is the one field it
  // stops from.
  useEffect(() => {
    if (!ticking || !stopShortcut) return
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.repeat || !matchBinding(e, 'stop-turn')) return
      if (document.querySelector('[aria-modal=true]')) return
      if (typesText(e.target) && !(e.target as HTMLElement).closest('[data-turn-stop-scope]')) return
      e.preventDefault()
      stop.current()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [ticking, stopShortcut])

  const words = stateWords(shows, ends ? ending.line : line)
  // The turn's start and its tokens, as they were while it ran: its last frame names no turn, so
  // the chat hands the ending no start and no usage, and the ending keeps the running turn's.
  const kept = useRef<{ began: number | null; usage: TurnUsage | null }>({ began: null, usage: null })
  if (running) kept.current = { began: startedAt ?? seenAt.current ?? now, usage }
  if (!shown || (!running && !ends)) return null
  // What the live region says: the running turn's words. An ending is said by the transcript's
  // narration, so while it holds the region is empty: removing words says nothing, and nothing
  // stale is left for a screen reader to read as still running.
  const said = running ? words : ''
  const began = (running ? startedAt ?? seenAt.current : kept.current.began) ?? now
  const quietFor = now - (lastStepAt ?? began)
  const quiet = works && !SAYS_ITS_WAIT.includes(shows) && quietFor >= STALL_AFTER_MS
  const used = running ? usage : kept.current.usage
  const reported = used && (used.input > 0 || used.output > 0) ? used : null
  return (
    <div data-turn-status="" data-phase={shows} data-turn-stage={stage}
      className={cx('flex min-w-0 items-center gap-s transition-opacity duration-(--duration-medium) ease-out motion-reduce:transition-none', className)}
      style={stage === 'leaving' && !reduced ? { opacity: 0 } : undefined}>
      <AgentOrb phase={shows} size={20} onSettled={onSettled} bloom={bloomsFor(ends ? ending : null)} />
      <span data-turn-state="" data-type="body-s" className="min-w-0 truncate text-on-surface">{words}</span>
      {turns ? (
        <span data-turn-word="" aria-hidden="true" data-type="body-s" className="shrink-0 text-on-surface-var">
          {STATUS_WORDS[turn % STATUS_WORDS.length]}
        </span>
      ) : null}
      <span data-turn-elapsed="" data-type="label-s" className="shrink-0 tabular-nums text-on-surface-var">{elapsedWords(now - began)}</span>
      {quiet ? (
        <span data-turn-quiet="" data-type="label-s" className="shrink-0 tabular-nums text-on-surface-var">
          No new activity for {quietWords(quietFor)}
        </span>
      ) : null}
      {reported ? (
        <>
          {/* The words before the numbers, for a screen reader; the tooltip says the same to a
              pointer, so it adds no description. */}
          <span className="sr-only">Tokens the model reported for this turn:</span>
          <Tooltip text="Tokens the model reported for this turn" describes={false}>{(trigger) => (
            <span {...trigger} data-turn-tokens="" data-type="label-s" className="shrink-0 tabular-nums text-on-surface-var">
              {reported.input.toLocaleString('en-US')} in · {reported.output.toLocaleString('en-US')} out
            </span>
          )}</Tooltip>
        </>
      ) : null}
      {/* The row's one live region: what the turn is doing, said once each time it changes. */}
      <span role="status" aria-live="polite" className="sr-only">{said}</span>
    </div>
  )
}
