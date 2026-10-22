import { describe, expect, it } from 'vitest'
import type { ApprovalInput } from './attentionLanes'
import type { ChatAttention } from './api'
import { toThreads, type Thread, type ThreadChat, type ThreadSources } from './useThreads'
import { endedState, runState, runningState } from '../test/runState'

// ── A chat's pip reads the gateway's `attention`, under Home's lanes (#3814 B2, #3812 A6) ──────
//
// Unread is a fact the gateway keeps (`chat_seen`), so every window and the phone agree on it:
// each chat-list row carries `attention: {unread, needs_you, your_turn}`. One pip shows, by
// precedence: needs you, then running, then failed, then unread. A failed chat you have seen
// shows nothing, and a helper's waiting ask names the helper on the row.

const SEEN: ChatAttention = { unread: false, needs_you: false, your_turn: true }
const UNREAD: ChatAttention = { unread: true, needs_you: false, your_turn: true }
const NEEDS_YOU: ChatAttention = { unread: false, needs_you: true, your_turn: false }

function chat(over: Partial<ThreadChat> = {}): ThreadChat {
  return { key: 'chat-1', title: 'Plan the garden', run_state: runState(), origin: 'manual', project_id: 'p-garden', ...over }
}

function approval(over: Partial<ApprovalInput> = {}): ApprovalInput {
  return { id: 'req-1', request_id: 'req-1', tool: 'Bash', session: 'chat-1', ts: 1_000, risk: '', grant_agent: '', source_label: 'chat', ...over }
}

function thread(over: Partial<ThreadSources>): Thread {
  const src: ThreadSources = { sessions: [], loops: [], items: [], approvals: [], ...over }
  const found = toThreads(src).flatMap((g) => g.threads).find((t) => t.key === 'chat:chat-1')
  expect(found, 'no thread chat:chat-1').toBeDefined()
  return found!
}

describe("a chat's pip", () => {
  it('a reply you have not seen is unread', () => {
    expect(thread({ sessions: [chat({ run_state: endedState('complete'), attention: UNREAD })] }).pip).toBe('unread')
  })

  it('a reply you have seen shows nothing', () => {
    expect(thread({ sessions: [chat({ run_state: endedState('complete'), attention: SEEN })] }).pip).toBeNull()
  })

  it('a failure you have not seen is failed, and one you have seen shows nothing', () => {
    expect(thread({ sessions: [chat({ run_state: endedState('error'), attention: UNREAD })] }).pip).toBe('failed')
    expect(thread({ sessions: [chat({ run_state: endedState('error'), attention: SEEN })] }).pip).toBeNull()
  })

  it('needs you outranks running', () => {
    const t = thread({ sessions: [chat({ run_state: runningState({ phase: 'approval' }), attention: NEEDS_YOU })] })
    expect(t.pip).toBe('waiting')
  })

  it('what the gateway says waits on you shows, though no approval has reached this window yet', () => {
    expect(thread({ sessions: [chat({ attention: NEEDS_YOU })] }).pip).toBe('waiting')
  })

  it('running outranks failed', () => {
    const t = thread({ sessions: [chat({ run_state: runningState({ last_outcome: 'error' }), attention: UNREAD })] })
    expect(t.pip).toBe('running')
  })

  it('failed outranks unread: an unseen failure is marked failed', () => {
    expect(thread({ sessions: [chat({ run_state: endedState('error'), attention: UNREAD })] }).pip).toBe('failed')
    expect(thread({ sessions: [chat({ run_state: endedState('interrupted'), attention: UNREAD })] }).pip).toBe('unread')
  })

  it('a row the gateway served no attention for shows only what the lanes say', () => {
    expect(thread({ sessions: [chat({ run_state: endedState('complete') })] }).pip).toBeNull()
    expect(thread({ sessions: [chat({ run_state: runningState() })] }).pip).toBe('running')
  })

  it("a helper's waiting ask is waiting on you, and the row names the helper", () => {
    const helper = approval({ id: 'spawn:h1', request_id: 'spawn:h1', tool: 'spawn_agent', tool_purpose: 'Research seed suppliers' })
    const t = thread({ sessions: [chat({ attention: NEEDS_YOU })], approvals: [helper] })
    expect(t.pip).toBe('waiting')
    expect(t.pipNote).toBe('A helper is waiting for your approval: Research seed suppliers')
  })

  it("the chat's own ask names no helper", () => {
    const t = thread({ sessions: [chat({ attention: NEEDS_YOU })], approvals: [approval()] })
    expect(t.pip).toBe('waiting')
    expect(t.pipNote).toBeUndefined()
  })
})
