import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, waitFor } from '@testing-library/react'
import type { NotificationItem } from '../../lib/api'
import { HOSTILE, LEADING, MD_LINE, inert, rawSinks, rendersLine } from '../../test/markdownSinks'

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
    const strong = (await screen.findAllByText('Key point'))[0]
    expect(strong.tagName).toBe('STRONG')
    expect(strong.closest('p.truncate')!.querySelector('div, p, h1, h2, h3, h4, ul, ol, li, pre, blockquote, table'), 'an excerpt is a line').toBeNull()
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

  it('a hostile body is inert in its excerpt', async () => {
    API.notifications.mockResolvedValue({ notifications: [{ ...NOTE, ts: '2026-11-07T10:41:00+00:00', title: 'Build', body: HOSTILE }] })
    render(<NotificationsPage query={{}} setQuery={vi.fn()} navigate={vi.fn()} />)
    const sink = (await screen.findAllByText(/open the report/))[0].closest('p.truncate')!
    expect(sink, 'the excerpt').not.toBeNull()
    expect(inert(sink)).toEqual([])
  })

  it.each(LEADING)("%s: a title that reads as a block elsewhere is the text it is", async (_construct, text) => {
    API.notifications.mockResolvedValue({ notifications: [{ ...NOTE, ts: '2026-11-07T10:42:00+00:00', title: text, body: '' }] })
    render(<NotificationsPage query={{}} setQuery={vi.fn()} navigate={vi.fn()} />)
    await waitFor(() => expect(document.querySelector('span.text-\\[0\\.9375rem\\]')).not.toBeNull())
    expect(document.querySelector('span.text-\\[0\\.9375rem\\]')!.textContent).toBe(text)
  })

  it("a body's first line is named as its excerpt draws it: no block mark in any name", async () => {
    API.notifications.mockResolvedValue({ notifications: [{ ...NOTE, ts: '2026-11-07T10:40:00+00:00', title: 'Build', body: '## Failed on main\nmore' }] })
    render(<NotificationsPage query={{}} setQuery={vi.fn()} navigate={vi.fn()} />)
    await screen.findByText('Failed on main')
    const names = Array.from(document.querySelectorAll('[aria-label]')).map((el) => el.getAttribute('aria-label') ?? '')
    expect(names.some((n) => n.includes('Failed on main')), 'the row is named for its body').toBe(true)
    for (const name of names) expect(name, 'a name carries a block mark').not.toContain('## ')
  })

  it('the panel heads with the rendered title, its tooltip the plain line', async () => {
    render(<NotificationsPage query={{ open: NOTE.ts }} setQuery={vi.fn()} navigate={vi.fn()} />)
    const heading = await screen.findByRole('heading', { level: 2, name: SEEN })
    expect(rendersLine(heading)).toEqual([])
    await waitFor(() => expect(heading.getAttribute('title')).toBe(SEEN))
  })
})

describe("no file that draws a note or an Inbox row draws what an agent wrote raw", () => {
  const FIELDS = ['title', 'body', 'message', 'text', 'preview', 'reason']
  const FILES = [
    'pages/notifications/NotificationsPage.tsx', 'ui/NotificationBell.tsx', 'ui/Toaster.tsx', 'pages/inbox/InboxPage.tsx',
    'pages/inbox/InboxDetail.tsx', 'pages/inbox/ProposalsLens.tsx', 'pages/inbox/TriageDigestCard.tsx',
  ]
  /** Sinks in these files that draw no agent's words as text: a proposal's diff preview, which stays
   *  a diff. */
  const NOT_WRITTEN: Record<string, string[]> = {
    'pages/inbox/ProposalsLens.tsx': ['{p.preview}'],
  }
  const read = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')
  const raw = (rel: string, source = read(rel)) =>
    rawSinks(source, FIELDS).filter((sink) => !(NOT_WRITTEN[rel] ?? []).some((o) => sink.endsWith(`: ${o}`)))

  it.each(FILES)('%s', (rel) => {
    expect(raw(rel)).toEqual([])
  })

  it('the sinks that draw no agent\'s words are still drawn where they were', () => {
    for (const [rel, sinks] of Object.entries(NOT_WRITTEN)) {
      const found = rawSinks(read(rel), FIELDS)
      for (const sink of sinks) expect(found.some((f) => f.endsWith(`: ${sink}`)), `${rel}: ${sink}`).toBe(true)
    }
  })

  it.each(FILES)('plant: a title drawn raw in %s is caught', (rel) => {
    const planted = `${read(rel)}\n<p>{x.title}</p>`
    expect(raw(rel, planted)).toEqual([`${planted.split('\n').length}: {x.title}`])
  })
})
