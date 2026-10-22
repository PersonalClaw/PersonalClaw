import { describe, expect, it } from 'vitest'
import { foldAgentActivity, type AgentActivitySources } from './useAgentActivity'
import type { ChatSessionSummary, Loop, PendingApproval, RunningRow } from './api'
import { STATE_VISUAL, layoutScene, sceneSummary } from '../pages/dashboard/world/worldScene'
import { runState } from '../test/runState'
import { phaseToken } from './workPhase'

// ── The world reads the running list (#3889) ─────────────────────────────────────────────────────
//
// What runs reaches Agent World from ONE list, `GET /api/running`, where the gateway has already
// folded a loop's workers and planner into their loop, marked a queued, retrying or model-waiting
// row `blocked`, and listed every kind: a chat's turn, a helper, a loop, a workflow run, a trigger's
// fire and a webhook's turn. The fold paints what the list says. The other lists only add the
// ambient background: chats that are quiet now, and loops that are not running.
//
// It used to fold four lists of its own: a queued helper painted `working`, a plain workflow run was
// not painted at all, and a loop's two running workers painted as two chat nodes beside their loop.

function row(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'chat:c1', kind: 'chat', session: 'c1', phase: 'thinking', line: 'Thinking',
    since: '2026-10-21T10:00:00Z', name: 'Plan the garden', agent: 'personalclaw', started_at: '2026-10-21T09:59:00Z',
    route: '#/chat/c1', blocked: false, waiting: [], ...over,
  }
}

const loop = (over: Partial<Loop> = {}): Loop => ({
  id: '0a1b2c3d', kind: 'goal', name: 'Find a seed supplier', task: 'find one', execution: 'solo', agent: 'a',
  model: 'm', attended: false, max_cycles: 10, idle_secs: 60, success_criteria: null, status: 'running',
  total_cycles: 4, error_message: null, created_at: 1, started_at: 1, completed_at: null, kind_config: {},
  session_key: 'loop-0a1b2c3d', ...over,
} as Loop)

const chat = (over: Partial<ChatSessionSummary> = {}): ChatSessionSummary => ({
  key: 'quiet', title: 'A quiet chat', messages: 3, run_state: runState(), ...over,
})

const asked = (id: string): PendingApproval => ({
  id, request_id: id, source: 'workflow', tool: 'bash', session: 'workflow:r1:fetch', ts: 1, session_title: '',
  agent: '', risk: '', grant_agent: '', source_label: 'workflow “nightly”', work: 'workflow:r1', step: 'fetch', room: '',
})

function sources(over: Partial<AgentActivitySources> = {}): AgentActivitySources {
  return { running: { running: [], finished: [] }, sessions: [], loops: [], approvals: [], ...over }
}

const fold = (src: AgentActivitySources) => foldAgentActivity(src).entities
const byId = (src: AgentActivitySources) => Object.fromEntries(fold(src).map((e) => [e.id, e]))

describe('every row of the list is painted, as the kind of work it is', () => {
  it('a chat, a loop, a run, a helper, a fire and a webhook turn are each one node', () => {
    const running = [
      row(),
      row({ work: 'loop:0a1b2c3d', kind: 'loop', session: '', name: 'Find a seed supplier', route: '#/loops/0a1b2c3d' }),
      row({ work: 'workflow:r1', kind: 'workflow', session: '', name: 'nightly', route: '#/workflows/runs/r1' }),
      row({ work: 'subagent:h1', kind: 'subagent', session: 'c1', name: 'grep the tree', route: '#/chat/c1' }),
      row({ work: 'trigger:t1', kind: 'trigger', session: '', name: 'Friday digest', route: '#/triggers?open=t1' }),
      row({ work: 'webhook:deploy', kind: 'webhook', session: '', name: 'Deploy hook', route: '' }),
    ]
    const seen = byId(sources({ running: { running, finished: [] } }))
    expect(Object.keys(seen).sort()).toEqual([
      'loop:0a1b2c3d', 'run:r1', 'session:c1', 'subagent:h1', 'trigger:t1', 'webhook:deploy',
    ])
    expect(seen['run:r1']).toMatchObject({ kind: 'run', state: 'working', title: 'nightly', refs: { link: '#/workflows/runs/r1' } })
    expect(seen['session:c1'].refs).toEqual({ link: '#/chat/c1', session: 'c1' })
  })

  it('🔴 a queued helper paints blocked, never working', () => {
    const queued = row({ work: 'subagent:h4', kind: 'subagent', phase: 'queued', line: 'Waiting to start', blocked: true })
    const [helper] = fold(sources({ running: { running: [queued], finished: [] } }))
    expect(helper.state).toBe('blocked')
    // Its own ring and pace, apart from working's; its tone is queued work's, as one tone per phase
    // paints it (#3833), so the ring, the pace and the summary's word tell it from working.
    expect(STATE_VISUAL.blocked.tone).toBe(phaseToken('queued'))
    expect(STATE_VISUAL.blocked.ring).not.toBe(STATE_VISUAL.working.ring)
    expect(STATE_VISUAL.blocked.speed).toBeLessThan(STATE_VISUAL.working.speed)
    const [placed] = layoutScene([helper])
    expect(placed.tone).toBe(STATE_VISUAL.blocked.tone)
    expect(sceneSummary([helper], 0)).toBe('1 blocked.')
  })

  it('🔴 a loop with two running workers is one loop node and no worker nodes', () => {
    // The list holds the loop alone; the chat list still carries its two workers' sessions.
    const workers = [
      chat({ key: 'loop-0a1b2c3d', title: 'loop-0a1b2c3d', origin: 'loop', source_id: '0a1b2c3d' }),
      chat({ key: 'loop-0a1b2c3d-t-1a2b', title: 'Untitled chat', origin: 'loop', source_id: '0a1b2c3d' }),
    ]
    const running = [row({ work: 'loop:0a1b2c3d', kind: 'loop', session: '', name: 'Find a seed supplier' })]
    const entities = fold(sources({ running: { running, finished: [] }, sessions: workers, loops: [loop()] }))
    expect(entities.map((e) => e.id)).toEqual(['loop:0a1b2c3d'])
    // The loop's cycle budget still reads as its progress.
    expect(entities[0].progress).toBeCloseTo(0.4)
  })

  it('a run-backed loop is one node, named by its run, with its progress', () => {
    // Its row is its run's (`workflow:<run>`, kind `loop`), and the loop list names it by its run.
    const running = [row({ work: 'workflow:r7', kind: 'loop', session: '', name: 'Weekly checklist' })]
    const loops = [loop({ id: 'r7', run_id: 'r7', kind: 'general', name: 'Weekly checklist', session_key: '', total_cycles: 1, max_cycles: 4 })]
    const entities = fold(sources({ running: { running, finished: [] }, loops }))
    expect(entities.map((e) => e.id)).toEqual(['loop:r7'])
    expect(entities[0].progress).toBeCloseTo(0.25)
  })

  it('what waits on you for a row decides it: an approval, else a question', () => {
    const running = [
      row({ work: 'workflow:r1', kind: 'workflow', session: '', waiting: ['ask-1'] }),
      row({ work: 'workflow:r2', kind: 'workflow', session: '', waiting: ['q-7'] }),
    ]
    const seen = byId(sources({ running: { running, finished: [] }, approvals: [asked('ask-1')] }))
    expect(seen['run:r1'].state).toBe('waiting_approval')
    expect(seen['run:r2'].state).toBe('needs_input')
  })
})

describe('the background is what the list does not hold', () => {
  it('a quiet chat is idle, and a chat the list holds is not painted twice', () => {
    const entities = fold(sources({
      running: { running: [row()], finished: [] },
      sessions: [chat({ key: 'c1', title: 'Plan the garden' }), chat()],
    }))
    expect(entities.map((e) => [e.id, e.state])).toEqual([['session:c1', 'working'], ['session:quiet', 'idle']])
  })

  it('a loop that is not running keeps its own state; one the list holds is the list\'s', () => {
    const entities = fold(sources({
      running: { running: [row({ work: 'loop:0a1b2c3d', kind: 'loop', session: '', phase: 'input', line: 'Needs your answer' })], finished: [] },
      loops: [loop(), loop({ id: 'dead', name: 'Broke', status: 'failed', session_key: '' })],
    }))
    expect(entities.map((e) => [e.id, e.state])).toEqual([['loop:0a1b2c3d', 'needs_input'], ['loop:dead', 'error']])
  })

  it('a just-finished failure paints its error; a just-finished success rests', () => {
    const finished = [
      row({ work: 'subagent:h2', kind: 'subagent', phase: 'error', line: 'Stopped: the tool failed' }),
      row({ work: 'workflow:r9', kind: 'workflow', session: '', phase: 'done', line: 'Done in 12 seconds' }),
    ]
    const seen = byId(sources({ running: { running: [], finished } }))
    expect(seen['subagent:h2'].state).toBe('error')
    expect(seen['run:r9'].state).toBe('idle')
  })
})
