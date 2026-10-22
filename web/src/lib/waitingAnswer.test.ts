import { describe, expect, it } from 'vitest'
import type { WaitingItem, WaitingKind } from './api'
import { waitingAnswerId, waitingQuestion } from './waitingAnswer'
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
]

/** The kinds answered in place, each with the route it answers on and what it answers with. */
const ANSWERED: { kind: WaitingKind; own: string[]; answered: (w: WaitingItem) => string }[] = [
  { kind: 'approval', own: [ROUTES.approval], answered: waitingAnswerId },
  { kind: 'browse_grant', own: [ROUTES.browse_grant], answered: waitingAnswerId },
  { kind: 'bridge_confirmation', own: [ROUTES.bridge_confirmation], answered: waitingAnswerId },
  { kind: 'gate', own: [ROUTES.gate_resume, ROUTES.gate_confirm], answered: (w) => waitingQuestion(w)?.runId ?? '' },
  { kind: 'park', own: [ROUTES.park], answered: (w) => waitingQuestion(w)?.triggerId ?? '' },
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
