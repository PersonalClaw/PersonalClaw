import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ApiError, type WorkflowRunDetailData, type WorkflowWorkspaceReview } from '../../lib/api'
import { WorkspacePanel } from './WorkspacePanel'
import { WorkflowRunDetail } from './WorkflowRunDetail'

// ── WF2WOR-4: the code-run cockpit's workspace panel (WORK-CONTAINERS §4.1, criterion 7) ────
//
// The panel fetches `api.workflowRunWorkspace(runId)` ON OPEN and renders the diff plus the two
// reintegration verbs. What these pins actually protect:
//   1. changed files + the two verbs render, and each verb shows the COMMAND it corresponds to —
//      the offer is readable before it is acted on;
//   2. reintegration is OFFERED, never performed: a conflicted apply is marked unsafe, checkout
//      stays safe, and NO api method that performs either verb exists to be called;
//   3. a run with no declared workspace renders an explanation, not an empty panel — a workspace
//      is a declaration rather than a default, so this is the COMMON case;
//   4. setup failures surface here (they never failed the run, by contract) and a degraded
//      provisioning is visible (a scratch fallback has no branch, so the verbs are inapplicable
//      rather than broken);
//   5. the run cockpit exposes the trigger on a terminal run — exactly when a user decides what
//      to do with the work.
//
// The api module is mocked at the boundary with the REAL ApiError kept: the panel branches on
// `instanceof ApiError` for the 404, and a fake class would fall through to the generic path.

const workflowRunWorkspace = vi.fn<(id: string) => Promise<WorkflowWorkspaceReview>>()
const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const workflowContinuations = vi.fn<(id: string) => Promise<{ continuations: [] }>>()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRunWorkspace: (id: string) => workflowRunWorkspace(id),
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: (id: string) => workflowContinuations(id),
      workflowRunStreamUrl: () => 'http://localhost/stream',
    },
  }
})

function review(over: Partial<WorkflowWorkspaceReview> = {}): WorkflowWorkspaceReview {
  return {
    run_id: 'run-1',
    workspace: {
      run_id: 'run-1',
      path: '/tmp/worktrees/run-1',
      branch: 'pclaw/run-run-1',
      alive: true,
      dirty: true,
      changed: [
        { path: 'src/auth.py', status: 'modified', staged: false },
        { path: 'tests/test_auth.py', status: 'added', staged: true },
      ],
      preserved_workspace_path: '/tmp/worktrees/run-1',
    },
    reintegration: {
      run_id: 'run-1',
      branch: 'pclaw/run-run-1',
      changed_files: 2,
      conflicts: [],
      verbs: [
        { verb: 'apply_locally', label: 'Apply Locally', detail: 'put the diff in your working tree', safe: true },
        { verb: 'checkout_branch', label: 'Checkout Branch Locally', detail: 'switch to the branch', safe: true },
      ],
      note: 'Nothing is applied automatically.',
    },
    declared: {
      mode: 'worktree',
      isolated: true,
      setup: { ran: ['npm ci'], skipped: [], failed: [], blocked_run: false },
    },
    ...over,
  }
}

describe('WorkspacePanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches on open and renders the diff plus both verbs with their commands', async () => {
    workflowRunWorkspace.mockResolvedValue(review())
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    await waitFor(() => expect(workflowRunWorkspace).toHaveBeenCalledWith('run-1'))

    const files = within(await screen.findByTestId('changed-files')).getAllByRole('listitem')
    expect(files).toHaveLength(2)
    expect(files[0]).toHaveTextContent('src/auth.py')
    expect(files[0]).toHaveTextContent('modified')
    // staged and unstaged are separate facts — collapsing them would make "discard" ambiguous.
    expect(files[1]).toHaveTextContent('staged')

    const verbs = within(screen.getByTestId('reintegration-verbs')).getAllByRole('listitem')
    expect(verbs).toHaveLength(2)
    // the COMMAND is shown, so the user reads what would happen before running it.
    expect(verbs[0]).toHaveTextContent('git merge --squash pclaw/run-run-1')
    expect(verbs[1]).toHaveTextContent('git checkout pclaw/run-run-1')
    expect(screen.getByTestId('workspace-mode')).toHaveTextContent('worktree')
    // a dirty live workspace surfaces its preserved path — the run record's own value.
    expect(screen.getByTestId('preserved-path')).toHaveTextContent('/tmp/worktrees/run-1')
  })

  it('marks a conflicted apply unsafe, keeps checkout safe, and performs NOTHING', async () => {
    workflowRunWorkspace.mockResolvedValue(review({
      reintegration: {
        run_id: 'run-1', branch: 'pclaw/run-run-1', changed_files: 2,
        conflicts: ['src/auth.py'],
        verbs: [
          { verb: 'apply_locally', label: 'Apply Locally', detail: 'put the diff in your tree', safe: false },
          // Checkout stays safe WITH conflicts: nothing merges, so there is nothing to conflict
          // with until the user decides to merge.
          { verb: 'checkout_branch', label: 'Checkout Branch Locally', detail: 'switch to the branch', safe: true },
        ],
        note: '1 file(s) conflict with your working tree — checkout is the safer verb.',
      },
    }))
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByTestId('unsafe-apply_locally')).toBeInTheDocument()
    expect(screen.queryByTestId('unsafe-checkout_branch')).not.toBeInTheDocument()
    expect(within(screen.getByTestId('workspace-conflicts')).getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByTestId('reintegration-note')).toHaveTextContent('checkout is the safer verb')
    // The ruling, asserted structurally: there is no client method that performs a verb, so the
    // panel cannot become one that acts. A future POST companion would red this line.
    const actual = await import('../../lib/api')
    expect(Object.keys(actual.api).filter((k) => /reintegrat|applyLocal|checkoutBranch/i.test(k))).toEqual([])
  })

  it('explains a run with no declared workspace instead of rendering an empty panel', async () => {
    workflowRunWorkspace.mockResolvedValue(review({
      workspace: {
        run_id: 'run-1', path: '', branch: '', alive: false, dirty: false,
        changed: [], preserved_workspace_path: '',
      },
      reintegration: { run_id: 'run-1', branch: '', changed_files: 0, conflicts: [], verbs: [], note: '' },
      declared: {},
    }))
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByTestId('workspace-none')).toHaveTextContent(/did not declare a workspace/i)
    expect(screen.queryByTestId('changed-files')).not.toBeInTheDocument()
    expect(screen.queryByTestId('reintegration-verbs')).not.toBeInTheDocument()
  })

  it('surfaces setup failures and a degraded provisioning', async () => {
    workflowRunWorkspace.mockResolvedValue(review({
      declared: {
        mode: 'worktree',
        isolated: false,
        degraded_reason: 'git is not on PATH; using a scratch dir',
        setup: { ran: [], skipped: [], failed: ['npm ci: exited 1: ENOTFOUND registry'], blocked_run: false },
      },
    }))
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)

    expect(await screen.findByTestId('workspace-degraded')).toHaveTextContent('git is not on PATH')
    expect(within(screen.getByTestId('setup-failed')).getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText(/Setup never blocks a run/i)).toBeInTheDocument()
  })

  it('shows a not-found message on a 404 without crashing the panel', async () => {
    workflowRunWorkspace.mockRejectedValue(new ApiError('gone', 404))
    render(<WorkspacePanel runId="run-1" onClose={() => {}} />)
    expect(await screen.findByText(/could not be found/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Close')).toBeInTheDocument()
  })
})

describe('the run cockpit exposes the Workspace trigger', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workflowContinuations.mockResolvedValue({ continuations: [] })
    workflowRunWorkspace.mockResolvedValue(review())
  })

  it('opens the panel from a TERMINAL run — when the user decides what to do with the work', async () => {
    // A complete run opens no SSE stream, so jsdom needs no EventSource.
    workflowRun.mockResolvedValue({
      run_id: 'run-1', workflow: 'demo', status: 'complete', spec_version: 1,
      nodes: [{ instance_path: 'root', node_id: 'build', state: 'done' }],
    })
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    const trigger = await screen.findByTitle(/Workspace — changed files/i)
    // Nothing is fetched until the panel is actually opened: answering costs a `git status` plus
    // a conflict probe, and most runs are never reviewed.
    expect(workflowRunWorkspace).not.toHaveBeenCalled()

    fireEvent.click(trigger)
    await waitFor(() => expect(workflowRunWorkspace).toHaveBeenCalledWith('run-1'))
    expect(await screen.findByTestId('workspace-panel-body')).toBeInTheDocument()
    expect(await screen.findByTestId('changed-files')).toBeInTheDocument()
  })
})
