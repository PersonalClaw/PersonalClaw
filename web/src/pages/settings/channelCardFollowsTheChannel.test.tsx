import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ProviderCard } from './ProviderCard'
import { ProvidersPanel } from './ProvidersPanel'
import { api, type ChannelRuntime, type SettingsProvider } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { noteInputForTests } from '../../lib/useVisiblePoll'

// ── A channel's card follows the channel while the page is open ─────────────────────────────────
//
// Telegram's bot token was revoked, its receiver stopped on the 401, and the open Providers page
// went on reading "Connected · Bot token configured" until it was reloaded: it re-read the channels
// only while one was starting. Then, after a Test ("getMe failed … 401") and a Save of a new token,
// the card read "Connected  getMe failed: getMe: Unauthorized (code=401)": the status was the new
// one and the sentence beside it was the old Test's. The page now re-reads the channels while it
// is open, the sentence beside the status is always the channel's own, and a press's answer is its
// own line, named by the press, which a save clears.

const TOKEN_REJECTED = 'Inbound STOPPED — Telegram rejected the bot token (401 Unauthorized), so the long-poll receiver stopped.'

const ext: SettingsProvider = {
  name: 'telegram-channel', displayName: 'Telegram Channel', enabled: true, managed: true,
  provider: { type: 'channel', capabilities: ['messaging'], hasConfigSchema: true },
  availability: { state: 'available', reason: '', checkedAt: 1 },
}

function channel(state: string, detail: string): ChannelRuntime {
  return { name: 'telegram', display_name: 'Telegram', app: 'telegram-channel', connected: true, health: { state, detail } }
}

afterEach(() => {
  vi.useRealTimers(); vi.restoreAllMocks()
  invalidateKeys('settings:', true)
})

describe("a channel's card", () => {
  it('🔑 the open page picks up a receiver that stopped on its own, without a reload', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
    noteInputForTests()
    vi.spyOn(api, 'settingsProviders').mockResolvedValue([ext])
    vi.spyOn(api, 'agentRuntimes').mockResolvedValue([])
    vi.spyOn(api, 'modelsAvailable').mockResolvedValue([])
    const channels = vi.spyOn(api, 'channels').mockResolvedValue([channel('ready', 'Bot token configured')])
    render(<ProvidersPanel query={{}} setQuery={() => {}} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText('Bot token configured')).toBeTruthy()

    // The token is revoked; the receiver stops. Nobody touches the page.
    channels.mockResolvedValue([channel('error', TOKEN_REJECTED)])
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(screen.getByText(TOKEN_REJECTED)).toBeTruthy()
    expect(screen.getByText('Error')).toBeTruthy()
    expect(screen.queryByText('Connected')).toBeNull()
  })

  it("a Test's answer is its own line, and the status beside it stays the channel's own", async () => {
    vi.spyOn(api, 'testChannel').mockResolvedValue({ ok: false, detail: 'getMe failed: getMe: Unauthorized (code=401)' })
    const { rerender } = render(<ProviderCard ext={ext} channel={channel('error', TOKEN_REJECTED)} open={false}
      onOpenChange={() => {}} onChanged={() => {}} />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Test: telegram' })) })
    expect(screen.getByRole('status')).toHaveTextContent('Test: getMe failed: getMe: Unauthorized (code=401)')
    expect(screen.getByText(TOKEN_REJECTED)).toBeTruthy()

    // The channel recovers: its status and sentence say so, whatever the old Test said.
    rerender(<ProviderCard ext={ext} channel={channel('ready', 'Bot token configured')} open={false}
      onOpenChange={() => {}} onChanged={() => {}} />)
    expect(screen.getByText('Connected')).toBeTruthy()
    expect(screen.getByText('Bot token configured')).toBeTruthy()
  })

  it("a save clears what a press answered before it", async () => {
    vi.spyOn(api, 'testChannel').mockResolvedValue({ ok: false, detail: 'getMe failed: getMe: Unauthorized (code=401)' })
    vi.spyOn(api, 'providerSchema').mockResolvedValue({ properties: { bot_token: { type: 'string', 'x-meta': { label: 'Bot Token', sensitive: true } } } })
    vi.spyOn(api, 'providerConfig').mockResolvedValue({ config: { bot_token: '' }, _secret_set: ['bot_token'], revision: 'rev-1' })
    vi.spyOn(api, 'saveProviderConfig').mockResolvedValue({ config: { bot_token: '' }, _secret_set: ['bot_token'], revision: 'rev-2' })
    render(<ProviderCard ext={ext} channel={channel('error', TOKEN_REJECTED)} open
      onOpenChange={() => {}} onChanged={() => {}} />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Test: telegram' })) })
    expect(screen.getByText(/^Test: getMe failed/)).toBeTruthy()

    fireEvent.change(await screen.findByLabelText('Bot Token'), { target: { value: '123:NEW' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    await waitFor(() => expect(screen.queryByText(/getMe failed/)).toBeNull())
  })
})
