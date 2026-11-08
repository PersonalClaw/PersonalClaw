import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { NotificationItem } from '../../lib/api'
import { HOSTILE, MD_LINE, inert, rendersLine } from '../../test/markdownSinks'

// ── The Notifications list renders what its notes say ──────────────────────────────────────────
//
// 🔴 A note's title and the first line of its body were drawn as text, marks and all, in every row
// of the list and in the panel's heading. The title now renders as a line, the first line as an
// excerpt (a fence line skipped, the cut left to CSS), the panel heads with the rendered title, and
// each row's name and the heading's tooltip are the same words, plain.

const { API } = vi.hoisted(() => ({
  API: { notifications: vi.fn(), ackNotification: vi.fn(), autonomyLadder: vi.fn() },
}))
vi.mock('../../lib/api', async (orig) => ({ ...(await orig<Record<string, unknown>>()), api: API }))

const { NotificationsPage } = await import('./NotificationsPage')

const SEEN = 'Skip model_calls in export_digest() in published.py'
const NOTE: NotificationItem = {
  kind: 'info', title: MD_LINE, body: '```sh\n**Key point**: run `make test`\n```', ts: '2026-11-07T10:38:00+00:00', acked: false,
}

beforeEach(() => {
  sessionStorage.clear()
  API.notifications.mockReset().mockResolvedValue({ notifications: [NOTE, { ...NOTE, ts: '2026-11-07T10:39:00+00:00', title: HOSTILE }] })
  API.ackNotification.mockReset().mockResolvedValue({ ok: true })
  API.autonomyLadder.mockReset().mockRejectedValue(new Error('no ladder in this test'))
})

describe('a notification row', () => {
  it('renders its title as a line and its first line as an excerpt, named in plain text', async () => {
    render(<NotificationsPage query={{}} setQuery={vi.fn()} navigate={vi.fn()} />)
    const title = await screen.findByTitle(SEEN)
    expect(rendersLine(title)).toEqual([])
    expect((await screen.findAllByText('Key point'))[0].tagName).toBe('STRONG')
    expect(document.body.textContent).not.toContain('```')
    for (const el of document.querySelectorAll('[aria-label], [title]')) {
      const name = `${el.getAttribute('aria-label') ?? ''} ${el.getAttribute('title') ?? ''}`
      expect(name, 'a name carries a mark').not.toMatch(/`|\*\*/)
    }
  })

  it('a hostile title is inert in its row', async () => {
    render(<NotificationsPage query={{}} setQuery={vi.fn()} navigate={vi.fn()} />)
    for (const line of await screen.findAllByText(/open the report/)) {
      expect(inert(line.closest('[title]') ?? line)).toEqual([])
    }
  })

  it('the panel heads with the rendered title, its tooltip the plain line', async () => {
    render(<NotificationsPage query={{ open: NOTE.ts }} setQuery={vi.fn()} navigate={vi.fn()} />)
    const heading = await screen.findByRole('heading', { level: 2, name: SEEN })
    expect(rendersLine(heading)).toEqual([])
    await waitFor(() => expect(heading.getAttribute('title')).toBe(SEEN))
  })
})
