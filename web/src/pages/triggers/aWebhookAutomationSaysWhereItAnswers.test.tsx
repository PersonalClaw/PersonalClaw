import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Trigger as WireTrigger, WebhookDoor } from '../../lib/api'

// ── A webhook automation's page says where its address answers, and makes what fires it ─────────
//
// A webhook automation runs when a program posts to its address with a sender token made for it
// (`inbound/webhook.py`). Its page said "When its webhook receives a request" and nothing more: no
// address to give a program, no word of what to send, and no way to make the token, so nothing
// could ever fire it. The words here are the server's (`door.url`, `door.reach`), and the token is
// made through Settings' route, pinned to this automation, and shown once with a command that fires
// it.

const { API, CONFIRM } = vi.hoisted(() => ({
  API: {
    externalAccessCreateClient: vi.fn(),
    externalAccessRevokeClient: vi.fn(() => Promise.resolve()),
  },
  CONFIRM: vi.fn(() => Promise.resolve(true)),
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))
vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  confirmDestructive: CONFIRM,
}))
vi.mock('../schedule/ScheduleDetail', () => ({ RunHistory: () => null }))

const URL = 'http://127.0.0.1:10000/api/triggers/store:webhook:build-finished/fire'
const REACH =
  "It takes requests only from programs on the machine PersonalClaw runs on. From another machine, " +
  "forward a port to that machine's 127.0.0.1 over SSH, or run a relay on it, and send through that."

function door(over: Partial<WebhookDoor> = {}): WebhookDoor {
  return { url: URL, reach: REACH, body_limit_bytes: 64 * 1024, senders: [], ...over }
}

function row(over: Partial<WireTrigger> = {}): WireTrigger {
  return {
    kind: 'store', id: 'store:webhook:build-finished', raw_id: 'webhook:build-finished',
    name: 'Build finished', enabled: true, action: { provider: 'notify', config: {} },
    store_kind: 'webhook', spec: {}, broken: [], webhook: door(),
    ...over,
  }
}

async function mount(trigger: WireTrigger, onChanged = vi.fn()) {
  const { StoreTriggerDetail } = await import('./StoreTriggerDetail')
  render(<StoreTriggerDetail trigger={trigger} onChanged={onChanged} onDeleted={() => {}} />)
  return onChanged
}

beforeEach(() => {
  API.externalAccessCreateClient.mockReset()
  API.externalAccessRevokeClient.mockClear()
  CONFIRM.mockClear()
})

describe("a webhook automation's page", () => {
  it('shows its address, where that answers and what a program sends, and that nothing fires it yet', async () => {
    await mount(row())

    expect(screen.getByText(URL)).toBeInTheDocument()
    expect(screen.getByText(REACH)).toBeInTheDocument()
    expect(screen.getByText('Authorization: Bearer <sender token>')).toBeInTheDocument()
    expect(screen.getByText(/up to 64 KB, reaches what it runs as data/)).toBeInTheDocument()
    expect(screen.getByText('No sender tokens yet, so nothing outside PersonalClaw can fire it.')).toBeInTheDocument()
  })

  it('makes a sender token pinned to this automation, and shows it once with a command that fires it', async () => {
    const curl = `curl -H 'Authorization: Bearer tok-1' --data ping ${URL}`
    API.externalAccessCreateClient.mockResolvedValue({
      ok: true, client_id: 'c1', label: 'Build server', surfaces: ['webhook'], token: 'tok-1',
      token_notice: 'Copy this now — it is stored only as a hash and cannot be shown again.',
      webhook: { url: URL, header: 'Authorization: Bearer tok-1', curl },
    })
    const onChanged = await mount(row())

    fireEvent.change(screen.getByRole('textbox', { name: 'What the sender token is for' }), { target: { value: 'Build server' } })
    fireEvent.click(screen.getByRole('button', { name: /Make a sender token/ }))

    await waitFor(() => expect(screen.getByText('tok-1')).toBeInTheDocument())
    expect(API.externalAccessCreateClient).toHaveBeenCalledWith({
      label: 'Build server', surfaces: ['webhook'], scope: { trigger: 'store:webhook:build-finished' },
    })
    expect(screen.getByText(curl)).toBeInTheDocument()
    expect(screen.getByText(/stored only as a hash/)).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalled()
  })

  it('names a sender token for the automation when it is not told what it is for', async () => {
    API.externalAccessCreateClient.mockResolvedValue({
      ok: true, client_id: 'c2', label: 'Build finished sender', surfaces: ['webhook'], token: 'tok-2',
      token_notice: '', webhook: { url: URL, header: 'Authorization: Bearer tok-2', curl: 'curl …' },
    })
    await mount(row())

    fireEvent.click(screen.getByRole('button', { name: /Make a sender token/ }))

    await waitFor(() => expect(API.externalAccessCreateClient).toHaveBeenCalled())
    expect(API.externalAccessCreateClient.mock.calls[0][0]).toMatchObject({ label: 'Build finished sender' })
  })

  it('lists each sender token with when it stops working, and revokes one after asking', async () => {
    const sender = {
      client_id: 'c1', label: 'Build server', created_at: '2026-10-04T09:00:00+00:00',
      expires_at: Date.now() / 1000 + 86400 * 30, last_seen_at: '', disabled: false,
    }
    const onChanged = await mount(row({ webhook: door({ senders: [sender] }) }))

    expect(screen.getByText('“Build server”')).toBeInTheDocument()
    expect(screen.getByText(/works until .* never used/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Revoke/ }))

    await waitFor(() => expect(API.externalAccessRevokeClient).toHaveBeenCalledWith('c1'))
    expect(CONFIRM).toHaveBeenCalled()
    expect(onChanged).toHaveBeenCalled()
  })

  it('is not offered on a webhook automation someone else wrote, which this one never fires', async () => {
    await mount(row({ author: 'sam', read_only: true }))

    expect(screen.queryByText(URL)).toBeNull()
    expect(screen.queryByRole('button', { name: /Make a sender token/ })).toBeNull()
  })

  it('is not on another kind of automation (the floor)', async () => {
    await mount(row({ store_kind: 'file', webhook: null, spec: { paths: ['~/notes/**'] } }))

    expect(screen.queryByRole('button', { name: /Make a sender token/ })).toBeNull()
  })
})
