import { describe, expect, it } from 'vitest'
import type { WaitingItem, WaitingKind } from './api'
import { waitingAnswerId, waitingQuestion, waitingQuestionRef, waitingReply } from './waitingAnswer'
import { waitingItem } from '../test/waiting'

// ── An item is answered on its own route's exact shape, or not at all (#3888) ──────────────────
//
// `answer` is an item's exact action and its `id` only a key, so the ids a surface's buttons post
// with are read off `answer.path`, by one anchored pattern per kind. Each kind is handed every
// other kind's route, a malformed one and one whose id segment decodes to `..` or a `/`, and none
// of them is answered: a surface given one offers no control and makes no call.

/** One route of each shape the list hands out, by what answers on it. */
const ROUTES = {
  approval: '/api/approvals/ap-1/{answer}',
  browse_grant: '/api/browse/grants/g-1/{answer}',
  bridge_confirmation: '/api/external-access/bridge/confirmations/tok-1',
  gate_resume: '/api/workflows/runs/r-1/resume',
  gate_confirm: '/api/workflows/runs/r-1/confirm',
  park: '/api/triggers/schedule%3Aclock%3Anightly/answer',
  loop_question: '/api/loops/l-1/nudge',
  question: '/api/chat/sessions/c-1/questions/q-1/answer',
  plan_review: '/api/loops/l-1/plan/approve',
} as const

/** Routes no kind answers on: malformed, or an id that would climb out of its route. */
const BROKEN = [
  '',
  'not a route',
  '/api/approvals//{answer}',
  '/api/approvals/ap-1/{answer}/extra',
  '/x/api/approvals/ap-1/{answer}',
  'https://elsewhere.example/api/approvals/ap-1/{answer}',
  '/api/approvals/%2E%2E/{answer}',
  '/api/approvals/a%2Fb/{answer}',
  '/api/approvals/%5C/{answer}',
  '/api/approvals/%E0%A4%A/{answer}',
  '/api/browse/grants/%2e%2e/{answer}',
  '/api/external-access/bridge/confirmations/..',
  '/api/external-access/bridge/confirmations/%2E',
  '/api/workflows/runs/%2E%2E/resume',
  '/api/workflows/runs/r-1/answer',
  '/x/api/workflows/runs/r-1/resume',
  'https://elsewhere.example/api/workflows/runs/r-1/confirm',
  '/api/workflows/runs/r-1/resume/',
  '/api/triggers/%2F/answer',
  '/api/triggers/%2e%2e/answer',
  '/x/api/triggers/t-1/answer',
  '/api/loops/l-1/answer',
  '/api/triggers/t-1/resume',
  // An agent's question's route, broken, climbing out, or a lookalike of another source's.
  '/api/chat/sessions//questions/q-1/answer',
  '/api/chat/sessions/c-1/questions//answer',
  '/api/chat/sessions/%2E%2E/questions/q-1/answer',
  '/api/chat/sessions/c-1/questions/%2e%2e/answer',
  '/api/chat/sessions/c%2F1/questions/q-1/answer',
  '/api/chat/sessions/c-1/questions/q-1/answer/',
  '/x/api/chat/sessions/c-1/questions/q-1/answer',
  'https://elsewhere.example/api/chat/sessions/c-1/questions/q-1/answer',
  '/api/loops/x/questions/y/answer',
  '/api/chat/c-1/questions/q-1/answer',
]

/** The kinds answered in place, each with the route it answers on and what it answers with. */
const ANSWERED: { kind: WaitingKind; own: string[]; answered: (w: WaitingItem) => string }[] = [
  { kind: 'approval', own: [ROUTES.approval], answered: waitingAnswerId },
  { kind: 'browse_grant', own: [ROUTES.browse_grant], answered: waitingAnswerId },
  { kind: 'bridge_confirmation', own: [ROUTES.bridge_confirmation], answered: waitingAnswerId },
  { kind: 'gate', own: [ROUTES.gate_resume, ROUTES.gate_confirm], answered: (w) => waitingQuestion(w)?.runId ?? '' },
  { kind: 'park', own: [ROUTES.park], answered: (w) => waitingQuestion(w)?.triggerId ?? '' },
  // The buddy answers an agent's question in place (#3832), through the chat and question its route names.
  { kind: 'question', own: [ROUTES.question], answered: (w) => { const r = waitingQuestionRef(w); return r ? `${r.session}/${r.question}` : '' } },
]

const on = (kind: WaitingKind, path: string) =>
  waitingItem({ kind, answer: { method: 'POST', path, takes: [], body: { resume_token: 'tok' } } })

describe('each kind is answered on its own route alone', () => {
  it.each(ANSWERED)('$kind is answered on its own route', ({ kind, own, answered }) => {
    for (const path of own) expect(answered(on(kind, path)), path).not.toBe('')
  })

  it.each(ANSWERED)('$kind is answered on no other kind’s route', ({ kind, own, answered }) => {
    const others = Object.values(ROUTES).filter((p) => !own.includes(p))
    expect(others.length, 'vacuity floor').toBeGreaterThanOrEqual(7)
    for (const path of others) {
      expect(answered(on(kind, path)), `${kind} on ${path}`).toBe('')
      expect(waitingAnswerId(on(kind, path)) === '' || own.includes(path), path).toBe(true)
    }
  })

  it.each(ANSWERED)('$kind is answered on no malformed route, nor one whose id climbs out', ({ kind, answered }) => {
    for (const path of BROKEN) {
      expect(answered(on(kind, path)), `${kind} on ${JSON.stringify(path)}`).toBe('')
      expect(waitingQuestion(on(kind, path)), path).toBeNull()
    }
  })

  it('the ids are the route’s own, decoded', () => {
    expect(waitingAnswerId(on('approval', '/api/approvals/spawn%3Aresearcher/{answer}'))).toBe('spawn:researcher')
    expect(waitingQuestion(on('park', ROUTES.park))?.triggerId).toBe('schedule:clock:nightly')
    expect(waitingQuestion(on('gate', ROUTES.gate_confirm))).toMatchObject({ runId: 'r-1', confirm: true, approval: true })
    expect(waitingQuestion(on('gate', ROUTES.gate_resume))).toMatchObject({ runId: 'r-1', confirm: false })
    expect(waitingQuestionRef(on('question', '/api/chat/sessions/dashboard%3Aui/questions/q%3A1/answer'))).toEqual({ session: 'dashboard:ui', question: 'q:1' })
  })

  it('a question\'s chat and id come from no route of another kind\'s, even one shaped like it', () => {
    for (const kind of ['approval', 'loop_question', 'gate', 'park', 'plan_review'] as const) {
      expect(waitingQuestionRef(on(kind, ROUTES.question)), kind).toBeNull()
    }
  })

  it('the kinds answered by opening their page are answered in place nowhere', () => {
    for (const kind of ['question', 'loop_question', 'plan_review'] as const) {
      for (const path of Object.values(ROUTES)) {
        expect(waitingAnswerId(on(kind, path)), `${kind} on ${path}`).toBe('')
        expect(waitingQuestion(on(kind, path)), `${kind} on ${path}`).toBeNull()
      }
    }
  })
})

// A typed line goes the same way (#3813): a free-text gate through its resume token, a loop's
// question as its nudge, an agent's one question as your own answer, each read off its own route.
describe('a typed reply goes to its own route alone', () => {
  const text = (kind: WaitingKind, path: string, brief: Record<string, unknown> = {}) =>
    waitingItem({ kind, brief, answer: { method: 'POST', path, takes: ['text'], body: { resume_token: 'tok' } } })
  const ONE = { questions: [{ question: 'Which version?' }] }
  const GATE_TEXT = { ask: { kind: 'text', prompt: 'Name it' } }
  const REPLIED: { kind: WaitingKind; own: string; brief: Record<string, unknown> }[] = [
    { kind: 'gate', own: ROUTES.gate_resume, brief: GATE_TEXT },
    { kind: 'loop_question', own: ROUTES.loop_question, brief: {} },
    { kind: 'question', own: ROUTES.question, brief: ONE },
  ]
  const REPLY_BROKEN = [
    '/api/loops/%2E%2E/nudge',
    '/api/loops/a%2Fb/nudge',
    '/api/loops/l-1/nudge/',
    '/x/api/loops/l-1/nudge',
    '/api/chat/sessions/%2e%2e/questions/q-1/answer',
    '/api/chat/sessions/c-1/questions/a%5Cb/answer',
    '/api/chat/sessions/c-1/questions/q-1/answer/extra',
  ]

  it.each(REPLIED)('$kind takes a line on its own route, and its ids decoded', ({ kind, own, brief }) => {
    expect(waitingReply(text(kind, own, brief)), own).not.toBeNull()
  })

  it.each(REPLIED)('$kind takes no line on another kind’s route, a malformed one, or one that climbs out', ({ kind, own, brief }) => {
    for (const path of [...Object.values(ROUTES).filter((p) => p !== own && !(kind === 'gate' && p === ROUTES.gate_confirm)), ...BROKEN, ...REPLY_BROKEN]) {
      expect(waitingReply(text(kind, path, brief)), `${kind} on ${JSON.stringify(path)}`).toBeNull()
    }
  })

  it('reads the route’s own ids', () => {
    expect(waitingReply(text('gate', ROUTES.gate_resume, GATE_TEXT))).toEqual({ kind: 'gate', runId: 'r-1', resumeToken: 'tok' })
    expect(waitingReply(text('loop_question', '/api/loops/loop%3A7/nudge'))).toEqual({ kind: 'loop', loopId: 'loop:7' })
    expect(waitingReply(text('question', ROUTES.question, ONE))).toEqual({ kind: 'question', session: 'c-1', questionId: 'q-1' })
  })

  it('takes no line for an ask answered otherwise: a yes or a no, a choice, a confirmation, a wake, several questions', () => {
    expect(waitingReply(text('gate', ROUTES.gate_resume, { ask: { kind: 'approval' } }))).toBeNull()
    expect(waitingReply(text('gate', ROUTES.gate_resume, { ask: { kind: 'choice', choices: ['a', 'b'] } }))).toBeNull()
    expect(waitingReply(text('gate', ROUTES.gate_confirm, GATE_TEXT))).toBeNull()
    expect(waitingReply(text('gate', ROUTES.gate_resume, { ask: { kind: 'event' } }))).toBeNull()
    expect(waitingReply(text('question', ROUTES.question, { questions: [{ question: 'A?' }, { question: 'B?' }] }))).toBeNull()
    for (const kind of ['approval', 'park', 'browse_grant', 'plan_review', 'bridge_confirmation'] as const) {
      for (const path of Object.values(ROUTES)) expect(waitingReply(text(kind, path)), `${kind} on ${path}`).toBeNull()
    }
  })
})
