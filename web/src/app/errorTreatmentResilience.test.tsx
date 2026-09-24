/**
 * PERSONALITY-THEMES §S2 (T2.3) — the error surface must not be able to fail.
 *
 * An `ErrorBoundary` cannot catch a throw from its own fallback: the error escapes
 * upward, so a decorative lookup that throws while the fallback is rendering turns
 * ONE broken page into a blank app. That is a strictly worse outcome than having no
 * personality feature at all, which makes it the risk this atom actually carries.
 *
 * So the guarantee is asserted, not reasoned about: with the personality registry
 * itself throwing, the fallback still renders — and renders the UNTREATED default,
 * because "no treatment" is the safe answer to "I could not resolve one".
 *
 * The registry is mocked at module scope, so this lives in its own file (the skin
 * tests next door need the real one).
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ErrorBoundary } from './ErrorBoundary'

/** Counts the throws. Without this the test would pass even if the mock were
 *  inert — an untreated fallback is also what a HEALTHY default renders, so
 *  "it rendered" proves nothing on its own. */
const probe = vi.hoisted(() => ({ throws: 0 }))

vi.mock('../design/personalities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../design/personalities')>()
  return {
    ...actual,
    // The provider-less path in `usePersonality` resolves the default identity
    // through this function, so throwing here throws inside the hook read.
    resolvePersonality: () => {
      probe.throws += 1
      throw new Error('the personality registry exploded')
    },
  }
})

function Boom(): never {
  throw new Error('kaboom')
}

describe('a throwing personality lookup cannot take the app down', () => {
  it('renders the fallback, untreated, instead of propagating', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    // No provider: `usePersonality` takes its tolerant branch, which calls the
    // mocked (throwing) resolver. Nothing here may reach the caller.
    expect(() =>
      render(
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>,
      ),
    ).not.toThrow()

    expect(probe.throws, 'the registry never threw — this test proves nothing').toBeGreaterThan(0)
    expect(screen.getByText('This page hit an error')).toBeTruthy()
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
    // Untreated: the alert glyph keeps the base ink class, so nothing half-applied
    // a skin on the way to failing.
    const icon = screen.getByText('This page hit an error').parentElement?.querySelector('svg')
    expect(icon?.getAttribute('class')).toContain('text-on-surface-low')
    spy.mockRestore()
  })
})
