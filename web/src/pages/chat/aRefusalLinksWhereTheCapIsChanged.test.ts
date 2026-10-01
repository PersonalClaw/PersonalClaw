/**
 * A chat turn a spend cap refused keeps the Settings page the gateway names for it, so the notice
 * links where the cap is changed (`meta.settings` on the error row), and an ordinary failure links
 * nothing.
 */
import { describe, expect, it } from 'vitest'
import { hydrateTurns, type ErrorSegment } from './chatTypes'

const CAP = 'The daily dollar budget is spent ($4.10 of $4.00): raise Max dollars / day in Settings → Guardrails (0 removes the cap), or wait for it to reset at midnight.'

function errorOf(meta?: Record<string, unknown>): ErrorSegment {
  const turns = hydrateTurns([
    { role: 'user', content: 'Plan the change.' },
    { role: 'error', content: CAP, ...(meta ? { meta } : {}) },
  ])
  const seg = turns.flatMap((t) => t.segments).find((s) => s.kind === 'error')
  expect(seg).toBeTruthy()
  return seg as ErrorSegment
}

describe('a refused turn read back from history', () => {
  it('carries the Settings page its notice links', () => {
    const seg = errorOf({ settings: 'guardrails' })
    expect(seg.text).toBe(CAP)
    expect(seg.settings).toBe('guardrails')
  })

  it('an ordinary failure names no page', () => {
    expect(errorOf().settings).toBeUndefined()
  })
})
