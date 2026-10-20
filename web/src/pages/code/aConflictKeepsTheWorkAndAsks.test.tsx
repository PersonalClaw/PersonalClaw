/** A finished task whose work conflicts with your branch is kept, and the page asks what to do.
 *
 *  When a task's merge conflicted, a loop on autopilot reset the task's branch and ran the task
 *  again, twice at most, with nobody asked: the finished work went with the reset. Now the merge is
 *  undone, the work stays on its own branch, and the loop pauses with this card: what conflicts, the
 *  work as a review shows it, and three choices. Redo and Drop discard the work, so each asks first
 *  and names what goes; Resume is for a conflict she resolved herself.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { CodeProject, LoopConflictReview, LoopMergeConflict } from '../../lib/api'

const conflict: LoopMergeConflict = {
  task_id: 't-1', title: 'Escape the digest titles', branch: 'pclaw/task-t-1', tip: 'a1b2c3d4e5f6',
  into: 'main', files: ['CHANGELOG.md'], path: '/home/user/.personalclaw/projects/p-1/worktrees/t-1',
}
const review: LoopConflictReview = {
  into: 'main', files: ['CHANGELOG.md'], cut: false,
  task: {
    task_id: 't-1', title: 'Escape the digest titles', branch: 'pclaw/task-t-1', tip: 'a1b2c3d4e5f6',
    commits: ['a1b2c3d task t-1: work'], stat: ' CHANGELOG.md | 1 +',
    diff: 'diff --git a/CHANGELOG.md b/CHANGELOG.md\n+- Digest titles are escaped once.\n',
    path: conflict.path,
  },
}
const calls = vi.hoisted(() => ({ review: vi.fn(), choose: vi.fn(), action: vi.fn(), confirm: vi.fn() }))

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      approvals: () => Promise.resolve([]),
      uLoopKeptWork: () => Promise.resolve({ kept: [], resumable: false }),
      uLoopConflictReview: calls.review,
      uLoopConflict: calls.choose,
      uLoopAction: calls.action,
    },
  }
})
vi.mock('../../ui/dialog', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../ui/dialog')>()
  return { ...actual, confirmDestructive: calls.confirm }
})
vi.mock('../../lib/useChatSocket', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/useChatSocket')>()
  return { ...actual, useChatSocket: () => {} }
})

const { MergeConflict, dropBody, redoBody } = await import('./MergeConflict')
const { ProjectFooter } = await import('./CodeCockpitPage')
const { ApiError } = await import('../../lib/api')

beforeEach(() => {
  calls.review.mockReset().mockResolvedValue(review)
  calls.choose.mockReset().mockResolvedValue({ ok: true })
  calls.action.mockReset().mockResolvedValue({})
  calls.confirm.mockReset()
})
afterEach(cleanup)

describe('the card says what conflicts and that nothing is lost', () => {
  it('🔴 names the files, the branch the work is kept on, and the three choices', async () => {
    render(<MergeConflict loopId="l1" conflict={conflict} onChosen={() => {}} />)
    const card = await screen.findByRole('region', { name: '“Escape the digest titles” conflicts with main' })
    expect(card.textContent).toMatch(/The task is done, but its work conflicts with main in CHANGELOG\.md, so\s+none of it was merged\./)
    expect(card.textContent).toMatch(/It is kept as it is on branch pclaw\/task-t-1 until you choose\./)
    expect(await screen.findByText('What its work changes')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Redo on top of main/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Resume/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Drop its work/ })).toBeTruthy()
    // Resolving it herself needs to know where the branch is checked out.
    expect(screen.getByText(conflict.path)).toBeTruthy()
    expect(calls.review).toHaveBeenCalledWith('l1')
  })

  it('once git says it merges cleanly, the card says Resume merges it', async () => {
    calls.review.mockResolvedValue({ ...review, files: [] })
    render(<MergeConflict loopId="l1" conflict={conflict} onChosen={() => {}} />)
    expect(await screen.findByText(/It merges cleanly with main now\. Resume, and the loop merges it\./)).toBeTruthy()
  })
})

describe('her choice, and only hers', () => {
  it('🔴 Drop asks first, naming what goes, and does nothing on No', async () => {
    calls.confirm.mockResolvedValue(false)
    render(<MergeConflict loopId="l1" conflict={conflict} onChosen={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: /Drop its work/ }))
    await waitFor(() => expect(calls.confirm).toHaveBeenCalled())
    const [title, body] = calls.confirm.mock.calls[0]
    expect(title).toBe('Drop the work on “Escape the digest titles”?')
    expect(body).toBe(dropBody('pclaw/task-t-1', 1))
    expect(body).toMatch(/^Its branch pclaw\/task-t-1 is deleted from your repository with its 1 commit, its folder is deleted, and the task is cancelled\./)
    expect(calls.choose).not.toHaveBeenCalled()
  })

  it('🔴 Redo asks first, then sends the commit she read, and the page reads the loop again', async () => {
    calls.confirm.mockResolvedValue(true)
    const onChosen = vi.fn()
    render(<MergeConflict loopId="l1" conflict={conflict} onChosen={onChosen} />)
    fireEvent.click(await screen.findByRole('button', { name: /Redo on top of main/ }))
    await waitFor(() => expect(calls.choose).toHaveBeenCalledWith('l1', 'redo', 't-1', 'a1b2c3d4e5f6'))
    const [title, body] = calls.confirm.mock.calls[0]
    expect(title).toBe('Redo “Escape the digest titles” on top of main?')
    expect(body).toBe(redoBody('main', 'pclaw/task-t-1', 1))
    expect(body).toMatch(/The work it did, 1 commit on pclaw\/task-t-1 and anything not committed in its folder, is discarded\./)
    await waitFor(() => expect(onChosen).toHaveBeenCalled())
  })

  it('work that moved after she read it is read again, and nothing was redone or dropped', async () => {
    calls.confirm.mockResolvedValue(true)
    calls.choose.mockRejectedValue(new ApiError('moved', 409, 'loop_conflict_moved'))
    const onChosen = vi.fn()
    render(<MergeConflict loopId="l1" conflict={conflict} onChosen={onChosen} />)
    fireEvent.click(await screen.findByRole('button', { name: /Drop its work/ }))
    expect(await screen.findByText(/The work changed since you read it, so nothing was redone or dropped/)).toBeTruthy()
    await waitFor(() => expect(calls.review).toHaveBeenCalledTimes(2))
    expect(onChosen).not.toHaveBeenCalled()
  })

  it('Resume is for the conflict she resolved herself', async () => {
    const onChosen = vi.fn()
    render(<MergeConflict loopId="l1" conflict={conflict} onChosen={onChosen} />)
    fireEvent.click(await screen.findByRole('button', { name: /Resume/ }))
    await waitFor(() => expect(calls.action).toHaveBeenCalledWith('l1', 'resume'))
    await waitFor(() => expect(onChosen).toHaveBeenCalled())
    expect(calls.confirm).not.toHaveBeenCalled()
  })
})

describe('the cockpit', () => {
  it('🔴 shows the card, and not a question to answer in the steer box', async () => {
    const project = {
      id: 'l1', name: 'Fix the digest', status: 'needs_input', kind: 'code', stages: [],
      workspace_dir: '/home/user/newsfold', total_cycles: 3, max_cycles: 30,
      pending_question: { question: 'Task "Escape the digest titles" is done, but its work conflicts with main.', asked_by: 'scheduler', conflict },
    } as unknown as CodeProject
    render(<ProjectFooter project={project} gateFail={null} stalled={null} onNudged={() => {}} />)
    expect(await screen.findByRole('region', { name: '“Escape the digest titles” conflicts with main' })).toBeTruthy()
    expect(screen.queryByText('The worker needs your input')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Use your best judgment' })).toBeNull()
    // The worker asked nothing: the steer box steers it, it does not "answer" it.
    expect(screen.getByPlaceholderText('Steer the worker…')).toBeTruthy()
  })

  it('a question the worker asked is still answered in the steer box', async () => {
    const project = {
      id: 'l1', name: 'Fix the digest', status: 'needs_input', kind: 'code', stages: [],
      workspace_dir: '/home/user/newsfold', total_cycles: 3, max_cycles: 30,
      pending_question: { question: 'Which heading should the Fixed line go under?' },
    } as unknown as CodeProject
    render(<ProjectFooter project={project} gateFail={null} stalled={null} onNudged={() => {}} />)
    expect(await screen.findByText('The worker needs your input')).toBeTruthy()
    expect(screen.getByPlaceholderText('Answer the worker…')).toBeTruthy()
  })
})
