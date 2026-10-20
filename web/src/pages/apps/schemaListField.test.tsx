/**
 * A settings list whose schema says what its entries are is edited as a list, not typed as JSON.
 *
 * Both schema-driven forms (Apps › Configure and Settings › Providers) rendered every `array`
 * field as a JSON text area, whatever its schema declared. So the Slack channel's Allowed Users
 * asked for `[{"slack_id": …, "name": …}]` typed by hand, and a mailbox's allowed senders for a
 * JSON list of strings. A list of texts is now chips, and a list of records is a row per entry
 * with a control per field. A field whose schema says nothing about its entries, or a stored
 * value of another shape, keeps the JSON editor — the form never rewrites what it cannot show.
 */
import { useState } from 'react'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react'
import { AppConfigFields, type SchemaProp } from './appConfigForm'
import { SchemaField } from '../settings/ProviderConfigForm'
import type { ProviderSchemaProp } from '../../lib/api'

afterEach(() => cleanup())

const USERS: SchemaProp = {
  type: 'array',
  items: {
    type: 'object',
    'x-meta': { label: 'user' },
    properties: {
      slack_id: { type: 'string', 'x-meta': { label: 'Slack user ID' } },
      name: { type: 'string', 'x-meta': { label: 'Name' } },
    },
    required: ['slack_id'],
  },
  'x-meta': { label: 'Allowed Users' },
}

const OPEN_CHANNELS: SchemaProp = {
  type: 'array',
  items: { type: 'string' },
  'x-meta': { label: 'Open Channels' },
}

/** The Configure form as `useAppConfig` drives it: an edit comes back as the field's new value,
 *  and Discard puts the stored copy back, as discarding an edit refused as stale does. */
function renderApp(props: Record<string, SchemaProp>, stored: Record<string, unknown>) {
  const set = vi.fn()
  function Form() {
    const [cur, setCur] = useState(stored)
    return (
      <>
        <AppConfigFields appName="chat-app" props={props} cur={cur}
          set={(k, v) => { set(k, v); setCur((c) => ({ ...c, [k]: v })) }} />
        <button type="button" onClick={() => setCur(stored)}>Discard</button>
      </>
    )
  }
  render(<Form />)
  return set
}

describe('a list of records', () => {
  it('shows a row per entry with a named control per field, not JSON', () => {
    renderApp({ allowed_users: USERS }, { allowed_users: [{ slack_id: 'U100', name: 'Robin' }] })

    expect(document.querySelector('textarea')).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Slack user ID, user 1' })).toHaveValue('U100')
    expect(screen.getByRole('textbox', { name: 'Name, user 1' })).toHaveValue('Robin')
    expect(screen.getByRole('textbox', { name: 'Slack user ID, user 1' })).toHaveAttribute('aria-required', 'true')
  })

  it('saves an edited field, keeping what the schema does not describe', () => {
    const set = renderApp(
      { allowed_users: USERS },
      { allowed_users: [{ slack_id: 'U100', name: 'Robin', note: 'kept' }] },
    )
    fireEvent.change(screen.getByRole('textbox', { name: 'Name, user 1' }), { target: { value: 'Robin Ash' } })
    expect(set).toHaveBeenLastCalledWith('allowed_users', [{ slack_id: 'U100', name: 'Robin Ash', note: 'kept' }])
  })

  it('adds a row to fill in, and saves it only once it holds something', () => {
    const set = renderApp({ allowed_users: USERS }, { allowed_users: [] })
    fireEvent.click(screen.getByRole('button', { name: /Add user/ }))

    const id = screen.getByRole('textbox', { name: 'Slack user ID, user 1' })
    expect(set).toHaveBeenLastCalledWith('allowed_users', [])
    fireEvent.change(id, { target: { value: 'U200' } })
    expect(set).toHaveBeenLastCalledWith('allowed_users', [{ slack_id: 'U200' }])
  })

  it('removes the row asked for', () => {
    const set = renderApp(
      { allowed_users: USERS },
      { allowed_users: [{ slack_id: 'U1', name: 'Ash' }, { slack_id: 'U2', name: 'Birch' }] },
    )
    fireEvent.click(screen.getByRole('button', { name: 'Remove user 1' }))
    expect(set).toHaveBeenLastCalledWith('allowed_users', [{ slack_id: 'U2', name: 'Birch' }])
    expect(screen.getByRole('textbox', { name: 'Slack user ID, user 1' })).toHaveValue('U2')
  })

  it('shows the stored copy again once an edit is discarded, and a later edit starts from it', () => {
    const set = renderApp({ allowed_users: USERS }, { allowed_users: [{ slack_id: 'U1', name: 'Ash' }] })
    fireEvent.click(screen.getByRole('button', { name: /Add user/ }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Slack user ID, user 2' }), { target: { value: 'U2' } })
    expect(set).toHaveBeenLastCalledWith('allowed_users', [{ slack_id: 'U1', name: 'Ash' }, { slack_id: 'U2' }])

    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(screen.queryByRole('textbox', { name: 'Slack user ID, user 2' })).toBeNull()
    fireEvent.change(screen.getByRole('textbox', { name: 'Name, user 1' }), { target: { value: 'Ash Grey' } })
    expect(set).toHaveBeenLastCalledWith('allowed_users', [{ slack_id: 'U1', name: 'Ash Grey' }])
  })
})

describe('a list of texts', () => {
  it('is chips, and a typed entry is added on Enter', () => {
    const set = renderApp({ open_channels: OPEN_CHANNELS }, { open_channels: ['C100'] })
    expect(document.querySelector('textarea')).toBeNull()
    expect(screen.getByText('C100')).toBeInTheDocument()

    const field = screen.getByRole('textbox', { name: /Open Channels/ })
    fireEvent.change(field, { target: { value: 'C200' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(set).toHaveBeenLastCalledWith('open_channels', ['C100', 'C200'])
  })
})

describe('what stays the JSON editor', () => {
  it('a list whose schema does not describe its entries', () => {
    renderApp({ ids: { type: 'array', 'x-meta': { label: 'IDs' } } }, { ids: ['a'] })
    expect(screen.getByRole('textbox', { name: 'IDs' }).tagName).toBe('TEXTAREA')
  })

  it('a stored value of another shape than the schema declares', () => {
    renderApp({ open_channels: OPEN_CHANNELS }, { open_channels: [{ channel_id: 'C300' }] })
    expect(screen.getByRole('textbox', { name: 'Open Channels' }).tagName).toBe('TEXTAREA')
  })
})

describe('Settings › Providers edits the same lists the same way', () => {
  it('renders a list of records as rows, with the caption naming the group', () => {
    const onChange = vi.fn()
    render(
      <SchemaField fieldKey="allowed_users" prop={USERS as ProviderSchemaProp} value={[{ slack_id: 'U100', name: 'Robin' }]}
        onChange={onChange} />,
    )
    expect(document.querySelector('textarea')).toBeNull()
    const group = screen.getByRole('group', { name: 'Allowed Users' })
    fireEvent.change(within(group).getByRole('textbox', { name: 'Slack user ID, user 1' }), { target: { value: 'U300' } })
    expect(onChange).toHaveBeenLastCalledWith([{ slack_id: 'U300', name: 'Robin' }])
    // No `<label for>` pointing at a control that does not exist.
    expect(document.querySelector('label[for]')).toBeNull()
  })
})
