import { useId, useState } from 'react'
import { motion } from 'framer-motion'
import { Check, Circle, CircleDot, MessageCircleQuestion, Square, SquareCheck } from 'lucide-react'
import { messageEnter } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { Button } from '../../ui/Button'
import { TextInput } from '../../ui/forms'
import { ApiError, type AgentQuestion, type QuestionAnswer } from '../../lib/api'
import type { QuestionSegment } from './chatTypes'

/** What answering sends: her answers (one per question), or her Skip. */
export type QuestionReply = { answers: QuestionAnswer[] } | { skip: true }

/** Why Send is not ready, said on the button itself so a keyboard user hears it. */
const NOT_READY = 'Choose an option or write an answer for each question first.'

/** An agent's question to its owner, in the chat that asked it.
 *
 *  The agent's call is waiting on it, so the card says what it needs and gives every way to
 *  answer: the options (one, or several where the question takes several), her own words where the
 *  tool takes them, Send, and Skip, which lets the agent go on without an answer. Nothing is chosen
 *  for her and nothing is sent by arriving, by focus or by Enter in her own-words box: choosing an
 *  option is a press on it, and sending is a press on Send. Each option is its own control, so Tab
 *  walks them and Space or Enter chooses one.
 *
 *  Once it ends the card says how: what she answered, that she skipped it, or why it was withdrawn
 *  (her Stop, nobody answering in time). A question whose tool PersonalClaw cannot send an answer
 *  to shows its questions and says so (`note`), with nothing to press. */
export function QuestionCard({ seg, onAnswer }: {
  seg: QuestionSegment
  onAnswer: (id: string, reply: QuestionReply) => Promise<unknown>
}) {
  const titleId = useId()
  const [picked, setPicked] = useState<number[][]>(() => seg.questions.map(() => []))
  const [own, setOwn] = useState<string[]>(() => seg.questions.map(() => ''))
  const [sending, setSending] = useState<'' | 'answer' | 'skip'>('')
  const [failed, setFailed] = useState('')
  // The gateway said it no longer waits (answered elsewhere, or withdrawn): its sentence, verbs gone.
  const [closed, setClosed] = useState('')

  if (!seg.answerable) return <Shell titleId={titleId} title="The agent asked you this" askedBy={seg.askedBy}>
    {seg.questions.map((q, i) => <QuestionText key={i} q={q} id={`${titleId}-q${i}`} />)}
    {seg.note && <p data-type="caption" className="mt-s text-on-surface-low">{seg.note}</p>}
  </Shell>

  if (seg.outcome) return <Ended seg={seg} />

  // Nothing left to answer: the questions, and the gateway's sentence saying why, with no verbs.
  if (closed) return <Shell titleId={titleId} title="Question for you" askedBy={seg.askedBy}>
    {seg.questions.map((q, i) => <QuestionText key={i} q={q} id={`${titleId}-q${i}`} />)}
    <p role="status" data-type="caption" className="mt-s text-on-surface-low">{closed}</p>
  </Shell>

  const ready = seg.questions.every((_, i) => picked[i].length > 0 || own[i].trim() !== '')
  // While her answer is on its way the choice holds still; Send and Skip say they are busy.
  const toggle = (qi: number, oi: number) => sending ? undefined : setPicked((prev) => prev.map((sel, i) => {
    if (i !== qi) return sel
    if (!seg.questions[qi].multiSelect) return sel.includes(oi) ? [] : [oi]
    return sel.includes(oi) ? sel.filter((x) => x !== oi) : [...sel, oi].sort((a, b) => a - b)
  }))
  const send = (reply: QuestionReply, which: 'answer' | 'skip') => {
    setSending(which); setFailed('')
    onAnswer(seg.id, reply)
      .catch((e: unknown) => {
        // Nothing left to answer: say what happened and take the verbs away, since a retry
        // would change nothing. Any other failure keeps them, saying why it was not sent.
        if (e instanceof ApiError && (e.code === 'question_answered' || e.code === 'question_ended')) setClosed(e.message)
        else setFailed(`Your answer was not sent: ${e instanceof Error && e.message ? e.message : String(e)}`)
      })
      .finally(() => setSending(''))
  }

  return (
    <Shell titleId={titleId} title="Question for you" askedBy={seg.askedBy} alert>
      {seg.questions.map((q, qi) => {
        const qid = `${titleId}-q${qi}`
        return (
          <div key={qi} className="mt-s first:mt-0">
            <QuestionText q={q} id={qid} />
            <div role={q.multiSelect ? 'group' : 'radiogroup'} aria-labelledby={qid} className="mt-xs flex flex-col gap-xs">
              {q.options.map((o, oi) => (
                <OptionRow key={oi} label={o.label} description={o.description} multi={q.multiSelect}
                  checked={picked[qi].includes(oi)} onToggle={() => toggle(qi, oi)} />
              ))}
            </div>
            {q.free_text && (
              <div className="mt-xs">
                <TextInput size="sm" value={own[qi]} maxLength={2000}
                  placeholder="Or answer in your own words" ariaLabel={`Your own answer to: ${q.question}`}
                  onChange={(v) => setOwn((prev) => prev.map((t, i) => (i === qi ? v : t)))} />
              </div>
            )}
          </div>
        )
      })}
      <div className="mt-s flex flex-wrap items-center gap-s">
          <Button size="sm" variant="primary" loading={sending === 'answer'}
            disabled={!ready || !!sending} disabledReason={!ready ? NOT_READY : undefined}
            onClick={() => send({ answers: seg.questions.map((_, i) => ({ selected: picked[i], other: own[i].trim() })) }, 'answer')}>
            <Check size={14} aria-hidden /> Send answer
          </Button>
          <Button size="sm" variant="ghost" loading={sending === 'skip'} disabled={!!sending}
            ariaLabel="Skip these questions — the agent goes on without your answer"
            onClick={() => send({ skip: true }, 'skip')}>
            Skip
          </Button>
        </div>
      {failed && <p role="alert" data-type="caption" className="mt-xs text-danger">{failed}</p>}
    </Shell>
  )
}

function Shell({ titleId, title, askedBy, alert = false, children }: {
  titleId: string
  title: string
  askedBy: string
  alert?: boolean
  children: React.ReactNode
}) {
  return (
    <motion.div variants={messageEnter} initial="initial" animate="animate"
      role="group" aria-labelledby={titleId}
      className="my-xs overflow-hidden rounded-md border border-warn/40 bg-warn/10 px-m py-s">
      <div className="flex items-start gap-s">
        <MessageCircleQuestion size={15} className="mt-xs shrink-0 text-warn" aria-hidden />
        <div className="min-w-0 flex-1">
          {/* An alert on arrival, as a permission prompt's is: the agent is halted until she answers. */}
          <div id={titleId} role={alert ? 'alert' : undefined} data-type="label-s" className="text-on-surface" style={fvs(500)}>{title}</div>
          {askedBy && <p data-type="caption" className="text-on-surface-low">From {askedBy}</p>}
          <div className="mt-s">{children}</div>
        </div>
      </div>
    </motion.div>
  )
}

function QuestionText({ q, id }: { q: AgentQuestion; id: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-s">
      {q.header && <span data-type="caption" className="rounded-pill bg-surface-high px-s text-on-surface-var">{q.header}</span>}
      <p id={id} data-type="body-s" className="min-w-0 text-on-surface">{q.question}</p>
    </div>
  )
}

/** One option: a press on it chooses it (or, where several may be chosen, toggles it). */
function OptionRow({ label, description, multi, checked, onToggle }: {
  label: string
  description: string
  multi: boolean
  checked: boolean
  onToggle: () => void
}) {
  const Mark = multi ? (checked ? SquareCheck : Square) : (checked ? CircleDot : Circle)
  return (
    <button type="button" role={multi ? 'checkbox' : 'radio'} aria-checked={checked}
      onClick={onToggle}
      className={`flex w-full items-start gap-s rounded-md border px-s py-xs text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${checked ? 'border-primary bg-surface-high' : 'border-outline-variant/40 hover:bg-surface-high'}`}>
      <Mark size={14} className={`mt-xs shrink-0 ${checked ? 'text-primary' : 'text-on-surface-low'}`} aria-hidden />
      <span className="min-w-0">
        <span data-type="label-s" className="block text-on-surface">{label}</span>
        {description && <span data-type="caption" className="block text-on-surface-low">{description}</span>}
      </span>
    </button>
  )
}

/** How the question ended, in the words every surface uses. */
function Ended({ seg }: { seg: QuestionSegment }) {
  if (seg.outcome === 'answered') {
    return (
      <div data-type="caption" className="my-xs flex items-start gap-xs text-ok">
        <Check size={13} className="mt-xs shrink-0" aria-hidden />
        <div className="min-w-0">
          {seg.questions.map((q, i) => {
            const a = seg.answers?.[i]
            const chose = (a?.selected ?? []).map((j) => q.options[j]?.label).filter(Boolean).join(', ')
            const said = a?.other ? `“${a.other}”` : ''
            return <p key={i}>You answered “{q.header || q.question}”: {[chose, said].filter(Boolean).join('; ') || '—'}</p>
          })}
        </div>
      </div>
    )
  }
  const said = seg.outcome === 'skipped'
    ? 'You skipped this question; the agent went on without your answer.'
    : seg.outcome === 'expired' || seg.outcome === 'cancelled'
      ? `The question was withdrawn: ${seg.ended || 'the agent stopped waiting for an answer'}.`
      : `The question ended (${seg.outcome}).`
  return (
    <p data-type="caption" className="my-xs text-on-surface-low">
      {seg.questions[0]?.question ? `“${seg.questions[0].question}” — ` : ''}{said}
    </p>
  )
}
