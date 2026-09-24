/** The QuestionSlider stepper's PURE state machine (UNIVERSAL-PLANNING UP-R5, WF2UNI-10).
 *
 *  The deep-rigor Round is a handful of typed questions the planner needs answered before it
 *  emits a spec. Rendering them one-at-a-time with gated forward navigation and a single Submit
 *  is a UI concern; deciding WHICH question is answerable, when Submit unlocks, and what typed
 *  answer record the walk produces is not — it is logic, and logic that lives inside a component
 *  can only be checked by driving a browser. So it lives here, as a reducer + predicates the
 *  widget renders on top of, and `questionSlider.test.ts` pins the advance/back/custom-answer/
 *  submit-gate behaviour without a DOM.
 *
 *  The typed kinds mirror `workflows/grill_protocol.py::Question.kind` (text | choice | slider |
 *  boundary) so the same round the backend builds renders here with no planner-specific shim —
 *  which is the whole point of the grill protocol emitting the engine's own typed shape. */

/** The four question kinds the deep-rigor Round emits. Kept in lockstep with
 *  `grill_protocol.Question.kind`: a `choice` is a closed option set, a `slider` a bounded
 *  number, a `boundary` the unconditional "what must this NOT do" (never required), and `text`
 *  the freeform default. */
export type SliderQuestionKind = 'text' | 'choice' | 'slider' | 'boundary'

/** The escape-hatch option appended to every `choice` question. Mirrors
 *  `grill_protocol.OTHER` — a closed option set is a claim the planner enumerated the
 *  possibilities, and it is wrong often enough that removing the hatch would silently force a
 *  wrong answer rather than let the user surface the missing one. */
export const OTHER_CHOICE = 'Other (describe)'

export interface SliderQuestion {
  id: string
  prompt: string
  kind: SliderQuestionKind
  /** `choice` options (the escape hatch is appended by the widget, never stored here). */
  choices?: string[]
  /** The planner's recommended answer — seeded as the initial value so accepting it is one
   *  click, not a retype (the grill's whole speed story). */
  recommended?: string
  /** Load-bearing: forward navigation is gated on it and Submit stays locked until it is
   *  answered. A boundary question is never required. */
  required?: boolean
  /** `slider` bounds (rendered as a bounded numeric field). */
  min?: number
  max?: number
  step?: number
  /** Phase ribbon (guided decomposition): "Phase N of M · <title>". Absent on a flat walk. */
  phase?: string
  phaseIndex?: number
  phaseCount?: number
}

export interface SliderState {
  /** The question currently on screen (one-at-a-time). */
  index: number
  /** Answer text per question id. Seeded from `recommended` so a recommendation shows filled. */
  answers: Record<string, string>
  /** Questions where the user chose the custom-answer escape hatch (a `choice` question whose
   *  answer is now freeform rather than one of the enumerated options). */
  custom: Record<string, boolean>
}

export type SliderAction =
  | { type: 'answer'; id: string; value: string }
  | { type: 'toggleCustom'; id: string; on: boolean }
  | { type: 'next'; total: number }
  | { type: 'back' }
  | { type: 'goto'; index: number; total: number }

/** Seed the walk. `recommended` fills the answer so a load-bearing question with a
 *  recommendation is answered-by-default (accept, don't retype); a prior answer store (a
 *  resumed review) wins over the recommendation. A choice answer that is not one of the
 *  enumerated options starts in custom mode, so a resumed freeform answer keeps its box open. */
export function initSliderState(
  questions: SliderQuestion[],
  seed: Record<string, string> = {},
): SliderState {
  const answers: Record<string, string> = {}
  const custom: Record<string, boolean> = {}
  for (const q of questions) {
    const prior = seed[q.id]
    const value = prior != null && prior !== '' ? prior : (q.recommended ?? '')
    if (value) answers[q.id] = value
    if (q.kind === 'choice' && value && !(q.choices ?? []).includes(value)) custom[q.id] = true
  }
  return { index: 0, answers, custom }
}

export function sliderReducer(state: SliderState, action: SliderAction): SliderState {
  switch (action.type) {
    case 'answer':
      return { ...state, answers: { ...state.answers, [action.id]: action.value } }
    case 'toggleCustom': {
      // Leaving custom mode clears the freeform text so a stale "Other" answer can't linger as
      // the stored value once the user goes back to picking an enumerated option.
      const answers = action.on ? state.answers : { ...state.answers, [action.id]: '' }
      return { ...state, answers, custom: { ...state.custom, [action.id]: action.on } }
    }
    case 'next':
      return { ...state, index: Math.min(action.total - 1, state.index + 1) }
    case 'back':
      return { ...state, index: Math.max(0, state.index - 1) }
    case 'goto':
      return { ...state, index: Math.max(0, Math.min(action.total - 1, action.index)) }
  }
}

/** Whether a question has a usable answer. Empty/whitespace is unanswered; a boundary question
 *  with no answer is still "answered enough" because it is never load-bearing. */
export function isAnswered(q: SliderQuestion, state: SliderState): boolean {
  return (state.answers[q.id] ?? '').trim().length > 0
}

/** Forward navigation is GATED on the current question: a required (load-bearing) question must
 *  be answered before the walk advances past it — advancing over it would hand the planner a
 *  guess nobody made. Non-required questions (incl. every boundary) are skippable. */
export function canAdvance(q: SliderQuestion, state: SliderState): boolean {
  return !q.required || isAnswered(q, state)
}

/** Submit unlocks only once every REQUIRED question is answered — the single gate at the end of
 *  the walk. A walk with no required questions is submittable immediately. */
export function canSubmit(questions: SliderQuestion[], state: SliderState): boolean {
  return questions.every((q) => !q.required || isAnswered(q, state))
}

/** The typed answer record the walk returns — id → trimmed answer, dropping the empties so a
 *  skipped question is absent rather than a stored "". This is what parameterizes the plan. */
export function answerRecord(
  questions: SliderQuestion[],
  state: SliderState,
): Record<string, string> {
  const out: Record<string, string> = {}
  for (const q of questions) {
    const v = (state.answers[q.id] ?? '').trim()
    if (v) out[q.id] = v
  }
  return out
}
