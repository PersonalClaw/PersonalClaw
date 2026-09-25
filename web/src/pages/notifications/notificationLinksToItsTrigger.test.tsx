import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { notificationLink } from './notificationMeta'
import type { NotificationItem } from '../../lib/api'

// ── A trigger's notification leads back to the trigger (B8, c1b-075) ─────────────────────────────
//
// Every fire's notification record has carried `statusUrl: "#/triggers?open=<id>"` since R18, and
// nothing in `web/src` read it: the detail panel offered Mark read and Delete, so a notification about
// an automation was a dead end with no way back to the automation that sent it.
//
// `statusUrl` rides the note's `meta`, which any emitter — an app bundle included — controls. So the
// link is followed only as an IN-APP route: a scheme, a host or a `javascript:` value is no link.

describe('notificationLink — only an in-app route is a link', () => {
  it('a trigger fire opens that trigger', () => {
    expect(notificationLink({ statusUrl: '#/triggers?open=clock:standup-nudge' }))
      .toEqual({ label: 'Open trigger', path: 'triggers?open=clock:standup-nudge' })
  })

  it('a run link opens the run', () => {
    expect(notificationLink({ statusUrl: '#/workflows/runs/r-42' }))
      .toEqual({ label: 'Open run', path: 'workflows/runs/r-42' })
  })

  it('the missed-runs review opens the list', () => {
    expect(notificationLink({ statusUrl: '#/triggers' })).toEqual({ label: 'Open triggers', path: 'triggers' })
  })

  it.each([
    ['javascript:alert(1)'],
    ['https://evil.example/#/triggers'],
    ['//evil.example'],
    ['#//evil.example'],
    ['#/'],
    [' #/triggers'],
    [''],
  ])('%j is not a link', (statusUrl) => {
    expect(notificationLink({ statusUrl })).toBeNull()
  })

  it('a note with no statusUrl (or a non-string one) offers nothing', () => {
    expect(notificationLink({})).toBeNull()
    expect(notificationLink({ statusUrl: 42 as unknown as string })).toBeNull()
  })
})

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

const { NotificationsPage } = await import('./NotificationsPage')

const NOTE: NotificationItem = {
  kind: 'info', title: 'Standup nudge: review Q4 tasks', body: 'Fired by the Standup nudge trigger.',
  ts: '2026-09-25T10:38:00+00:00', acked: false, statusUrl: '#/triggers?open=clock:standup-nudge',
}

beforeEach(() => {
  sessionStorage.clear()
  API.notifications.mockReset()
  API.ackNotification.mockReset().mockResolvedValue({ ok: true })
  API.autonomyLadder.mockReset().mockRejectedValue(new Error('no ladder in this test'))
})

describe('the notification detail panel follows the link', () => {
  it('offers "Open trigger", which navigates to the trigger and marks the note read', async () => {
    API.notifications.mockResolvedValue({ notifications: [NOTE] })
    const navigate = vi.fn()
    render(<NotificationsPage query={{ open: NOTE.ts }} setQuery={vi.fn()} navigate={navigate} />)
    fireEvent.click(await screen.findByRole('button', { name: /open trigger/i }))
    expect(navigate).toHaveBeenCalledWith('triggers?open=clock:standup-nudge')
    expect(API.ackNotification).toHaveBeenCalledWith(NOTE.ts)
  })

  it('a note without a link offers no such button (the vacuity leg)', async () => {
    API.notifications.mockResolvedValue({ notifications: [{ ...NOTE, statusUrl: undefined }] })
    render(<NotificationsPage query={{ open: NOTE.ts }} setQuery={vi.fn()} navigate={vi.fn()} />)
    await screen.findByRole('button', { name: /^mark read$/i })
    expect(screen.queryByRole('button', { name: /^open/i })).toBeNull()
  })
})
