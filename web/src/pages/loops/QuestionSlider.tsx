import { useMemo, useReducer } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowLeft, ArrowRight, Check, HelpCircle, Sparkles, PencilLine } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Segmented } from '../../ui/Segmented'
import { Slider } from '../../ui/Slider'
import { Field, TextArea, NumberField } from '../../ui/forms'
import { spring } from '../../design/motion'
import {
  initSliderState,
  sliderReducer,
  isAnswered,
  canAdvance,
  canSubmit,
  answerRecord,
  OTHER_CHOICE,
  type SliderQuestion,
} from './sliderState'

/** The QuestionSlider / ask() stepper (UNIVERSAL-PLANNING UP-R5, WF2UNI-10).
 *
 *  The deep-rigor Round is rendered one question at a time with gated forward navigation and a
 *  single Submit at the end — the fast way through a short interrogation, versus a flat list of
 *  five boxes that reads as a form. Typed kinds (text / choice / slider / boundary) come straight
 *  from `grill_protocol.Question.kind`, and every `choice` question carries the custom-answer
 *  escape hatch the protocol mandates (a closed option set is a claim the planner enumerated the
 *  possibilities; it is wrong often enough that the hatch has to exist).
 *
 *  All navigation/gating logic lives in the pure `questionSlider` reducer (unit-tested there);
 *  this component is the chrome + the typed-control switch over it. */
export function QuestionSlider({ questions, seed, onSubmit, onExit, submitLabel = 'Submit answers' }: {
  questions: SliderQuestion[]
  /** Prior answers (a resumed review) — win over the planner's recommendations. */
  seed?: Record<string, string>
  onSubmit: (answers: Record<string, string>) => void
  /** Back from the FIRST question exits the stepper (returns to the enclosing step) — so the
   *  slider owns the whole walk's navigation and the host needs no competing nav bar. */
  onExit?: () => void
  submitLabel?: string
}) {
  const [state, dispatch] = useReducer(sliderReducer, undefined, () => initSliderState(questions, seed ?? {}))
  const total = questions.length
  const q = questions[Math.min(state.index, total - 1)]
  const answeredCount = useMemo(() => questions.filter((x) => isAnswered(x, state)).length, [questions, state])
  const onLast = state.index === total - 1

  if (!q) return null
  const phased = q.phase != null && q.phaseIndex != null && q.phaseCount != null

  return (
    <div className="flex flex-col gap-l max-w-[640px] mx-auto py-l">
      {/* progress: a dot per question, filled once answered — the "how many left" the walk owes. */}
      <div className="flex items-center justify-between">
        <span className="text-on-surface-low text-[0.75rem]">Question {state.index + 1} of {total}</span>
        <div className="flex items-center gap-1.5" aria-hidden>
          {questions.map((x, i) => (
            <span key={x.id}
              className="size-1.5 rounded-pill transition-colors"
              style={{ background: i === state.index ? 'var(--color-primary)' : isAnswered(x, state) ? 'color-mix(in srgb, var(--color-primary) 45%, transparent)' : 'var(--color-on-surface-low)' }} />
          ))}
        </div>
      </div>

      <AnimatePresence mode="wait">
        <motion.div key={q.id}
          initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }}
          transition={spring.spatialFast} className="flex flex-col gap-m">
          {phased && (
            <span className="self-start inline-flex items-center gap-1.5 rounded-pill px-2.5 h-6 text-[0.75rem]"
              style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)', color: 'var(--color-primary)' }}>
              <Sparkles size={12} /> Phase {q.phaseIndex! + 1} of {q.phaseCount} · {q.phase}
            </span>
          )}
          <div className="flex items-start gap-s">
            <HelpCircle size={20} className="text-info shrink-0 mt-0.5" />
            <h2 data-type="headline-s" className="text-on-surface">{q.prompt}</h2>
          </div>

          <QuestionControl q={q} state={state} dispatch={dispatch} />

          {!q.required && (
            <span className="text-on-surface-low text-[0.75rem]">
              {q.kind === 'boundary'
                ? 'Optional — leave blank if there are no hard limits.'
                : "Optional — skip it and I'll investigate or assume during the run."}
            </span>
          )}
        </motion.div>
      </AnimatePresence>

      {/* Footer: Back / Skip / Next while walking; a single Submit on the last question. */}
      <div className="flex items-center justify-between border-t border-outline-variant/30 pt-m">
        {/* Reachable at the first question rather than natively disabled: walking back with the
            keyboard otherwise destroys focus on arrival: the button being pressed drops out of
            the tab order and focus falls to <body>. */}
        <Button variant="ghost" size="sm"
          onClick={() => state.index === 0 ? onExit?.() : dispatch({ type: 'back' })}
          disabled={state.index === 0 && !onExit}
          disabledReason="This is the first question">
          <ArrowLeft size={15} /> Back
        </Button>
        {onLast ? (
          <Button size="sm" onClick={() => onSubmit(answerRecord(questions, state))} disabled={!canSubmit(questions, state)}
            disabledReason="Answer the required questions first">
            <Check size={15} /> {submitLabel} · {answeredCount}/{total}
          </Button>
        ) : (
          <div className="flex items-center gap-s">
            {!q.required && (
              <Button variant="ghost" size="sm" onClick={() => dispatch({ type: 'next', total })}>Skip</Button>
            )}
            <Button size="sm" onClick={() => dispatch({ type: 'next', total })} disabled={!canAdvance(q, state)}
              disabledReason="This question is required">
              Next <ArrowRight size={15} />
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

/** The typed control for one question — the switch over `kind`. A `choice` carries the
 *  custom-answer escape hatch: picking "Other (describe)" flips to a freeform box, so the closed
 *  option set never silently forces a wrong answer. */
function QuestionControl({ q, state, dispatch }: {
  q: SliderQuestion
  state: ReturnType<typeof initSliderState>
  dispatch: React.Dispatch<Parameters<typeof sliderReducer>[1]>
}) {
  const value = state.answers[q.id] ?? ''
  const setValue = (v: string) => dispatch({ type: 'answer', id: q.id, value: v })

  if (q.kind === 'choice') {
    const custom = !!state.custom[q.id]
    const options = [...(q.choices ?? []).filter(Boolean), OTHER_CHOICE]
    const selected = custom ? OTHER_CHOICE : value
    return (
      <div className="flex flex-col gap-s">
        <Segmented
          ariaLabel={q.prompt}
          value={selected}
          collapse="scroll"
          options={options.map((c) => ({ key: c, label: c === OTHER_CHOICE ? 'Other…' : c }))}
          onChange={(k) => {
            if (k === OTHER_CHOICE) { dispatch({ type: 'toggleCustom', id: q.id, on: true }) }
            else { dispatch({ type: 'toggleCustom', id: q.id, on: false }); setValue(k) }
          }}
        />
        {custom && (
          <Field label="Your answer">
            <TextArea autoFocus value={value} onChange={setValue} rows={2}
              ariaLabel="Your custom answer" placeholder="Describe your answer…" />
          </Field>
        )}
      </div>
    )
  }

  if (q.kind === 'slider') {
    const min = q.min ?? 0
    const max = q.max ?? 10
    const num = Number(value)
    const current = Number.isFinite(num) && value !== '' ? num : min
    return (
      <div className="flex items-center gap-m">
        <div className="flex-1">
          <Slider value={current} min={min} max={max} step={q.step} ariaLabel={q.prompt}
            onChange={(n) => setValue(String(n))} />
        </div>
        <NumberField value={current} min={min} max={max} step={q.step} width="w-20"
          onChange={(n) => setValue(String(n))} ariaLabel={`${q.prompt} — value`} />
      </div>
    )
  }

  // text + boundary both render as a freeform box; a boundary reads as the Stop/never-do prompt.
  return (
    <Field label={q.kind === 'boundary' ? 'Hard limits' : 'Your answer'} right={q.kind === 'boundary' ? <PencilLine size={13} className="text-on-surface-low" /> : undefined}>
      <TextArea autoFocus value={value} onChange={setValue} rows={q.kind === 'boundary' ? 3 : 4}
        ariaLabel={q.prompt}
        placeholder={q.kind === 'boundary' ? 'e.g. never touch prod; don’t email anyone' : 'Type your answer…'} />
    </Field>
  )
}
