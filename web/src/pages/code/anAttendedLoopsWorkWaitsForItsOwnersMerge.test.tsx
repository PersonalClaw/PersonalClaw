import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ApiError, type LoopMerge, type LoopMergeReview, type LoopMergeWaiting } from '../../lib/api'

// ── An Attended code loop's finished work waits for its owner to merge it ───────────────────────
//
// Measured: an Attended Code loop committed its tasks' work and merged it into its owner's `main`
// with no card and no notice, and afterwards the working tree was clean, so nothing on the page
// said what had landed. Now the loop pauses with the work it would merge: each task's branch, its
// commits and its diff. Merge approves exactly the commits shown; work that moved after it was
// read is not merged on that approval, and the review is read again. What a loop did merge is
// listed with who approved it.

const { review, merge } = vi.hoisted(() => ({ review: vi.fn(), merge: vi.fn() }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoopMergeReview: (...a: unknown[]) => review(...a),
    uLoopMerge: (...a: unknown[]) => merge(...a),
  },
}))

const { MergeReview, MergedWork } = await import('./MergeReview')

const LOOP_ID = '0a1b2c3d'
const TIP = 'a'.repeat(40)
const MOVED = 'b'.repeat(40)
const WAITING: LoopMergeWaiting = {
  into: 'main',
  tasks: [{ task_id: 't-0a1b2c3d', title: 'Escape titles once', branch: 'pclaw/task-t-0a1b2c3d', tip: TIP }],
}

function reviewAt(tip: string, line = "+TITLE = 'Q&A'  # escaped once"): LoopMergeReview {
  return {
    into: 'main', cut: false,
    tasks: [{
      task_id: 't-0a1b2c3d', title: 'Escape titles once', branch: 'pclaw/task-t-0a1b2c3d', tip,
      commits: [`${tip.slice(0, 7)} task t-0a1b2c3d: work`], stat: ' digest.py | 2 +-',
      diff: `--- a/digest.py\n+++ b/digest.py\n@@ -1 +1 @@\n-TITLE = 'Q&A'\n${line}`,
    }],
  }
}

beforeEach(() => {
  review.mockReset()
  merge.mockReset()
})
afterEach(() => cleanup())

describe('the review of an Attended loop’s waiting work', () => {
  it('shows what would land, and Merge approves it at the commits shown', async () => {
    review.mockResolvedValue(reviewAt(TIP))
    merge.mockResolvedValue({ ok: true, loop: { id: LOOP_ID } })
    const onMerged = vi.fn()
    render(<MergeReview loopId={LOOP_ID} waiting={WAITING} onMerged={onMerged} />)

    expect(screen.getByRole('region', { name: 'Work waiting to merge into main' })).toBeTruthy()
    expect(screen.getByText(/Nothing of it is on main until you merge it/)).toBeTruthy()
    expect(await screen.findByText(/escaped once/)).toBeTruthy()
    expect(screen.getByText(`${TIP.slice(0, 7)} task t-0a1b2c3d: work`)).toBeTruthy()
    expect(merge).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /Merge into main/ }))

    await waitFor(() => expect(onMerged).toHaveBeenCalled())
    expect(merge).toHaveBeenCalledWith(LOOP_ID, { 't-0a1b2c3d': TIP })
  })

  it('work that moved after it was read is not merged: the review is read again', async () => {
    review.mockResolvedValueOnce(reviewAt(TIP)).mockResolvedValueOnce(reviewAt(MOVED, '+TITLE = "moved"'))
    merge.mockRejectedValue(new ApiError('The work changed since you read it.', 409, 'loop_merge_moved'))
    const onMerged = vi.fn()
    render(<MergeReview loopId={LOOP_ID} waiting={WAITING} onMerged={onMerged} />)
    await screen.findByText(/escaped once/)

    fireEvent.click(screen.getByRole('button', { name: /Merge into main/ }))

    expect(await screen.findByText(/The work changed since you read it, so nothing was merged/)).toBeTruthy()
    expect(await screen.findByText(/"moved"/)).toBeTruthy()
    expect(onMerged).not.toHaveBeenCalled()
    expect(review).toHaveBeenCalledTimes(2)
  })

  it('a review that cannot be read says so, and offers no merge', async () => {
    review.mockRejectedValue(new Error('git is not answering'))
    render(<MergeReview loopId={LOOP_ID} waiting={WAITING} onMerged={() => {}} />)

    expect((await screen.findByRole('alert')).textContent).toContain('git is not answering')
    expect(screen.queryByRole('button', { name: /Merge into main/ })).toBeNull()
  })
})

describe('what a loop merged', () => {
  const merged = (by: string): LoopMerge => ({
    task_id: 't-0a1b2c3d', title: 'Escape titles once', branch: 'pclaw/task-t-0a1b2c3d', into: 'main',
    commits: ['abc1234 task t-0a1b2c3d: work'], head: 'c'.repeat(40), by, at: 0,
  })

  it('names each merge, its commit and who approved it', () => {
    render(<MergedWork merges={[merged('you'), merged('the loop')]} />)
    expect(screen.getByText('Merged by this loop (2)')).toBeTruthy()
    expect(screen.getByText(/you approved it/)).toBeTruthy()
    expect(screen.getByText(/merged by the loop \(Unattended\)/)).toBeTruthy()
    expect(screen.getAllByText('ccccccc')).toHaveLength(2)
  })

  it('🪤 VACUITY: a loop that merged nothing shows nothing', () => {
    const { container } = render(<MergedWork merges={[]} />)
    expect(container.textContent).toBe('')
  })
})
