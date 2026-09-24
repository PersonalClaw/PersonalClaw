import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── TSE2-3: a shared inbox shows every owner's items and counts only YOURS ────────────────────
//
// The product half of the atom. The backend rail (`tests/test_shared_inbox_multi_owner.py`) proves
// the counters and the two predicates on the server; this proves the three things only the rendered
// surface can be wrong about:
//
//   1. BOTH owners' rows are on screen. A shared inbox that quietly hid a teammate's rows would
//      look correct from the owner's seat and orphan every deep link into one.
//   2. The counter on screen says how many are MINE, not how many exist. "12 pending" in a queue
//      where nine are somebody else's is a number that misreads as personal backlog — which is the
//      same defect TSE2-1's "my runs" exclusion exists to prevent, one surface up.
//   3. A foreign row is LABELLED where it is read. `(from dana)` has to be on the ROW, not only
//      inside the panel: a label that appears after you open an item arrives after you have already
//      read the text as your own. Per `docs/architecture/shared-store-provider-conformance.md`
//      clause 2 — the same convention `identity.contributor_label` emits server-side, not a second
//      one invented here.
//
// 🪤 THIS FILE CANNOT PASS ON SINGLE-OWNER CODE, and not because of a string: `InboxItem` had no
// `owner_username` field and `InboxStatus` had no `owner` / `my_open_count` — so the fixture
// below describes a shape origin/main cannot produce.
//
// 🪤 The owner chips are derived from the ITEMS this page already reads, not from a fourth
// request to `/api/inbox/owners`. The first draft did add that query, and it broke four unrelated
// test files whose api mocks predated it — the same trap this file's sibling records about
// `proactiveDigest`, met from the other side. The endpoint still exists as API surface; the page
// derives locally exactly as it already does for the KIND chips.
//
// 🪤 The owner CHIPS are deliberately gated on more than one attributed owner being present (the
// dead-control rule the kind chips already follow), so the last test pins the solo case: one owner
// means no owner controls and the original unscoped count, i.e. a solo install is untouched.

const OWNER = 'keyur-golani'
const FOREIGN = 'dana'

function item(over: Record<string, unknown> = {}) {
  return {
    id: 'C1_1', channel: 'C1', channel_name: '#ops', thread_ts: null,
    message: 'the body', sender_id: 'U1', sender_name: 'Sender',
    classification: 'needs_reply', confidence: 'high', status: 'pending',
    created_at: 1700000000, item_kind: 'message', can_reply: true,
    owner_username: OWNER,
    ...over,
  }
}

const MINE = item({ id: 'C1_1', message: 'my own thing', owner_username: OWNER })
const THEIRS = item({ id: 'C1_2', message: 'please approve the invoice', owner_username: FOREIGN })
// The row that makes the two predicates distinguishable: written before attribution existed, so it
// counts as the owner's without being "authored by" them.
const LEGACY = item({ id: 'C1_3', message: 'from before attribution', owner_username: '' })

function mockApi(over: Record<string, unknown> = {}) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      inbox: () => Promise.resolve([MINE, THEIRS, LEGACY]),
      inboxStatus: () => Promise.resolve({
        enabled: true, health: {},
        open_count: 3, total_count: 3,
        owner: OWNER, my_open_count: 2, my_total_count: 2,
      }),
      // The page mounts TriageDigestCard, which reads this — a mock must cover what the tree
      // MOUNTS, not only what the assertion touches (the sibling rail's lesson).
      proactiveDigest: () => Promise.resolve({ installed: false }),
      openInboxItem: () => Promise.resolve({ ok: true }),
      markInboxSeen: () => Promise.resolve({ ok: true, seen: 0 }),
      ...over,
    },
  }))
}

async function renderInbox(query: Record<string, string> = {}) {
  const { InboxPage } = await import('./InboxPage')
  render(<InboxPage query={query} setQuery={() => {}} navigate={vi.fn()} />)
}

describe('the shared inbox surfaces every owner but counts only yours', () => {
  beforeEach(() => { vi.resetModules() })

  it('renders BOTH owners’ items', async () => {
    mockApi()
    await renderInbox()
    // Wait on the FOREIGN row specifically: waiting on the owner's own would pass against a
    // build that dropped every foreign row.
    await waitFor(() => expect(screen.getByText(/please approve the invoice/)).toBeInTheDocument())
    expect(screen.getByText(/my own thing/), "the owner's own row is still there").toBeInTheDocument()
    expect(screen.getByText(/from before attribution/)).toBeInTheDocument()
  })

  it('the counter says how many are MINE, not how many exist', async () => {
    mockApi()
    await renderInbox()
    // known-true: the owner-scoped sentence is on screen with both numbers in it.
    await waitFor(() => expect(screen.getByText(/2 of 3 open are yours/)).toBeInTheDocument())
    // known-false: the unscoped "3 pending" phrasing must NOT be what a shared inbox shows —
    // that is the number that misreads as personal backlog.
    expect(screen.queryByText(/^3 open ·/), 'a shared queue must not report 3 as yours').toBeNull()
  })

  it('labels a foreign row on the ROW, with the same "(from x)" form the server uses', async () => {
    mockApi()
    await renderInbox()
    await waitFor(() => expect(screen.getByText(/please approve the invoice/)).toBeInTheDocument())
    // The badge renders `from dana`; its title carries the full explanation. Queried by title so
    // the assertion is about the labelled REGION, not an incidental text match on the message.
    const badge = screen.getByTitle(`Attributed to ${FOREIGN} — quoted, not your own`)
    expect(badge).toBeInTheDocument()
    expect(badge.textContent).toContain(`from ${FOREIGN}`)
    // known-false: the owner's OWN row carries no label. Labelling every row would make the
    // label meaningless, which is the reason `contributor_label` labels only foreign records.
    expect(screen.queryByTitle(`Attributed to ${OWNER} — quoted, not your own`)).toBeNull()
  })

  it('offers per-owner filtering, and "Mine" is a separate scope from an owner handle', async () => {
    mockApi()
    await renderInbox()
    await waitFor(() => expect(screen.getByRole('radiogroup', { name: 'Filter by owner' })).toBeInTheDocument())
    const chips = screen.getByRole('radiogroup', { name: 'Filter by owner' })
    // Everyone (the shared default), Mine (belongs_to), and a chip per OTHER owner. There is
    // deliberately no chip for the unattributed bucket: it is not a person, and Mine covers it.
    expect(chips.textContent).toContain('Everyone')
    expect(chips.textContent).toContain('Mine')
    expect(chips.textContent).toContain(FOREIGN)
  })

  it('?owner=<handle> narrows to exactly that owner and excludes the unattributed row', async () => {
    mockApi()
    await renderInbox({ owner: FOREIGN })
    await waitFor(() => expect(screen.getByText(/please approve the invoice/)).toBeInTheDocument())
    expect(screen.queryByText(/my own thing/), 'not this owner’s').toBeNull()
    expect(screen.queryByText(/from before attribution/),
      'an unattributed row is not "authored by" anyone').toBeNull()
  })

  it('?owner=mine keeps the unattributed row — that is the belongs_to bargain', async () => {
    mockApi()
    await renderInbox({ owner: 'mine' })
    await waitFor(() => expect(screen.getByText(/my own thing/)).toBeInTheDocument())
    expect(screen.getByText(/from before attribution/),
      'an unattributed row counts as the owner’s').toBeInTheDocument()
    expect(screen.queryByText(/please approve the invoice/)).toBeNull()
  })

  it('a SOLO install gets no owner controls and the original unscoped count', async () => {
    // The other half: everything above must stay invisible on a single-owner inbox, or this atom
    // would have added a permanent control and a reworded header to every existing install.
    mockApi({
      inbox: () => Promise.resolve([MINE]),
      inboxStatus: () => Promise.resolve({
        enabled: true, health: {},
        open_count: 1, total_count: 1,
        owner: OWNER, my_open_count: 1, my_total_count: 1,
      }),
    })
    await renderInbox()
    await waitFor(() => expect(screen.getByText(/1 open · 1 total/)).toBeInTheDocument())
    expect(screen.queryByRole('radiogroup', { name: 'Filter by owner' }),
      'one owner is nothing to choose between').toBeNull()
    expect(screen.queryByText(/are yours/), 'no need to scope a queue that is entirely yours').toBeNull()
  })
})
