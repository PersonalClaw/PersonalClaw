/**
 * A list setting takes the list you type or paste, and a save sends exactly what you entered.
 *
 * Mail Inbox's Allowed Senders is a list of addresses. Pasting the list (as JSON, as a line of
 * comma-separated addresses, or one per line) gave ONE entry holding the whole pasted text, which
 * matches no sender, so the inbox read no mail. And where a setting is a JSON editor (a record the
 * schema does not describe, or a stored value of another shape), text that did not parse left the
 * form holding the last value that did: Save closed the dialog as if saved and sent that old value,
 * an empty list included, with the user's text thrown away.
 *
 * Now a paste holding several entries adds each one, and a form whose JSON text does not parse
 * refuses to save, naming the field, until the text parses or is cleared.
 */
import { useState } from 'react'
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AppConfigFields, type SchemaProp } from './appConfigForm'
import { SchemaField } from '../settings/ProviderConfigForm'
import type { ProviderSchemaProp } from '../../lib/api'

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })
afterEach(() => cleanup())

const SENDERS: SchemaProp = {
  type: 'array', items: { type: 'string' }, default: [],
  'x-meta': { label: 'Allowed Senders' },
}

/** The Configure form as `useAppConfig` drives it: each edit comes back as the field's value. */
function renderSenders(stored: string[] = []) {
  const set = vi.fn()
  function Form() {
    const [cur, setCur] = useState<Record<string, unknown>>({ allow_senders: stored })
    return (
      <AppConfigFields appName="mail-inbox" props={{ allow_senders: SENDERS }} cur={cur}
        set={(k, v) => { set(k, v); setCur((c) => ({ ...c, [k]: v })) }} />
    )
  }
  render(<Form />)
  return { set, field: screen.getByRole('textbox', { name: /Allowed Senders/ }) }
}

describe('a pasted list', () => {
  it('as JSON adds each address', async () => {
    const { set, field } = renderSenders()
    await userEvent.click(field)
    await userEvent.paste('["talks@nights.example", "*@build.example", "sam@home.example"]')
    expect(set).toHaveBeenLastCalledWith('allow_senders', ['talks@nights.example', '*@build.example', 'sam@home.example'])
    expect(field).toHaveValue('')
  })

  it('as a comma-separated line adds each address', async () => {
    const { set, field } = renderSenders()
    await userEvent.click(field)
    await userEvent.paste('talks@nights.example, *@build.example,sam@home.example')
    expect(set).toHaveBeenLastCalledWith('allow_senders', ['talks@nights.example', '*@build.example', 'sam@home.example'])
  })

  it('one per line adds each address', async () => {
    const { set, field } = renderSenders()
    await userEvent.click(field)
    await userEvent.paste('talks@nights.example\n*@build.example\r\nsam@home.example\n')
    expect(set).toHaveBeenLastCalledWith('allow_senders', ['talks@nights.example', '*@build.example', 'sam@home.example'])
  })

  it('joins the addresses already there, once each', async () => {
    const { set, field } = renderSenders(['sam@home.example'])
    await userEvent.click(field)
    await userEvent.paste('sam@home.example, lee@home.example')
    expect(set).toHaveBeenLastCalledWith('allow_senders', ['sam@home.example', 'lee@home.example'])
  })

  it('of one address goes into the field to finish, as typing does', async () => {
    const { set, field } = renderSenders()
    await userEvent.click(field)
    await userEvent.paste('sam@home.example')
    expect(field).toHaveValue('sam@home.example')
    expect(set).not.toHaveBeenCalled()
    await userEvent.keyboard('{Enter}')
    expect(set).toHaveBeenLastCalledWith('allow_senders', ['sam@home.example'])
  })

  it('keeps a long number as the digits pasted', async () => {
    const { set, field } = renderSenders()
    await userEvent.click(field)
    await userEvent.paste('[1289011223344556677, 1052983746291048451]')
    expect(set).toHaveBeenLastCalledWith('allow_senders', ['1289011223344556677', '1052983746291048451'])
  })

  it('is taken the same way in Settings › Providers', async () => {
    const onChange = vi.fn()
    function Form() {
      const [v, setV] = useState<unknown>([])
      return <SchemaField fieldKey="allow_senders" prop={SENDERS as ProviderSchemaProp} value={v}
        onChange={(nv) => { onChange(nv); setV(nv) }} />
    }
    render(<Form />)
    await userEvent.click(screen.getByRole('textbox', { name: /Allowed Senders/ }))
    await userEvent.paste('a@one.example, b@two.example')
    expect(onChange).toHaveBeenLastCalledWith(['a@one.example', 'b@two.example'])
  })
})

// ── the save sends what is on screen ─────────────────────────────────────────────────────────

function mockApi(schema: unknown, config: unknown, saveAppConfig: unknown) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      appConfig: () => Promise.resolve({ schema, config, _secret_set: [], revision: 'rev-1' }),
      saveAppConfig,
    },
  }))
}

/** `useAppConfig` with its fields and a Save, as the Configure dialog wires them. */
async function mountForm(name: string) {
  const { useAppConfig, AppConfigFields: Fields } = await import('./appConfigForm')
  function Probe() {
    const cfg = useAppConfig(name)
    return (
      <div>
        <span data-testid="loading">{String(cfg.loading)}</span>
        <Fields appName={name} props={cfg.props} cur={cfg.cur} set={cfg.set} />
        <span data-testid="unparsed">{cfg.unparsedLabels.join('|')}</span>
        <button type="button" onClick={() => cfg.save()}>Save</button>
        <span data-testid="err">{cfg.err ?? ''}</span>
      </div>
    )
  }
  render(<Probe />)
  await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'))
}

describe('Save', () => {
  it('sends an address typed and not yet added', async () => {
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    mockApi({ properties: { allow_senders: SENDERS } }, { allow_senders: [] }, saveAppConfig)
    await mountForm('mail-inbox')

    await userEvent.type(screen.getByRole('textbox', { name: /Allowed Senders/ }), 'sam@home.example')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(saveAppConfig).toHaveBeenCalledTimes(1))
    expect(saveAppConfig).toHaveBeenCalledWith('mail-inbox', { allow_senders: ['sam@home.example'] }, 'rev-1')
  })

  const REACTIONS: SchemaProp = { type: 'object', 'x-meta': { label: 'Reactions' } }

  it('is refused while a JSON setting does not parse, naming it, and sends nothing', async () => {
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    mockApi({ properties: { reactions: REACTIONS } }, { reactions: { done: 'white_check_mark' } }, saveAppConfig)
    await mountForm('chat-app')

    const editor = screen.getByRole('textbox', { name: 'Reactions' })
    fireEvent.change(editor, { target: { value: '{"done": "white_check_mark", "seen": ' } })
    expect(screen.getByTestId('unparsed').textContent).toBe('Reactions')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.getByTestId('err').textContent).toContain('Reactions'))
    expect(saveAppConfig, 'the last value that parsed was sent in place of the text on screen').not.toHaveBeenCalled()
    expect(editor, 'the text typed is kept, to be fixed').toHaveValue('{"done": "white_check_mark", "seen": ')
  })

  it('goes through once the JSON parses', async () => {
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    mockApi({ properties: { reactions: REACTIONS } }, { reactions: {} }, saveAppConfig)
    await mountForm('chat-app')

    const editor = screen.getByRole('textbox', { name: 'Reactions' })
    fireEvent.change(editor, { target: { value: '{"seen": ' } })
    fireEvent.change(editor, { target: { value: '{"seen": "eyes"}' } })
    expect(screen.getByTestId('unparsed').textContent).toBe('')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(saveAppConfig).toHaveBeenCalledTimes(1))
    expect(saveAppConfig).toHaveBeenCalledWith('chat-app', { reactions: { seen: 'eyes' } }, 'rev-1')
  })
})

describe('Settings › Providers refuses the same save', () => {
  it('disables Save with the field named while its JSON does not parse', async () => {
    const saveProviderConfig = vi.fn(() => Promise.resolve({ config: {}, _secret_set: [], revision: 'rev-2' }))
    vi.doMock('../../lib/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        providerSchema: () => Promise.resolve({ properties: { reactions: { type: 'object', 'x-meta': { label: 'Reactions' } } } }),
        providerConfig: () => Promise.resolve({ config: { reactions: {} }, _secret_set: [], revision: 'rev-1' }),
        saveProviderConfig,
      },
    }))
    const { ProviderConfigForm } = await import('../settings/ProviderConfigForm')
    render(<ProviderConfigForm name="chat-app" />)
    const editor = await screen.findByRole('textbox', { name: 'Reactions' })

    fireEvent.change(editor, { target: { value: '{"seen": ' } })

    const save = screen.getByRole('button', { name: 'Save' })
    // Off with a reason, so it keeps its tab stop and says why (`ui/Button`'s `disabledReason`).
    expect(save.getAttribute('aria-disabled')).toBe('true')
    expect(save.getAttribute('title') ?? '', 'the reason names the field').toContain('Reactions')
    await userEvent.click(save)
    expect(saveProviderConfig).not.toHaveBeenCalled()
  })
})
