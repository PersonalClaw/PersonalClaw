/**
 * A settings object whose schema says what it holds is edited with controls, not typed as JSON.
 *
 * Lists got rows and chips (`schemaListField`), but every `object` field stayed a JSON text area:
 * the Slack channel's Reactions asked for `{"thinking": "brain", "done": null}` and its per-channel
 * settings for `{"C0123": {"activation": "always", "agent": ""}}`, typed by hand. Two shapes of
 * object are edited as forms now, both plain JSON Schema:
 *
 * - named fields (`properties`): one control per field. A field that may be `null` has a switch;
 *   off stores `null`. A text left blank is left out, so the app's default applies.
 * - entries by key (`additionalProperties`, the key described by `propertyNames`): a row per entry,
 *   its key and its value's fields, with Remove per row and Add below.
 *
 * An object whose schema says neither, or a stored value of another shape, keeps the JSON editor.
 */
import { useState } from 'react'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react'
import { AppConfigFields, type SchemaProp } from './appConfigForm'
import { objectFieldKind } from './schemaObjectField'
import { SchemaField } from '../settings/ProviderConfigForm'
import type { ProviderSchemaProp } from '../../lib/api'

afterEach(() => cleanup())

const phase = (label: string, emoji: string): SchemaProp => ({
  type: ['string', 'null'], 'x-meta': { label, placeholder: emoji },
})

const REACTIONS: SchemaProp = {
  type: 'object',
  properties: { queued: phase('Queued', 'eyes'), thinking: phase('Thinking', 'thinking_face'), done: phase('Done', 'white_check_mark') },
  additionalProperties: false,
  'x-meta': { label: 'Reactions' },
}

const CHANNELS: SchemaProp = {
  type: 'object',
  propertyNames: { type: 'string', 'x-meta': { label: 'Channel ID', placeholder: 'C0123456789' } },
  additionalProperties: {
    type: 'object',
    'x-meta': { label: 'channel' },
    properties: {
      activation: { type: 'string', enum: ['always', 'mention', 'off'], default: 'mention', 'x-meta': { label: 'Activation' } },
      agent: { type: 'string', 'x-meta': { label: 'Agent' } },
    },
  },
  'x-meta': { label: 'Per-channel Config' },
}

/** The Configure form as `useAppConfig` drives it, with Discard putting the stored copy back. */
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

describe('which objects get a form', () => {
  it('named fields, and entries by key; nothing else', () => {
    expect(objectFieldKind(REACTIONS)).toBe('fields')
    expect(objectFieldKind(CHANNELS)).toBe('entries')
    expect(objectFieldKind({ type: 'object' })).toBeNull()
    expect(objectFieldKind({ type: 'object', additionalProperties: { type: 'object' } })).toBeNull()
    expect(objectFieldKind({ type: 'object', properties: { deep: { type: 'object' } } })).toBeNull()
  })
})

describe('an object of named fields', () => {
  it('is a control per field, showing what is stored and each default as its hint', () => {
    renderApp({ reactions: REACTIONS }, { reactions: { thinking: 'brain', done: null } })
    expect(document.querySelector('textarea')).toBeNull()
    const group = screen.getByRole('group', { name: 'Reactions' })
    expect(within(group).getByRole('textbox', { name: 'Thinking' })).toHaveValue('brain')
    expect(within(group).getByRole('textbox', { name: 'Queued' })).toHaveValue('')
    expect(within(group).getByRole('textbox', { name: 'Queued' })).toHaveAttribute('placeholder', 'eyes')
    expect(within(group).getByRole('switch', { name: 'Done on' })).toHaveAttribute('aria-checked', 'false')
    expect(within(group).getByRole('switch', { name: 'Queued on' })).toHaveAttribute('aria-checked', 'true')
  })

  it('saves a typed value, and leaves a cleared one out so its default applies', () => {
    const set = renderApp({ reactions: REACTIONS }, { reactions: { thinking: 'brain', done: null } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Queued' }), { target: { value: 'wave' } })
    expect(set).toHaveBeenLastCalledWith('reactions', { thinking: 'brain', done: null, queued: 'wave' })
    fireEvent.change(screen.getByRole('textbox', { name: 'Thinking' }), { target: { value: '' } })
    expect(set).toHaveBeenLastCalledWith('reactions', { done: null, queued: 'wave' })
  })

  it('switches a field off as null, and back on to its default', () => {
    const set = renderApp({ reactions: REACTIONS }, { reactions: { done: null } })
    fireEvent.click(screen.getByRole('switch', { name: 'Queued on' }))
    expect(set).toHaveBeenLastCalledWith('reactions', { done: null, queued: null })
    fireEvent.click(screen.getByRole('switch', { name: 'Done on' }))
    expect(set).toHaveBeenLastCalledWith('reactions', { queued: null })
  })
})

describe('an object of entries by key', () => {
  it('is a row per entry: its key, then its fields', () => {
    renderApp({ channels: CHANNELS }, { channels: { C100: { activation: 'always', agent: 'Scout' } } })
    expect(document.querySelector('textarea')).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Channel ID, channel 1' })).toHaveValue('C100')
    expect(screen.getByRole('combobox', { name: 'Activation, channel 1' })).toHaveValue('always')
    expect(screen.getByRole('textbox', { name: 'Agent, channel 1' })).toHaveValue('Scout')
  })

  it('adds an entry, saved once it has a key', () => {
    const set = renderApp({ channels: CHANNELS }, { channels: {} })
    fireEvent.click(screen.getByRole('button', { name: /Add channel/ }))
    expect(set).toHaveBeenLastCalledWith('channels', {})
    fireEvent.change(screen.getByRole('textbox', { name: 'Channel ID, channel 1' }), { target: { value: 'C200' } })
    expect(set).toHaveBeenLastCalledWith('channels', { C200: { activation: 'mention' } })
  })

  it('renames and removes entries, keeping what the schema does not describe', () => {
    const set = renderApp(
      { channels: CHANNELS },
      { channels: { C1: { activation: 'off', note: 'kept' }, C2: { activation: 'always' } } },
    )
    fireEvent.change(screen.getByRole('textbox', { name: 'Channel ID, channel 1' }), { target: { value: 'C9' } })
    expect(set).toHaveBeenLastCalledWith('channels', { C9: { activation: 'off', note: 'kept' }, C2: { activation: 'always' } })
    fireEvent.click(screen.getByRole('button', { name: 'Remove channel 2' }))
    expect(set).toHaveBeenLastCalledWith('channels', { C9: { activation: 'off', note: 'kept' } })
  })

  it('says a key listed twice is not saved twice', () => {
    const set = renderApp({ channels: CHANNELS }, { channels: { C1: { activation: 'off' } } })
    fireEvent.click(screen.getByRole('button', { name: /Add channel/ }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Channel ID, channel 2' }), { target: { value: 'C1' } })
    expect(set).toHaveBeenLastCalledWith('channels', { C1: { activation: 'off' } })
    const second = screen.getByRole('group', { name: 'channel 2' })
    expect(within(second).getByText(/C1 is listed above, so this channel is not saved/)).toBeTruthy()
  })
})

describe('what stays the JSON editor', () => {
  it('an object whose schema says nothing of what it holds', () => {
    renderApp({ extra: { type: 'object', 'x-meta': { label: 'Extra' } } }, { extra: { a: 1 } })
    expect(screen.getByRole('textbox', { name: 'Extra' }).tagName).toBe('TEXTAREA')
  })

  it('a stored value the form could not show as it is', () => {
    renderApp({ reactions: REACTIONS }, { reactions: { sleeping: 'zzz' } })
    expect(screen.getByRole('textbox', { name: 'Reactions' }).tagName).toBe('TEXTAREA')
  })
})

describe('Settings › Providers edits the same objects the same way', () => {
  it('renders named fields and entries by key, with no JSON box', () => {
    const onReactions = vi.fn()
    const onChannels = vi.fn()
    render(
      <>
        <SchemaField fieldKey="reactions" prop={REACTIONS as ProviderSchemaProp} value={{}} onChange={onReactions} />
        <SchemaField fieldKey="channels" prop={CHANNELS as ProviderSchemaProp} value={{}} onChange={onChannels} />
      </>,
    )
    expect(document.querySelector('textarea')).toBeNull()
    fireEvent.change(within(screen.getByRole('group', { name: 'Reactions' })).getByRole('textbox', { name: 'Done' }),
      { target: { value: 'tada' } })
    expect(onReactions).toHaveBeenLastCalledWith({ done: 'tada' })
    fireEvent.click(within(screen.getByRole('group', { name: 'Per-channel Config' })).getByRole('button', { name: /Add channel/ }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Channel ID, channel 1' }), { target: { value: 'C7' } })
    expect(onChannels).toHaveBeenLastCalledWith({ C7: { activation: 'mention' } })
    expect(document.querySelector('label[for]')).toBeNull()
  })
})
