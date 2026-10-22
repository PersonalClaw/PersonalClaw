// ── A day in the life, scripted (#3892) ─────────────────────────────────────────────────────────
//
// The playground's controls on a timetable: work starts and steps advance, an approval, a question,
// a failure, finished work, listening, absence and return, a lost connection, a restart and quiet
// hours. It runs on the playground's clock, so every timer the machine starts fires at its length
// as the day passes. The playground plays it at a chosen speed, paused or a step at a time; the
// acceptance run (#3902) and the playground's tests step it the same way. A file drop is later
// work (#3895), so this day has none.

import { advance, startPlayground, use, type Playground } from './playground'

export interface DayStep {
  /** When it happens, in ms from the day's start. */
  at: number
  /** The playground control that is used. */
  control: string
  /** What happens, in the words the playground shows. */
  note: string
}

const s = (seconds: number) => seconds * 1000

export const SCRIPTED_DAY: readonly DayStep[] = [
  { at: s(1), control: 'start', note: 'Release notes starts' },
  { at: s(4), control: 'phase', note: 'It searches' },
  { at: s(8), control: 'start', note: 'Nightly digest starts beside it' },
  { at: s(12), control: 'approval', note: 'Release notes asks to run git push' },
  { at: s(18), control: 'allow', note: 'The owner allows it' },
  { at: s(22), control: 'other', note: 'Nightly digest moves on' },
  { at: s(26), control: 'question', note: 'Release notes asks which branch' },
  { at: s(32), control: 'reply', note: 'The owner answers' },
  { at: s(40), control: 'done', note: 'Release notes finishes' },
  { at: s(50), control: 'failed', note: 'Nightly digest fails' },
  { at: s(56), control: 'withdrawn', note: 'The failure is cleared' },
  { at: s(60), control: 'listen', note: 'The owner holds push-to-talk' },
  { at: s(64), control: 'unlisten', note: 'and lets go' },
  { at: s(70), control: 'start', note: 'Tidy downloads starts' },
  { at: s(75), control: 'away', note: 'The owner steps away' },
  { at: s(90), control: 'done', note: 'Tidy downloads finishes while they are away' },
  { at: s(120), control: 'return', note: 'The owner comes back to a summary' },
  { at: s(130), control: 'start', note: 'Inbox sweep starts' },
  { at: s(134), control: 'lost', note: 'The connection drops' },
  { at: s(142), control: 'back', note: 'and comes back' },
  { at: s(148), control: 'restarting', note: 'The gateway restarts' },
  { at: s(154), control: 'back', note: 'and is back' },
  { at: s(158), control: 'quietOn', note: 'Quiet hours start' },
  { at: s(162), control: 'approval', note: 'An approval arrives, quietly' },
  { at: s(168), control: 'deny', note: 'The owner denies it' },
  { at: s(172), control: 'quietOff', note: 'Quiet hours end' },
  { at: s(178), control: 'done', note: 'Inbox sweep finishes' },
]

/** How long the day runs: its last step, and the hold after it. */
export const DAY_LENGTH = SCRIPTED_DAY[SCRIPTED_DAY.length - 1].at + s(10)

/** The day played until `until` (ms) from its start: every step due by then used at its time, the
 *  timers between them firing as they fall due. */
export function dayAt(until: number, from: Playground = startPlayground(), done = 0): { pg: Playground; done: number } {
  let pg = from
  let i = done
  for (; i < SCRIPTED_DAY.length && SCRIPTED_DAY[i].at <= until; i++) {
    const step = SCRIPTED_DAY[i]
    pg = use(advance(pg, Math.max(0, step.at - pg.now)), step.control)
  }
  return { pg: advance(pg, Math.max(0, until - pg.now)), done: i }
}
