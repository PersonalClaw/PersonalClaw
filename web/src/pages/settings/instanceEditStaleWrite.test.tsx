import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ProviderInstance, ProviderSchemaProp, SettingsProvider } from '../../lib/api'

// ── An instance's settings are saved over the copy its editor was opened on ─────────────────────
//
// `PUT /api/providers/{name}/instances/{id}` replaces the instance's whole config, and the editor
// used to PUT the copy the card listed — so an editor opened before another tab saved this
// instance put its stale values back over that save. The save now names the revision the list
// reported for the instance; a stale one is refused, the editor stays open with what the user
// typed, and the edit can be re-applied field by field on top of what is stored.

const providerInstances = vi.fn()
const updateProviderInstance = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<typeof import('../../lib/api')>()),
  api: {
    providerSchema: () => Promise.resolve(SCHEMA),
    providerInstances: (name: string) => providerInstances(name),
    updateProviderInstance: (name: string, id: string, config: Record<string, unknown>, base: string) =>
      updateProviderInstance(name, id, config, base),
    testProviderInstance: vi.fn(),
    deleteProviderInstance: vi.fn(),
    enableProvider: vi.fn(),
    disableProvider: vi.fn(),
  },
}))

import { MultiInstanceCard } from './MultiInstanceCard'
import { ApiError } from '../../lib/api'
import { resetDataStore } from '../../lib/data'

const text = (label: string, sensitive = false): ProviderSchemaProp =>
  ({ type: 'string', 'x-meta': { label, ...(sensitive ? { sensitive: true } : {}) } })
const SCHEMA = {
  type: 'object',
  properties: { api_key: text('API Key', true), default_model: text('Default Model'), region: text('Region') },
}
const EXT = { name: 'openai-models', displayName: 'OpenAI', enabled: true, multiInstance: true } as SettingsProvider

const instance = (config: Record<string, unknown>, revision: string): ProviderInstance => ({
  id: 'abc123', extension_name: 'openai-models', display_name: 'Primary',
  config: { api_key: '••••••••', ...config }, enabled: true, _secret_set: ['api_key'], revision,
})

beforeEach(() => {
  resetDataStore()
  sessionStorage.clear()
  providerInstances.mockReset().mockResolvedValue([instance({ default_model: 'gpt-4o', region: '' }, 'rev-1')])
  updateProviderInstance.mockReset().mockResolvedValue({})
})

/** Open the editor on the listed copy, change the model, let `meanwhile` happen elsewhere, Save. */
async function editDefaultModel(to: string, meanwhile: () => void = () => {}) {
  render(<MultiInstanceCard ext={EXT} onChanged={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Edit' }))
  const model = await screen.findByLabelText('Default Model') as HTMLInputElement
  fireEvent.change(model, { target: { value: to } })
  meanwhile()
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
}

describe('an instance is saved over the copy its editor was opened on', () => {
  it('a save names the revision the list reported for the instance', async () => {
    await editDefaultModel('gpt-5')
    // The stored secret is blanked in the editor, so a blank here is the PUT's "keep it".
    await waitFor(() => expect(updateProviderInstance).toHaveBeenCalledWith(
      'openai-models', 'abc123', { api_key: '', default_model: 'gpt-5', region: '' }, 'rev-1'))
  })

  it('an instance saved elsewhere since is not overwritten: the edit is kept, then re-applied on top', async () => {
    updateProviderInstance.mockRejectedValueOnce(new ApiError("The settings of instance 'abc123' changed.", 409, 'stale_write'))
    // Another tab sets the region while this editor is open.
    await editDefaultModel('gpt-5', () => {
      providerInstances.mockResolvedValue([instance({ default_model: 'gpt-4o', region: 'eu' }, 'rev-2')])
    })

    const said = await screen.findByText(/changed elsewhere/)
    expect(said.closest('[role="alert"]')).not.toBeNull()
    // The editor stays open on what the user typed.
    expect((screen.getByLabelText('Default Model') as HTMLInputElement).value).toBe('gpt-5')
    expect(screen.getByRole('button', { name: 'Save' }).getAttribute('aria-disabled')).toBe('true')

    const reapply = await screen.findByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.getAttribute('aria-disabled')).not.toBe('true'))
    fireEvent.click(reapply)
    // This edit on top of what is stored now, over ITS revision: the other tab's region survives.
    await waitFor(() => expect(updateProviderInstance).toHaveBeenLastCalledWith(
      'openai-models', 'abc123', { api_key: '', default_model: 'gpt-5', region: 'eu' }, 'rev-2'))
    await waitFor(() => expect(screen.queryByLabelText('Default Model')).toBeNull())
  })
})
