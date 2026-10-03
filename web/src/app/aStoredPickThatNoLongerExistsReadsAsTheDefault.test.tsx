/**
 * An appearance pick the registry no longer offers reads as the token's DEFAULT, everywhere.
 *
 * The dot shapes are the live case. A shape can be removed from the picker, and a browser that had
 * chosen it still holds the pick in `localStorage['appearance']`. The store used to hand that pick
 * on verbatim, so the canvas bridge, the `--dot-shape` var and the Settings picker were all given a
 * value none of them could show: no glyph drew it and no pill was pressed. The store now drops it
 * as it loads, so the default is in effect in all three places, and the dropped pick is not
 * written back. A pick that IS still offered is kept exactly as before.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

// The store fetches saved themes on mount and nothing here cares. Left PENDING deliberately (see
// motionSliders.test.tsx): a promise settling after render lands a setState outside act().
vi.mock('../lib/api', () => ({
  api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) },
}))

const { AppearanceProvider, useAppearance } = await import('./appearance')
const { SelectControl } = await import('../ui/TokenControls')
const { TOKENS } = await import('../design/tokenRegistry')
const { runtime } = await import('../design/runtime')
type SelectToken = import('../design/tokenRegistry').SelectToken

const SELECTS = TOKENS.filter((t): t is SelectToken => t.kind === 'select')
const DOT_SHAPE = SELECTS.find((t) => t.varName === '--dot-shape')!
/** What the canvas bridge holds before any store has written it: its own declared default. */
const BRIDGE_DEFAULT = runtime.dotShape
const RUNTIME_SNAPSHOT = { ...runtime }

/** A pick no select token has ever offered. */
const UNKNOWN = 'retired-shape'

function seed(selects: Record<string, string>) {
  localStorage.setItem('appearance', JSON.stringify({ selects }))
}

const stored = () => JSON.parse(localStorage.getItem('appearance') ?? '{}') as { selects?: Record<string, string> }

beforeEach(() => localStorage.clear())
afterEach(() => {
  Object.assign(runtime, RUNTIME_SNAPSHOT)
  localStorage.clear()
  document.documentElement.removeAttribute('style')
})

describe('the default dot shape', () => {
  it('is the claw, PersonalClaw’s own mark, in the registry and in the canvas bridge alike', () => {
    expect(DOT_SHAPE.value).toBe('claw')
    expect(BRIDGE_DEFAULT, 'the bridge and the registry disagree about the default').toBe(DOT_SHAPE.value)
  })
})

describe('a stored dot shape that no longer exists', () => {
  it('reads as the default on the canvas, in the CSS var and in the picker', () => {
    seed({ '--dot-shape': UNKNOWN })
    render(<AppearanceProvider><SelectControl token={DOT_SHAPE} /></AppearanceProvider>)
    expect(runtime.dotShape, 'the canvas was handed a shape no glyph draws').toBe('claw')
    expect(document.documentElement.style.getPropertyValue('--dot-shape')).toBe('claw')
    const pressed = screen.getAllByRole('button', { pressed: true }).map((b) => b.getAttribute('aria-label'))
    expect(pressed, 'the picker should show the default chosen').toEqual(['Dot shape: claw'])
  })

  it('is not written back', () => {
    seed({ '--dot-shape': UNKNOWN })
    render(<AppearanceProvider><SelectControl token={DOT_SHAPE} /></AppearanceProvider>)
    expect(stored().selects?.['--dot-shape']).toBeUndefined()
  })

  it('while one the picker still offers is kept', () => {
    seed({ '--dot-shape': 'sparkle' })
    render(<AppearanceProvider><SelectControl token={DOT_SHAPE} /></AppearanceProvider>)
    expect(runtime.dotShape).toBe('sparkle')
    expect(document.documentElement.style.getPropertyValue('--dot-shape')).toBe('sparkle')
    expect(screen.getByRole('button', { name: 'Dot shape: sparkle' }).getAttribute('aria-pressed')).toBe('true')
    expect(stored().selects?.['--dot-shape']).toBe('sparkle')
  })
})

describe('every select token resolves a stored pick the same way', () => {
  let read: (t: SelectToken) => string = () => ''
  function Probe() {
    const { selectValue } = useAppearance()
    read = selectValue
    return null
  }

  it.each(SELECTS.map((t) => [t.varName, t] as const))('%s', (_name, token) => {
    // A non-default option, so "kept" cannot pass by reading the default.
    const other = token.options.find((o) => o !== token.value)!
    seed({ [token.varName]: UNKNOWN })
    const first = render(<AppearanceProvider><Probe /></AppearanceProvider>)
    expect(read(token), 'an unknown pick did not fall back to the default').toBe(token.value)
    first.unmount()

    seed({ [token.varName]: other })
    render(<AppearanceProvider><Probe /></AppearanceProvider>)
    expect(read(token), 'an offered pick was not kept').toBe(other)
  })
})
