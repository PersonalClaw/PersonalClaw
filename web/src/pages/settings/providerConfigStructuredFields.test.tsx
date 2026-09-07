/**
 * A provider's structured settings are editable, and a stored secret says it is stored.
 *
 * Both found by driving the real Settings → Providers form against a live gateway while
 * fixing the Slack dashboard-config issues (#952 / #953):
 *
 * 1. An `array`/`object` schema field fell through to the text branch, whose `String(value)`
 *    painted slack-channel's **Allowed Users** as the literal
 *    `[object Object],[object Object],[object Object]`. The setting #953 is about was
 *    unreadable AND unfillable in the only UI that offers it — fixing the runtime read
 *    without this would have left the operator no way to write the value.
 * 2. Sensitive fields are masked on the wire now, so a masked field rendered as password
 *    dots would be indistinguishable from an empty one. It renders BLANK with an explicit
 *    "saved" placeholder instead — the treatment the Apps Configure dialog already used.
 */
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { SchemaField } from './ProviderConfigForm'
import type { ProviderSchemaProp } from '../../lib/api'

afterEach(() => cleanup())

function renderField(prop: ProviderSchemaProp, value: unknown, extra: { secretAlreadySet?: boolean } = {}) {
  const onChange = vi.fn()
  const { container } = render(
    <SchemaField fieldKey="allowed_users" prop={prop} value={value} onChange={onChange} {...extra} />,
  )
  return { container, onChange }
}

const ARRAY_PROP: ProviderSchemaProp = {
  type: 'array',
  'x-meta': { label: 'Allowed Users', help: 'Each entry: {slack_id, name}.' },
}

describe('a structured provider setting is editable as JSON', () => {
  it('renders an array of objects as JSON, not "[object Object]"', () => {
    const { container } = renderField(ARRAY_PROP, [{ slack_id: 'U_ALICE', name: 'Alice' }])
    const ta = container.querySelector('textarea') as HTMLTextAreaElement
    expect(ta).toBeTruthy()
    expect(ta.value).not.toContain('[object Object]')
    expect(JSON.parse(ta.value)).toEqual([{ slack_id: 'U_ALICE', name: 'Alice' }])
    expect(container.querySelector('input')).toBeNull()
  })

  it('an unset array starts as an empty array, so the operator can type into it', () => {
    const { container } = renderField(ARRAY_PROP, undefined)
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('[]')
  })

  it('a valid edit commits the parsed value', () => {
    const { container, onChange } = renderField(ARRAY_PROP, [])
    fireEvent.change(container.querySelector('textarea')!, {
      target: { value: '[{"slack_id":"U_BOB"}]' },
    })
    expect(onChange).toHaveBeenCalledWith([{ slack_id: 'U_BOB' }])
  })

  it('invalid JSON explains itself and commits NOTHING', () => {
    const { container, onChange } = renderField(ARRAY_PROP, [{ slack_id: 'U_ALICE' }])
    fireEvent.change(container.querySelector('textarea')!, { target: { value: '[{"slack_id":' } })
    // Never commit a half-typed entry: a partial parse would save `{}` over a working
    // allowlist, which is worse than refusing.
    expect(onChange).not.toHaveBeenCalled()
    expect(container.textContent).toContain('⚠')
    expect(container.textContent).toContain('Each entry')
  })

  it('an object field gets the same editor, seeded as {}', () => {
    const { container } = renderField({ type: 'object', 'x-meta': { label: 'Reactions' } }, undefined)
    expect((container.querySelector('textarea') as HTMLTextAreaElement).value).toBe('{}')
  })
})

describe('a stored secret is legible as stored', () => {
  const SECRET: ProviderSchemaProp = { type: 'string', 'x-meta': { label: 'Bot Token', sensitive: true } }

  it('a set secret renders blank with a "saved" placeholder, never the value', () => {
    const { container } = renderField(SECRET, '', { secretAlreadySet: true })
    const el = container.querySelector('input') as HTMLInputElement
    expect(el.type).toBe('password')
    expect(el.value).toBe('')
    expect(el.placeholder).toBe('saved — leave blank to keep')
  })

  it('an UNSET secret keeps the neutral placeholder — "not configured" is not a secret', () => {
    const { container } = renderField(SECRET, '')
    expect((container.querySelector('input') as HTMLInputElement).placeholder).not.toMatch(/saved/)
  })
})
