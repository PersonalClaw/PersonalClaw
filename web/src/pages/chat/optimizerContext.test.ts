import { describe, it, expect } from 'vitest'
import { buildOptimizerContext, CTX_BUDGET_CHARS, CTX_MAX_TURNS, CTX_TURN_CHARS } from './optimizerContext'
import type { ChatTurn } from './chatTypes'

const turn = (role: ChatTurn['role'], text: string): ChatTurn => ({ role, segments: [{ kind: 'text', text }] })

describe('buildOptimizerContext', () => {
  it('labels every turn with its role', () => {
    const ctx = buildOptimizerContext([turn('user', 'read config.py'), turn('assistant', 'It sets the port.')])
    expect(ctx).toBe('user: read config.py\nassistant: It sets the port.')
  })

  it('orders newest LAST', () => {
    // Deliberately asymmetric: a symmetric fixture passes either orientation, which is
    // how a reversed context ships green.
    const ctx = buildOptimizerContext([
      turn('user', 'OLDEST'),
      turn('assistant', 'MIDDLE'),
      turn('user', 'NEWEST'),
    ])
    expect(ctx.split('\n')).toEqual(['user: OLDEST', 'assistant: MIDDLE', 'user: NEWEST'])
    expect(ctx.indexOf('OLDEST')).toBeLessThan(ctx.indexOf('NEWEST'))
    expect(ctx.endsWith('user: NEWEST')).toBe(true)
  })

  it('keeps the newest CTX_MAX_TURNS turns and drops the oldest whole', () => {
    const turns = Array.from({ length: 14 }, (_, i) => turn(i % 2 ? 'assistant' : 'user', `t${i}`))
    const lines = buildOptimizerContext(turns).split('\n')
    expect(lines).toHaveLength(CTX_MAX_TURNS)
    // t0..t3 are gone; every survivor still carries its label (nothing decapitated).
    expect(lines[0]).toBe('user: t4')
    expect(lines[lines.length - 1]).toBe('assistant: t13')
    for (const l of lines) expect(l).toMatch(/^(user|assistant): /)
  })

  it('clips a long turn at the per-turn budget and marks the cut', () => {
    const ctx = buildOptimizerContext([turn('user', 'x'.repeat(CTX_TURN_CHARS + 50))])
    expect(ctx).toBe(`user: ${'x'.repeat(CTX_TURN_CHARS)}…`)
  })

  it('skips turns with no text (activity-only / still empty) rather than emitting a bare label', () => {
    const noText: ChatTurn = { role: 'assistant', segments: [{ kind: 'activity', text: 'Thinking…' }] }
    const ctx = buildOptimizerContext([turn('user', 'hi'), noText, turn('assistant', 'hello')])
    expect(ctx).toBe('user: hi\nassistant: hello')
  })

  it('flattens embedded newlines so a line boundary always means a turn boundary', () => {
    const ctx = buildOptimizerContext([turn('user', 'line one\n\nline two')])
    expect(ctx).toBe('user: line one line two')
    expect(ctx.split('\n')).toHaveLength(1)
  })

  // ── the arithmetic claim: this survives the handler's cap ──

  it('worst case is EXACTLY the declared budget', () => {
    // Ten assistant turns (the wider label) each far over the per-turn budget: the
    // tightest possible statement of "the most this module can emit".
    const turns = Array.from({ length: CTX_MAX_TURNS }, () => turn('assistant', 'y'.repeat(5000)))
    expect(buildOptimizerContext(turns).length).toBe(CTX_BUDGET_CHARS)
  })

  it('budget matches the handler cap the Python side derives', () => {
    // MAX_CONTEXT_CHARS in src/personalclaw/dashboard/handlers/optimizer.py. Two
    // literals, one across the language boundary — if this number moves, move both.
    expect(CTX_BUDGET_CHARS).toBe(4129)
  })

  it('a real-sized context survives a tail slice at the cap with its newest turn intact', () => {
    // Twenty turns of ~600 chars — well past the ~4000 chars the ten-turn window
    // implies, so the interesting question is what got DROPPED.
    const turns = Array.from({ length: 20 }, (_, i) =>
      turn(i % 2 ? 'assistant' : 'user', `turn${i} ` + 'z'.repeat(600)),
    )
    const ctx = buildOptimizerContext(turns)
    // Nothing for the handler to cut: the tail slice is a no-op.
    expect(ctx.length).toBeLessThanOrEqual(CTX_BUDGET_CHARS)
    expect(ctx.slice(-CTX_BUDGET_CHARS)).toBe(ctx)
    // The newest turn is present, labeled, and last — the end a naive slice loses.
    expect(ctx.split('\n')).toHaveLength(CTX_MAX_TURNS)
    expect(ctx.split('\n')[CTX_MAX_TURNS - 1].startsWith('assistant: turn19 ')).toBe(true)
    expect(ctx).not.toContain('turn9 ') // the oldest ten dropped whole
  })
})
