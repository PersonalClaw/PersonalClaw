import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { InboxPage } from './InboxPage'
import { closeDialog, subscribeDialogs } from '../../ui/dialog/dialogStore'
import type { InboxItem, InboxStatus } from '../../lib/api'

// ── "Needs reply" lists what needs her reply, and the page uses one word per filter ────────────
//
// Measured on a live install: Filter & sort › Needs reply 5 held her own captured note, three
// "Update available for …" notices and a "Loop stopped before it finished" row — none of them a
// message anyone waits on. Every row is stored with the verdict `needs_reply` until something
// triages it, only a channel message is ever triaged, and the filter read the stored verdict alone.
// An app-update notice also arrived with a kind this build does not know (`update`), which the
// page read as a message: a Reply arrow, a triage verdict, and the sender "apps" as its name.
//
// And the Dismiss all dialog promised "they stay readable under Handled", on a page whose filter
// for them is called "Done".

function row(id: string, over: Partial<InboxItem> & { item_kind?: string }): InboxItem {
  return {
    id, channel: 'agent', channel_name: '', thread_ts: null,
    message: `row ${id}`, sender_id: 's', sender_name: 'Sender',
    classification: 'needs_reply', confidence: 'high', draft: '', status: 'pending',
    created_at: 1000, source: 'native', can_reply: false, refs: {},
    ...over,
  } as unknown as InboxItem
}

const ITEMS: InboxItem[] = [
  // What does need a reply: a message someone sent, which the triage layer classified.
  row('email-asks', { item_kind: 'email', message: 'Can you send the slides by Friday?', can_reply: true,
    sender_name: 'Pat Example', channel_name: 'DM' }),
  row('email-fyi', { item_kind: 'email', classification: 'fyi', message: 'The venue changed.', can_reply: true }),
  row('email-done', { item_kind: 'email', status: 'handled', message: 'Already answered.', can_reply: true }),
  // What does not, though each is stored with the default verdict.
  row('note', { item_kind: 'user_note', message: 'Buy stamps on the way home', sender_name: 'user' }),
  row('update-new', { item_kind: 'system', message: 'Update available for Weather\n\nVersion 2 is available.', sender_name: 'apps' }),
  row('update-old', { item_kind: 'update' as InboxItem['item_kind'], message: 'Update available for Notes Sync', sender_name: 'apps', channel_name: 'apps' }),
  row('loop', { item_kind: 'needs_input', message: 'Loop stopped before it finished', sender_name: 'loop' }),
]

const STATUS: InboxStatus = {
  enabled: true, open_count: 6, total_count: ITEMS.length, health: { running: true },
  sources: [], watched_channels: [],
}

vi.mock('../../lib/api', () => ({
  api: {
    inbox: () => Promise.resolve(ITEMS),
    inboxStatus: () => Promise.resolve(STATUS),
    markInboxSeen: () => Promise.resolve({ ok: true, seen: 0 }),
    openInboxItem: () => Promise.resolve({ ok: true }),
    dismissAllInbox: () => Promise.resolve({ ok: true, dismissed: 0 }),
    restartInbox: () => Promise.resolve({ ok: true }),
  },
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('./InboxDetail', () => ({ InboxDetail: () => null }))
vi.mock('./InboxSettingsPanel', () => ({ InboxSettingsPanel: () => null }))
vi.mock('./ProposalsLens', () => ({ ProposalsLens: () => null }))
vi.mock('./TriageDigestCard', () => ({ TriageDigestCard: () => null }))

function renderInbox(query: Record<string, string> = {}) {
  return render(<InboxPage query={query} setQuery={vi.fn()} navigate={() => {}} />)
}

/** The Filter & sort menu's option rows, as "<label><count>" (`ui/FilterRow`). */
async function filterOptions(): Promise<string[]> {
  fireEvent.click(screen.getByRole('button', { name: /filter & sort/i }))
  return waitFor(() => {
    const texts = screen.getAllByRole('button').map((b) => b.textContent || '').filter((t) => /^(Open|Needs reply|All|Done)\d+$/.test(t))
    if (texts.length < 4) throw new Error(`the filter rows never rendered: ${texts}`)
    return texts
  })
}

describe('Needs reply', () => {
  it('lists only the open message that asks for a reply', async () => {
    renderInbox({ filter: 'needs_reply' })
    await waitFor(() => expect(screen.getByText('Can you send the slides by Friday?')).toBeTruthy())
    for (const text of ['row note', 'Buy stamps on the way home', 'Loop stopped before it finished',
      'The venue changed.', 'Already answered.']) {
      expect(screen.queryByText(text), `"${text}" is under Needs reply`).toBeNull()
    }
    expect(screen.queryByText(/Update available for/), 'an update notice is under Needs reply').toBeNull()
  })

  it('counts what it lists', async () => {
    renderInbox()
    await waitFor(() => expect(screen.getByText('Can you send the slides by Friday?')).toBeTruthy())
    expect(await filterOptions()).toContain('Needs reply1')
  })

  it('reads a kind it does not know as a system notice, not as a message from "apps"', async () => {
    renderInbox({ filter: 'all' })
    const notice = await waitFor(() => screen.getByRole('button', { name: /Update available for Notes Sync/ }))
    expect(notice.getAttribute('aria-label') ?? notice.textContent).toMatch(/^System/)
    expect(notice.textContent).not.toContain('#apps')
  })
})

describe('the Dismiss all dialog', () => {
  it('names the filter the dismissed rows stay under by the word the page shows for it', async () => {
    let body = ''
    let id = 0
    const stop = subscribeDialogs((ds) => {
      const top = ds[ds.length - 1]
      if (top) { body = String(top.body); id = top.id }
    })
    try {
      renderInbox()
      await waitFor(() => expect(screen.getByRole('button', { name: /dismiss all/i })).toBeTruthy())
      const settled = (await filterOptions()).find((t) => t.startsWith('Done'))
      expect(settled, 'the page has a Done filter').toBeTruthy()
      fireEvent.click(screen.getByRole('button', { name: /dismiss all/i }))
      await waitFor(() => expect(body).not.toBe(''))
      expect(body).toContain('they stay readable under Done.')
      expect(body).not.toContain('Handled')
      closeDialog(id, false)
    } finally {
      stop()
    }
  })
})
