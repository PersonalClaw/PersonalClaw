/**
 * An automation's action field the provider schema marks `sensitive` is typed as a credential.
 *
 * The automation form renders an action's settings from the provider's schema, and a webhook URL
 * or a password was a plain text box: the value showed on screen as it was typed. It is a password
 * input now, as the Providers form's secret fields are, and an ordinary field is unchanged. What
 * a read hands back for a held field is the mask, which the save sends back to keep the value.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { SchemaField } from '../tools/schema'

afterEach(() => cleanup())

function input(schema: Record<string, unknown>, value = '') {
  const { container } = render(
    <SchemaField name="url" schema={schema} required={false} value={value} onChange={() => {}} />,
  )
  return container.querySelector('input') as HTMLInputElement
}

describe('a sensitive action field', () => {
  it('is a password input, kept out of autofill', () => {
    const el = input({ type: 'string', 'x-meta': { label: 'Webhook URL', sensitive: true } }, '••••••••')
    expect(el.type).toBe('password')
    expect(el.autocomplete).toBe('off')
    expect(el.value).toBe('••••••••')
  })

  it('leaves an ordinary field a text input', () => {
    const el = input({ type: 'string', 'x-meta': { label: 'Method' } })
    expect(el.type).toBe('text')
  })
})
