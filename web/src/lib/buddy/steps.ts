// ── The expanded card's step window (#3893, `docs/design/buddy/surfaces.md`) ────────────────────
//
// The focused work's run as one flow: the step before, dimmed and done; the current step at full
// ink; and the next step, dimmed, when it is known. When a step finishes the window moves up one
// line and the run's length goes to a trail of done marks above it, at most eight and then `+N`,
// so the run stays visible without its history. Never more than three lines, never a log.
//
// Pure, beside the state machine (`machine.ts`), so a recorded run replays through it like the
// other folds: `foldSteps` takes one step event at a time, and `stepsOnInput` turns the machine's
// own phase inputs into those events for every piece of work. A step names its kind (a plan, a
// step, a tool or a result) and, when the work says how far it is, its progress.

import { wordAndLine } from '../runningRows'
import { phaseLabel, type WorkPhase } from '../workPhase'
import type { BuddyInput } from './inputs'

export type StepKind = 'plan' | 'step' | 'tool' | 'result'

export interface Step {
  id: string
  kind: StepKind
  /** What the step does, in the gateway's words. */
  label: string
  /** How far it is, 0 to 1, when the work says; absent when there is no progress to show. */
  progress?: number
  /** A helper batch: one phase per member, each drawn in its own state. */
  helpers?: readonly WorkPhase[]
}

export interface StepWindow {
  /** The step before the current one, done. */
  before: Step | null
  current: Step | null
  /** The next step, when the work knows it (a plan's, a loop's tasks, a workflow's next node). */
  next: Step | null
  /** How many steps of the run have finished: the trail's length. */
  done: number
}

export type StepEvent =
  /** A step starts. The current one, if any, has finished. */
  | { type: 'begin'; step: Step }
  | { type: 'progress'; id: string; progress: number }
  /** The current step finishes with nothing after it yet. */
  | { type: 'finish'; id: string }
  /** What comes next, or that it is not known. */
  | { type: 'next'; step: Step | null }

export const EMPTY_WINDOW: StepWindow = { before: null, current: null, next: null, done: 0 }

/** The most done marks the trail draws before it says `+N`. */
export const TRAIL_MARKS = 8

/** A finished step as the line above the current one draws it: done, with no progress left. */
function finished(step: Step): Step {
  const { progress: _, ...rest } = step
  return rest
}

export function foldSteps(w: StepWindow, e: StepEvent): StepWindow {
  switch (e.type) {
    case 'begin':
      return {
        before: w.current ? finished(w.current) : w.before,
        current: e.step,
        // The next line was this step, or a guess this step overtook: unknown again either way.
        next: null,
        done: w.done + (w.current ? 1 : 0),
      }
    case 'progress':
      return w.current?.id === e.id ? { ...w, current: { ...w.current, progress: e.progress } } : w
    case 'finish':
      return w.current?.id === e.id ? { ...w, before: finished(w.current), current: null, done: w.done + 1 } : w
    case 'next':
      return { ...w, next: e.step }
  }
}

export type LinePlace = 'before' | 'current' | 'next'

/** The window's lines, top to bottom: never more than three. */
export function linesOf(w: StepWindow): { place: LinePlace; step: Step }[] {
  const lines: { place: LinePlace; step: Step }[] = []
  if (w.before) lines.push({ place: 'before', step: w.before })
  if (w.current) lines.push({ place: 'current', step: w.current })
  if (w.next) lines.push({ place: 'next', step: w.next })
  return lines
}

/** The trail above the window: a mark per finished step, up to eight, and how many more. */
export function trailOf(done: number): { marks: number; more: number } {
  const marks = Math.min(Math.max(0, done), TRAIL_MARKS)
  return { marks, more: Math.max(0, done - TRAIL_MARKS) }
}

/** A phase's step kind: planning is a plan, a search or a tool call is a tool, the end a result. */
function kindOf(phase: WorkPhase): StepKind {
  if (phase === 'planning') return 'plan'
  if (phase === 'tool' || phase === 'searching') return 'tool'
  if (phase === 'done') return 'result'
  return 'step'
}

/** Every piece of work's window, by its id. */
export type StepWindows = Readonly<Record<string, StepWindow>>

/** The windows after the machine hears `input`: a start opens the work's first step, each change
 *  of phase or line is its next step, and its end finishes the step it was on. A phase change that
 *  repeats the current step's words is the same step. Anything else leaves them as they are. */
export function stepsOnInput(steps: StepWindows, input: BuddyInput): StepWindows {
  const begin = (id: string, phase: WorkPhase, line: string, helpers?: number): StepWindows => {
    const w = steps[id] ?? EMPTY_WINDOW
    const label = wordAndLine(phaseLabel(phase), line)
    if (w.current?.label === label) return steps
    const step: Step = { id: `${id}:${w.done + (w.current ? 1 : 0) + 1}`, kind: kindOf(phase), label }
    if (phase === 'delegating' && helpers) step.helpers = Array.from({ length: helpers }, () => 'thinking' as const)
    return { ...steps, [id]: foldSteps(w, { type: 'begin', step }) }
  }
  switch (input.type) {
    case 'start':
      return begin(input.work.id, input.work.phase, input.work.line, input.work.helpers)
    case 'phase':
      return steps[input.id] ? begin(input.id, input.phase, input.line) : steps
    case 'finish': {
      const w = steps[input.id]
      return w?.current ? { ...steps, [input.id]: foldSteps(w, { type: 'finish', id: w.current.id }) } : steps
    }
    default:
      return steps
  }
}
