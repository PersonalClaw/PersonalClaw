/** EA-7 — the sender-trust panel: the owner's read/revoke surface over the channel allowlist.
 *
 *  The five things this surface can get wrong, in the order they would hurt:
 *  a revoke that does not name WHO it is revoking; a dismissed confirm that revokes anyway;
 *  a confirmed revoke that mutates locally instead of re-reading (so the row lies); a failed
 *  revoke that is swallowed (the row stays and the owner believes access is gone); and a
 *  failed READ that renders as "nobody can reach your agent" — which on a security page reads
 *  as an all-clear.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SenderTrustPanel } from './SenderTrustPanel'
import { DialogHost } from '../../ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../ui/dialog/dialogStore'
import { api } from '../../lib/api'
import type { ChannelTrust, ChannelTrustProvider, ChannelTrustSender } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'

function sender(over: Partial<ChannelTrustSender> = {}): ChannelTrustSender {
  return { sender_id: 'u1', name: 'Alice', added_at: '2026-08-01T10:00:00+00:00', via: 'owner', ...over }
}

function provider(over: Partial<ChannelTrustProvider> = {}): ChannelTrustProvider {
  return {
    provider: 'telegram',
    policies: { dm: 'pairing', group: 'tracked_only' },
    allowed_senders: [sender()],
    tracked_channels: [],
    pairing_active: false,
    pairing_expires_at: '',
    ...over,
  }
}

function trust(over: Partial<ChannelTrust> = {}): ChannelTrust {
  return {
    providers: [provider()],
    dm_policies: ['pairing', 'owner_only', 'open'],
    group_policies: ['tracked_only', 'off'],
    default_dm_policy: 'pairing',
    default_group_policy: 'tracked_only',
    ...over,
  }
}

/** Collect toast messages the way the app emits them (a `ne:toast` CustomEvent). */
function captureToasts(): string[] {
  const seen: string[] = []
  window.addEventListener('ne:toast', ((e: Event) => {
    seen.push(String((e as CustomEvent).detail?.message ?? ''))
  }) as EventListener)
  return seen
}

const mount = () => render(<><SenderTrustPanel /><DialogHost /></>)

beforeEach(() => {
  // Mandatory: without this the previous test's payload seeds the next mount from cache.
  invalidateKeys('settings:sender-trust')
})

afterEach(() => {
  // Drain any dialog this test left open, or it leaks into the next one.
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list })()
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('SenderTrustPanel', () => {
  it('lists each trusted sender with its provenance and the channel it applies to', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [provider({
        allowed_senders: [
          sender({ sender_id: '111', name: 'Alice', via: 'owner' }),
          sender({ sender_id: '222', name: '', via: 'pairing' }),
        ],
      })],
    }))
    mount()

    expect(await screen.findByText('Alice')).toBeTruthy()
    expect(screen.getByText(/You allowed them/)).toBeTruthy()
    expect(screen.getByText(/Redeemed a pairing code/)).toBeTruthy()
    // A sender with no display name still renders its id — that is what identifies them.
    expect(screen.getByText('222')).toBeTruthy()
    // The provider's DM posture is stated: what happens to someone NOT on this list.
    expect(screen.getByText(/Strangers must redeem a pairing code/)).toBeTruthy()
  })

  it("names the row AND the channel in the revoke control's accessible name", async () => {
    // The same sender id can be trusted on two providers, so "Revoke Alice" alone would be
    // ambiguous across sections. Two buttons, two distinct names.
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [
        provider({ provider: 'telegram', allowed_senders: [sender({ sender_id: 'u1', name: 'Alice' })] }),
        provider({ provider: 'discord', allowed_senders: [sender({ sender_id: 'u1', name: 'Alice' })] }),
      ],
    }))
    mount()

    expect(await screen.findByRole('button', { name: /Revoke Alice on Telegram/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Revoke Alice on Discord/i })).toBeTruthy()
  })

  it('names the sender in the confirmation and says what revoking actually does', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust())
    const revoke = vi.spyOn(api, 'revokeChannelSender').mockResolvedValue(undefined as never)
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Revoke Alice on Telegram/i }))

    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent).toContain('Alice')
    // The body's claim must match what `channel_trust.deny_sender` does: it drops the sender
    // from the allowlist. It does NOT end a turn already running, and must not imply it does.
    expect(dialog.textContent).toMatch(/allowlist/i)
    expect(dialog.textContent).toMatch(/not interrupted/i)
    expect(revoke).not.toHaveBeenCalled()
  })

  it('writes nothing when the confirmation is dismissed', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust())
    const revoke = vi.spyOn(api, 'revokeChannelSender').mockResolvedValue(undefined as never)
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Revoke Alice on Telegram/i }))
    await screen.findByRole('alertdialog')
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }))

    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(revoke).not.toHaveBeenCalled()
  })

  it('sends the provider AND sender id on confirm, then RE-READS the list', async () => {
    const list = vi.spyOn(api, 'channelTrust').mockResolvedValue(trust())
    const revoke = vi.spyOn(api, 'revokeChannelSender').mockResolvedValue(undefined as never)
    const toasts = captureToasts()
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Revoke Alice on Telegram/i }))
    await screen.findByRole('alertdialog')
    await userEvent.click(screen.getByRole('button', { name: /Revoke access/i }))

    await waitFor(() => expect(revoke).toHaveBeenCalledWith('telegram', 'u1'))
    // A local splice would leave the panel disagreeing with the store on the next mount;
    // re-reading is what makes the row honest.
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1))
    await waitFor(() => expect(toasts.some((t) => /Alice/.test(t))).toBe(true))
  })

  it("reports a failed revoke with the server's own reason, and keeps the row", async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust())
    vi.spyOn(api, 'revokeChannelSender').mockRejectedValue(
      new Error('That sender is not on this channel’s allowlist.'),
    )
    const toasts = captureToasts()
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Revoke Alice on Telegram/i }))
    await screen.findByRole('alertdialog')
    await userEvent.click(screen.getByRole('button', { name: /Revoke access/i }))

    await waitFor(() => expect(toasts.some((t) => /not on this channel/i.test(t))).toBe(true))
    // The row is still there: nothing was removed, and the surface did not pretend otherwise.
    expect(screen.getByRole('button', { name: /Revoke Alice on Telegram/i })).toBeTruthy()
  })

  it('renders a load error instead of an empty allowlist when the read fails', async () => {
    // The dishonest failure mode this rules out: a 500 rendering as "no trusted senders",
    // which on a security page reads as an all-clear.
    vi.spyOn(api, 'channelTrust').mockRejectedValue(new Error('boom'))
    mount()

    expect(await screen.findByText(/Couldn't load your sender trust/i)).toBeTruthy()
    expect(screen.queryByText(/No channel has any trust state yet/i)).toBeNull()
    expect(screen.queryByText(/No trusted senders/i)).toBeNull()
  })

  it('says a channel has nobody trusted without claiming the read failed', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [provider({ allowed_senders: [] })],
    }))
    mount()

    expect(await screen.findByText(/Nobody is trusted on Telegram/i)).toBeTruthy()
    expect(screen.queryByText(/Couldn't load/i)).toBeNull()
  })

  it('surfaces an outstanding pairing code without ever showing a code', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [provider({ pairing_active: true, pairing_expires_at: '2026-08-01T10:10:00+00:00' })],
    }))
    const { container } = mount()

    expect(await screen.findByText(/A pairing code is outstanding for Telegram/i)).toBeTruthy()
    // The API never sends a code or its hash; assert the rendered surface carries no digit run
    // that could be read as one, so a future projection change cannot leak it silently here.
    expect(container.textContent ?? '').not.toMatch(/\b\d{8}\b/)
  })

  it('reads an unrecognized provider and provenance as itself, never as blank', async () => {
    // `provider` is an opaque key a transport picked; core does not enumerate them. A channel
    // this UI has no copy for must still be revocable rather than rendering nameless.
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [provider({ provider: 'matrix', allowed_senders: [sender({ via: 'imported' })] })],
    }))
    mount()

    expect(await screen.findByRole('button', { name: /Revoke Alice on matrix/i })).toBeTruthy()
    expect(screen.getByText(/imported/)).toBeTruthy()
  })

  it('reads a missing added_at as unknown rather than as today', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [provider({ allowed_senders: [sender({ added_at: '' })] })],
    }))
    mount()

    expect(await screen.findByText(/date unknown/i)).toBeTruthy()
  })
})
