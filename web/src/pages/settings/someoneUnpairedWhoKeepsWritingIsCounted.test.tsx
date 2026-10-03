/** Settings › Sender trust lists the people who messaged your agent and aren't paired.
 *
 *  In the day after someone first writes, they are answered at most once and you are told once (the
 *  pairing note, the notice). What they wrote after that was refused with nobody told, so a sender
 *  you had just revoked, or a stranger who kept writing, left no trace anywhere. The page now counts
 *  them: who they are, how many messages, the last one's time. Never what they wrote, and nothing to
 *  grant from the list: a pairing code or Allow on the notice lets someone in.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { SenderTrustPanel } from './SenderTrustPanel'
import { api, type ChannelTrust, type ChannelTrustProvider, type ChannelTrustSeenSender } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'

const NOW = new Date(2026, 9, 2, 16, 5)
const LIST = "People who messaged your agent on Telegram and aren't paired"

/** A moment on Oct `day` at `h`:`m`, the reader's clock, as the gateway sends it (with its offset). */
const at = (h: number, m: number, day = 2) => new Date(2026, 9, day, h, m).toISOString()
const clock = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })

function wrote(over: Partial<ChannelTrustSeenSender> = {}): ChannelTrustSeenSender {
  return { sender_id: '700100', name: 'Robin Example', since: at(15, 2, 1), last_seen: at(15, 40), count: 3, ...over }
}

function provider(over: Partial<ChannelTrustProvider> = {}): ChannelTrustProvider {
  return {
    provider: 'telegram', display_name: 'Telegram', registered: true,
    policies: { dm: 'pairing', group: 'tracked_only' },
    allowed_senders: [], seen_senders: [], tracked_channels: [], seen_channels: [],
    pairing_active: false, pairing_expires_at: '', groups: false, speaks_as_owner: false, pairing_hint: '', ...over,
  }
}

function trust(p: ChannelTrustProvider): ChannelTrust {
  return {
    providers: [p], dm_policies: ['pairing', 'owner_only', 'open'], group_policies: ['tracked_only', 'off'],
    default_dm_policy: 'pairing', default_group_policy: 'tracked_only', pairing_code_ttl_secs: 600,
  }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW })
  invalidateKeys('settings:sender-trust')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe("people who messaged your agent and aren't paired", () => {
  it("are listed by name and id, with how many messages since when and the last one's time", async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(provider({ seen_senders: [wrote()] })))
    render(<SenderTrustPanel />)

    const list = await screen.findByRole('group', { name: LIST })
    expect(within(list).getByText('Robin Example')).toBeTruthy()
    expect(within(list).getByText('Telegram id 700100')).toBeTruthy()
    expect(within(list).getByText(`3 messages since ${day(at(15, 2, 1))}, the last ${clock(at(15, 40))} · not read`)).toBeTruthy()
  })

  it('say one message as one, and name someone with no name by their id', async () => {
    const once = wrote({ name: '', count: 1, since: at(15, 40), last_seen: at(15, 40) })
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(provider({ seen_senders: [once] })))
    render(<SenderTrustPanel />)

    const list = await screen.findByRole('group', { name: LIST })
    expect(within(list).getByText('Telegram id 700100')).toBeTruthy()
    expect(within(list).getByText(`1 message, ${clock(at(15, 40))} · not read`)).toBeTruthy()
  })

  it('offer nothing to grant: someone is let in by a code or by Allow on the notice', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(provider({ seen_senders: [wrote()] })))
    render(<SenderTrustPanel />)

    const list = await screen.findByRole('group', { name: LIST })
    expect(within(list).queryAllByRole('button')).toEqual([])
    // The floor: the section's own way in is still there, beside the list.
    expect(screen.getByRole('button', { name: /Pair someone/ })).toBeTruthy()
  })

  it('lose the one you let in, who is listed as trusted, and the list goes with its last name', async () => {
    const trusted = { sender_id: '700100', name: 'Robin Example', added_at: at(15, 50), via: 'owner' }
    vi.spyOn(api, 'channelTrust')
      .mockResolvedValueOnce(trust(provider({ seen_senders: [wrote()] })))
      .mockResolvedValue(trust(provider({ allowed_senders: [trusted] })))
    const first = render(<SenderTrustPanel />)
    expect(await screen.findByRole('group', { name: LIST })).toBeTruthy()
    first.unmount()

    invalidateKeys('settings:sender-trust')
    render(<SenderTrustPanel />)
    expect(await screen.findByRole('button', { name: 'Revoke Robin Example on Telegram' })).toBeTruthy()
    expect(screen.queryByRole('group', { name: LIST })).toBeNull()
  })
})
