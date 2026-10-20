import { describe, expect, it } from 'vitest'
import { emptyHint } from './SkillProposals'
import type { SkillLadderReview } from '../../lib/api'

// ── An empty proposals queue used to be two facts wearing one face ───────────────────────────────
//
// The queue's empty state read "when the system synthesizes a skill from your sessions, it lands
// here" — a sentence that is equally true when the reviewer has run a hundred times and found
// nothing and when it has never run once. A user waiting on a proposal could not tell a working
// reviewer from a dead one, and neither could a drive from outside: the API said `{"proposals": []}`
// in both worlds.
//
// These rails assert the three states are now SEPARATE SENTENCES. The floor that makes them rails
// rather than decoration is `test_three_states_produce_three_distinct_sentences`: without it, a
// helper that returned one constant string would satisfy every individual assertion below.

const review = (over: Partial<SkillLadderReview> = {}): SkillLadderReview => ({
  verdict: 'no_action',
  elapsed_ms: 4210,
  session_key: 'sess:1',
  detail: '',
  at: '2026-08-23T12:00:00+00:00',
  ...over,
})

describe('skill-proposals empty state', () => {
  it('says the reviewer has NOT RUN when there is no last pass', () => {
    const hint = emptyHint(null)
    expect(hint).toMatch(/has not run yet/i)
    // It must also say what would make it run — "not run yet" without a cause is a dead end.
    expect(hint).toMatch(/corrected|tools/i)
  })

  it('says the reviewer RAN AND FOUND NOTHING, and calls that healthy', () => {
    const hint = emptyHint(review({ verdict: 'no_action' }))
    expect(hint).toMatch(/nothing worth proposing/i)
    expect(hint).toMatch(/healthy/i)
    expect(hint).not.toMatch(/has not run/i)
  })

  it('names a FAILED pass as a failure rather than an idle queue', () => {
    const hint = emptyHint(review({ verdict: 'provider_error' }))
    expect(hint).toMatch(/could not run/i)
    expect(hint).toContain('provider_error') // the raw verdict is on the surface, not swallowed
    expect(hint).not.toMatch(/healthy/i)
    expect(hint).not.toMatch(/nothing worth proposing/i)
  })

  it('says WHY a pass could not run when every model failed', () => {
    // Measured: every model of the chain answered with nothing, and the page went on to read as
    // a review that found nothing. The reason the gateway recorded is the sentence shown.
    const detail = 'local:tiny failed before it replied (it answered with nothing), and so did cloud:swift (it answered with nothing)'
    const hint = emptyHint(review({ verdict: 'provider_error', detail }))
    expect(hint).toContain(`: ${detail}.`)
    expect(hint).not.toMatch(/nothing worth proposing/i)
  })

  it('treats an UNMAPPED verdict as something to look at, not as health', () => {
    // Mirrors the backend, which logs an unrecognised verdict at WARNING deliberately. A
    // default branch that swallowed the unknown into "all is well" is the defect class itself.
    const hint = emptyHint(review({ verdict: 'some_future_verdict' }))
    expect(hint).toMatch(/could not run/i)
    expect(hint).toContain('some_future_verdict')
  })

  it('reports every healthy verdict the backend can emit as healthy', () => {
    // Kept in step with `_LADDER_VERDICT_LEVEL`'s "the pass worked" half. If the backend adds a
    // success verdict and this list is not updated, that verdict starts reading as a FAILURE to
    // the user — loud, which is the safe direction, and this rail is where it gets noticed.
    for (const v of ['env_failure_claim', 'no_action', 'enqueue_skipped', 'filed', 'template_filed', 'template_declined']) {
      expect(emptyHint(review({ verdict: v })), v).toMatch(/nothing worth proposing/i)
    }
  })

  it('three states produce three distinct sentences', () => {
    // THE vacuity floor. Every assertion above passes against a helper that ignores its argument
    // and returns one string; this is the one that does not.
    const sentences = new Set([
      emptyHint(null),
      emptyHint(review({ verdict: 'no_action' })),
      emptyHint(review({ verdict: 'provider_error' })),
    ])
    expect(sentences.size).toBe(3)
  })

  it('shows WHEN the pass ran, so a stale review is not read as a fresh one', () => {
    expect(emptyHint(review())).toContain(new Date('2026-08-23T12:00:00+00:00').toLocaleString())
  })
})
