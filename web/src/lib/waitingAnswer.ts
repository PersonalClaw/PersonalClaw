import type { WaitingItem } from './api'

// ── How a surface answers an item of the waiting list, read off its own route (#3888) ──────────
//
// An item's `answer` is its exact action: the route its own source answers it on. Its `id` is a
// key, never an address. So the ids a button posts with (a run's, a trigger's, an approval's, a
// grant's, a bridge confirmation's) are read here, and only here, from `answer.path`, and only from
// a path of the exact shape its kind answers on. A path of any other shape, or one whose id
// segment does not decode to one plain segment, is answered nowhere: the surface shows the item
// and offers no control for it. Needs you's cards answer through this, on Needs you and on a project's page.

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
  /** What it asks for, as the gate's ask names it (`approval`, `choice`, `text`, `form`, `event`),
   *  `approval` for a park: a form is answered only in the panel, a text gate by a typed line. */
  kind: string
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
      kind: confirm ? 'approval' : kind,
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
      kind: 'approval',
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
    // A form is filled in its panel: a line typed on a card would answer only its first field.
    if (!q || q.kind !== 'text') return null
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

const LOOP_MERGE = /^\/api\/loops\/([^/]+)\/merge$/
const LOOP_CONFLICT = /^\/api\/loops\/([^/]+)\/conflict$/

/** A loop's merge or conflict question, and the loop it is answered on, read off its own route as
 *  `waitingReply` reads a nudge's. `null` for any other item, or a route not of that shape. */
export function waitingLoopChoice(item: WaitingItem): { kind: 'merge' | 'conflict'; loopId: string } | null {
  if (item.kind !== 'loop_question') return null
  const path = text(record(item.answer).path)
  const merge = LOOP_MERGE.exec(path)
  if (merge && segment(merge[1])) return { kind: 'merge', loopId: segment(merge[1]) }
  const conflict = LOOP_CONFLICT.exec(path)
  if (conflict && segment(conflict[1])) return { kind: 'conflict', loopId: segment(conflict[1]) }
  return null
}

/** What a plan answers on: a loop's or a chat's step under review (approved, or commented on to
 *  redraft it, naming the step as the list read it: `base`, its review revision), or a loop's
 *  plan waiting for its launch. */
export type PlanTarget =
  | { kind: 'step'; on: 'loop' | 'chat'; id: string; stepId: string; base: string }
  | { kind: 'launch'; loopId: string }

const LOOP_PLAN = /^\/api\/loops\/([^/]+)\/plan\/approve$/
const CHAT_PLAN = /^\/api\/chat\/sessions\/([^/]+)\/plan\/approve$/
const LOOP_LAUNCH = /^\/api\/loops\/([^/]+)$/

/** A plan's answer, read off its own route: `null` for any other item, or a route, a step or a
 *  revision not of the shape a plan answers on. */
export function waitingPlan(item: WaitingItem): PlanTarget | null {
  if (item.kind !== 'plan_review') return null
  const answer = record(item.answer)
  const path = text(answer.path)
  const body = record(answer.body)
  if (text(answer.method) === 'PATCH') {
    const m = LOOP_LAUNCH.exec(path)
    const loopId = m ? segment(m[1]) : ''
    return loopId && body.action === 'start' ? { kind: 'launch', loopId } : null
  }
  const stepId = text(body.step_id)
  const quoted = text(record(answer.headers)['If-Match'])
  const base = /^"[^"]+"$/.test(quoted) ? quoted.slice(1, -1) : ''
  if (!stepId || !base) return null
  const loop = LOOP_PLAN.exec(path)
  if (loop && segment(loop[1])) return { kind: 'step', on: 'loop', id: segment(loop[1]), stepId, base }
  const chat = CHAT_PLAN.exec(path)
  if (chat && segment(chat[1])) return { kind: 'step', on: 'chat', id: segment(chat[1]), stepId, base }
  return null
}
