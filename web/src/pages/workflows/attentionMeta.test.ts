/**
 * #565 — the escalation record had no reader, so a failed run explained itself to nobody.
 *
 * The engine writes `run.attention = escalation_artifact(...)` when a node exhausts its retries
 * or a breaker trips, and journals it too. Measured server-side (see
 * `tests/test_workflow_escalation_is_reported.py`): on that path the run goes terminal through
 * `_finish(status)` with **no** `error`, so `payload.error === ''` and the escalation is the only
 * account of what happened.
 *
 * Every client reader looked for `attention.prompt` — a gate ask's field. An escalation has none,
 * so it read as blank everywhere. These pin the discrimination that ends that, and the two rules
 * that are easy to get wrong in the other direction: a gate ask must keep working, and an
 * unknown reason must not be smoothed into one of the eight known ones.
 */
import { describe, it, expect } from 'vitest'
import { attentionLine, escalationHeadline, readAttention } from './attentionMeta'

/** The real payload, copied from a measured run (`reason: retries_exhausted`). */
const ESCALATION = {
  kind: 'escalation',
  node_id: 'consume',
  reason: 'retries_exhausted',
  detail: "transform binding failed: unresolved reference at 'done'",
  options: ['reassign', 'decompose', 'revise', 'accept_with_limitations', 'defer'],
  attempts: [
    {
      attempt: 1,
      failure_class: 'user',
      error: 'ConnectionError: network down',
      expected: 'a bound reference',
      actual: 'nothing',
      evidence: 'nodes.nested.output',
      fix_instruction: 'check the referenced node id and field exist',
      severity: 'error',
      error_signature: '1d5db3e93353',
      tokens: 0,
      duration_secs: 0.002,
    },
  ],
}

describe('an escalation is read as a diagnosis', () => {
  it('yields the reason as a sentence, the cause, and the attempts', () => {
    const read = readAttention(ESCALATION)
    expect(read?.kind).toBe('escalation')
    if (read?.kind !== 'escalation') return
    expect(read.headline).toBe('every retry was spent and the step still failed')
    expect(read.reason).toBe('retries_exhausted')
    expect(read.nodeId).toBe('consume')
    expect(read.detail).toContain('unresolved reference')
    expect(read.attempts).toHaveLength(1)
    expect(read.attempts[0].fixInstruction).toBe('check the referenced node id and field exist')
    expect(read.attempts[0].failureClass).toBe('user')
    expect(read.attempts[0].signature).toBe('1d5db3e93353')
  })

  it('is NOT read as an ask — that conflation is the whole bug', () => {
    // 🔑 The pre-fix behaviour, kept as a permanent inverted mutant: `attention.prompt` on this
    // record is `undefined`, which is why every surface printed its generic fallback.
    expect((ESCALATION as Record<string, unknown>).prompt).toBeUndefined()
    const read = readAttention(ESCALATION)
    expect(read?.kind).not.toBe('ask')
  })

  it('gives a glance surface one honest line', () => {
    expect(attentionLine(ESCALATION)).toBe('Stopped: every retry was spent and the step still failed')
  })

  it('survives a record with no attempts and no detail', () => {
    // The breaker path escalates with an empty `attempts` list when it trips before any attempt
    // was recorded. A panel that assumed at least one row would crash on exactly the runs it
    // exists to explain.
    const read = readAttention({ kind: 'escalation', reason: 'token_cap', node_id: 'n' })
    if (read?.kind !== 'escalation') throw new Error('not read as an escalation')
    expect(read.attempts).toEqual([])
    expect(read.detail).toBe('')
    expect(read.headline).toBe('the run reached its token budget')
  })

  it('numbers an attempt that arrived without an index', () => {
    const read = readAttention({ kind: 'escalation', reason: 'x', attempts: [{}, {}] })
    if (read?.kind !== 'escalation') throw new Error('not read as an escalation')
    expect(read.attempts.map((a) => a.attempt)).toEqual([1, 2])
  })
})

describe('a gate ask still reads as an ask', () => {
  it('carries its prompt through', () => {
    // Vacuity floor in the other direction: a change that read everything as an escalation would
    // break the surface that already worked.
    const read = readAttention({ kind: 'approval', prompt: 'Ship it?' })
    expect(read).toEqual({ kind: 'ask', askKind: 'approval', prompt: 'Ship it?' })
    expect(attentionLine({ kind: 'approval', prompt: 'Ship it?' })).toBe('Ship it?')
  })

  it('reads an UNKNOWN kind that carries a prompt as an ask', () => {
    // Deliberately not an allow-list of the four known ask kinds: a fifth kind carrying a
    // question is still a question, and refusing it would repeat this module's own bug.
    expect(attentionLine({ kind: 'ranked_choice', prompt: 'Which first?' })).toBe('Which first?')
  })

  it('falls back when the ask carries no words of its own', () => {
    expect(attentionLine({ kind: 'approval' })).toBe('Waiting on you')
    expect(attentionLine(null)).toBe('Waiting on you')
    expect(attentionLine({})).toBe('Waiting on you')
  })

  it('reads nothing out of a non-record', () => {
    expect(readAttention(null)).toBeNull()
    expect(readAttention('escalation')).toBeNull()
    expect(readAttention([{ kind: 'escalation' }])).toBeNull()
  })
})

describe('an unmapped reason is shown, not smoothed', () => {
  it('falls through to the engine token verbatim', () => {
    // 🪤 A friendly default here would report a NEW failure mode as one of the eight known
    // ones. The raw token is greppable in the Python source, which is what a reader needs.
    expect(escalationHeadline('some_new_breaker')).toBe('some_new_breaker')
    expect(attentionLine({ kind: 'escalation', reason: 'some_new_breaker' }))
      .toBe('Stopped: some_new_breaker')
  })

  it('covers every reason the engine can produce today', () => {
    // The Python side asserts this against the source of truth
    // (`test_the_reason_vocabulary_is_what_the_frontend_maps` parses `resilience.py` +
    // `loop/tick.py`). Restated here as the list this file's expectations rest on.
    for (const reason of [
      'retries_exhausted', 'max_iterations', 'repeated_error', 'identical_output',
      'token_cap', 'identical_call', 'hypothesis_exhausted', 'no_progress',
      'iterations_failed',
    ]) {
      expect(escalationHeadline(reason), `${reason} has no sentence`).not.toBe(reason)
    }
  })

  it('does not say "iteration ceiling" for a loop whose iterations failed', () => {
    // #3524: the measured banner read "the loop reached its iteration ceiling" on a run where five
    // of six iterations never called a model. The two tokens are one word apart in the engine and
    // opposite in meaning to a reader, so the sentences must not be near-duplicates of each other —
    // a reader who sees "ceiling" shrinks a task that was never too big.
    expect(escalationHeadline('iterations_failed')).not.toContain('ceiling')
    expect(escalationHeadline('iterations_failed')).toContain('failing')
    expect(escalationHeadline('max_iterations')).toContain('ceiling')
  })
})
