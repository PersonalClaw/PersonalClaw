import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { ChannelOwnerSection } from './ChannelOwnerSection'
import { ProviderCard } from './ProviderCard'
import { api, type ChannelOwnerStatus, type ChannelRuntime, type SettingsProvider } from '../../lib/api'

const clipboard = vi.hoisted(() => ({ copyText: vi.fn(async () => true) }))
vi.mock('../../app/clipboard', () => clipboard)

// ── A channel's owner is set on its Configure page ─────────────────────────────────────────────
//
// The owner id — who the gateway sends your results, scheduled messages and approvals to on a
// channel — was set only by `personalclaw setup`. After a UI-only setup nothing reached you. The
// Configure page now says who the channel reaches you as, and pairs it: it shows a code, you send
// the code to the bot, and the page follows the pairing until it ends.

const idle: ChannelOwnerStatus['pairing'] = { active: false, expires_at: '', attempts_left: 0, ended: '', ended_at: '' }

function status(over: Partial<ChannelOwnerStatus> = {}): ChannelOwnerStatus {
  return {
    channel: 'telegram', display_name: 'Telegram', owner_id: '', owner_name: '', source: '',
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
    read.mockResolvedValue(status({
      owner_id: '7000000001', owner_name: 'Ada Example', source: 'channel',
      pairing: { ...idle, ended: 'paired', ended_at: expires },
    }))
    await act(async () => { await vi.advanceTimersByTimeAsync(2100) })
    expect(screen.queryByText('48151623')).toBeNull()
    expect(screen.getByText('Telegram reaches you as Ada Example.')).toBeTruthy()
    expect(onChanged).toHaveBeenCalled()
  })

  // Every channel that pairs from here shows this one section: Telegram and Discord alike.
  for (const ch of [{ name: 'telegram', display: 'Telegram' }, { name: 'discord', display: 'Discord' }]) {
    it(`says the pairing ended once on ${ch.display}: the line you see is the one a screen reader hears`, async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      const read = vi.spyOn(api, 'channelOwner').mockResolvedValue(status({ channel: ch.name, display_name: ch.display }))
      const expires = new Date(Date.now() + 600_000).toISOString()
      vi.spyOn(api, 'startChannelOwnerPairing').mockResolvedValue({
        code: '48151623', expires_at: expires, ttl_secs: 600,
        pairing: { active: true, expires_at: expires, attempts_left: 5, ended: '', ended_at: '' },
      })
      render(<ChannelOwnerSection channel={ch.name} />)
      fireEvent.click(await screen.findByRole('button', { name: /Pair as owner/ }))
      await screen.findByText('48151623')
      read.mockResolvedValue(status({
        channel: ch.name, display_name: ch.display, owner_id: '4242', owner_name: 'Ada Example', source: 'channel',
        pairing: { ...idle, ended: 'paired', ended_at: expires },
      }))
      await act(async () => { await vi.advanceTimersByTimeAsync(2100) })
      expect(screen.getByText(`${ch.display} reaches you as Ada Example.`)).toBeTruthy()
      // 🔴 Before: the sentence was shown, and then said again by a hidden copy of it, so the
      // accessibility tree held "Paired." twice. It is one live region now, which is also what you see.
      expect(screen.getAllByText('Paired.')).toHaveLength(1)
      const said = screen.getByRole('status')
      expect(said.textContent).toBe('Paired.')
      expect(said.className).not.toMatch(/sr-only/)
    })
  }

  it('the confirmation region is mounted empty and out of the layout until there is something to say', async () => {
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status())
    render(<ChannelOwnerSection channel="telegram" />)
    await screen.findByRole('button', { name: /Pair as owner/ })
    const said = screen.getByRole('status')
    expect(said.textContent).toBe('')
    expect(said.className).toMatch(/sr-only/)
  })

  it('names the owner as the channel knows them, with the platform id under it', async () => {
    // 🔴 Before: "Telegram reaches you as 7000000001." A chat id is a number nobody recognises;
    // the name the sender goes by on Telegram is already in the channel's trust list.
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status({ owner_id: '7000000001', owner_name: 'Ada Example', source: 'channel' }))
    render(<ChannelOwnerSection channel="telegram" />)
    const line = await screen.findByText('Telegram reaches you as Ada Example.')
    expect(line.textContent).not.toContain('7000000001')
    // The id stays, as the smaller, quieter detail under the name — and it can still be copied.
    const id = screen.getByText('Telegram id 7000000001')
    expect(line.contains(id)).toBe(false)
    expect(id.closest('[data-type="caption"]')).toBeTruthy()
    expect(id.closest('.text-on-surface-low')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Copy your Telegram id' }))
    expect(clipboard.copyText).toHaveBeenCalledWith('7000000001', 'your Telegram id')
  })

  it('with no name for the owner, says the id is the channel\'s id', async () => {
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status({ owner_id: '7000000001', source: 'channel' }))
    render(<ChannelOwnerSection channel="telegram" />)
    expect(await screen.findByText('Telegram reaches you as Telegram id 7000000001.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Copy your Telegram id/ })).toBeNull()
  })

  it('a code offered over a paired owner names who it would replace by name', async () => {
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status({ owner_id: '7000000001', owner_name: 'Ada Example', source: 'channel' }))
    const expires = new Date(Date.now() + 600_000).toISOString()
    vi.spyOn(api, 'startChannelOwnerPairing').mockResolvedValue({
      code: '48151623', expires_at: expires, ttl_secs: 600,
      pairing: { active: true, expires_at: expires, attempts_left: 5, ended: '', ended_at: '' },
    })
    render(<ChannelOwnerSection channel="telegram" />)
    fireEvent.click(await screen.findByRole('button', { name: /Pair a new owner/ }))
    const caption = await screen.findByText(/Whoever sends it becomes Telegram's owner/)
    expect(caption.textContent).toMatch(/in place of Ada Example\.$/)
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

  it('a shared owner id this channel knows a name for is named, and not called another app\'s', async () => {
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status({ owner_id: '777', owner_name: 'Ada Example', source: 'shared' }))
    render(<ChannelOwnerSection channel="telegram" />)
    expect(await screen.findByText('Telegram reaches you as Ada Example, by the owner id every channel used to share.')).toBeTruthy()
    expect(screen.getByText('Telegram id 777')).toBeTruthy()
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
    render(<ProviderCard ext={ext} channel={channel({ id: '', source: '', name: '' })} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(screen.getByText(/No owner yet — pair one in Configure/)).toBeTruthy()
  })

  it('says who the channel reaches you as, by the name it knows you by', () => {
    render(<ProviderCard ext={ext} channel={channel({ id: '7000000001', source: 'channel', name: 'Ada Example' })} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(screen.getByText('Reaches you as Ada Example')).toBeTruthy()
    expect(screen.queryByText(/7000000001/)).toBeNull()
  })

  it('with no name, says the id is the channel\'s id', () => {
    render(<ProviderCard ext={ext} channel={channel({ id: '4242', source: 'channel', name: '' })} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(screen.getByText('Reaches you as Telegram id 4242')).toBeTruthy()
  })

  it('names a shared id as shared', () => {
    render(<ProviderCard ext={ext} channel={channel({ id: 'U0SLACK', source: 'shared', name: '' })} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(screen.getByText('Reaches you as U0SLACK (the id every channel used to share)')).toBeTruthy()
  })

  it('a shared id this channel knows a name for is named', () => {
    render(<ProviderCard ext={ext} channel={channel({ id: '777', source: 'shared', name: 'Ada Example' })} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(screen.getByText('Reaches you as Ada Example (by the id every channel used to share)')).toBeTruthy()
  })

  it('opening Configure shows the owner section beside the settings', async () => {
    vi.spyOn(api, 'providerSchema').mockResolvedValue({ properties: {} })
    vi.spyOn(api, 'providerConfig').mockResolvedValue({ config: {}, _secret_set: [], revision: 'r0' })
    vi.spyOn(api, 'channelOwner').mockResolvedValue(status())
    render(<ProviderCard ext={ext} channel={channel({ id: '', source: '', name: '' })} open onOpenChange={() => {}} onChanged={() => {}} />)
    expect(await screen.findByRole('region', { name: 'Telegram owner' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Pair as owner/ })).toBeTruthy()
  })
})
