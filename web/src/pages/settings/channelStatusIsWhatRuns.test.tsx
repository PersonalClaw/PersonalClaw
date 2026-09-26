import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ProviderCard } from './ProviderCard'
import { api, type ChannelRuntime, type SettingsProvider } from '../../lib/api'

// ── A channel's status says what its receiver is doing, and follows a save ──────────────────────
//
// The status word came from `connected`, which a channel app derives from "a token is present". A
// channel installed after boot had a token and no receiver, so the row read "Connected" beside a
// red dot and a detail saying inbound had not started. The gateway now starts and stops receivers
// as channels change, and reports `starting` while one starts, so the word comes from the state.

const ext: SettingsProvider = {
  name: 'telegram-channel', displayName: 'Telegram Channel', enabled: true, managed: true,
  provider: { type: 'channel', capabilities: ['messaging'], hasConfigSchema: true },
  availability: { state: 'available', reason: '', checkedAt: 1 },
}

function channel(state: string, detail: string, connected = true): ChannelRuntime {
  return { name: 'telegram', display_name: 'Telegram', connected, health: { state, detail } }
}

function mount(c: ChannelRuntime, onChanged = () => {}, open = false) {
  return render(<ProviderCard ext={ext} channel={c} open={open} onOpenChange={() => {}} onChanged={onChanged} />)
}

afterEach(() => vi.restoreAllMocks())

describe('the channel status row', () => {
  it('a channel with a token whose receiver did not start reads Error, not Connected', () => {
    const why = 'Telegram is not receiving messages — its receiver did not start: RuntimeError: boom. Fix its settings, or turn it off and on, to try again.'
    mount(channel('error', why))
    expect(screen.getByText('Error')).toBeTruthy()
    expect(screen.queryByText('Connected')).toBeNull()
    expect(screen.getByText(why)).toBeTruthy()
  })

  it('a receiver being started reads Starting…', () => {
    mount(channel('starting', 'Telegram is starting to receive messages.'))
    expect(screen.getByText('Starting…')).toBeTruthy()
    expect(screen.getByText('Telegram is starting to receive messages.')).toBeTruthy()
  })

  it('ready reads Connected and offline reads Not connected', () => {
    const { unmount } = mount(channel('ready', 'Bot token configured'))
    expect(screen.getByText('Connected')).toBeTruthy()
    unmount()
    mount(channel('offline', 'No bot token configured', false))
    expect(screen.getByText('Not connected')).toBeTruthy()
  })

  it('saving the channel settings re-reads the card, so the row shows the receiver the save started', async () => {
    vi.spyOn(api, 'providerSchema').mockResolvedValue({ properties: { bot_token: { type: 'string', 'x-meta': { label: 'Bot Token', sensitive: true } } } })
    vi.spyOn(api, 'providerConfig').mockResolvedValue({ config: { bot_token: '' }, _secret_set: [] })
    const save = vi.spyOn(api, 'saveProviderConfig').mockResolvedValue({ config: {} })
    const onChanged = vi.fn()
    mount(channel('offline', 'No bot token configured', false), onChanged, true)

    const field = await screen.findByLabelText('Bot Token')
    fireEvent.change(field, { target: { value: '123:ABC' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect(save).toHaveBeenCalledWith('telegram-channel', { bot_token: '123:ABC' })
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })
})
