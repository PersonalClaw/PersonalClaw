import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, cleanup, waitFor } from '@testing-library/react'
import type { CodeProject } from '../../../../lib/api'

// ── The cockpit meta strip's controller ───────────────────────────────────────────────────────
//
// `useCockpitMeta` keeps what the strip under the cockpit's title says: the run's status in the
// registry's words, its elapsed time (ticking while it runs), its cycles against the budget, its
// workspace, and the Project that contains it.

const h = vi.hoisted(() => ({ projects: vi.fn(), useVisiblePoll: vi.fn() }))
vi.mock('../../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../../lib/api')>()
  return { ...real, api: { ...real.api, projects: h.projects } }
})
vi.mock('../../../../lib/useVisiblePoll', () => ({ useVisiblePoll: h.useVisiblePoll }))

let useCockpitMeta: typeof import('./useCockpitMeta')['useCockpitMeta']
const project = (over: Record<string, unknown> = {}) => ({ id: 'loop-1', status: 'paused', ...over } as unknown as CodeProject)
const meta = (over: Record<string, unknown> = {}) => renderHook(() => useCockpitMeta({ project: project(over) })).result

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.projects.mockResolvedValue([{ id: 'p-0', name: 'Seeds' }, { id: 'p-1', name: 'Feed' }])
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-02T10:00:00Z'))
  ;({ useCockpitMeta } = await import('./useCockpitMeta'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('useCockpitMeta', () => {
  it('names the one Project that contains the run: the one it launched into, else the one made for its tasks', async () => {
    const launched = meta({ tasks_project_id: 'p-1', project_id: 'p-0' })
    await waitFor(() => expect(launched.current.projName).toBe('Seeds'))
    expect(launched.current.projId).toBe('p-0')
    const madeFor = meta({ tasks_project_id: 'p-1', project_id: 'p-gone' })
    await waitFor(() => expect(madeFor.current.projName).toBe('Feed'))
    expect(madeFor.current.projId).toBe('p-1')
    const none = meta()
    expect([none.current.projId, none.current.projName]).toEqual(['', ''])
  })

  it('names a project made while the cockpit is open, read again once for its id', async () => {
    h.projects.mockResolvedValueOnce([{ id: 'p-0', name: 'Seeds' }])
    h.projects.mockResolvedValue([{ id: 'p-0', name: 'Seeds' }, { id: 'p-new', name: 'Fix the gate' }])
    const started = meta({ project_id: '', tasks_project_id: 'p-new' })
    await waitFor(() => expect(started.current.projName).toBe('Fix the gate'))
    expect(started.current.projId).toBe('p-new')
  })

  it('adds the running stretch to the banked time, and ticks only while running', () => {
    const now = Date.parse('2026-10-02T10:00:00Z') / 1000
    expect(meta({ status: 'running', elapsed_seconds: 600, started_at: now - 3700 }).current.elapsedText).toBe('1h 11m')
    expect(h.useVisiblePoll.mock.calls.at(-1)?.[1]).toBe(30000)
    expect(meta({ elapsed_seconds: 90061 }).current.elapsedText).toBe('1d 1h')
    expect(h.useVisiblePoll.mock.calls.at(-1)?.[1]).toBeNull()
    expect(meta().current.elapsedText).toBe('')
  })

  it('counts cycles against the budget, and shows the budget before the first cycle', () => {
    expect(meta({ total_cycles: 5, max_cycles: 60 }).current.cyclesText).toBe('5 / 60 cycles')
    expect(meta({ total_cycles: 0, max_cycles: 20 }).current.cyclesText).toBe('0 / 20 cycles')
    expect(meta({ total_cycles: 1 }).current.cyclesText).toBe('1 cycle')
    expect(meta().current.cyclesText).toBe('')
  })

  it('names the bound workspace only, by its folder', () => {
    expect(meta({ workspace_dir: '/repo/feed/' }).current.wsBase).toBe('feed')
    expect(meta({ files_dir: '/home/me/.personalclaw/loop/da50a558' }).current.wsBase).toBe('')
  })

  it("says the run's status in the registry's words", () => {
    expect(meta({ status: 'running' }).current.dispStatus).toBe('running')
    expect(meta({ status: 'running', held: 'approval' }).current.dispStatus).toBe('held')
    expect(meta({ status: 'complete', stop_reason: 'budget' }).current.dispStatus).toBe('ended_early')
  })
})
