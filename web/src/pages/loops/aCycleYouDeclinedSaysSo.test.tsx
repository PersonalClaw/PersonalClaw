import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop } from '../../lib/api'
import type { RunLifecycleEvent } from './useRunStream'

// ── A cycle you ended with a Deny says so, and its loop waits for you ───────────────────────────
//
// You denied an Attended loop's worker the write of its cycle's finding, and the loop told the worker
// to "write the files" anyway, then would have asked you for the same write on its next cycle. Now the
// cycle ends without its finding, saying what you declined as a workflow cycle's Deny says it, and
// the loop waits for you (`LoopWatchdog.hold_after_decline`). Its page says so while it waits — as
// the loop's wait, not as a question its agent asked — offers your steer, re-reads the loop the
// moment the wait begins, and lists the cycle under "Declined by you" after the wait is over too.

const SAID = 'Cycle 1 ended without its finding: you declined write_file (findings/cycle_001.json).'
const ASK = `${SAID} The loop waits for you: tell it what to do instead, or resume or stop it.`

const { STORE, STREAM } = vi.hoisted(() => ({
  STORE: { loop: null as Loop | null, reads: 0 },
  STREAM: { onLifecycle: null as null | ((event: RunLifecycleEvent, data: unknown) => void) },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loop ? [STORE.loop] : []),
    uLoop: () => { STORE.reads += 1; return STORE.loop ? Promise.resolve({ ...STORE.loop }) : Promise.reject(new Error('gone')) },
    uLoopAction: vi.fn(() => Promise.resolve(null)),
    uLoopReport: () => Promise.resolve({ report: '', log: '' }),
    artifacts: () => Promise.resolve([]),
    task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Test project' }),
    deleteULoop: () => Promise.resolve(),
    updateULoop: () => Promise.resolve(null),
    uLoopNudge: () => Promise.resolve(),
    chatSessionDetail: () => Promise.resolve({ messages: [] }),
  },
}))
vi.mock('./useRunStream', () => ({
  useRunStream: (_id: string, _enabled: boolean, handlers: { onLifecycle: (event: RunLifecycleEvent, data: unknown) => void }) => {
    STREAM.onLifecycle = handlers.onLifecycle
    return { connected: true }
  },
}))

const { LoopCockpitPage } = await import('./LoopCockpitPage')

function goal(over: Partial<Loop> = {}): Loop {
  return {
    id: 'g1', kind: 'goal', name: 'Count the migrations', task: 'count the files in migrations/',
    execution: 'solo', agent: 'PersonalClaw', model: '', attended: true, max_cycles: 30,
    idle_secs: 60, success_criteria: null, status: 'running', total_cycles: 0, error_message: null,
    created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: null,
    kind_config: { goal_type: 'open_ended' }, held: '',
    ...over,
  } as Loop
}

const DECLINED = [{ cycle: 1, task_id: '', reason: SAID, declined: ['write_file (findings/cycle_001.json)'], ts: '2026-10-03T00:17:40Z' }]

async function mount(query: Record<string, string> = {}): Promise<void> {
  render(<LoopCockpitPage id="g1" onBack={() => {}} query={query} setQuery={() => {}} />)
  await waitFor(() => screen.getByRole('button', { name: 'Details' }))
}

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  STORE.loop = null
  STORE.reads = 0
  STREAM.onLifecycle = null
})

describe('a loop waiting after your Deny', () => {
  it('says why it waits, as the loop’s wait, and offers your steer', async () => {
    STORE.loop = goal({
      status: 'needs_input',
      pending_question: { question: ASK, declined: true },
      declined: DECLINED,
    })
    await mount()
    expect(await screen.findByText(ASK)).toBeTruthy()
    expect(screen.getByText('Waiting for you after your Deny')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Steer & resume/ })).toBeTruthy()
    expect(screen.queryByText('The agent needs your input')).toBeNull()
  })

  it('keeps a question its agent asked in the agent’s words', async () => {
    STORE.loop = goal({ status: 'needs_input', pending_question: { question: 'Which feed should I count?' } })
    await mount()
    expect(await screen.findByText('Which feed should I count?')).toBeTruthy()
    expect(screen.getByText('The agent needs your input')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Answer & resume/ })).toBeTruthy()
  })

  it('is read again the moment the wait begins', async () => {
    STORE.loop = goal()
    await mount()
    await waitFor(() => expect(STREAM.onLifecycle).not.toBeNull())
    const before = STORE.reads
    STORE.loop = goal({ status: 'needs_input', pending_question: { question: ASK, declined: true }, declined: DECLINED })
    act(() => { STREAM.onLifecycle!('declined', { loop_id: 'g1', task_id: '', reason: SAID }) })
    await waitFor(() => expect(STORE.reads).toBeGreaterThan(before))
    expect(await screen.findByText(ASK)).toBeTruthy()
  })
})

describe('the cycles you declined', () => {
  it('are listed on the loop’s page after the wait is over', async () => {
    STORE.loop = goal({ status: 'stopped', declined: DECLINED })
    await mount({ details: '1' })
    expect(await screen.findByText('Declined by you')).toBeTruthy()
    expect(screen.getByText(SAID)).toBeTruthy()
  })

  it('are not listed for a loop you declined nothing of', async () => {
    STORE.loop = goal({ status: 'stopped' })
    await mount({ details: '1' })
    await screen.findByText('Findings Log')
    expect(screen.queryByText('Declined by you')).toBeNull()
  })
})
