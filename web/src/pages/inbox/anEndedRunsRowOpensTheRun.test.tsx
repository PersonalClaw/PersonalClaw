/** The Inbox row a workflow run leaves when it ends needing you opens THAT run.
 *
 *  A run that fails or escalates raises one row naming its run (`refs.workflow`) and no step, so
 *  the pane offers the way to the run, where Retry is. The link spelled `workflows/<id>`, which is
 *  no route of the Workflows section: it read the id as nothing and showed the list of runs. A
 *  gate's own "Open the run" (its fallback once the run has ended) did the same. Both now go to
 *  `workflows/runs/<id>`.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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
    id: 'needs_input_2', channel: 'loop', channel_name: 'loop', message,
    sender_id: 'loop', sender_name: 'loop', item_kind: 'needs_input', classification: 'needs_reply',
    confidence: 'high', status: 'pending', refs,
  } as InboxItem
}

afterEach(() => { vi.clearAllMocks() })

describe('an ended run’s Inbox row', () => {
  it('🔑 opens the run it is about, and asks nothing', async () => {
    const navigate = vi.fn()
    const ended = row(
      { workflow: 'run-7f3c' },
      'Workflow run stopped before it finished\n\ndeep-research — The run continued past “investigate”, which escalated.',
    )
    render(<InboxDetail item={ended} onChanged={() => {}} navigate={navigate} />)

    expect(screen.queryByText('Waiting on you')).toBeNull()
    expect(continuations).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /Open the workflow run/ }))
    expect(navigate).toHaveBeenCalledWith('workflows/runs/run-7f3c')
  })

  it('a gate whose run has ended offers the run at its own route too', async () => {
    continuations.mockResolvedValue({ continuations: [], run_status: 'escalated' })
    const navigate = vi.fn()
    render(
      <InboxDetail item={row({ workflow: 'run-7f3c', workflow_node: 'approve' }, 'Approve the draft?')}
        onChanged={() => {}} navigate={navigate} />,
    )
    await waitFor(() => expect(screen.getByText(/This run was escalated/)).toBeTruthy())
    await userEvent.click(screen.getByText('Open the run'))
    expect(navigate).toHaveBeenCalledWith('workflows/runs/run-7f3c')
  })
})
