import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import type { PendingApproval, WorkflowBatchState, WorkflowRunDetailData } from '../../lib/api'
import type { ChatTurn, Segment, ToolSegment } from './chatTypes'

// ── A run's card is shown once in its chat, says why a step waits, and a batch's card follows its
//    one ask to its run ─────────────────────────────────────────────────────────────────────────────
//
// 🔴 Seen live: a batch's card was repeated once per status read the agent made (22 copies of
// "Completed · 2/2"), it said "Running 0/2" while each step waited on her answer with nothing saying
// so, and a batch waiting for her Allow had no card at all, since it has no run yet.

const tool = (id: string, name: string, output: string): ToolSegment => ({ kind: 'tool', id, tool: name, output, done: true })
const turn = (...segments: Segment[]): ChatTurn => ({ role: 'assistant', segments })

describe('a run has one live card in its chat', () => {
  it('🔴 shows it where the chat first names the run, however often the agent reads it after', async () => {
    const { liveWorkflowCards } = await import('./WorkflowProgressCard')
    const started = tool('c1', 'subagent_run', '{"run_id": "a1b2c3d4", "status": "running"}\nCompiled 2 tasks…')
    const reads = [1, 2, 3].map((n) => tool(`s${n}`, 'workflow_status', `{\n  "run_id": "a1b2c3d4",\n  "workflow": "subagent-batch-1",\n  "status": "running"\n}`))
    const cards = liveWorkflowCards([turn(started, reads[0]), turn(reads[1]), turn(reads[2])])
    expect([...cards]).toEqual([started])
  })

  it('and a batch waiting for its ask keeps its one card once it has a run', async () => {
    const { liveWorkflowCards, workflowRefFromTool } = await import('./WorkflowProgressCard')
    const waiting = tool('c1', 'subagent_run', '{"status": "awaiting_approval", "approval": "batch:subagent-batch-7-a1b2c3", "batch": "subagent-batch-7-a1b2c3"}\nNot started yet…')
    expect(workflowRefFromTool(waiting.tool, waiting.output)).toEqual({ runId: '', created: true, batch: 'subagent-batch-7-a1b2c3' })
    const read = tool('s1', 'workflow_status', '{"run_id": "deadbeef", "workflow": "subagent-batch-7-a1b2c3", "status": "complete"}')
    expect([...liveWorkflowCards([turn(waiting), turn(read)])]).toEqual([waiting])
  })

  it('the control: two runs are two cards, each where it is first named', async () => {
    const { liveWorkflowCards } = await import('./WorkflowProgressCard')
    const one = tool('w1', 'workflow_start', '{"run_id": "11111111", "status": "running"}')
    const two = tool('w2', 'workflow_start', '{"run_id": "22222222", "status": "running"}')
    expect([...liveWorkflowCards([turn(one, two)])]).toEqual([one, two])
  })

  it('a tool result that names no run is not a card', async () => {
    const { liveWorkflowCards } = await import('./WorkflowProgressCard')
    expect(liveWorkflowCards([turn(tool('t1', 'read_file', '{"run_id": "a1b2c3d4"}'))]).size).toBe(0)
  })
})

// ── the card itself ──────────────────────────────────────────────────────────────────────────────

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const approvals = vi.fn<() => Promise<PendingApproval[]>>()
const workflowBatch = vi.fn<(name: string) => Promise<WorkflowBatchState>>()

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../workflows/useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, workflowRun, approvals, workflowBatch } }
})

const run = (): WorkflowRunDetailData => ({
  run_id: 'a1b2c3d4', workflow: 'subagent-batch-1', status: 'running', spec_version: 1, error: '',
  attention: null, tokens: 0, elapsed_secs: 0,
  nodes: [
    { instance_path: 'root.children[0]', node_id: 'find_the_retry_callers_0', label: 'Find the retry callers', state: 'running' },
    { instance_path: 'root.children[1]', node_id: 'find_the_retry_tests_1', label: 'Find the retry tests', state: 'running' },
  ],
}) as WorkflowRunDetailData

const ask = (over: Partial<PendingApproval>): PendingApproval => ({
  id: 'subagent:ab12cd34:1', request_id: 'subagent:ab12cd34:1', source: 'subagent', tool: 'bash',
  session: 'workflow:a1b2c3d4:find_the_retry_callers_0', ts: 0, session_title: '', agent: '', risk: 'caution',
  grant_agent: '', source_label: '', ...over,
})

async function mount(refObj: { runId: string; created: boolean; batch?: string }) {
  const { WorkflowProgressCard } = await import('./WorkflowProgressCard')
  await act(async () => {
    render(<WorkflowProgressCard refObj={refObj} />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

beforeEach(() => {
  cleanup()
  workflowRun.mockReset(); approvals.mockReset(); workflowBatch.mockReset()
  workflowRun.mockResolvedValue(run())
  approvals.mockResolvedValue([])
})

describe('a step that waits says why', () => {
  it('🔴 names the step and what it waits on her answer for, with where to answer it', async () => {
    approvals.mockResolvedValue([
      ask({}),
      ask({ id: 'spawn:ef56ab78', tool: 'subagent_run', session: 'workflow:a1b2c3d4:find_the_retry_tests_1' }),
    ])
    await mount({ runId: 'a1b2c3d4', created: true })
    expect(await screen.findByText('Find the retry callers waits for your answer on bash')).toBeTruthy()
    expect(screen.getByText('Find the retry tests waits for your Allow to start')).toBeTruthy()
    const answer = screen.getByRole('link', { name: 'Answer it: Find the retry callers waits for your answer on bash' })
    expect(answer.getAttribute('href')).toBe('#/workflows/runs/a1b2c3d4?node=find_the_retry_callers_0')
  })

  it('the control: another run’s asks, and a chat’s own, are not this run’s', async () => {
    approvals.mockResolvedValue([
      ask({ session: 'workflow:deadbeef:find_the_retry_callers_0' }),
      ask({ id: 'chat-a:1', session: 'chat-a' }),
    ])
    await mount({ runId: 'a1b2c3d4', created: true })
    expect(screen.queryByText(/waits for your/)).toBeNull()
    // The card itself rendered: the absence is the filter's, not an empty card.
    expect(screen.getByText('Find the retry callers')).toBeTruthy()
  })
})

describe('a batch waiting for its ask has a card that follows it to its run', () => {
  it('🔴 says it waits for her Allow, and where to answer it', async () => {
    workflowBatch.mockResolvedValue({ batch: 'subagent-batch-7-a1b2c3', status: 'asking', tasks: 2 })
    await mount({ runId: '', created: true, batch: 'subagent-batch-7-a1b2c3' })
    expect(workflowBatch).toHaveBeenCalledWith('subagent-batch-7-a1b2c3')
    expect(await screen.findByText('Waits for your Allow before any of 2 tasks start.')).toBeTruthy()
    expect(screen.getByRole('link', { name: /Answer in Inbox/ }).getAttribute('href')).toBe('#/inbox')
  })

  it('becomes the run’s card once it has started', async () => {
    workflowBatch.mockResolvedValue({ batch: 'subagent-batch-7-a1b2c3', status: 'started', run_id: 'a1b2c3d4' })
    await mount({ runId: '', created: true, batch: 'subagent-batch-7-a1b2c3' })
    expect(workflowRun).toHaveBeenCalledWith('a1b2c3d4')
    expect(await screen.findByText('Find the retry callers')).toBeTruthy()
  })

  it('says why it never started', async () => {
    workflowBatch.mockResolvedValue({ batch: 'subagent-batch-7-a1b2c3', status: 'not_started', error: 'batch declined, so it never started' })
    await mount({ runId: '', created: true, batch: 'subagent-batch-7-a1b2c3' })
    expect(await screen.findByText('Batch declined, so it never started.')).toBeTruthy()
    expect(screen.queryByRole('link', { name: /Answer in Inbox/ })).toBeNull()
  })
})
