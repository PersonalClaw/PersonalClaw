import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ProviderConfigForm } from './ProviderConfigForm'
import { api, ApiError } from '../../lib/api'

// ── A provider's settings are saved over the copy the form was read at ──────────────────────────
//
// The form PATCHes every field it shows, built from the config it read — and the same file is
// written by Apps → Configure and by the app itself. So a form opened before either saved put its
// stale values back over that save. The save now names the revision the form read; a stale one is
// refused, the form keeps what the user typed, and the edit can be re-applied field by field on
// top of what is stored.

const SCHEMA = {
  properties: {
    bot_token: { type: 'string', 'x-meta': { label: 'Bot Token', sensitive: true } },
    chat_id: { type: 'string', 'x-meta': { label: 'Chat ID' } },
    greeting: { type: 'string', 'x-meta': { label: 'Greeting' } },
  },
}
const read = (chatId: string, revision: string) =>
  ({ config: { bot_token: '••••••••', chat_id: chatId, greeting: 'hi' }, _secret_set: ['bot_token'], revision })

afterEach(() => vi.restoreAllMocks())

/** Mount the form on the stored copy, change the greeting, let `meanwhile` happen elsewhere, Save. */
async function editGreeting(meanwhile: () => void = () => {}) {
  vi.spyOn(api, 'providerSchema').mockResolvedValue(SCHEMA)
  vi.spyOn(api, 'providerConfig').mockResolvedValue(read('42', 'rev-1'))
  const save = vi.spyOn(api, 'saveProviderConfig').mockImplementation((_n, config) =>
    Promise.resolve({ config: { ...config, bot_token: '••••••••' }, _secret_set: ['bot_token'], revision: 'rev-3' }))
  render(<ProviderConfigForm name="telegram-channel" />)
  fireEvent.change(await screen.findByLabelText('Greeting'), { target: { value: 'hello' } })
  meanwhile()
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  return { save }
}

describe('the provider settings form is saved over the copy it read', () => {
  it('a save names the revision the form read', async () => {
    const { save } = await editGreeting()
    // The stored secret is blanked in the form, so a blank here is the PATCH's "keep it".
    await waitFor(() => expect(save).toHaveBeenCalledWith(
      'telegram-channel', { bot_token: '', chat_id: '42', greeting: 'hello' }, 'rev-1'))
  })

  it('settings saved elsewhere since are not overwritten: the edit is kept, then re-applied on top', async () => {
    const { save } = await editGreeting(() => {
      // Apps → Configure changes the chat while this form is open.
      vi.mocked(api.providerConfig).mockResolvedValue(read('99', 'rev-2'))
      vi.mocked(api.saveProviderConfig).mockRejectedValueOnce(new ApiError("The settings of 'telegram-channel' changed.", 409, 'stale_write'))
    })

    const said = await screen.findByText(/changed elsewhere/)
    expect(said.closest('[role="alert"]')).not.toBeNull()
    // What the user typed is still in the form.
    expect((screen.getByLabelText('Greeting') as HTMLInputElement).value).toBe('hello')
    expect(screen.getByText('Unsaved changes')).toBeTruthy()

    const reapply = await screen.findByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.getAttribute('aria-disabled')).not.toBe('true'))
    fireEvent.click(reapply)
    // The greeting on top of what is stored now, over ITS revision: the other chat survives.
    await waitFor(() => expect(save).toHaveBeenLastCalledWith(
      'telegram-channel', { bot_token: '', chat_id: '99', greeting: 'hello' }, 'rev-2'))
    await waitFor(() => expect(screen.queryByText(/changed elsewhere/)).toBeNull())
    expect((screen.getByLabelText('Chat ID') as HTMLInputElement).value).toBe('99')
  })
})
