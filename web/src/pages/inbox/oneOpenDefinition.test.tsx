import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { InboxPage } from './InboxPage'
import { OPEN_STATUSES, isOpen } from './inboxMeta'
import { OPEN_STATUSES as LANE_OPEN_STATUSES, isOpenStatus, STATUS_OPEN } from '../../lib/attentionLanes'
import { closeDialog, subscribeDialogs } from '../../ui/dialog/dialogStore'
import type { InboxItem, InboxItemStatus, InboxStatus } from '../../lib/api'

// ── ONE definition of "open", shown once (issue 493) ────────────────────────────────────────
//
// The header read `status.pending_count` — PENDING only — while the Open filter, every
// classification count and every kind chip on the same screen used `isOpen = pending | seen`.
// Measured against a real gateway on a seeded home: header 33, Open filter 37, four `seen` rows
// apart. Neither was wrong on its own terms, so neither side looked broken.
//
// The second half was worse. Opening a row POSTs `/api/inbox/seen`, so merely GLANCING at an item
// moved the header 33 → 32 while the filters stayed at 37 and nothing was resolved — a count that
// falls because the user looked at something. And `dismissAll`'s confirm was sized from the same
// field: it offered to dismiss 33 and the endpoint answered `{"dismissed": 37}`.
//
// The ruling: a row you have read but not answered is still your work. SEEN is open, the wire
// carries ONE count (`open_count`), and "new" stays a per-ROW signal — the unread dot and accent
// rail, which still key off `pending`. That distinction is asserted here too, because collapsing it
// would be the opposite over-correction.

// `npm test` runs the web workspace with cwd = `web/`, which is the resolution every other
// source-inspection suite here uses (`design/accentChipTone.test.tsx`).
const SRC = join(process.cwd(), 'src')

/** 3 PENDING + 2 SEEN + 3 resolved. `open` (5) and `pending` (3) are different numbers, which is
 *  what makes every assertion below able to fail. */
const ITEMS: InboxItem[] = ([
  ['a1', 'pending', 'proposal'],
  ['a2', 'pending', 'proposal'],
  ['a3', 'pending', 'message'],
  ['b1', 'seen', 'proposal'],
  ['b2', 'seen', 'message'],
  ['c1', 'handled', 'proposal'],
  ['c2', 'dismissed', 'message'],
  ['c3', 'filtered', 'system'],
] as Array<[string, InboxItemStatus, string]>).map(([id, status, kind], i) => ({
  id, status, item_kind: kind,
  channel: 'agent', channel_name: '', thread_ts: null,
  message: `row ${id}`, sender_id: 's', sender_name: 'Sender',
  classification: 'fyi', confidence: 'high', draft: '',
  created_at: 1000 + i, source: 'native', can_reply: false, refs: {},
} as unknown as InboxItem))

const OPEN_TOTAL = 5
const PENDING_ONLY = 3

/** What the fixed server publishes: ONE count, over the open set. */
const STATUS: InboxStatus = {
  enabled: true,
  open_count: OPEN_TOTAL,
  total_count: ITEMS.length,
  health: { running: true },
  sources: [],
  watched_channels: [],
}

const dismissAllInbox = vi.fn(() => Promise.resolve({ ok: true, dismissed: OPEN_TOTAL }))
const markInboxSeen = vi.fn(() => Promise.resolve({ ok: true, seen: 1 }))

vi.mock('../../lib/api', () => ({
  api: {
    inbox: () => Promise.resolve(ITEMS),
    inboxStatus: () => Promise.resolve(STATUS),
    markInboxSeen: () => markInboxSeen(),
    openInboxItem: () => Promise.resolve({ ok: true }),
    updateInboxItem: () => Promise.resolve({} as InboxItem),
    favoriteInboxItem: () => Promise.resolve({ ok: true, favorited: true }),
    draftInboxReply: () => Promise.resolve({} as InboxItem),
    sendInboxReply: () => Promise.resolve({ ok: true }),
    restoreInboxItem: () => Promise.resolve({} as InboxItem),
    createInboxNote: () => Promise.resolve({ ok: true, id: 'n', item: {} as InboxItem }),
    dismissAllInbox: () => dismissAllInbox(),
    restartInbox: () => Promise.resolve({ ok: true }),
    digestInboxChannel: () => Promise.resolve({} as InboxItem),
  },
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('./InboxDetail', () => ({ InboxDetail: () => null }))
vi.mock('./InboxSettingsPanel', () => ({ InboxSettingsPanel: () => null }))
vi.mock('./ProposalsLens', () => ({ ProposalsLens: () => null }))
vi.mock('./TriageDigestCard', () => ({ TriageDigestCard: () => null }))

function renderInbox(query: Record<string, string> = {}) {
  const setQuery = vi.fn()
  const r = render(<InboxPage query={query} setQuery={setQuery} navigate={() => {}} />)
  return { ...r, setQuery }
}

describe('the predicate has one owner', () => {
  it('inboxMeta re-exports lib/attentionLanes rather than carrying a copy', () => {
    // Reference identity, not deep equality: a copy that happens to agree today is exactly what
    // produced two definitions in the first place.
    expect(isOpen).toBe(isOpenStatus)
    expect(OPEN_STATUSES).toBe(LANE_OPEN_STATUSES)
  })

  it('derives the list from the exhaustive record instead of a literal pair', () => {
    expect(OPEN_STATUSES).toEqual(Object.keys(STATUS_OPEN).filter((s) => STATUS_OPEN[s as InboxItemStatus]))
    expect(OPEN_STATUSES).toContain('seen')
  })

  it('treats an unrecognised status as OPEN — fail open on an attention surface', () => {
    // The direction is chosen: hiding an unresolved row is worse than showing a resolved one. This
    // used to differ between the two modules (the inbox page said closed, the lanes said open),
    // which is a third disagreement about the same word.
    expect(isOpen('some-future-status')).toBe(true)
    expect(isOpen(undefined)).toBe(true)
    expect(isOpen('handled')).toBe(false)
    expect(isOpen('filtered')).toBe(false)
  })
})

describe('the inbox header and its filters show the same number', () => {
  beforeEach(() => { dismissAllInbox.mockClear(); markInboxSeen.mockClear() })

  it('the header reads N open from open_count, not a pending-only count', async () => {
    renderInbox()
    await waitFor(() => expect(screen.getByText(`${OPEN_TOTAL} open · ${ITEMS.length} total`)).toBeTruthy())
    expect(screen.queryByText(`${PENDING_ONLY} pending · ${ITEMS.length} total`)).toBeNull()
  })

  it('and the Open filter counts the same rows the header does', async () => {
    renderInbox()
    await waitFor(() => expect(screen.getByText(`${OPEN_TOTAL} open · ${ITEMS.length} total`)).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /filter & sort/i }))
    // Each option renders with its live count (`ui/FilterRow`, an aria-pressed button). The Open row
    // must read the same 5 the header does — the two numbers a user sees side by side, which is the
    // entire issue.
    const openOption = await waitFor(() => {
      const row = screen.getAllByRole('button').find((b) => /^Open\d/.test(b.textContent || ''))
      if (!row) throw new Error('the Open filter row never rendered')
      return row
    })
    expect(openOption.textContent).toBe(`Open${OPEN_TOTAL}`)
    // And the sibling counts stay their own facts — All is every row, Done is the resolved ones.
    const named = (re: RegExp) => screen.getAllByRole('button').find((b) => re.test(b.textContent || ''))?.textContent
    expect(named(/^All\d/)).toBe(`All${ITEMS.length}`)
    expect(named(/^Done\d/)).toBe('Done2')
  })

  it('lists every open row, including the ones already read', async () => {
    renderInbox()
    // The default filter is `open`; a SEEN row must still be in the list. Before the ruling was
    // settled, "make the header match the filters" was the tempting fix — it would have deleted
    // these two rows from the user's queue instead.
    await waitFor(() => expect(screen.getByText('row b1')).toBeTruthy())
    expect(screen.getByText('row b2')).toBeTruthy()
    expect(screen.queryByText('row c1')).toBeNull()
  })

  it('sizes the Dismiss-all confirm from the count the sweep will reach', async () => {
    // Read off the dialog STORE rather than the DOM: `confirm()` is imperative and `DialogHost`
    // renders app-wide, not inside this page. Same approach `ui/dialog/…SaysWhatGoes.test.ts` takes.
    let seen: { id: number; title: string; body: unknown } | undefined
    const stop = subscribeDialogs((ds) => {
      const top = ds[ds.length - 1]
      if (top) seen = { id: top.id, title: top.title, body: top.body }
    })
    try {
      renderInbox()
      await waitFor(() => expect(screen.getByRole('button', { name: /dismiss all/i })).toBeTruthy())
      fireEvent.click(screen.getByRole('button', { name: /dismiss all/i }))
      await waitFor(() => expect(seen).toBeTruthy())
      // "5 open", not "3 pending": the endpoint sweeps `open_items()`, so the confirm must name that
      // number or it understates the blast radius of the page's most destructive control. Measured
      // before the fix: the dialog said 33 and `dismiss-all` answered `{"dismissed": 37}`.
      expect(seen!.title).toBe(`Dismiss all ${OPEN_TOTAL} open items?`)
      expect(seen!.title).not.toContain(`${PENDING_ONLY}`)
      expect(String(seen!.body)).toContain('including ones you have already read')
      // Declining must not sweep — otherwise the count assertion above would be the only thing
      // standing between a user and an unconfirmed bulk dismiss.
      closeDialog(seen!.id, false)
      await waitFor(() => expect(dismissAllInbox).not.toHaveBeenCalled())
    } finally {
      stop()
    }
  })
})

describe('the source itself', () => {
  const page = readFileSync(join(SRC, 'pages/inbox/InboxPage.tsx'), 'utf8')

  it('never reads a pending-only count off the wire', () => {
    // Source inspection, because a second count field is the shape of the defect rather than one
    // wrong number: any surface that renders it re-opens the disagreement. The comments explaining
    // the fix DO name the old field, so the check is on code lines only.
    const code = page.split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l))
    expect(code.filter((l) => l.includes('pending_count'))).toEqual([])
    expect(page).toContain('status.open_count')
  })

  it('keeps the unread signal per-row, where "new" belongs', () => {
    // The correction is about COUNTS, not about the read/unread boundary. The accent rail and the
    // dot still distinguish a never-opened row; collapsing that would be the opposite mistake.
    expect(page).toContain("const unread = it.status === 'pending'")
    expect(page).toContain('accent={unread ? accentTone : undefined}')
  })
})
