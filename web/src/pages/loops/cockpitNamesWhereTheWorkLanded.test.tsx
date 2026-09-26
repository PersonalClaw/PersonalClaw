import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop } from '../../lib/api'

// ── A Goal loop's result is findable from its cockpit, and a failed read is not "no outputs" ────
//
// Measured 2026-09-25: a Goal loop wrote `packing.md` into its working directory while its cockpit
// said "No outputs saved yet". Outputs lists the deliverable DOCUMENT and the artifacts a loop
// saved; a worker can finish its task writing neither, and nothing on the page named the directory
// it wrote into (`effective_dir` — the bound workspace, the project context dir or the workspace
// root, which is not the loop dir the page does know). And both output reads were `.catch(() =>
// {})`, so a failed read painted the same "No outputs saved yet" — a claim about work the page
// never looked at.

const { STORE } = vi.hoisted(() => ({
  STORE: { loop: null as Loop | null, reportFails: false },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loop ? [STORE.loop] : []),
    uLoop: () => (STORE.loop ? Promise.resolve(STORE.loop) : Promise.reject(new Error('no such loop'))),
    uLoopAction: vi.fn(() => Promise.resolve(null)),
    uLoopReport: () => (STORE.reportFails
      ? Promise.reject(new Error('the gateway could not read the loop directory'))
      : Promise.resolve({ report: '', log: '' })),
    artifacts: () => Promise.resolve([]),
    task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Test project' }),
    deleteULoop: () => Promise.resolve(),
    updateULoop: () => Promise.resolve(null),
    uLoopNudge: () => Promise.resolve(),
  },
}))
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))

const { LoopCockpitPage } = await import('./LoopCockpitPage')

function goal(over: Partial<Loop> = {}): Loop {
  return {
    id: 'g1', kind: 'goal', name: 'Packing note', task: 'write a packing note for a weekend trip',
    execution: 'solo', agent: 'claude-code', model: 'sonnet', attended: false, max_cycles: 30,
    idle_secs: 60, success_criteria: null, status: 'running', total_cycles: 1, error_message: null,
    created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: null,
    kind_config: { goal_type: 'open_ended' }, work_dir: '/home/me/personalclaw-workspace',
    ...over,
  } as Loop
}

async function mount(): Promise<void> {
  render(<LoopCockpitPage id="g1" onBack={() => {}} query={{}} setQuery={() => {}} />)
  await waitFor(() => screen.getByRole('button', { name: 'Details' }))
}

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  STORE.loop = null
  STORE.reportFails = false
})

describe('the cockpit says where the worker’s files went', () => {
  it('names the working directory, with a way to open it', async () => {
    STORE.loop = goal()
    await mount()
    expect(await screen.findByText('/home/me/personalclaw-workspace')).toBeTruthy()
    const link = screen.getByRole('link', { name: /open it in Files/ })
    expect(link.getAttribute('href')).toBe(`#/files?dir=${encodeURIComponent('/home/me/personalclaw-workspace')}`)
  })

  it('says nothing about a directory it was not told', async () => {
    STORE.loop = goal({ work_dir: '' })
    await mount()
    await screen.findByText(/No outputs saved yet/)
    expect(screen.queryByRole('link', { name: /open it in Files/ })).toBeNull()
  })
})

describe('a failed output read is not an empty one', () => {
  it('says it could not load the outputs instead of "No outputs saved yet"', async () => {
    STORE.loop = goal()
    STORE.reportFails = true
    await mount()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/could not read the loop directory|Couldn.t load this loop.s outputs/)
    expect(screen.queryByText(/No outputs saved yet/)).toBeNull()
  })
})

describe('an unapplied nudge says what it is waiting for', () => {
  it('does not promise a next cycle to a loop that has ended', async () => {
    const { pendingNudgeLabel } = await import('./LoopCockpitPage')
    expect(pendingNudgeLabel('running')).toBe('nudge queued — applies next cycle')
    expect(pendingNudgeLabel('paused')).toBe('nudge queued — applies when the loop resumes')
    for (const ended of ['stopped', 'complete', 'failed']) {
      expect(pendingNudgeLabel(ended)).toBe('nudge not applied — the loop ended before its next cycle')
    }
  })
})
