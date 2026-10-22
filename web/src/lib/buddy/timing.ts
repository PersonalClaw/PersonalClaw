// ── The buddy's timings (`docs/design/motion.md` §9, `docs/design/buddy/state-machine.md`) ──────
//
// Every duration the buddy's state machine waits or holds on is one of these names, never a
// literal: the machine asks its host for a timer by name, and the host reads its length here.
// `machine.timing.test.ts` scans the module for a numeric duration outside this file.
//
// Reduced motion changes how the buddy moves, never when it acts: a wait keeps its length, and
// only `beat` changes what it bounds, every exit being instant so the truth shows at once.

/** The timers the state machine starts and cancels by name. */
export const TIMER_NAMES = [
  'beat', 'hoverExpand', 'peekLeave', 'collapse', 'collapseCountdown', 'finishedHold', 'sleep',
  'offlineGrace', 'alertGap', 'showDelay', 'minHold',
] as const

export type TimerName = (typeof TIMER_NAMES)[number]

/** Every timing token's length in milliseconds, as motion.md §9 sets it. `absence` is the shell's
 *  (it compares the system's idle time), and `badgeDwell` the renderer's; both are named here so no
 *  part of the buddy writes one as a number. */
export const TIMINGS: Readonly<Record<TimerName | 'absence' | 'badgeDwell', number>> = {
  beat: 240,
  hoverExpand: 650,
  peekLeave: 400,
  collapse: 60_000,
  collapseCountdown: 10_000,
  finishedHold: 5_000,
  absence: 180_000,
  sleep: 600_000,
  offlineGrace: 5_000,
  alertGap: 400,
  showDelay: 150,
  minHold: 300,
  badgeDwell: 450,
}

/** How long a timer runs under reduced motion: every wait the same, since reduced motion changes
 *  how the buddy moves and never when it acts. */
export function timingFor(name: TimerName | 'absence' | 'badgeDwell', _reducedMotion: boolean): number {
  return TIMINGS[name]
}

/** The seconds between two of the machine's clock readings (ms), as a line says a duration. */
export function secondsBetween(from: number, to: number): number {
  return (to - from) / 1000
}
