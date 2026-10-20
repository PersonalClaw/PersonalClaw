import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { NotificationItem } from '../../lib/api'

// ── Someone new is answered from the notification that says they wrote ──────────────────────────
//
// The trust gate's note ("Someone you haven't paired messaged you on Telegram … Allow them to talk
// to it, or deny.") carried `actions: ["allow", "deny"]`, and the panel showed the words with no
// buttons: nothing here could answer it. It now offers Allow and Deny, and once answered says how.

const { API } = vi.hoisted(() => ({
  API: {
    notifications: vi.fn(),
    ackNotification: vi.fn(),
    autonomyLadder: vi.fn(),
    answerUnknownSender: vi.fn(),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))

const { NotificationsPage } = await import('./NotificationsPage')
const { ConsentDeclined } = await import('../../lib/securityConsent')

const ASKS: NotificationItem = {
  kind: 'warning', title: "Someone you haven't paired messaged you on Telegram",
  body: "Pat Example messaged your agent on Telegram and isn't paired. Allow them to talk to it, or deny.",
  ts: '2026-09-25T10:38:00+00:00', acked: false,
  event: 'channel.unknown_sender', provider: 'telegram', sender_id: '4242', sender_name: 'Pat Example',
  actions: ['allow', 'deny'],
}

function open(note: NotificationItem) {
  API.notifications.mockResolvedValue({ notifications: [note] })
  render(<NotificationsPage query={{ open: note.ts }} setQuery={vi.fn()} navigate={vi.fn()} />)
}

beforeEach(() => {
  sessionStorage.clear()
  API.notifications.mockReset()
  API.ackNotification.mockReset().mockResolvedValue({ ok: true })
  API.autonomyLadder.mockReset().mockRejectedValue(new Error('no ladder in this test'))
  API.answerUnknownSender.mockReset().mockResolvedValue({ ok: true, answer: 'allowed' })
})

describe('the note about someone new', () => {
  it('offers Allow, which answers the note', async () => {
    open(ASKS)
    fireEvent.click(await screen.findByRole('button', { name: /allow pat example/i }))
    await waitFor(() => expect(API.answerUnknownSender).toHaveBeenCalledWith(ASKS.ts, 'allow'))
  })

  it('offers Deny, which answers the note', async () => {
    open(ASKS)
    fireEvent.click(await screen.findByRole('button', { name: /^deny$/i }))
    await waitFor(() => expect(API.answerUnknownSender).toHaveBeenCalledWith(ASKS.ts, 'deny'))
  })

  it('a declined consent changes nothing and says no error', async () => {
    API.answerUnknownSender.mockRejectedValue(new ConsentDeclined('sender'))
    open(ASKS)
    fireEvent.click(await screen.findByRole('button', { name: /allow pat example/i }))
    await waitFor(() => expect(API.answerUnknownSender).toHaveBeenCalled())
    expect(screen.queryByText(/couldn't allow/i)).toBeNull()
  })

  it.each([
    ['allowed', /allowed\. pat example can talk to your agent/i],
    ['denied', /denied\. pat example can't talk to your agent/i],
  ] as const)('once %s, it says so and offers neither again', async (answer, said) => {
    open({ ...ASKS, trust_answer: answer, acked: true })
    expect(await screen.findByText(said)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /allow pat example/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /^deny$/i })).toBeNull()
  })

  it('any other note offers no such answer (the vacuity leg)', async () => {
    open({ kind: 'info', title: 'Standup nudge', body: 'Fired.', ts: ASKS.ts, acked: false })
    await screen.findByRole('button', { name: /^mark read$/i })
    expect(screen.queryByRole('button', { name: /^allow/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /^deny$/i })).toBeNull()
  })
})
