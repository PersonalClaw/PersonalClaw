import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop } from '../../lib/api'
import { MD_LINE, rendersLine } from '../../test/markdownSinks'

// ── A loop's linked tasks render their titles ──────────────────────────────────────────────────
//
// 🔴 The loop cockpit's Tasks tab lists the tasks a loop made, titled by the agent that made them,
// and showed each title's marks. Each now renders as a line, its tooltip the plain words.

const SEEN = 'Skip model_calls in export_digest() in published.py'
const { STORE } = vi.hoisted(() => ({ STORE: { loop: null as Loop | null } }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loop ? [STORE.loop] : []),
    uLoop: () => (STORE.loop ? Promise.resolve(STORE.loop) : Promise.reject(new Error('no such loop'))),
    uLoopAction: vi.fn(() => Promise.resolve(null)),
    uLoopReport: () => Promise.resolve({ report: '', log: '' }),
    artifacts: () => Promise.resolve([]),
    task: (id: string) => Promise.resolve({ id, title: MD_LINE, status: 'open', priority: 'medium' }),
    project: () => Promise.resolve({ name: 'Test project' }),
    deleteULoop: () => Promise.resolve(),
    updateULoop: () => Promise.resolve(null),
    uLoopNudge: () => Promise.resolve(),
  },
}))
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))

const { LoopCockpitPage } = await import('./LoopCockpitPage')

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  STORE.loop = {
    id: 'g1', kind: 'goal', name: 'Packing note', task: 'write a packing note', execution: 'solo', agent: 'claude-code',
    model: 'sonnet', attended: false, max_cycles: 30, idle_secs: 60, success_criteria: null, status: 'running',
    total_cycles: 1, error_message: null, created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: null,
    kind_config: { goal_type: 'open_ended' }, work_dir: '/home/me/personalclaw-workspace', linked_task_ids: ['t-1'],
  } as unknown as Loop
})

describe("the loop cockpit's Tasks tab", () => {
  it('renders each linked task title as a line, its tooltip plain', async () => {
    render(<LoopCockpitPage id="g1" onBack={() => {}} query={{}} setQuery={() => {}} />)
    fireEvent.click(await screen.findByRole('tab', { name: /Tasks · 1/ }))
    const title = await waitFor(() => screen.getByTitle(SEEN))
    expect(rendersLine(title)).toEqual([])
  })
})
