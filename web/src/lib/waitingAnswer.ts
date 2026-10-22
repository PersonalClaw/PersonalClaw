import type { WaitingItem } from './api'

// ── How a surface answers an item of the waiting list, read off its own route (#3888) ──────────
//
// An item's `answer` is its exact action: the route its own source answers it on. Its `id` is a
// key, never an address. So the ids a button posts with (a run's, a trigger's, an approval's, a
// grant's, a bridge confirmation's) are read here, and only here, from `answer.path`, and only from
// a path of the exact shape its kind answers on. A path of any other shape, or one whose id
// segment does not decode to one plain segment, is answered nowhere: the surface shows the item
// and offers no control for it. Home's Needs You and Mission Control both answer through this.

/** What a gate's or a park's answer needs, read off its waiting item (or an Inbox row's card),
 *  for the answer buttons. */
export interface CardQuestion {
  runId: string
  /** A trigger's action that stopped for you, answered at the trigger. Empty for a run's gate. */
  triggerId: string
  nodeId: string
  resumeToken: string
  prompt: string
  choices: string[]
  /** A yes or a no. */
  approval: boolean
  /** A run's confirmation, answered by its confirm route with a verb. */
  confirm: boolean
  /** Parked until something wakes it (an `event` gate): the card wakes it, and decides nothing. */
  event: boolean
}

function text(v: unknown): string {
  return typeof v === 'string' ? v.trim() : ''
}

function record(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
}

/** One path segment, decoded, or "" when it does not decode to one plain segment: an encoded
 *  `/` or `\`, or a `.` or `..` that would climb out of the route its button posts to. */
function segment(raw: string): string {
  let value: string
  try {
    value = decodeURIComponent(raw)
  } catch {
    return ''
  }
  if (value === '.' || value === '..' || /[/\\]/.test(value)) return ''
  return value
}

const RUN_ANSWER = /^\/api\/workflows\/runs\/([^/]+)\/(resume|confirm)$/
const PARK_ANSWER = /^\/api\/triggers\/([^/]+)\/answer$/
const GRANT_ANSWER = /^\/api\/browse\/grants\/([^/]+)\/\{answer\}$/
const BRIDGE_ANSWER = /^\/api\/external-access\/bridge\/confirmations\/([^/]+)$/
const APPROVAL_ANSWER = /^\/api\/approvals\/([^/]+)\/\{answer\}$/

/** A gate's or a park's answer, or null for an item a surface answers by opening its route.
 *
 *  The ids come from the item's own route, `answer.path`, and only from a path of the exact shape
 *  its kind answers on: the buttons then call the same `api` function the run page and the
 *  trigger's own card call, so the route a surface posts to is the item's own, unchanged. A path of
 *  any other shape is not answered here at all. */
export function waitingQuestion(item: WaitingItem): CardQuestion | null {
  const b = record(item.brief)
  const answer = record(item.answer)
  const path = text(answer.path)
  const body = record(answer.body)
  const token = text(body.resume_token)
  if (item.kind === 'gate') {
    const m = RUN_ANSWER.exec(path)
    const runId = m ? segment(m[1]) : ''
    if (!runId) return null
    const ask = record(b.ask)
    const kind = text(ask.kind) || 'approval'
    const confirm = m?.[2] === 'confirm'
    const choices = (Array.isArray(ask.choices) ? ask.choices : [])
      .filter((c): c is string => typeof c === 'string' && c.length > 0)
    return {
      runId,
      triggerId: '',
      nodeId: text(b.node_id),
      resumeToken: token,
      prompt: text(ask.prompt),
      choices: confirm || kind === 'approval' ? [] : choices,
      approval: confirm || kind === 'approval',
      confirm,
      event: !confirm && kind === 'event',
    }
  }
  if (item.kind === 'park') {
    const m = PARK_ANSWER.exec(path)
    const triggerId = m ? segment(m[1]) : ''
    if (!triggerId) return null
    return {
      runId: '',
      triggerId,
      nodeId: '',
      resumeToken: token,
      prompt: text(b.question),
      choices: [],
      approval: true,
      confirm: false,
      event: false,
    }
  }
  return null
}

/** The id a yes-or-no item is answered by, read off its own route as `waitingQuestion` reads a
 *  gate's: an approval's registry id, a browser grant's request id, a bridge confirmation's token.
 *  "" for an item of another kind, or whose route is not the shape its kind answers on. */
export function waitingAnswerId(item: WaitingItem): string {
  const path = text(record(item.answer).path)
  const re = item.kind === 'approval' ? APPROVAL_ANSWER
    : item.kind === 'browse_grant' ? GRANT_ANSWER
      : item.kind === 'bridge_confirmation' ? BRIDGE_ANSWER
        : null
  const m = re ? re.exec(path) : null
  return m ? segment(m[1]) : ''
}

/** Where a free-text ask takes a typed line, read off its own route as `waitingQuestion` reads a
 *  gate's: a run's gate that asks for text (answered through its resume token), a loop's question
 *  (its nudge), or an agent's one question in a chat (its answer route, as your own words). */
export type ReplyTarget =
  | { kind: 'gate'; runId: string; resumeToken: string }
  | { kind: 'loop'; loopId: string }
  | { kind: 'question'; session: string; questionId: string }

const LOOP_NUDGE = /^\/api\/loops\/([^/]+)\/nudge$/
const CHAT_QUESTION = /^\/api\/chat\/sessions\/([^/]+)\/questions\/([^/]+)\/answer$/

/** The reply a free-text ask takes, or null for one answered otherwise (a yes or a no, a choice, a
 *  merge, a card of several questions) or whose route is not the shape its kind answers on. */
export function waitingReply(item: WaitingItem): ReplyTarget | null {
  const path = text(record(item.answer).path)
  if (item.kind === 'gate') {
    const q = waitingQuestion(item)
    if (!q || q.approval || q.event || q.confirm || q.choices.length > 0) return null
    return { kind: 'gate', runId: q.runId, resumeToken: q.resumeToken }
  }
  if (item.kind === 'loop_question') {
    const m = LOOP_NUDGE.exec(path)
    const loopId = m ? segment(m[1]) : ''
    return loopId ? { kind: 'loop', loopId } : null
  }
  if (item.kind === 'question') {
    const qs = record(item.brief).questions
    if (!Array.isArray(qs) || qs.length !== 1) return null
    const m = CHAT_QUESTION.exec(path)
    const session = m ? segment(m[1]) : ''
    const questionId = m ? segment(m[2]) : ''
    return session && questionId ? { kind: 'question', session, questionId } : null
  }
  return null
}

/** The chat and the question an agent's question is answered by, read off its own route as
 *  `waitingAnswerId` reads an approval's. `null` for an item of another kind, or whose route is not
 *  the shape a question answers on. */
export function waitingQuestionRef(item: WaitingItem): { session: string; question: string } | null {
  if (item.kind !== 'question') return null
  const m = CHAT_QUESTION.exec(text(record(item.answer).path))
  const session = m ? segment(m[1]) : ''
  const question = m ? segment(m[2]) : ''
  return session && question ? { session, question } : null
}
