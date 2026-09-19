/**
 * DSC-6 residual — the schema-driven provider form rides the shared form family.
 *
 * `ProviderConfigForm` was the last holdout of the raw-input drawdown: a local `inputCls`
 * chrome string that four bespoke controls consumed (an enum select, a numeric field, the
 * show/hide-secret field, the plain text field). Two things were wrong with that, and this
 * file is the rail for both:
 *
 *  1. THE CHROME WAS A COPY. `inputCls` re-spelled the family's own tokens with `px-3` in
 *     place of `px-m`, so the form silently opted out of the `--space-scale` token every
 *     other field honours: a user who scaled spacing moved every input in the app EXCEPT
 *     these. Asserted as rendered classes, because the invariant is what the browser sees.
 *  2. THE VISIBLE LABEL NAMED NOTHING. The form publishes its own `<label htmlFor>`, but a
 *     primitive's id was internal, so the JSON rows (already on TextArea before this change)
 *     had a caption that named them for sighted users alone. Every stacked branch must now
 *     resolve `label[for]` to a real control.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { SchemaField } from './ProviderConfigForm'
import type { ProviderSchemaProp } from '../../lib/api'

afterEach(() => cleanup())

function row(prop: ProviderSchemaProp, value: unknown = undefined) {
  return render(
    <SchemaField fieldKey="api_key" prop={prop} value={value} onChange={() => {}} />,
  ).container
}
function classOf(el: Element | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

// Every non-boolean branch, with the control the family should render for it.
const BRANCHES: [string, ProviderSchemaProp, 'input' | 'select' | 'textarea'][] = [
  ['plain text', { type: 'string' }, 'input'],
  ['numeric', { type: 'integer', minimum: 1, maximum: 600 }, 'input'],
  ['sensitive', { type: 'string', 'x-meta': { sensitive: true } }, 'input'],
  ['enum', { type: 'string', enum: ['a', 'b'] }, 'select'],
  ['structured JSON', { type: 'array' }, 'textarea'],
]

describe('the provider form renders no bespoke form chrome', () => {
  it.each(BRANCHES)('the %s branch rides the shared family, not a copied class string', (_n, prop, tag) => {
    const c = row(prop)
    const el = c.querySelector(tag)
    expect(el, `expected a <${tag}> for this branch`).not.toBeNull()
    const cls = classOf(el)
    // The family's spacing token — NOT the bespoke `px-3` the local chrome string used.
    expect(cls).not.toContain('px-3')
    expect(cls).toContain('rounded-md')
    // The form sits on a panel, so every field takes the `high` fill rather than re-spelling it.
    expect(cls).toContain('bg-surface-high')
  })

  it('the fixed-height branches take the md rung (the h-9 the bespoke chrome hardcoded)', () => {
    for (const prop of [
      { type: 'string' } as ProviderSchemaProp,
      { type: 'integer' } as ProviderSchemaProp,
      { type: 'string', 'x-meta': { sensitive: true } } as ProviderSchemaProp,
      { type: 'string', enum: ['a'] } as ProviderSchemaProp,
    ]) {
      const el = row(prop).querySelector('input, select')
      expect(classOf(el), `for ${JSON.stringify(prop)}`).toContain('h-9')
    }
  })

  it('the secret field keeps its trailing eye inside the primitive, and it still toggles', () => {
    const c = row({ type: 'string', 'x-meta': { sensitive: true } })
    const el = c.querySelector('input') as HTMLInputElement
    // The trailing control gets its own inset rather than a bespoke `pr-10` bolted on.
    expect(classOf(el)).toContain('pr-10')
    expect(el.type).toBe('password')
    const eye = c.querySelector('button') as HTMLButtonElement
    expect(eye.getAttribute('aria-label') ?? eye.title).toContain('Show')
    fireEvent.click(eye)
    expect((c.querySelector('input') as HTMLInputElement).type).toBe('text')
  })
})

describe('the visible label is programmatically bound on every stacked branch', () => {
  it.each(BRANCHES)('the %s branch resolves label[for] to its control', (_n, prop, tag) => {
    const c = row(prop)
    const label = c.querySelector('label')
    expect(label, 'the row publishes a visible label').not.toBeNull()
    const target = label!.getAttribute('for')
    expect(target, 'the label declares a target').toBeTruthy()
    // The bug this closes: `for` pointed at an id no element carried.
    const bound = c.querySelector(`#${CSS.escape(target!)}`)
    expect(bound, `label[for="${target}"] must resolve to a rendered control`).not.toBeNull()
    expect(bound!.tagName.toLowerCase()).toBe(tag)
  })
})
