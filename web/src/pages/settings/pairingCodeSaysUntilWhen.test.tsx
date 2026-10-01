import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ChannelOwnerSection } from './ChannelOwnerSection'
import { SenderTrustPanel } from './SenderTrustPanel'
import { api, type ChannelOwnerStatus, type ChannelTrust, type ChannelTrustProvider } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { expiryStamp } from '../../lib/epoch'

// Before, in a browser whose locale writes the time of day as "5:45 a.m.":
//   Configure › Owner › Pair as owner: "It works once, until 05:45 a.m.. Whoever sends it becomes
//   Discord's owner." — the sentence's full stop after the locale's own.
//   Settings › Sender trust: "A pairing code is outstanding for Discord until Sep 29, 2026." — for a
//   code that lasts ten minutes, a date and no time.

/** 05:35 on 29 September, in the reader's own zone; the code lasts ten minutes. */
const NOW = new Date(2026, 8, 29, 5, 35)
const EXPIRES = new Date(2026, 8, 29, 5, 45).toISOString()

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  // The locale the owner reads in: it ends the time with a full stop.
  vi.spyOn(Date.prototype, 'toLocaleTimeString').mockReturnValue('05:45 a.m.')
  invalidateKeys('settings:sender-trust')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

const idle: ChannelOwnerStatus['pairing'] = { active: false, expires_at: '', attempts_left: 0, ended: '', ended_at: '' }

function owner(over: Partial<ChannelOwnerStatus> = {}): ChannelOwnerStatus {
  return {
    channel: 'discord', display_name: 'Discord', owner_id: '', owner_name: '', source: '',
    pairing_supported: true, pairing: idle, ...over,
  }
}

describe("a channel owner's pairing code", () => {
  it('says until when it works with one full stop', async () => {
    vi.spyOn(api, 'channelOwner').mockResolvedValue(owner())
    vi.spyOn(api, 'startChannelOwnerPairing').mockResolvedValue({
      code: '44184895', expires_at: EXPIRES, ttl_secs: 600,
      pairing: { active: true, expires_at: EXPIRES, attempts_left: 5, ended: '', ended_at: '' },
    })
    render(<ChannelOwnerSection channel="discord" />)
    fireEvent.click(await screen.findByRole('button', { name: /Pair as owner/ }))
    const line = await screen.findByText(/^It works once/)
    // 🔴 Before: "It works once, until 05:45 a.m.. Whoever sends it becomes Discord's owner."
    expect(line.textContent).toBe("It works once, until 05:45 a.m. Whoever sends it becomes Discord's owner.")
  })

  it('and so does the note about a code already waiting', async () => {
    vi.spyOn(api, 'channelOwner').mockResolvedValue(owner({ pairing: { ...idle, active: true, expires_at: EXPIRES } }))
    render(<ChannelOwnerSection channel="discord" />)
    const line = await screen.findByText(/^A pairing code is still waiting/)
    expect(line.textContent).toBe('A pairing code is still waiting to be sent, until 05:45 a.m. Pairing again replaces it.')
  })
})

function provider(over: Partial<ChannelTrustProvider> = {}): ChannelTrustProvider {
  return {
    provider: 'discord', display_name: 'Discord', registered: true,
    policies: { dm: 'pairing', group: 'tracked_only' },
    allowed_senders: [], tracked_channels: [], seen_channels: [],
    pairing_active: false, pairing_expires_at: '', groups: true, speaks_as_owner: false, pairing_hint: '', ...over,
  }
}

function trust(p: ChannelTrustProvider): ChannelTrust {
  return {
    providers: [p], dm_policies: ['pairing', 'owner_only', 'open'], group_policies: ['tracked_only', 'off'],
    default_dm_policy: 'pairing', default_group_policy: 'tracked_only', pairing_code_ttl_secs: 600,
  }
}

describe("a sender's pairing code", () => {
  it('outstanding elsewhere, says the time it stops working, not only the day', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(provider({ pairing_active: true, pairing_expires_at: EXPIRES })))
    render(<SenderTrustPanel />)
    const line = await screen.findByText(/^A pairing code is outstanding/)
    // 🔴 Before: "A pairing code is outstanding for Discord until Sep 29, 2026. Anyone who sends it …"
    expect(line.textContent).toBe(
      'A pairing code is outstanding for Discord until 05:45 a.m. Anyone who sends it becomes a trusted sender.',
    )
  })

  it('shown after minting, says until when with one full stop', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(provider()))
    vi.spyOn(api, 'startSenderPairing').mockResolvedValue({ code: '82543602', expires_at: EXPIRES, ttl_secs: 600 })
    render(<SenderTrustPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /Pair someone/ }))
    const line = await screen.findByText(/^It works once/)
    expect(line.textContent).toBe('It works once, until 05:45 a.m. Whoever sends it can talk to your agent on Discord.')
  })
})

describe('the expiry of something short-lived', () => {
  it('is a time today, and the day and time on another day', () => {
    vi.mocked(Date.prototype.toLocaleTimeString).mockRestore()
    const today = expiryStamp(EXPIRES, NOW.getTime())
    expect(today).not.toMatch(/2026|Sep/)
    const tomorrow = expiryStamp(new Date(2026, 8, 30, 0, 5).toISOString(), NOW.getTime())
    expect(tomorrow).toMatch(/Sep 30/)
    expect(expiryStamp('not a time', NOW.getTime())).toBe('')
  })
})
