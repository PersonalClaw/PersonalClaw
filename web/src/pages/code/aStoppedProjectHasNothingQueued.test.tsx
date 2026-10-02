/** A project that finished or was stopped has nothing queued, and its Tasks rail says so.
 *
 *  Nothing of a stopped or completed project runs again. Its record kept the task ids it had
 *  queued, so its rail read "1 queued" beside a task "waiting" for a stage that would never start,
 *  and once the queue was emptied it would have read "can queue" and "ready to queue" for work the
 *  gateway refuses to queue on an ended loop. The ending now empties the queue (`manager.end_run`),
 *  and the rail reads an ended project's unfinished tasks as "not done", with no Autopilot switch
 *  or Queue all left to press. A failed project can be resumed, so its queue is still its queue.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { CodeProject, TaskItem } from '../../lib/api'

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, api: { ...actual.api, approvals: () => Promise.resolve([]) } }
})
vi.mock('../../lib/useChatSocket', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/useChatSocket')>()
  return { ...actual, useChatSocket: () => {} }
})

const { StageTasks } = await import('./CodeCockpitPage')

afterEach(cleanup)

const stage = (key: string, title: string) => ({ stage: key, title, objective: '', exit_criteria: [], deliverable: '', task_list_name: title })
const tasks: Record<string, TaskItem[]> = {
  'list-impl': [
    { id: 't-done', title: 'Escape the digest titles', status: 'done' },
    { id: 't-open', title: 'Note it in the changelog', status: 'open' },
  ],
  'list-review': [{ id: 't-later', title: 'Review the digest output', status: 'open' }],
}

/** A project whose record still lists "Note it in the changelog" as queued. */
const project = (status: string): CodeProject => ({
  id: 'l1', name: 'Fix the digest', status, kind: 'code', stages: [], total_cycles: 4, max_cycles: 30,
  autopilot: true, queued_task_ids: ['t-open'],
  stage_plan: [stage('implementation', 'Implementation'), stage('review', 'Review')],
  task_list_ids: { implementation: 'list-impl', review: 'list-review' },
  stage_status: { implementation: 'active', review: 'pending' },
}) as unknown as CodeProject

function rail(status: string) {
  return render(<StageTasks project={project(status)} onTasksChanged={() => {}} loading={false}
    tasksByList={tasks} onSelect={() => {}} activeTaskIds={new Set()} mainActivity={[]} />)
}

describe('the Tasks rail of a project that ended', () => {
  it.each(['stopped', 'complete'])('🔴 a %s project has nothing queued, queueable or waiting', (status) => {
    const { container } = rail(status)
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/queued|can queue|waiting/)
    expect(screen.getByText('2 not done')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Note it in the changelog — not done' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Review the digest output — not done' })).toBeTruthy()
    // Positive control: a task it did finish still reads done.
    expect(screen.getByRole('button', { name: 'Escape the digest titles — done' })).toBeTruthy()
    // Nothing is left to drive.
    expect(screen.queryByRole('button', { name: 'Autopilot' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Queue all/ })).toBeNull()
  })

  it('a project at work still reads its queue and the stage its next task waits for', () => {
    rail('running')
    expect(screen.getByText('1 queued')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Note it in the changelog — queued' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Review the digest output — waiting for its stage' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Autopilot' })).toBeTruthy()
  })

  it('a failed project can be resumed, so its queue is still what Resume runs', () => {
    rail('failed')
    expect(screen.getByText('1 queued')).toBeTruthy()
    expect(screen.queryByText(/not done/)).toBeNull()
  })
})
