/** A Code loop that ended with a task still at work names the work it kept, and only its owner
 *  discards it.
 *
 *  Every ending keeps a task's work nobody merged (`manager.end_run`): a budget that ran out, a
 *  failure, a Stop. The end screen says what is kept (the task), how much (commits and changed files
 *  the workspace lacks), and where (its branch and folder), shows what merging it brings in the way a
 *  waiting merge's review does (its commits and diff), and offers Merge, at exactly the commit shown,
 *  and Discard. A failed run says Resume carries it on. Discard asks first and names what goes.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { CodeProject, KeptWork as KeptTaskWork } from '../../lib/api'

const kept: KeptTaskWork = {
  task_id: 't-1', title: 'Escape the digest titles', branch: 'pclaw/task-t-1',
  path: '/home/user/.personalclaw/projects/p-1/worktrees/t-1', commits: 1, changed: 0,
  tip: 'a1b2c3d4e5f6', log: ['a1b2c3d docs: escape the digest titles once'],
  stat: ' CHANGELOG.md | 1 +', diff: 'diff --git a/CHANGELOG.md b/CHANGELOG.md\n+- Digest titles are escaped once.\n',
}
const calls = vi.hoisted(() => ({
  list: vi.fn(), merge: vi.fn(), discard: vi.fn(), confirm: vi.fn(),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      approvals: () => Promise.resolve([]),
      uLoopKeptWork: calls.list,
      uLoopKeptMerge: calls.merge,
      uLoopKeptDiscard: calls.discard,
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

const { KeptWork, keptWorkSize } = await import('./KeptWork')
const { ProjectFooter } = await import('./CodeCockpitPage')
const { ApiError } = await import('../../lib/api')

beforeEach(() => {
  calls.list.mockReset().mockResolvedValue({ kept: [kept], resumable: false })
  calls.merge.mockReset()
  calls.discard.mockReset().mockResolvedValue(undefined)
  calls.confirm.mockReset()
})
afterEach(cleanup)

describe('the end screen names the kept work', () => {
  it('🔴 says what is kept, how much, and where', async () => {
    render(<KeptWork loopId="l1" status="complete" />)
    expect(await screen.findByText('Work not merged into your workspace')).toBeTruthy()
    expect(screen.getByText('Escape the digest titles')).toBeTruthy()
    expect(screen.getByText(/1 commit your workspace does not have, on branch/)).toBeTruthy()
    expect(screen.getByText('pclaw/task-t-1')).toBeTruthy()
    expect(screen.getByText(kept.path)).toBeTruthy()
    expect(screen.getByText(/kept, each task on its own branch, until you merge it or discard it/)).toBeTruthy()
    expect(screen.getByText(/To carry on with it yourself, open its folder or check out its branch/)).toBeTruthy()
    expect(calls.list).toHaveBeenCalledWith('l1')
  })

  it('a failed run says Resume carries it on', async () => {
    calls.list.mockResolvedValue({ kept: [kept], resumable: true })
    render(<KeptWork loopId="l1" status="failed" />)
    expect(await screen.findByText(/Resume carries on with it/)).toBeTruthy()
  })

  it('a list it could not read says so, never that nothing was kept, and reads it again', async () => {
    calls.list.mockReset().mockRejectedValueOnce(new ApiError('gateway restarting', 503)).mockResolvedValue({ kept: [kept], resumable: false })
    render(<KeptWork loopId="l1" status="complete" />)
    expect((await screen.findByRole('alert')).textContent).toMatch(/^Couldn't check for work this run kept unmerged/)
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Escape the digest titles')).toBeTruthy()
  })

  it('a reply that lists nothing is a read that did not answer, not nothing kept', async () => {
    calls.list.mockReset().mockResolvedValue({})
    render(<KeptWork loopId="l1" status="stopped" />)
    expect((await screen.findByRole('alert')).textContent).toBe("Couldn't check for work this run kept unmerged: the reply did not list it")
  })

  it('a loop still at work shows nothing and asks nothing', () => {
    const { container } = render(<KeptWork loopId="l1" status="running" />)
    expect(container.innerHTML).toBe('')
    expect(calls.list).not.toHaveBeenCalled()
  })

  it('is on the Code cockpit’s end screen for an ended project with a workspace', async () => {
    const project = {
      id: 'l1', name: 'Fix the digest', status: 'stopped', kind: 'code', stages: [],
      workspace_dir: '/home/user/newsfold', total_cycles: 3, max_cycles: 30,
    } as unknown as CodeProject
    render(<ProjectFooter project={project} gateFail={null} stalled={null} onNudged={() => {}} />)
    expect(await screen.findByText('Escape the digest titles')).toBeTruthy()
  })

  it('says a count it could not read as such, not as nothing', () => {
    expect(keptWorkSize({ commits: 2, changed: 1 })).toBe('2 commits and 1 changed file')
    expect(keptWorkSize({ commits: null, changed: 0 })).toBe('changes that could not be counted')
  })
})

describe('her choice, and only hers', () => {
  it('🔴 Discard asks first, naming what goes, and does nothing on No', async () => {
    calls.confirm.mockResolvedValue(false)
    render(<KeptWork loopId="l1" status="complete" />)
    fireEvent.click(await screen.findByRole('button', { name: /Discard/ }))
    await waitFor(() => expect(calls.confirm).toHaveBeenCalled())
    const [title, body] = calls.confirm.mock.calls[0]
    expect(title).toBe('Discard the work on “Escape the digest titles”?')
    expect(body).toMatch(/Its worktree and its branch pclaw\/task-t-1 are deleted from your repository, with 1 commit/)
    expect(body).toMatch(/can't be undone/)
    expect(calls.discard).not.toHaveBeenCalled()
  })

  it('Discard on Yes discards that task’s work', async () => {
    calls.confirm.mockResolvedValue(true)
    render(<KeptWork loopId="l1" status="complete" />)
    fireEvent.click(await screen.findByRole('button', { name: /Discard/ }))
    await waitFor(() => expect(calls.discard).toHaveBeenCalledWith('l1', 't-1'))
  })

  it('a merge that conflicts names the files and keeps the work', async () => {
    calls.merge.mockRejectedValue(new ApiError('conflicts', 409, 'kept_work_conflicts', { conflicts: ['CHANGELOG.md'] }))
    render(<KeptWork loopId="l1" status="complete" />)
    fireEvent.click(await screen.findByRole('button', { name: /Merge into workspace/ }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/conflicts with your workspace in CHANGELOG\.md, so nothing was merged and the work is kept/)
    expect(alert.textContent).toMatch(/on branch pclaw\/task-t-1/)
    expect(screen.getByText('Escape the digest titles')).toBeTruthy()
  })

  it('a clean merge clears the task from the list', async () => {
    calls.merge.mockResolvedValue({ ok: true, kept: [] })
    render(<KeptWork loopId="l1" status="complete" />)
    fireEvent.click(await screen.findByRole('button', { name: /Merge into workspace/ }))
    await waitFor(() => expect(screen.queryByText('Escape the digest titles')).toBeNull())
    expect(calls.merge).toHaveBeenCalledWith('l1', 't-1', 'a1b2c3d4e5f6')
  })
})

describe('the same review a waiting merge gets', () => {
  it('🔴 shows what merging it brings in: its commits and its diff', async () => {
    calls.list.mockResolvedValue({ into: 'main', kept: [kept], cut: false, resumable: false })
    render(<KeptWork loopId="l1" status="stopped" />)
    expect(await screen.findByText('a1b2c3d docs: escape the digest titles once')).toBeTruthy()
    expect(screen.getByLabelText('Changes of Escape the digest titles')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Merge into main/ })).toBeTruthy()
  })

  it('🔴 merges exactly the commit it showed, and work that moved is read again, not merged', async () => {
    calls.list.mockResolvedValue({ into: 'main', kept: [kept], cut: false, resumable: false })
    calls.merge.mockRejectedValue(new ApiError('moved', 409, 'loop_merge_moved'))
    render(<KeptWork loopId="l1" status="stopped" />)
    fireEvent.click(await screen.findByRole('button', { name: /Merge into main/ }))
    expect((await screen.findByRole('status')).textContent).toBe('The work changed since you read it, so nothing was merged. This is the work as it is now.')
    expect(calls.merge).toHaveBeenCalledWith('l1', 't-1', 'a1b2c3d4e5f6')
    expect(calls.list).toHaveBeenCalledTimes(2)
  })

  it('work committed under another name is named and not merged', async () => {
    calls.merge.mockRejectedValue(new ApiError('other', 409, 'kept_work_other_name', { commits: ['a1b2c3d Someone <x@example.com>'] }))
    render(<KeptWork loopId="l1" status="complete" />)
    fireEvent.click(await screen.findByRole('button', { name: /Merge into workspace/ }))
    expect((await screen.findByRole('alert')).textContent).toMatch(/commits made under another name than yours \(a1b2c3d Someone <x@example\.com>\), so it was not merged/)
  })
})
