/** A loop that has not launched opens on its planning after a reload, even when the first read fails.
 *
 *  Seen once: reloading `#/code/<id>` for a Code loop still planning, its step waiting on review,
 *  opened its cockpit instead, which said "This project has finished." with "Start a new project",
 *  and "No task breakdown for this run". The section chose the walkthrough from one read of the loop
 *  when the route mounted; a read that failed left it on the cockpit, and nothing read it again. On
 *  `#/loops/<id>` the same failure said the loop no longer exists.
 *
 *  Now each section reads the loop again every 4 s while a read fails in a way that passes (a 5xx,
 *  a dropped request), as the cockpit's first load does, and a Code section also reads it again
 *  when its cockpit hears of a status that belongs on the walkthrough. A cockpit that does show a
 *  loop in `intake` or `planning` says it hasn't started yet. Each section renders on its route.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import type { CodeProject } from '../../lib/api'

const h = vi.hoisted(() => {
  const loops: Record<string, Record<string, unknown>> = {
    P1: {
      id: 'P1', name: 'Feed refetch fix', kind: 'code', status: 'planning', task: 'fix the feed refetch',
      plan: [], intake_rigor: 'grill', attended: true, kind_config: { project_kind: 'greenfield', entry_stage: 'build' },
    },
    L1: {
      id: 'L1', name: 'Tidy the reading list', kind: 'goal', status: 'planning', task: 'tidy the reading list',
      execution: 'solo', plan: [], intake_rigor: 'grill', attended: true, kind_config: { goal_type: 'open_ended' },
    },
  }
  const now = Date.now() / 1000
  const session = (id: string) => ({
    project_id: id, created_at: now, updated_at: now,
    steps: [
      { id: 's1', kind: 'goal', title: 'Understand the goal', status: 'approved', artifact: { markdown: 'Refetch once.' } },
      { id: 's2', kind: 'plan', title: 'Plan the stages', status: 'awaiting_review', artifact: { markdown: 'Build, then review.' } },
    ],
  })
  const state = { down: false }
  const reads: Record<string, (...args: unknown[]) => Promise<unknown>> = {
    uLoopPlanSession: (id) => Promise.resolve(session(id as string)),
    savedAgents: () => Promise.resolve([]),
    skills: () => Promise.resolve([]),
    approvals: () => Promise.resolve([]),
  }
  return { loops, reads, state }
})

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  const uLoop = (id: unknown) => h.state.down
    ? Promise.reject(new real.ApiError('Service unavailable', 503))
    : Promise.resolve(h.loops[id as string])
  const api = new Proxy(real.api as unknown as Record<string, unknown>, {
    get: (_target, prop: string) => (...args: unknown[]) =>
      prop === 'uLoop' ? uLoop(args[0]) : prop in h.reads ? h.reads[prop](...args) : Promise.resolve({}),
  })
  return { ...real, api }
})
vi.mock('../loops/useRunStream', () => ({ useRunStream: () => ({ connected: true }) }))
vi.mock('../../lib/useChatSocket', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/useChatSocket')>()
  return { ...real, useChatSocket: () => {} }
})

const { CodeSection } = await import('./CodeSection')
const { LoopsSection } = await import('../loops/LoopsSection')
const { StageTasks, ProjectFooter } = await import('./CodeCockpitPage')
const { noteInputForTests } = await import('../../lib/useVisiblePoll')

const navigate = vi.fn()
beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  noteInputForTests()
  h.state.down = false
  navigate.mockReset()
  Element.prototype.scrollTo = (() => {}) as unknown as typeof Element.prototype.scrollTo
})
afterEach(() => { cleanup(); vi.useRealTimers() })

/** Every read of the loop fails until the gateway answers again, 4.5 s in. */
async function readsFailThenAnswer() {
  h.state.down = true
  await act(async () => { await vi.advanceTimersByTimeAsync(500) })
  h.state.down = false
  noteInputForTests()
  await act(async () => { await vi.advanceTimersByTimeAsync(4500) })
}

const walkthrough = () => screen.findByText(/Planning steps \(1\/2 approved\)/)

describe('a loop still planning, reloaded while its first read fails', () => {
  it('🔴 a Code loop opens its walkthrough once a read lands, not its cockpit', async () => {
    h.state.down = true
    render(<CodeSection sub="P1" navigate={navigate} query={{}} setQuery={() => {}} navEpoch={0} />)
    await readsFailThenAnswer()

    expect(await walkthrough()).toBeTruthy()
    expect(screen.queryByText('This project has finished.')).toBeNull()
  })

  it('🔴 a goal loop opens its walkthrough once a read lands, and is never called gone', async () => {
    h.state.down = true
    render(<LoopsSection sub="L1" navigate={navigate} query={{}} setQuery={() => {}} navEpoch={0} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(screen.queryByText(/This loop no longer exists/)).toBeNull()
    await readsFailThenAnswer()

    expect(await walkthrough()).toBeTruthy()
    expect(screen.queryByText(/This loop no longer exists/)).toBeNull()
  })
})

/** The cockpit's parts, for a loop it shows before the section has moved it to its walkthrough. */
const project = (status: string): CodeProject => ({
  id: 'P1', name: 'Feed refetch fix', status, kind: 'code', stages: [], total_cycles: 0, max_cycles: 30,
  stage_plan: [], task_list_ids: {},
}) as unknown as CodeProject

describe.each(['intake', 'planning'])('the cockpit of a loop in %s', (status) => {
  it('🔴 its footer says it has not started, and offers no new project', () => {
    render(<ProjectFooter project={project(status)} gateFail={null} stalled={null} onNudged={() => {}} onStartNew={() => {}} />)

    expect(screen.getByText('This project hasn’t started yet.')).toBeTruthy()
    expect(screen.queryByText('This project has finished.')).toBeNull()
    expect(screen.queryByRole('button', { name: /Start a new project/ })).toBeNull()
  })

  it('🔴 its Tasks rail says tasks come once it is launched', () => {
    render(<StageTasks project={project(status)} onTasksChanged={() => {}} loading={false}
      tasksByList={{}} onSelect={() => {}} activeTaskIds={new Set()} mainActivity={[]} />)

    expect(screen.getByText('Tasks appear here once the project is launched.')).toBeTruthy()
    expect(screen.queryByText(/No task breakdown for this run/)).toBeNull()
  })
})

it('a finished loop still says it has finished', () => {
  render(<ProjectFooter project={project('complete')} gateFail={null} stalled={null} onNudged={() => {}} onStartNew={() => {}} />)

  expect(screen.getByText('This project has finished.')).toBeTruthy()
})
