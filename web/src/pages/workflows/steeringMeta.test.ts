import { describe, expect, it } from 'vitest'
import { canSteerComment, judgeComment, steerTextFromComment } from './steeringMeta'

// ── Judge-comment triage + steering text (LOOPS-EVOLUTION R14 / criterion 8) ──
//
// Pure tests over the triage decisions: which node carries a judge comment worth acting on,
// and what an "accept" sends to the worker. Accepting a comment POSTs to `/steer`, which the
// engine consumes at the next iteration boundary — the channel that makes "an accepted judge
// comment reaches the worker session" literally true.

describe('judgeComment', () => {
  it('prefers the remediation — the actionable half — over the bare cause', () => {
    const c = judgeComment({
      failure: { cause_plain: 'the tests did not pass', remediation: 'pin numpy to 1.26' },
    })
    expect(c).toBe('pin numpy to 1.26')
  })

  it('falls back to the cause when there is no remediation', () => {
    expect(judgeComment({ failure: { cause_plain: 'the tests did not pass' } })).toBe('the tests did not pass')
  })

  it('reads a degraded reason as a comment', () => {
    // Degraded is a success-with-a-caveat, still worth steering on.
    expect(judgeComment({ degraded_reason: 'ran with a stubbed source' })).toBe('ran with a stubbed source')
  })

  it('is empty when a node carries no verdict text', () => {
    expect(judgeComment({})).toBe('')
    expect(judgeComment({ failure: null })).toBe('')
  })
})

describe('steerTextFromComment', () => {
  it('frames the comment as feedback to act on, labelled by the node', () => {
    expect(steerTextFromComment('synthesize', 'tighten the summary')).toBe(
      'Address this feedback on "synthesize": tighten the summary',
    )
  })

  it('omits the node label when there is none', () => {
    expect(steerTextFromComment('', 'tighten the summary')).toBe('Address this feedback: tighten the summary')
  })

  it('returns empty for empty input, so a blank steer is never queued', () => {
    expect(steerTextFromComment('synthesize', '   ')).toBe('')
    expect(steerTextFromComment('synthesize', '')).toBe('')
  })
})

describe('canSteerComment', () => {
  it('is true only for a live run with a comment', () => {
    const withComment = { failure: { cause_plain: 'x' } }
    expect(canSteerComment(withComment, true)).toBe(true)
    expect(canSteerComment(withComment, false)).toBe(false)
    expect(canSteerComment({}, true)).toBe(false)
  })
})
