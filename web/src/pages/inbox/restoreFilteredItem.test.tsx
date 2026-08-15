import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { InboxDetail } from './InboxDetail'
import type { InboxItem } from '../../lib/api'

// ── INU-6: a withheld item must be reachable and recoverable from the UI ──────────
//
// The backend files a refuted item as FILTERED and withholds its notification; the Python
// suite proves the fire-exactly-once server contract. What no Python test can see is whether
// a user can actually GET to that item and undo the filter. A FILTERED row with no visible
// Restore control would be the silent drop the design forbids — so this asserts, at the level
// a user meets it, that (1) a filtered item renders a Restore button, (2) clicking it calls
// the restore endpoint with the item id, and (3) a non-filtered item shows no such control
// (a Restore on a delivered item would be a dead control).

const restoreInboxItem = vi.fn((_id: string) => Promise.resolve({}))

vi.mock('../../lib/api', () => ({
  api: {
    restoreInboxItem: (id: string) => restoreInboxItem(id),
    updateInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
    draftInboxReply: () => Promise.resolve({}),
    sendInboxReply: () => Promise.resolve({}),
  },
}))
// Children that fetch or need context are irrelevant to the Restore wiring.
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/FeedbackThumbs', () => ({ FeedbackThumbs: () => null }))
vi.mock('./WorkflowGateActions', () => ({ WorkflowGateActions: () => null }))
vi.mock('../../ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

function makeItem(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'agent_request_x_100.0',
    channel: '',
    channel_name: '',
    message: 'A flagged claim',
    sender_id: '',
    sender_name: '',
    item_kind: 'agent_request',
    status: 'filtered',
    refs: { verify: 'refuted' },
    ...over,
  } as InboxItem
}

describe('INU-6 Restore in the inbox detail panel', () => {
  beforeEach(() => restoreInboxItem.mockClear())

  it('a filtered item offers Restore and calls the restore endpoint on click', async () => {
    const onChanged = vi.fn()
    render(<InboxDetail item={makeItem()} onChanged={onChanged} navigate={() => {}} />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /restore/i }))
    fireEvent.click(btn)
    await waitFor(() => expect(restoreInboxItem).toHaveBeenCalledWith('agent_request_x_100.0'))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('a delivered (pending) item shows no Restore control', () => {
    render(<InboxDetail item={makeItem({ status: 'pending' })} onChanged={() => {}} navigate={() => {}} />)
    expect(screen.queryByRole('button', { name: /restore/i })).toBeNull()
  })
})
