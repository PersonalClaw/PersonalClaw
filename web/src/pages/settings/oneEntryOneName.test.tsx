import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ChannelOwnerSection } from './ChannelOwnerSection'
import { ProviderCard } from './ProviderCard'
import { SenderTrustPanel } from './SenderTrustPanel'
import { channelPerson } from './channelPerson'
import { api, type ChannelOwnerStatus, type ChannelRuntime, type ChannelTrust, type SettingsProvider } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'

// ── One trust-list entry, one name, on every page that shows it ─────────────────────────────────
//
// Settings › Sender trust and Telegram Channel › Configure › Owner show the same entry: the sender
// who paired as the owner. Sender trust named her; the Owner gave the bare chat id. Both now name a
// person through one formatter (`channelPerson`), and this pins the two pages to its one output.

const ID = '7000000001'

function trust(name: string): ChannelTrust {
  return {
    providers: [{
      provider: 'telegram', display_name: 'Telegram', registered: true,
      policies: { dm: 'pairing', group: 'tracked_only' },
      allowed_senders: [{ sender_id: ID, name, added_at: '2026-09-30T05:35:23+00:00', via: 'owner_pairing' }],
      tracked_channels: [], seen_channels: [], pairing_active: false, pairing_expires_at: '',
      groups: true, speaks_as_owner: false, pairing_hint: '',
    }],
    dm_policies: ['pairing', 'owner_only', 'open'], group_policies: ['tracked_only', 'off'],
    default_dm_policy: 'pairing', default_group_policy: 'tracked_only', pairing_code_ttl_secs: 600,
  }
}

function owner(name: string): ChannelOwnerStatus {
  return {
    channel: 'telegram', display_name: 'Telegram', owner_id: ID, owner_name: name, source: 'channel',
    pairing_supported: true, pairing: { active: false, expires_at: '', attempts_left: 0, ended: '', ended_at: '' },
  }
}

beforeEach(() => { invalidateKeys('settings:sender-trust') })
afterEach(() => { vi.restoreAllMocks() })

describe('Sender trust and the Owner name one entry the same way', () => {
  it('the formatter: a name leads with the id under it; no name, the id leads, said as one', () => {
    expect(channelPerson('Telegram', ID, 'Ada Example')).toEqual({ name: 'Ada Example', detail: `Telegram id ${ID}` })
    expect(channelPerson('Telegram', ID, '')).toEqual({ name: `Telegram id ${ID}`, detail: '' })
  })

  for (const name of ['Ada Example', '']) {
    it(`both pages say it, ${name ? 'with a name' : 'with no name'}`, async () => {
      const person = channelPerson('Telegram', ID, name)

      vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(name))
      const trustPage = render(<SenderTrustPanel />)
      expect(await screen.findByText(person.name)).toBeTruthy()
      if (person.detail) expect(screen.getByText(person.detail)).toBeTruthy()
      trustPage.unmount()

      vi.spyOn(api, 'channelOwner').mockResolvedValue(owner(name))
      render(<ChannelOwnerSection channel="telegram" />)
      expect(await screen.findByText(`Telegram reaches you as ${person.name}.`)).toBeTruthy()
      if (person.detail) expect(screen.getByText(person.detail)).toBeTruthy()
      else expect(screen.queryByText(`Telegram id ${ID}`)).toBeNull()
    })
  }
})

// ── …and every channel's card header and Owner line name its owner through it ─────────────────
//
// Slack's owner is set in the environment, not paired, and its app writes that owner into its own
// trust list with the name it has there; the card header and the Owner line both gave the bare
// member id. Telegram and Discord pair theirs. All of them name the owner one way.

const CHANNELS = [
  { name: 'telegram', display: 'Telegram', app: 'telegram-channel', id: '7000000001', pairing: true },
  { name: 'slack', display: 'Slack', app: 'slack-channel', id: 'U0EXAMPLE1', pairing: false },
  { name: 'discord', display: 'Discord', app: 'discord-channel', id: '700000000000000001', pairing: true },
]

describe("every channel's header and Owner line name its owner the same way", () => {
  for (const ch of CHANNELS) {
    for (const name of ['Ada Example', '']) {
      it(`${ch.display}, ${name ? 'with a name' : 'with no name'}`, async () => {
        const person = channelPerson(ch.display, ch.id, name)
        const ext: SettingsProvider = {
          name: ch.app, displayName: `${ch.display} Channel`, enabled: true, managed: true,
          provider: { type: 'channel', capabilities: ['messaging'], hasConfigSchema: true },
          availability: { state: 'available', reason: '', checkedAt: 1 },
        }
        const runtime: ChannelRuntime = {
          name: ch.name, display_name: ch.display, connected: true, app: ch.app,
          capabilities: { owner_pairing: ch.pairing }, health: { state: 'ready' },
          owner: { id: ch.id, source: 'channel', name },
        }
        const card = render(<ProviderCard ext={ext} channel={runtime} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
        expect(screen.getByText(`Reaches you as ${person.name}`)).toBeTruthy()
        if (name) expect(screen.queryByText(new RegExp(ch.id))).toBeNull()
        card.unmount()

        vi.spyOn(api, 'channelOwner').mockResolvedValue({
          channel: ch.name, display_name: ch.display, owner_id: ch.id, owner_name: name, source: 'channel',
          pairing_supported: ch.pairing, pairing: { active: false, expires_at: '', attempts_left: 0, ended: '', ended_at: '' },
        })
        render(<ChannelOwnerSection channel={ch.name} />)
        expect(await screen.findByText(`${ch.display} reaches you as ${person.name}.`)).toBeTruthy()
        if (person.detail) expect(screen.getByText(person.detail)).toBeTruthy()
        if (!ch.pairing) expect(screen.getByText(`${ch.display} can't pair its owner from here.`)).toBeTruthy()
      })
    }
  }
})
