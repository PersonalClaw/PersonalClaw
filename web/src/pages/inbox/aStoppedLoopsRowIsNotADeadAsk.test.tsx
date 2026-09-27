/** The row a loop leaves when it stops is not rendered as a question nobody can answer.
 *
 *  A loop that escalates leaves one Inbox row naming its run (`refs.workflow`) and its loop. The
 *  pane treated every `needs_input` row with a run as a workflow gate, so it drew the gate form,
 *  found no continuation to answer, and said "This run was escalated, so the request can no longer
 *  be answered" under a title that asked for a decision. A gate's row names the step it waits at
 *  (`refs.workflow_node`); only such a row gets the form. The stopped loop's row gets the way to
 *  its run instead, where Retry and Fork are.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { InboxItem } from '../../lib/api'
import { InboxDetail } from './InboxDetail'

const continuations = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    workflowContinuations: (...args: unknown[]) => continuations(...args),
    updateInboxItem: () => Promise.resolve({}),
    restoreInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
  },
}))
vi.mock('../../ui/InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../../ui/Markdown', () => ({ Markdown: ({ children }: { children?: unknown }) => (children ?? null) }))

function row(refs: Record<string, unknown>, message: string): InboxItem {
  return {
    id: 'needs_input_1', channel: 'loop', channel_name: 'loop', message,
    sender_id: 'loop', sender_name: 'loop', item_kind: 'needs_input', classification: 'needs_reply',
    confidence: 'high', status: 'pending', refs,
  } as InboxItem
}

afterEach(() => { vi.clearAllMocks() })

describe('a stopped loop’s Inbox row', () => {
  const stopped = row(
    { loop: '7c3a5e36', loop_kind: 'general', workflow: '7c3a5e36' },
    'Loop stopped at its budget\n\nDraft a note — It used its budget of 1 cycle, and the judge did not accept the last one.',
  )

  it('🔑 opens its loop, and asks nothing', async () => {
    const navigate = vi.fn()
    render(<InboxDetail item={stopped} onChanged={() => {}} navigate={navigate} />)

    expect(screen.queryByText(/can no longer be answered/)).toBeNull()
    expect(screen.queryByText('Waiting on you')).toBeNull()
    expect(continuations).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /Go to loop/ }))
    expect(navigate).toHaveBeenCalledTimes(1)
  })

  it('a gate’s row, which names its step, still gets the form', () => {
    continuations.mockResolvedValue({ continuations: [], run_status: 'needs_input' })
    render(
      <InboxDetail
        item={row({ workflow: 'r1', workflow_node: 'approve' }, 'Approve the draft?')}
        onChanged={() => {}}
        navigate={() => {}}
      />,
    )
    expect(screen.getByText('Waiting on you')).toBeTruthy()
  })
})
