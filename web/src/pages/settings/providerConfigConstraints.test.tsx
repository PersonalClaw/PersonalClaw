/**
 * #616 / #491 — a provider settingsSchema's declared constraints reach the browser.
 *
 * The provider write path now enforces the string constraints it previously ignored, so the
 * form has to state them too — otherwise a refusal is the user's first feedback, which is
 * exactly #491's complaint. `minimum`/`maximum` were already mirrored; these are the ones the
 * backend gained.
 *
 * `pattern` is withheld on a sensitive field (matching the app form): a regex on a secret
 * fights a paste, and the browser's own bubble would describe the secret's shape.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { SchemaField } from './ProviderConfigForm'
import type { ProviderSchemaProp } from '../../lib/api'

afterEach(() => cleanup())

function field(prop: ProviderSchemaProp) {
  const { container } = render(
    <SchemaField fieldKey="api_key" prop={prop} value={undefined} onChange={() => {}} />,
  )
  return container.querySelector('input') as HTMLInputElement
}

describe('provider config form mirrors declared constraints', () => {
  it('string constraints land as minLength/maxLength/pattern on a text input', () => {
    const el = field({ type: 'string', minLength: 8, maxLength: 40, pattern: '^sk-' })
    expect(el.type).toBe('text')
    expect(el.minLength).toBe(8)
    expect(el.maxLength).toBe(40)
    expect(el.pattern).toBe('^sk-')
  })

  it('numeric bounds still land as min/max', () => {
    const el = field({ type: 'integer', minimum: 1, maximum: 600 })
    expect(el.type).toBe('number')
    expect(el.min).toBe('1')
    expect(el.max).toBe('600')
  })

  it('a sensitive field takes the length bounds but not the pattern', () => {
    const el = field({
      type: 'string',
      minLength: 8,
      pattern: '^sk-',
      'x-meta': { sensitive: true },
    })
    expect(el.type).toBe('password')
    expect(el.minLength).toBe(8)
    expect(el.getAttribute('pattern')).toBeNull()
  })

  it('an unconstrained field renders no stray attributes', () => {
    const el = field({ type: 'string' })
    expect(el.getAttribute('pattern')).toBeNull()
    expect(el.getAttribute('minlength')).toBeNull()
    expect(el.getAttribute('maxlength')).toBeNull()
  })
})
