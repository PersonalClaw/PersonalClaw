/** An agent's question to its owner on the chat's segments (`owner_questions` on the gateway).
 *
 *  Three ways a card reaches a turn, and they must agree: the live `question_card` frame (opened)
 *  and `question_resolved` (settled); a page opened while one waits, which grafts the session's
 *  `pending_questions`; and a reload, where `hydrateTurns` reads how the card ended off the asking
 *  call's row (`meta.question`). All three decode through `questionSegmentOf`, which never casts:
 *  a frame or a row another build wrote may carry a shape this one cannot read, and that is no
 *  card at all rather than a card claiming what it cannot show.
 *
 *  Pure over the lists it is given, so the page applies them inside its state updaters. */
import type { AgentQuestion, QuestionAnswer } from '../../lib/api'
import type { ChatTurn, QuestionSegment, Segment } from './chatTypes'

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function questionOf(raw: unknown): AgentQuestion | null {
  if (!raw || typeof raw !== 'object') return null
  const q = raw as Record<string, unknown>
  const text = str(q.question)
  const options = Array.isArray(q.options)
    ? q.options.flatMap((o) => {
      if (!o || typeof o !== 'object') return []
      const label = str((o as Record<string, unknown>).label)
      return label ? [{ label, description: str((o as Record<string, unknown>).description) }] : []
    })
    : []
  if (!text || options.length === 0) return null
  return { question: text, header: str(q.header), multiSelect: q.multiSelect === true, free_text: q.free_text === true, options }
}

function answersOf(raw: unknown): QuestionAnswer[] | undefined {
  if (!Array.isArray(raw)) return undefined
  return raw.map((a) => {
    const rec = a && typeof a === 'object' ? (a as Record<string, unknown>) : {}
    const selected = Array.isArray(rec.selected) ? rec.selected.filter((i): i is number => Number.isInteger(i)) : []
    return { selected, other: str(rec.other) }
  })
}

/** A card's segment, from a `question_card` frame, a `pending_questions` entry, or the record a
 *  call's row keeps (`meta.question`, whose call is `toolCallId`). Null for anything unreadable. */
export function questionSegmentOf(raw: unknown, toolCallId = ''): QuestionSegment | null {
  if (!raw || typeof raw !== 'object') return null
  const d = raw as Record<string, unknown>
  const id = str(d.id)
  const questions = Array.isArray(d.questions) ? d.questions.map(questionOf) : []
  if (!id || questions.length === 0 || questions.some((q) => q === null)) return null
  const outcome = str(d.outcome)
  const unanswerable = d.answerable === false || outcome === 'unanswerable'
  return {
    kind: 'question',
    id,
    toolCallId: str(d.tool_call_id) || toolCallId,
    questions: questions as AgentQuestion[],
    askedBy: str(d.asked_by),
    answerable: !unanswerable,
    ...(str(d.note) ? { note: str(d.note) } : {}),
    ...(outcome && !unanswerable ? { outcome } : {}),
    ...(str(d.ended) ? { ended: str(d.ended) } : {}),
    ...(answersOf(d.answers) ? { answers: answersOf(d.answers) } : {}),
  }
}

/** `question_card`: the question's card joins the turn — once, however often the frame arrives. */
export function applyQuestionFrame(segs: Segment[], d: Record<string, unknown>): Segment[] {
  const seg = questionSegmentOf(d)
  if (!seg || segs.some((sg) => sg.kind === 'question' && sg.id === seg.id)) return segs
  return [...segs, seg]
}

/** `question_resolved`: how the question ended — her answer, her Skip, or why it was withdrawn. */
export function applyQuestionResolved(segs: Segment[], d: Record<string, unknown>): Segment[] {
  const id = str(d.id)
  const outcome = str(d.outcome)
  if (!id || !outcome) return segs
  const answers = answersOf(d.answers)
  return segs.map((sg) => sg.kind === 'question' && sg.id === id
    ? { ...sg, outcome, ...(str(d.ended) ? { ended: str(d.ended) } : {}), ...(answers ? { answers } : {}) }
    : sg)
}

/** The session's `pending_questions`, on a page that opened (or re-read the session) while they
 *  wait: each joins the turn running now (the last one, an answer under way) unless a turn already
 *  shows it. */
export function graftPendingQuestions(turns: ChatTurn[], pending: unknown): ChatTurn[] {
  const cards = (Array.isArray(pending) ? pending : []).map((p) => questionSegmentOf(p)).filter((s): s is QuestionSegment => s !== null)
  const shown = new Set(turns.flatMap((t) => t.segments.filter((sg) => sg.kind === 'question').map((sg) => (sg as QuestionSegment).id)))
  const missing = cards.filter((c) => !shown.has(c.id))
  if (missing.length === 0) return turns
  const last = turns[turns.length - 1]
  if (!last || last.role !== 'assistant') return [...turns, { role: 'assistant', segments: missing }]
  return [...turns.slice(0, -1), { ...last, segments: [...last.segments, ...missing] }]
}
