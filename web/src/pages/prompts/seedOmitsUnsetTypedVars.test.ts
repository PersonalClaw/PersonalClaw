import { describe, expect, it } from 'vitest'
import { seedRenderValues, omitWhenBlank } from './promptMeta'
import type { PromptVariable } from '../../lib/api'

/** #377 — the palette must not invent a value the render engine rejects.
 *
 *  `_build_value_ctx` (prompt_providers/engine.py) treats an ABSENT variable correctly:
 *  it applies `var.default`, or leaves an optional one unset. A seeded `''` is strictly
 *  worse than absent — `int('')` raises and `''` matches no `select` option — so every
 *  render 400s. For an OPTIONAL number/select that failure is unrecoverable through the
 *  UI: the submit gate only checks REQUIRED variables, so Insert/Send stay enabled and
 *  silently never insert. */

const v = (over: Partial<PromptVariable>): PromptVariable => ({
  name: 'x', type: 'text', required: false, ...over,
})

describe('seedRenderValues omits the types the engine cannot coerce from ""', () => {
  it('a number with NO default gets no key at all', () => {
    const out = seedRenderValues([v({ name: 'cap', type: 'number', required: true })])
    expect('cap' in out).toBe(false)
  })

  it('a select with NO default gets no key at all', () => {
    const out = seedRenderValues([v({ name: 'mode', type: 'select', options: ['fast', 'thorough'] })])
    expect('mode' in out).toBe(false)
  })

  it('an OPTIONAL number with a default is seeded WITH the default, not ""', () => {
    // The trap in the report: the default was never shadowed — it is checked first.
    // This pins that the omission does not regress it.
    const out = seedRenderValues([v({ name: 'hours', type: 'number', default: 6 })])
    expect(out.hours).toBe(6)
  })

  it('text and textarea KEEP "" — an empty string is a value a user may mean', () => {
    const out = seedRenderValues([
      v({ name: 's', type: 'text' }),
      v({ name: 't', type: 'textarea' }),
    ])
    expect(out.s).toBe('')
    expect(out.t).toBe('')
  })

  it('boolean still seeds false', () => {
    expect(seedRenderValues([v({ name: 'b', type: 'boolean' })]).b).toBe(false)
  })

  it('the omission survives the WIRE — the POST body carries no key', () => {
    // This is the property that actually fixes it: `api.renderPrompt` JSON-encodes the
    // map, and JSON.stringify drops an absent key AND an explicit `undefined`. Both
    // "never filled in" and "cleared the field" therefore reach the engine as absent.
    const seeded = seedRenderValues([v({ name: 'cap', type: 'number', required: true }), v({ name: 'q', type: 'text' })])
    expect(JSON.parse(JSON.stringify({ variables: seeded }))).toEqual({ variables: { q: '' } })
    const cleared = { ...seeded, cap: undefined }
    expect(JSON.parse(JSON.stringify({ variables: cleared }))).toEqual({ variables: { q: '' } })
  })
})

describe('omitWhenBlank names exactly the uncoercible types', () => {
  it.each([['number', true], ['select', true], ['text', false], ['textarea', false], ['boolean', false]] as const)(
    '%s → %s', (type, expected) => {
      expect(omitWhenBlank(v({ type }))).toBe(expected)
    })
})
