/** Settings › Sender trust words each channel by what that channel declares it can do.
 *
 *  Every section was one chat-bot template. The Email section read "Strangers must redeem a pairing
 *  code. Only tracked groups are read.", offered a Group chats rule, said "No group has messaged your
 *  agent on Email yet. Add the bot to a group…", and showed a code to send "to your bot in a direct
 *  message". Email has no bot and no groups, and a stranger there is sent nothing, which is what the
 *  page's own intro said a few lines above. The section now follows the channel's declarations
 *  (`groups`, `speaks_as_owner`, `pairing_hint` on the read); core names no channel.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SenderTrustPanel } from './SenderTrustPanel'
import { api, type ChannelTrust, type ChannelTrustProvider } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'

const MAIL_HINT = 'Have them mail this code from the address you want to let in, to noor@example.com. It can be anywhere in the message'

/** A bot channel that carries groups: the template every section used to get. */
function bot(over: Partial<ChannelTrustProvider> = {}): ChannelTrustProvider {
  return {
    provider: 'chat', display_name: 'Chat', registered: true,
    policies: { dm: 'pairing', group: 'tracked_only' },
    allowed_senders: [], tracked_channels: [], seen_channels: [],
    pairing_active: false, pairing_expires_at: '',
    groups: true, speaks_as_owner: false, pairing_hint: '', ...over,
  }
}

/** A channel that sends as you, from your own mailbox, with no groups. */
function mailbox(over: Partial<ChannelTrustProvider> = {}): ChannelTrustProvider {
  return bot({ provider: 'email', display_name: 'Email', groups: false, speaks_as_owner: true, pairing_hint: MAIL_HINT, ...over })
}

function trust(...providers: ChannelTrustProvider[]): ChannelTrust {
  return {
    providers, dm_policies: ['pairing', 'owner_only', 'open'], group_policies: ['tracked_only', 'off'],
    default_dm_policy: 'pairing', default_group_policy: 'tracked_only', pairing_code_ttl_secs: 600,
  }
}

const section = async (name: string) => {
  const heading = await screen.findByRole('heading', { name })
  return heading.closest('section') as HTMLElement
}

beforeEach(() => { invalidateKeys('settings:sender-trust') })
afterEach(() => { vi.restoreAllMocks() })

describe('a channel that sends as you and has no groups', () => {
  it('has no group rule and no word of a bot or a group', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(mailbox(), bot()))
    render(<SenderTrustPanel />)
    const email = await section('Email')

    expect(within(email).queryByText('Group chats')).toBeNull()
    expect(within(email).queryByRole('button', { name: /group chats/i })).toBeNull()
    expect(within(email).queryByText(/tracked groups/i)).toBeNull()
    expect(within(email).queryByText(/No group has messaged/)).toBeNull()
    expect(email.textContent).not.toMatch(/\bbot\b/)
    expect(email.textContent).not.toMatch(/Strangers must redeem a pairing code/)

    // The floor: the bot channel beside it keeps all of it.
    const chat = await section('Chat')
    expect(within(chat).getByRole('button', { name: 'Chat group chats: Only tracked groups' })).toBeTruthy()
    expect(within(chat).getByText(/No group has messaged your agent on Chat yet. Add the bot to a group/)).toBeTruthy()
    expect(within(chat).getAllByText(/Strangers must redeem a pairing code/).length).toBeGreaterThan(0)
  })

  it('says a stranger is sent nothing and waits in your Inbox, under each rule as it is', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(
      mailbox(),
      mailbox({ provider: 'email2', display_name: 'Work mail', policies: { dm: 'owner_only', group: 'tracked_only' } }),
      mailbox({ provider: 'email3', display_name: 'Open mail', policies: { dm: 'open', group: 'tracked_only' } }),
    ))
    render(<SenderTrustPanel />)

    const email = await section('Email')
    expect(within(email).getAllByText('A stranger is sent nothing: their message waits in your Inbox, and a pairing code you give them lets them in.').length).toBeGreaterThan(0)
    expect(within(email).getByText("Someone who writes to you on Email and isn't on the list below.")).toBeTruthy()
    expect(within(await section('Work mail')).getAllByText('A stranger is sent nothing: their message waits in your Inbox, and only you can let them in.').length).toBeGreaterThan(0)
    expect(within(await section('Open mail')).getAllByText('Anyone may talk to your agent, and it answers them as you.').length).toBeGreaterThan(0)
  })

  it('offers choices that say who lets a stranger in, since nobody asks them anything', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(mailbox()))
    render(<SenderTrustPanel />)
    const pressed = await screen.findByRole('button', { name: "Email messages from people you haven't paired: A code lets them in" })
    expect(pressed.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: "Email messages from people you haven't paired: Only you let them in" })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Ask for a code$/ })).toBeNull()
  })

  it("shows a code with the channel's own words for where it goes", async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(mailbox()))
    vi.spyOn(api, 'startSenderPairing').mockResolvedValue({ code: '48213579', expires_at: '2026-09-30T18:10:00+00:00', ttl_secs: 600 })
    render(<SenderTrustPanel />)

    await userEvent.click(await screen.findByRole('button', { name: 'Pair someone' }))
    expect(await screen.findByText(`${MAIL_HINT}:`)).toBeTruthy()
    expect(screen.queryByText(/to your bot in a direct message/)).toBeNull()
  })

  it('says to send the code to you when the channel gave no words of its own', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(mailbox({ pairing_hint: '' })))
    vi.spyOn(api, 'startSenderPairing').mockResolvedValue({ code: '48213579', expires_at: '2026-09-30T18:10:00+00:00', ttl_secs: 600 })
    render(<SenderTrustPanel />)

    await userEvent.click(await screen.findByRole('button', { name: 'Pair someone' }))
    expect(await screen.findByText('Have them send you this code on Email:')).toBeTruthy()
  })

  it('still lists a group the store holds for it, so nothing on record is hidden', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(mailbox({
      seen_channels: [{ channel_id: 'list-7', name: 'Book club', last_seen: '2026-09-30T20:20:00+00:00' }],
    })))
    render(<SenderTrustPanel />)
    expect(await screen.findByRole('button', { name: 'Track Book club on Email' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Email group chats: Only tracked groups' })).toBeTruthy()
  })
})

describe('the page around the channels', () => {
  it('names no channel in its intro and promises no bot', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(mailbox(), bot()))
    render(<SenderTrustPanel />)
    const intro = await screen.findByText(/^Who can talk to your agent through each chat channel/)
    expect(intro.textContent).not.toMatch(/email|bot/i)
    expect(intro.textContent).toMatch(/Each channel below says what a stranger gets there and how you let someone in\./)
  })
})

describe('a code is offered only while the rule takes one', () => {
  it('offers Pair someone under the rule that asks for a code, and not under the others', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(
      bot(),
      bot({ provider: 'quiet', display_name: 'Quiet', policies: { dm: 'owner_only', group: 'tracked_only' } }),
      mailbox({ policies: { dm: 'owner_only', group: 'tracked_only' } }),
    ))
    render(<SenderTrustPanel />)
    expect(within(await section('Chat')).getByRole('button', { name: 'Pair someone' })).toBeTruthy()
    expect(within(await section('Quiet')).queryByRole('button', { name: /Pair someone/ })).toBeNull()
    expect(within(await section('Email')).queryByRole('button', { name: /Pair someone/ })).toBeNull()
  })

  it('says a code made elsewhere lets nobody in while the rule takes none', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust(bot({
      policies: { dm: 'owner_only', group: 'tracked_only' },
      pairing_active: true, pairing_expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
    })))
    render(<SenderTrustPanel />)
    expect(await screen.findByText(/It lets nobody in while Chat's rule for strangers doesn't ask for a code\./)).toBeTruthy()
    expect(screen.queryByText(/Anyone who sends it becomes a trusted sender/)).toBeNull()
  })
})
