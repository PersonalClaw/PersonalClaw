import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { InboxPage } from './InboxPage'
import type { InboxItem, InboxStatus } from '../../lib/api'

// ── issue 409's REACH half: the VACUITY PARTNER for the Dismiss-all gate ──
//
// The positive half of this landed separately as issue 493 and is owned by
// `oneOpenDefinition.test.tsx`: the control renders over a browsed queue, its confirm is sized
// from `open_count` (the set `open_items()` actually sweeps) and not from a pending-only count,
// its body names the already-read rows, and declining does not sweep. None of that is repeated
// here — a second copy of an assertion is how two rails come to disagree about one rule.
//
// What no rail asserted is the NEGATIVE: that `(status?.open_count ?? 0) > 0` is a real gate.
// Every positive test above would pass just as well if the control were rendered
// unconditionally, which would put a danger-confirmed bulk sweep on an empty inbox. That is the
// one claim this file makes.
//
// 🪤 This file originally carried the positive half too, written when the status payload still
// published `pending_count` alongside `open_count`. Issue 493 REMOVED that field rather than
// publishing both (two counts is what licensed the header/filter disagreement in the first
// place), so those fixtures no longer type-check and one of them asserted the
// "N pending · N total" header that 493 deliberately replaced with "N open · N total".

const ITEMS: InboxItem[] = []

function status(overrides: Partial<InboxStatus>): InboxStatus {
  return {
    enabled: true,
    open_count: 0,
    total_count: 0,
    health: { running: true },
    sources: [],
    watched_channels: [],
    ...overrides,
  }
}

let STATUS: InboxStatus = status({})

vi.mock('../../lib/api', () => ({
  api: {
    inbox: () => Promise.resolve(ITEMS),
    inboxStatus: () => Promise.resolve(STATUS),
    markInboxSeen: () => Promise.resolve({ ok: true, seen: 0 }),
    openInboxItem: () => Promise.resolve({ ok: true }),
    updateInboxItem: () => Promise.resolve({} as InboxItem),
    favoriteInboxItem: () => Promise.resolve({ ok: true, favorited: true }),
    dismissAllInbox: () => Promise.resolve({ ok: true, dismissed: 0, proposals_rejected: 0 }),
    restartInbox: () => Promise.resolve({ ok: true }),
    digestInboxChannel: () => Promise.resolve({} as InboxItem),
    createInboxNote: () => Promise.resolve({ ok: true, id: 'n', item: {} as InboxItem }),
  },
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('./InboxDetail', () => ({ InboxDetail: () => null }))
vi.mock('./InboxSettingsPanel', () => ({ InboxSettingsPanel: () => null }))
vi.mock('./ProposalsLens', () => ({ ProposalsLens: () => null }))
vi.mock('./TriageDigestCard', () => ({ TriageDigestCard: () => null }))

function renderInbox() {
  return render(<InboxPage query={{}} setQuery={vi.fn()} navigate={() => {}} />)
}

/** Found by accessible name, because that is the control a user (and a screen reader) meets. */
function dismissAll(): HTMLElement | null {
  return screen.queryByRole('button', { name: /dismiss all/i })
}

describe('issue 409 — the Dismiss-all gate is a real gate', () => {
  it('does not render the control when nothing is open at all', async () => {
    STATUS = status({ open_count: 0, total_count: 12 })
    renderInbox()
    // The sibling control proves the header rendered — otherwise "absent" would be vacuous, and
    // this test would pass on a page that failed to mount at all.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /restart sources/i })).not.toBeNull(),
    )
    expect(dismissAll()).toBeNull()
  })

  it('renders it when rows are open, so the assertion above is not vacuous either', async () => {
    STATUS = status({ open_count: 32, total_count: 40 })
    renderInbox()
    await waitFor(() => expect(dismissAll()).not.toBeNull())
  })
})
