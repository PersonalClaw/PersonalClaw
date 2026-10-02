import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { NotificationItem } from '../../lib/api'

// ── Every list of the notification log shows it as the server sends it: newest first ──────────
//
// `GET /api/notifications` answered in the log's own order, oldest first, so each list had to turn
// it around, and the phone's Recent list, which shows the first six, did not: it listed the six
// oldest notes and none of the morning's. The order is the server's to state now, newest first by
// each note's own time (`tests/test_notifications_are_listed_newest_first.py`), and a list that
// turned it around again would put the oldest first. So each surface is handed the feed in the
// order the server sends it and must show its head first.

const { API } = vi.hoisted(() => ({
  API: {
    notifications: vi.fn(),
    ackNotification: vi.fn(),
    autonomyLadder: vi.fn(),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const { NotificationBell } = await import('../../ui/NotificationBell')
const { NotificationsPage } = await import('./NotificationsPage')
const { RecentSection } = await import('../companion/CompanionSections')
const { invalidateKeys } = await import('../../lib/data')

/** Nine notes an hour apart, as the server sends them: Note 9 is the newest. */
const NEWEST_FIRST = [9, 8, 7, 6, 5, 4, 3, 2, 1].map((k) => `Note ${k}`)
const feed = (): NotificationItem[] => NEWEST_FIRST.map((title, i) => ({
  kind: 'info', title, body: `What happened at ${8 - i}:00`,
  ts: `2026-08-26T0${8 - i}:00:00+00:00`, acked: false,
}))

/** The note titles a surface shows, top to bottom, once it shows any. */
const shown = async () => (await screen.findAllByText(/^Note \d$/)).map((el) => el.textContent)

beforeEach(() => {
  sessionStorage.clear()
  for (const key of ['notifications', 'notifications-companion']) invalidateKeys(key)
  // Fresh objects on every read, as `fetch().json()` hands back.
  API.notifications.mockReset().mockImplementation(async () => ({ notifications: feed(), unread: 9 }))
  API.ackNotification.mockReset().mockResolvedValue({ ok: true })
  API.autonomyLadder.mockReset().mockRejectedValue(new Error('no ladder in this test'))
})
afterEach(cleanup)

describe('every list of the notification log shows the newest first', () => {
  it("the bell's shade is the five newest", async () => {
    render(<NotificationBell navigate={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: /^Notifications, 9 unread$/ }))
    expect(await shown()).toEqual(NEWEST_FIRST.slice(0, 5))
  })

  it('the Notifications page lists them all, newest first', async () => {
    render(<NotificationsPage query={{}} setQuery={vi.fn()} navigate={vi.fn()} />)
    expect(await shown()).toEqual(NEWEST_FIRST)
  })

  it("the phone's Recent list is the six newest, and says it shows six of nine", async () => {
    render(<RecentSection />)
    expect(await shown()).toEqual(NEWEST_FIRST.slice(0, 6))
    expect(screen.getByText(/Showing 6 of 9/)).toBeTruthy()
  })
})
