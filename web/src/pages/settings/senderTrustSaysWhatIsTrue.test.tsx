/** Settings › Sender trust says what is true about codes and groups.
 *
 *  Two things it said that were not:
 *  - "A pairing code is outstanding for Telegram until 04:10 p.m. Anyone who sends it becomes a
 *    trusted sender." at 04:24 p.m., about a code the gate had refused since 04:10. The read no longer
 *    reports a code past its time; the page, open when that time comes, reads again then, so the line
 *    goes when the code does.
 *  - "Group chats  Tracked groups  None" directly above the channel it tracks: the group rule's two
 *    choices read as a label and its value, "Tracked groups: None". The choices now say what each
 *    does, and the groups it reads sit under their own "Tracked groups" heading.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, within } from '@testing-library/react'
import { SenderTrustPanel } from './SenderTrustPanel'
import { api, type ChannelTrust, type ChannelTrustProvider } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'

const NOW = new Date(2026, 8, 30, 16, 5)

function provider(over: Partial<ChannelTrustProvider> = {}): ChannelTrustProvider {
  return {
    provider: 'slack', display_name: 'Slack', registered: true,
    policies: { dm: 'pairing', group: 'tracked_only' },
    allowed_senders: [], seen_senders: [], tracked_channels: [], seen_channels: [],
    pairing_active: false, pairing_expires_at: '', groups: true, speaks_as_owner: false, pairing_hint: '', ...over,
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

describe('a pairing code made elsewhere', () => {
  it('stops being outstanding on the page when its time is up', async () => {
    const expires = new Date(NOW.getTime() + 5 * 60_000).toISOString()
    const read = vi.spyOn(api, 'channelTrust')
      .mockResolvedValueOnce(trust(provider({ pairing_active: true, pairing_expires_at: expires })))
      .mockResolvedValue(trust(provider()))
    render(<SenderTrustPanel />)
    expect(await screen.findByText(/^A pairing code is outstanding for Slack/)).toBeTruthy()
    const before = read.mock.calls.length

    await act(async () => { await vi.advanceTimersByTimeAsync(4 * 60_000) })
    expect(read.mock.calls.length).toBe(before)
    expect(screen.queryByText(/^A pairing code is outstanding/)).toBeTruthy()

    await act(async () => { await vi.advanceTimersByTimeAsync(2 * 60_000) })
    expect(read.mock.calls.length).toBeGreaterThan(before)
    expect(screen.queryByText(/^A pairing code is outstanding/)).toBeNull()
  })
})

describe('the groups a channel reads', () => {
  it('offers choices that say what they do, never a bare "None"', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(provider()))
    render(<SenderTrustPanel />)
    const only = await screen.findByRole('button', { name: 'Slack group chats: Only tracked groups' })
    expect(only.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Slack group chats: Ignore all groups' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /group chats: None$/ })).toBeNull()
  })

  it('lists the tracked ones under a heading of their own, by the name they were given', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(provider({
      tracked_channels: [{ channel_id: 'C0ALERTS', name: 'alerts', added_at: '2026-09-30T20:26:41+00:00' }],
    })))
    render(<SenderTrustPanel />)
    const list = await screen.findByRole('group', { name: 'Tracked groups on Slack' })
    expect(within(list).getByText('alerts')).toBeTruthy()
    expect(within(list).getByText(/^C0ALERTS · tracked/)).toBeTruthy()
    expect(screen.getByText('Tracked groups')).toBeTruthy()
  })

  it('has no tracked-groups heading while it tracks none', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(provider({
      seen_channels: [{ channel_id: '-4102', name: 'Family', last_seen: '2026-09-30T20:20:00+00:00' }],
    })))
    render(<SenderTrustPanel />)
    expect(await screen.findByText('Groups that messaged your agent')).toBeTruthy()
    expect(screen.queryByText('Tracked groups')).toBeNull()
  })
})
