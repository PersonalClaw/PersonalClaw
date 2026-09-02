import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { InboxPage } from './InboxPage'
import type { InboxItem, InboxStatus } from '../../lib/api'

// ── INU-9: the tray's `?capture=1` must WRITE, not just navigate ────────────────────────────
//
// DC-4 shipped a tray row "Quick Capture Note…" that deep-links `${DEEP_LINKS.inbox}?capture=1`
// (`desktop/main.js`). Measured before this change: `capture=1` appeared in exactly ONE place in
// the product — that producer — plus three lines of plan prose conceding it. `web/src` had zero
// readers; `useHashRoute` parses every param generically into `query`, and `InboxPage` read only
// `filter`, `kind`, `q`, `open` and `settings`. So the flag was parsed and dropped: the menu item
// opened the inbox and wrote nothing.
//
// The pair below is the whole point, and it is a PAIR on purpose:
//
//   1. `?capture=1` opens the compose surface and its Save reaches `POST /api/inbox/notes`.
//   2. Plain `#/inbox` (no flag) renders the ordinary inbox with NO compose surface.
//
// Delete the `useQueryFlag(query, setQuery, 'capture')` reader and (1) reds while (2) stays
// green — which is what makes (1) evidence about the FLAG rather than about the modal existing.
// Without (2), a reader that opened the composer unconditionally would pass (1) and would have
// broken every ordinary visit to the inbox.

const createInboxNote = vi.fn((_text: string) =>
  Promise.resolve({ ok: true, id: 'user_note_abc_1.0', item: {} as InboxItem }),
)

const ITEMS: InboxItem[] = []
const STATUS: InboxStatus = {
  enabled: true,
  pending_count: 0,
  total_count: 0,
  health: { running: true },
  sources: [],
  watched_channels: [],
}

vi.mock('../../lib/api', () => ({
  api: {
    inbox: () => Promise.resolve(ITEMS),
    inboxStatus: () => Promise.resolve(STATUS),
    createInboxNote: (text: string) => createInboxNote(text),
    markInboxSeen: () => Promise.resolve({ ok: true, seen: 0 }),
    // Opening a row fires an engagement signal + a seen mark; both are best-effort on the
    // page, but an unmocked method rejects as "not a function" and blanks the panel.
    openInboxItem: () => Promise.resolve({ ok: true }),
    updateInboxItem: () => Promise.resolve({} as InboxItem),
    favoriteInboxItem: () => Promise.resolve({ ok: true, favorited: true }),
    draftInboxReply: () => Promise.resolve({} as InboxItem),
    sendInboxReply: () => Promise.resolve({ ok: true }),
    restoreInboxItem: () => Promise.resolve({} as InboxItem),
    dismissAllInbox: () => Promise.resolve({ ok: true, dismissed: 0 }),
    restartInbox: () => Promise.resolve({ ok: true }),
    digestInboxChannel: () => Promise.resolve({} as InboxItem),
  },
}))
// The live socket and the measured list are irrelevant to the flag wiring, and both need a
// browser to behave. Children that fetch on mount are stubbed for the same reason.
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
// The panel HEADER (title + icon) is `InboxPage`'s to compose; the body is `InboxDetail`'s own
// concern with its own tests (`restoreFilteredItem.test.tsx`). Stubbed so these assertions read
// the header rather than dragging in the detail pane's fetch surface.
vi.mock('./InboxDetail', () => ({ InboxDetail: () => null }))
vi.mock('./InboxSettingsPanel', () => ({ InboxSettingsPanel: () => null }))
vi.mock('./ProposalsLens', () => ({ ProposalsLens: () => null }))
vi.mock('./TriageDigestCard', () => ({ TriageDigestCard: () => null }))

function renderInbox(query: Record<string, string>) {
  const setQuery = vi.fn()
  const r = render(<InboxPage query={query} setQuery={setQuery} navigate={() => {}} />)
  return { ...r, setQuery }
}

/** The compose surface, identified by its dialog role + accessible name rather than by a class
 *  or a test id — that is the thing a user meets, and it is what a screen reader announces. */
function composer(): HTMLElement | null {
  return screen.queryByRole('dialog', { name: /capture a note/i })
}

describe('INU-9 — the tray deep link reaches the note endpoint', () => {
  beforeEach(() => createInboxNote.mockClear())

  it('`?capture=1` opens the compose surface', async () => {
    renderInbox({ capture: '1' })
    await waitFor(() => expect(composer()).not.toBeNull())
  })

  it('and saving from it POSTs the note text', async () => {
    renderInbox({ capture: '1' })
    await waitFor(() => expect(composer()).not.toBeNull())
    const box = screen.getByRole('textbox', { name: /note/i })
    fireEvent.change(box, { target: { value: '  Chase the invoice discrepancy  ' } })
    fireEvent.click(screen.getByRole('button', { name: /save to inbox/i }))
    // Trimmed, because leading/trailing whitespace is not part of what the user wrote.
    await waitFor(() =>
      expect(createInboxNote).toHaveBeenCalledWith('Chase the invoice discrepancy'),
    )
  })

  it('closing the composer clears the flag, so Back does not reopen it', async () => {
    const { setQuery } = renderInbox({ capture: '1' })
    await waitFor(() => expect(composer()).not.toBeNull())
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }))
    await waitFor(() => expect(setQuery).toHaveBeenCalledWith({ capture: null }, undefined))
  })

  // ── the vacuity partner ──
  // This one must stay GREEN when the `capture` reader is deleted. It is what proves the two
  // tests above measure the FLAG and not merely the modal's existence.
  it('plain inbox navigation renders the inbox and NO compose surface', async () => {
    renderInbox({})
    await waitFor(() => expect(screen.getByText('Inbox')).toBeTruthy())
    expect(composer(), 'an unrequested modal would hijack every visit to the inbox').toBeNull()
    expect(createInboxNote).not.toHaveBeenCalled()
  })

  it('the header offers the same capture surface, so the tray is not its only entrance', async () => {
    const { setQuery } = renderInbox({})
    const btn = await waitFor(() => screen.getByRole('button', { name: /capture a note/i }))
    fireEvent.click(btn)
    // Sets the SAME flag the tray deep-links — one surface, two entrances, no second path.
    expect(setQuery).toHaveBeenCalledWith({ capture: '1' }, undefined)
  })
})

describe('a non-channel row names itself by its KIND, header included', () => {
  // The row and `InboxDetail` both already made this call; the SidePanel header had drifted, so
  // INU-9's own surface opened a panel titled "user" (measured in a browser). Asserted for the
  // note AND for a synthesized sibling, so the fix is the file's shared rule rather than a
  // note-only special case — and for a channel row, which must still lead with its sender.
  const row = (over: Partial<InboxItem>): InboxItem => ({
    id: 'x_1.0', channel: '', channel_name: '', message: 'the body',
    sender_id: 'user', sender_name: 'user', classification: 'needs_reply',
    confidence: 'high', status: 'seen', ...over,
  } as InboxItem)

  it.each([
    ['user_note', 'Notes'],
    ['digest', 'Digests'],
    ['needs_input', 'Needs you'],
  ])('a %s panel is titled by its kind (%s), not by the emitting subsystem', async (kind, label) => {
    ITEMS.splice(0, ITEMS.length, row({ id: 'k_1.0', item_kind: kind as InboxItem['item_kind'] }))
    try {
      renderInbox({ open: 'k_1.0' })
      await waitFor(() => expect(screen.getByRole('region', { name: label })).toBeTruthy())
      expect(screen.queryByRole('region', { name: 'user' })).toBeNull()
    } finally {
      ITEMS.length = 0
    }
  })

  it('a channel-backed row still leads with its sender — that IS its identity', async () => {
    ITEMS.splice(0, ITEMS.length, row({
      id: 'm_1.0', item_kind: 'message', sender_name: 'Priya', channel_name: 'ops',
    }))
    try {
      renderInbox({ open: 'm_1.0' })
      await waitFor(() => expect(screen.getByRole('region', { name: 'Priya' })).toBeTruthy())
    } finally {
      ITEMS.length = 0
    }
  })
})

describe('INU-9 — a capture that fails must not eat the note', () => {
  beforeEach(() => createInboxNote.mockClear())

  it("shows the server's own sentence and KEEPS the typed text", async () => {
    // Not a generic "Save failed": `note_not_saved`'s message is the one that tells the user
    // their words are still in the box, and `note_too_long` carries the real count. Replacing
    // either with a fixed string throws away the actionable half.
    createInboxNote.mockImplementationOnce(() =>
      Promise.reject(new Error('The note could not be written to the inbox, so it was not kept.')),
    )
    renderInbox({ capture: '1' })
    const box = await waitFor(() => screen.getByRole('textbox', { name: /note/i }))
    fireEvent.change(box, { target: { value: 'Something I cannot afford to lose' } })
    fireEvent.click(screen.getByRole('button', { name: /save to inbox/i }))

    // role="alert", so it is announced and not merely coloured.
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent).toMatch(/could not be written/i)
    // The only copy of the text is this textarea; the modal stays open holding it.
    expect((box as HTMLTextAreaElement).value).toBe('Something I cannot afford to lose')
    expect(composer()).not.toBeNull()
  })

  it('an empty note never reaches the network', async () => {
    renderInbox({ capture: '1' })
    await waitFor(() => expect(composer()).not.toBeNull())
    const box = screen.getByRole('textbox', { name: /note/i })
    fireEvent.change(box, { target: { value: '   \n  ' } })
    fireEvent.click(screen.getByRole('button', { name: /save to inbox/i }))
    expect(createInboxNote).not.toHaveBeenCalled()
    // And the disabled control says WHY rather than being inertly greyed out.
    expect(screen.getByRole('button', { name: /save to inbox/i })).toHaveAttribute('title', 'Write the note first')
  })
})
