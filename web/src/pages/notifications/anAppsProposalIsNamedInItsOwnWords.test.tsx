import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { NotificationItem } from '../../lib/api'

// ── An app's proposal reads as the kind its manifest named ───────────────────────────────────────
//
// An app that may raise proposals declares each kind in its manifest with a name ("Draft reply"),
// and each app's kind is its own on the wire (`app:<name>/proposal:<suffix>`). The page names kinds
// from a map of its own, which can have no row for them, so the detail chip and the filter read the
// raw wire string ('proposal:draft') beside a plain bell. The note now carries its kind's name.

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

const PROPOSAL: NotificationItem = {
  kind: 'app:demo-proposer/proposal:draft', kind_label: 'Draft reply', item_kind: 'proposal',
  title: 'Reply to the venue about Saturday', body: 'Thanks, Saturday at 7 works for us.',
  ts: '2026-09-30T13:06:06+00:00', acked: false,
}

beforeEach(() => {
  sessionStorage.clear()
  API.notifications.mockReset().mockResolvedValue({ notifications: [PROPOSAL] })
  API.ackNotification.mockReset().mockResolvedValue({ ok: true })
  API.autonomyLadder.mockReset().mockRejectedValue(new Error('no ladder in this test'))
})

describe("an app's proposal on the Notifications page", () => {
  it('the detail panel names its kind as the app declared it, never the wire string', async () => {
    render(<NotificationsPage query={{ open: PROPOSAL.ts }} setQuery={vi.fn()} navigate={vi.fn()} />)
    expect(await screen.findByText('Draft reply')).toBeTruthy()
    expect(screen.queryByText(/proposal:draft/)).toBeNull()
  })

  it('the filter offers it by that name', async () => {
    render(<NotificationsPage query={{}} setQuery={vi.fn()} navigate={vi.fn()} />)
    await screen.findByText(PROPOSAL.title)
    fireEvent.click(screen.getByRole('button', { name: 'Filter & sort' }))
    expect(await screen.findByRole('button', { name: /Draft reply/ })).toBeTruthy()
    expect(screen.queryByText(/proposal:draft/)).toBeNull()
  })
})
