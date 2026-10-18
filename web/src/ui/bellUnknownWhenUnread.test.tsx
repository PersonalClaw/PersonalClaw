/**
 * The bell's count is a claim, and an unknown count never shows as zero.
 *
 * Since Home stopped badging unread notifications, the bell is the one unread count on screen. Its
 * read swallowed a failure and left `items` null, so the count fell back to 0: no badge, and the
 * label said plain "Notifications", the same as a day with nothing unread. Opening the shade then
 * said "Loading…" for as long as the page stayed open.
 *
 * The rules held here:
 *   1. A failed first read shows the unknown state: a muted mark on the bell, labelled in the
 *      header pills' wording ("unread — couldn’t be read"), and never a count or nothing at all.
 *   2. The shade says the read failed and offers Retry, not "Loading…" or "You're all caught up".
 *   3. The next read that succeeds clears it, and the bell shows the real count.
 *   4. A read that succeeds says what it read (the controls): nothing unread draws no mark, and
 *      three unread draws 3.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react'

const item = (ts: number, acked: boolean) => ({
  ts, kind: 'info', title: `Note ${ts}`, body: 'body', acked, mode: '', targets: [],
})

let read: () => Promise<unknown> = () => Promise.resolve({ notifications: [] })
const notified: string[] = []

vi.mock('../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../lib/api')>()
  return { ...mod, api: { ...mod.api, notifications: () => read() } }
})
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../app/appSdk', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  notify: (m: string) => { notified.push(m) },
}))

import { NotificationBell } from './NotificationBell'

const boom = () => Promise.reject(new Error('gateway down'))
const UNKNOWN = 'Notifications, unread — couldn’t be read'
const bell = () => screen.getByRole('button', { name: /^Notifications/ })

beforeEach(() => { read = () => Promise.resolve({ notifications: [] }); notified.length = 0 })
afterEach(() => cleanup())

describe('a failed read is unknown on the bell, never zero', () => {
  it('a failed first read shows the unknown mark, not an empty bell', async () => {
    read = boom
    render(<NotificationBell navigate={() => {}} />)
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe(UNKNOWN))
    expect(bell().getAttribute('title')).toBe('unread — couldn’t be read')
    const mark = within(bell()).getByText('—')
    // Muted, not the accent: we do not know anything waits, only that we could not look.
    expect(mark.getAttribute('class') ?? '').toMatch(/\btext-on-surface-low\b/)
    expect(mark.getAttribute('style') ?? '', 'coral is a real unread count').not.toMatch(/--color-primary/)
    expect(notified, 'a failed poll is shown, never toasted').toEqual([])
  })

  it('the shade says the read failed and offers Retry', async () => {
    read = boom
    render(<NotificationBell navigate={() => {}} />)
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe(UNKNOWN))
    fireEvent.click(bell())
    expect(await screen.findByText(/Couldn’t load your notifications/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
    expect(screen.queryByText('Loading…')).toBeNull()
    expect(screen.queryByText(/all caught up/)).toBeNull()
  })

  it('a later read that succeeds clears it and shows the real count', async () => {
    read = boom
    render(<NotificationBell navigate={() => {}} />)
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe(UNKNOWN))
    fireEvent.click(bell())
    read = () => Promise.resolve({ notifications: [item(2, false), item(1, false), item(0, true)] })
    fireEvent.click(await screen.findByRole('button', { name: /retry/i }))
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notifications, 2 unread'))
    expect(within(bell()).queryByText('—')).toBeNull()
    expect(screen.queryByText(/Couldn’t load your notifications/)).toBeNull()
  })
})

describe('a read that worked says what it read (the controls)', () => {
  it('nothing unread draws no mark', async () => {
    read = () => Promise.resolve({ notifications: [item(1, true)] })
    render(<NotificationBell navigate={() => {}} />)
    fireEvent.click(bell())
    await screen.findByText('Note 1')
    expect(bell().getAttribute('aria-label')).toBe('Notifications')
    expect(within(bell()).queryByText('—')).toBeNull()
  })

  it('three unread draws 3', async () => {
    read = () => Promise.resolve({ notifications: [item(3, false), item(2, false), item(1, false)] })
    render(<NotificationBell navigate={() => {}} />)
    await waitFor(() => expect(bell().getAttribute('aria-label')).toBe('Notifications, 3 unread'))
    expect(within(bell()).getByText('3')).toBeInTheDocument()
  })
})
