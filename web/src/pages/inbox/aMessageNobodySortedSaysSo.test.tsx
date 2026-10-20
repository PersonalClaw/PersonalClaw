import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { InboxDetail } from './InboxDetail'
import { confMeta, verdictMeta } from './inboxMeta'
import type { InboxItem } from '../../lib/api'

// ── A message nobody has sorted says so, and offers nothing to rate ───────────────────────────
//
// Every mail row read "Needs reply · Needs review" with Mark accurate / Mark wrong under it, and no
// model had read any of them: the verdict was the row's stamped default, and the panel fell back to
// "Needs reply" and "Needs review" for a row with none. A verdict given there was credited to the
// sorting prompt. Now a message waits for the background sorter as "Not sorted yet"; one it could not
// sort says why and can go again; and the thumbs appear only where the server names the prompt that
// made the verdict.

const sortInboxItem = vi.fn((_id: string) => Promise.resolve({}))

vi.mock('../../lib/api', () => ({
  api: {
    updateInboxItem: () => Promise.resolve({}),
    restoreInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
    draftInboxReply: () => Promise.resolve({}),
    sendInboxReply: () => Promise.resolve({}),
    sortInboxItem: (id: string) => sortInboxItem(id),
  },
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/FeedbackThumbs', () => ({
  FeedbackThumbs: ({ targetKind }: { targetKind: string }) => <span data-testid={`thumbs-${targetKind}`} />,
}))
vi.mock('./WorkflowGateActions', () => ({ WorkflowGateActions: () => null }))
vi.mock('../../ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

const PROMPT = { producer_kind: 'prompt', producer_id: 'native:task-inbox-classify' }

function mail(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: 'mail-inbox_ab12_1790000000.0',
    channel: 'dana@example.com',
    channel_name: 'dana@example.com',
    message: 'Your talk is accepted. Could you send the abstract?',
    sender_id: 'programme@example.org',
    sender_name: 'Programme team',
    item_kind: 'email',
    source: 'mail-inbox',
    can_reply: true,
    classification: '',
    confidence: '',
    status: 'pending',
    refs: {},
    ...over,
  } as InboxItem
}

function show(item: InboxItem, sortingHeld = '') {
  return render(<InboxDetail item={item} sortingHeld={sortingHeld} onChanged={() => {}} navigate={() => {}} />)
}

describe('a message nobody sorted says so', () => {
  beforeEach(() => sortInboxItem.mockClear())

  it('reads "Not sorted yet", with no verdict, no confidence and nothing to rate', () => {
    show(mail())
    expect(screen.getByText('Not sorted yet')).toBeInTheDocument()
    expect(screen.getByText('Waiting for the background model to sort it.')).toBeInTheDocument()
    // The Reclassify control still offers every verdict; none of them is shown as the row's.
    expect(screen.getByRole('radio', { name: 'Needs reply' })).not.toBeChecked()
    expect(screen.queryByText('Needs review')).not.toBeInTheDocument()
    expect(screen.queryByTestId('thumbs-inbox_classification')).not.toBeInTheDocument()
  })

  it('says why it waits when the sorter is held', () => {
    show(mail(), 'Incident mode is on, so nothing is sorted until it is turned off.')
    expect(screen.getByText('Incident mode is on, so nothing is sorted until it is turned off.')).toBeInTheDocument()
  })

  it('a message the sorter could not sort says why, and can go again', async () => {
    show(mail({ classify_error: 'The background model could not sort it. Details: the model is not loaded' }))
    expect(screen.getByText("Couldn't sort")).toBeInTheDocument()
    expect(screen.getByText(/the model is not loaded/)).toBeInTheDocument()
    expect(screen.queryByTestId('thumbs-inbox_classification')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Sort again/ }))
    await waitFor(() => expect(sortInboxItem).toHaveBeenCalledWith('mail-inbox_ab12_1790000000.0'))
  })

  it("a sorted message shows its verdict, its confidence and the prompt's thumbs", () => {
    show(mail({ classification: 'needs_reply', confidence: 'high', feedback_producers: { classification: PROMPT } } as Partial<InboxItem>))
    // The verdict chip, beside the Reclassify option that now reads as chosen.
    expect(screen.getAllByText('Needs reply')).toHaveLength(2)
    expect(screen.getByRole('radio', { name: 'Needs reply' })).toBeChecked()
    expect(screen.getByText('High confidence')).toBeInTheDocument()
    expect(screen.getByTestId('thumbs-inbox_classification')).toBeInTheDocument()
    expect(screen.queryByText('Not sorted yet')).not.toBeInTheDocument()
  })

  it("a verdict no prompt made (an agent's own post) has no confidence and no thumbs", () => {
    show(mail({ source: 'native', item_kind: 'message', classification: 'needs_reply', confidence: '' }))
    expect(screen.getAllByText('Needs reply')).toHaveLength(2)
    expect(screen.queryByText('Needs review')).not.toBeInTheDocument()
    expect(screen.queryByTestId('thumbs-inbox_classification')).not.toBeInTheDocument()
  })

  it('the row metas stand in for a verdict nobody made instead of inventing one', () => {
    expect(verdictMeta({ classification: '' })).toMatchObject({ label: 'Not sorted yet', sorted: false })
    expect(verdictMeta({ classification: '', classify_error: 'x' })).toMatchObject({ label: "Couldn't sort", sorted: false })
    expect(verdictMeta({ classification: 'fyi' })).toMatchObject({ label: 'FYI', sorted: true })
    expect(confMeta('')).toBeNull()
  })
})
