/**
 * A long numeric ID typed into a text setting is sent exactly as typed, from both settings forms.
 *
 * A Discord application ID (19 digits) is past what a JavaScript number holds exactly: as a
 * number, 1289011223344556677 is 1289011223344556800. A text setting keeps it as text all the way:
 * the form, the request body and the stored file. This pins that for the two forms that edit an
 * app's settings, and for the list and JSON editors, which read an ID as text too
 * (`aListSettingSavesWhatYouEntered.test.tsx`, `appConfigForm.test.ts`).
 */
import { useState } from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AppConfigFields, type SchemaProp } from './appConfigForm'
import { SchemaField } from '../settings/ProviderConfigForm'
import type { ProviderSchemaProp } from '../../lib/api'

afterEach(() => cleanup())

const ID = '1289011223344556677'
const APPLICATION_ID: SchemaProp = { type: 'string', default: '', 'x-meta': { label: 'Application ID' } }

describe('a long numeric ID in a text setting', () => {
  it('reaches Settings › Providers as the digits typed', async () => {
    const seen: unknown[] = []
    function Form() {
      const [v, setV] = useState<unknown>('')
      return <SchemaField fieldKey="application_id" prop={APPLICATION_ID as ProviderSchemaProp} value={v}
        onChange={(nv) => { seen.push(nv); setV(nv) }} />
    }
    render(<Form />)
    await userEvent.type(screen.getByRole('textbox', { name: 'Application ID' }), ID)
    expect(seen.at(-1)).toBe(ID)
    expect(screen.getByRole('textbox', { name: 'Application ID' })).toHaveValue(ID)
  })

  it('typed bare into a JSON setting is refused with the fix, and a stored value is not blamed on the user', () => {
    const VARIABLES = { type: 'object', 'x-meta': { label: 'Variables' } } as ProviderSchemaProp
    function Form({ start }: { start: unknown }) {
      const [v, setV] = useState<unknown>(start)
      return <SchemaField fieldKey="vars" prop={VARIABLES} value={v} onChange={setV} />
    }
    // As the browser holds a stored number that long: already rounded when it read the config.
    // Telling the user to quote it would quote digits that are not the stored ones.
    const { container } = render(<Form start={{ channel: 1289011223344556800 }} />)
    expect(container.textContent).not.toContain('⚠')
    fireEvent.change(screen.getByRole('textbox', { name: 'Variables' }), { target: { value: `{"channel": ${ID}}` } })
    expect(container.textContent).toContain(`⚠ ${ID} has more digits than a number here can keep`)
  })

  it('reaches Apps › Configure as the digits typed', async () => {
    const seen: unknown[] = []
    function Form() {
      const [cur, setCur] = useState<Record<string, unknown>>({ application_id: '' })
      return <AppConfigFields appName="discord-channel" props={{ application_id: APPLICATION_ID }} cur={cur}
        set={(k, v) => { seen.push(v); setCur((c) => ({ ...c, [k]: v })) }} />
    }
    render(<Form />)
    await userEvent.type(screen.getByRole('textbox', { name: 'Application ID' }), ID)
    expect(seen.at(-1)).toBe(ID)
  })
})
