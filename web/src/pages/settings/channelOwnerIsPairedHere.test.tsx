import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { ChannelOwnerSection } from './ChannelOwnerSection'
import { ProviderCard } from './ProviderCard'
import { api, type ChannelOwnerStatus, type ChannelRuntime, type SettingsProvider } from '../../lib/api'

// ── A channel's owner is set on its Configure page ─────────────────────────────────────────────
//
// The owner id — who the gateway sends your results, scheduled messages and approvals to on a
// channel — was set only by `personalclaw setup`. After a UI-only setup nothing reached you. The
// Configure page now says who the channel reaches you as, and pairs it: it shows a code, you send
// the code to the bot, and the page follows the pairing until it ends.

const idle: ChannelOwnerStatus['pairing'] = { active: false, expires_at: '', attempts_left: 0, ended: '', ended_at: '' }

function status(over: Partial<ChannelOwnerStatus> = {}): ChannelOwnerStatus {
  return {
    channel: 'telegram', display_name: 'Telegram', owner_id: '', source: '',
    pairing_supported: true, pairing: idle, ...over,
  }
}

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

describe('the owner section', () => {
  it('says the channel cannot reach you before an owner is paired', async () => {
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status())
    render(<ChannelOwnerSection channel="telegram" />)
    expect(await screen.findByText(/Telegram doesn't know who you are yet, so nothing your agent sends you can reach you there/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Pair as owner/ })).toBeTruthy()
  })

  it('shows the code, waits for it, and says who the channel reaches once it is sent', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const read = vi.spyOn(api, 'channelOwner').mockResolvedValue(status())
    const expires = new Date(Date.now() + 600_000).toISOString()
    vi.spyOn(api, 'startChannelOwnerPairing').mockResolvedValue({
      code: '48151623', expires_at: expires, ttl_secs: 600,
      pairing: { active: true, expires_at: expires, attempts_left: 5, ended: '', ended_at: '' },
    })
    const onChanged = vi.fn()
    render(<ChannelOwnerSection channel="telegram" onChanged={onChanged} />)
    fireEvent.click(await screen.findByRole('button', { name: /Pair as owner/ }))
    expect(await screen.findByText('48151623')).toBeTruthy()
    expect(screen.getByText(/Send this code to your bot in a direct message on Telegram/)).toBeTruthy()
    expect(screen.getByText(/Waiting for your message/)).toBeTruthy()

    // The owner sends it; the next read says the pairing ended and who the channel reaches now.
    read.mockResolvedValue(status({ owner_id: '4242', source: 'channel', pairing: { ...idle, ended: 'paired', ended_at: expires } }))
    await act(async () => { await vi.advanceTimersByTimeAsync(2100) })
    expect(screen.queryByText('48151623')).toBeNull()
    expect(screen.getByText('Telegram reaches you as 4242.')).toBeTruthy()
    expect(screen.getAllByText('Paired.').length).toBeGreaterThan(0)
    expect(onChanged).toHaveBeenCalled()
  })

  it('a channel whose code is not sent in a DM says how it is sent', async () => {
    // Email is paired by mailing the code to the mailbox: "a direct message to your bot" would
    // send its owner looking for a bot that does not exist.
    const hint = 'Mail this code to me@example.test from the address that should get your approvals'
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status({ channel: 'email', display_name: 'Email', pairing_hint: hint }))
    const expires = new Date(Date.now() + 600_000).toISOString()
    vi.spyOn(api, 'startChannelOwnerPairing').mockResolvedValue({
      code: '48151623', expires_at: expires, ttl_secs: 600,
      pairing: { active: true, expires_at: expires, attempts_left: 5, ended: '', ended_at: '' },
    })
    render(<ChannelOwnerSection channel="email" />)
    fireEvent.click(await screen.findByRole('button', { name: /Pair as owner/ }))
    expect(await screen.findByText('48151623')).toBeTruthy()
    expect(screen.getByText(`${hint}:`)).toBeTruthy()
    expect(screen.queryByText(/direct message/)).toBeNull()
  })

  it('says why a pairing ended when the code was guessed at too often', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const read = vi.spyOn(api, 'channelOwner').mockResolvedValue(status())
    const expires = new Date(Date.now() + 600_000).toISOString()
    vi.spyOn(api, 'startChannelOwnerPairing').mockResolvedValue({
      code: '48151623', expires_at: expires, ttl_secs: 600,
      pairing: { active: true, expires_at: expires, attempts_left: 5, ended: '', ended_at: '' },
    })
    render(<ChannelOwnerSection channel="telegram" />)
    fireEvent.click(await screen.findByRole('button', { name: /Pair as owner/ }))
    await screen.findByText('48151623')
    read.mockResolvedValue(status({ pairing: { ...idle, ended: 'too_many_attempts', ended_at: expires } }))
    await act(async () => { await vi.advanceTimersByTimeAsync(2100) })
    expect(screen.getAllByText(/cancelled after too many wrong codes were sent to the bot/).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Pair as owner/ })).toBeTruthy()
  })

  it('a page opened while a code is outstanding says so and can cancel it', async () => {
    const expires = new Date(Date.now() + 300_000).toISOString()
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status({ pairing: { active: true, expires_at: expires, attempts_left: 5, ended: '', ended_at: '' } }))
    const cancel = vi.spyOn(api, 'cancelChannelOwnerPairing').mockResolvedValue(undefined as never)
    render(<ChannelOwnerSection channel="telegram" />)
    expect(await screen.findByText(/A pairing code is still waiting to be sent/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel that code' }))
    await vi.waitFor(() => expect(cancel).toHaveBeenCalledWith('telegram'))
  })

  it('a shared owner id is named as one, because it may be another app\'s', async () => {
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status({ owner_id: 'U0SLACK', source: 'shared' }))
    render(<ChannelOwnerSection channel="telegram" />)
    expect(await screen.findByText(/Telegram reaches you as U0SLACK, the owner id every channel used to share/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Pair a new owner/ })).toBeTruthy()
  })

  it('a channel that cannot pair from here offers no code', async () => {
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status({ display_name: 'Slack', pairing_supported: false }))
    render(<ChannelOwnerSection channel="slack" />)
    expect(await screen.findByText("Slack can't pair its owner from here.")).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Pair/ })).toBeNull()
  })
})

// ── …and the channel's status says whether it knows its owner ───────────────────────────────────

const ext: SettingsProvider = {
  name: 'telegram-channel', displayName: 'Telegram Channel', enabled: true, managed: true,
  provider: { type: 'channel', capabilities: ['messaging'], hasConfigSchema: true },
  availability: { state: 'available', reason: '', checkedAt: 1 },
}

function channel(owner: ChannelRuntime['owner'], pairing = true): ChannelRuntime {
  return {
    name: 'telegram', display_name: 'Telegram', connected: true, app: 'telegram-channel',
    capabilities: { owner_pairing: pairing }, health: { state: 'ready', detail: 'Bot token configured' }, owner,
  }
}

describe('the channel status row', () => {
  it('says the channel knows no owner, and where to pair one', () => {
    render(<ProviderCard ext={ext} channel={channel({ id: '', source: '' })} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(screen.getByText(/No owner yet — pair one in Configure/)).toBeTruthy()
  })

  it('says who the channel reaches you as', () => {
    render(<ProviderCard ext={ext} channel={channel({ id: '4242', source: 'channel' })} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(screen.getByText('Reaches you as 4242')).toBeTruthy()
  })

  it('names a shared id as shared', () => {
    render(<ProviderCard ext={ext} channel={channel({ id: 'U0SLACK', source: 'shared' })} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(screen.getByText('Reaches you as U0SLACK (the id every channel used to share)')).toBeTruthy()
  })

  it('opening Configure shows the owner section beside the settings', async () => {
    vi.spyOn(api, 'providerSchema').mockResolvedValue({ properties: {} })
    vi.spyOn(api, 'providerConfig').mockResolvedValue({ config: {}, _secret_set: [], revision: 'r0' })
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status())
    render(<ProviderCard ext={ext} channel={channel({ id: '', source: '' })} open onOpenChange={() => {}} onChanged={() => {}} />)
    expect(await screen.findByRole('region', { name: 'Telegram owner' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Pair as owner/ })).toBeTruthy()
  })
})
