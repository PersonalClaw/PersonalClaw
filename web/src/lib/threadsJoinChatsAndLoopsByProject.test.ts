import { describe, expect, it } from 'vitest'
import { NO_PROJECT, toThreads, type ThreadChat, type ThreadLoop, type ThreadSources } from './useThreads'
import { runState, runningState } from '../test/runState'

// ── Every chat, goal loop and code session is a thread, under its project (#3814) ──────────────
//
// The sidebar lists projects with their threads beneath, and a thread's type changes its layout,
// never where it lives. The join reads the chat list and the loop list, both of which say which
// project a row belongs to (`project_id`), and groups them.
//
// 🪤 A loop's workers are chat sessions too (`origin: 'loop'`, the loop's id as `source_id`): its
// stage worker, each task worker and its planner. Listing them would show one loop as several
// threads, titled by internal keys. They are the loop's, as Home's lanes fold them (`toLanes`).
//
// A chat with no project goes under its own "No project" group (`NO_PROJECT`), the word the
// composer's project picker uses. It is not filed under a built-in project, which would read as
// though the chat were fed that project's context.

function chat(over: Partial<ThreadChat> = {}): ThreadChat {
  return { key: 'chat-1', title: 'Plan the garden', run_state: runState(), origin: 'manual', project_id: 'p-garden', ...over }
}

function loop(over: Partial<ThreadLoop> = {}): ThreadLoop {
  return {
    id: 'a1b2c3d4', kind: 'goal', name: 'Find a seed supplier', task: 'find a seed supplier', status: 'complete',
    total_cycles: 3, max_cycles: 10, started_at: 1_000, session_key: 'loop-a1b2c3d4', project_id: 'p-garden',
    ...over,
  }
}

function sources(over: Partial<ThreadSources> = {}): ThreadSources {
  return { sessions: [], loops: [], items: [], approvals: [], ...over }
}

/** Each group as `project → [thread key, kind]`, so a test reads the whole shape at once. */
function shape(src: ThreadSources): Record<string, [string, string][]> {
  return Object.fromEntries(toThreads(src).map((g) => [g.projectId, g.threads.map((t) => [t.key, t.kind])]))
}

describe('the threads join', () => {
  it('a chat, a goal loop and a code session in one project are three threads under it', () => {
    const src = sources({
      sessions: [chat()],
      loops: [loop(), loop({ id: 'e5f6a7b8', kind: 'code', name: 'Fence gate', session_key: 'loop-e5f6a7b8' })],
    })
    expect(shape(src)).toEqual({
      'p-garden': [['chat:chat-1', 'chat'], ['loop:a1b2c3d4', 'loop'], ['loop:e5f6a7b8', 'code']],
    })
    const [group] = toThreads(src)
    expect(group.threads.map((t) => [t.title, t.route])).toEqual([
      ['Plan the garden', 'chat/chat-1'],
      ['Find a seed supplier', 'loops/a1b2c3d4'],
      ['Fence gate', 'code/e5f6a7b8'],
    ])
  })

  it("a loop's worker sessions are not threads of their own", () => {
    const workers: ThreadChat[] = [
      chat({ key: 'loop-a1b2c3d4', title: 'loop-a1b2c3d4', origin: 'loop', source_id: 'a1b2c3d4', run_state: runningState() }),
      chat({ key: 'loop-a1b2c3d4-t-1', title: 'Untitled chat', origin: 'loop', source_id: 'a1b2c3d4' }),
      chat({ key: 'loop-plan-a1b2c3d4', title: 'loop-plan-a1b2c3d4', origin: 'loop', source_id: 'a1b2c3d4' }),
      // A worker whose loop this snapshot does not hold is still a loop's, never a chat.
      chat({ key: 'loop-0f0f0f0f', title: 'loop-0f0f0f0f', origin: 'loop', source_id: '0f0f0f0f' }),
    ]
    expect(shape(sources({ sessions: [chat(), ...workers], loops: [loop()] }))).toEqual({
      'p-garden': [['chat:chat-1', 'chat'], ['loop:a1b2c3d4', 'loop']],
    })
  })

  it("a session the list names as a loop's own session is folded into the loop, whatever its origin says", () => {
    const stage = chat({ key: 'loop-a1b2c3d4', title: 'loop-a1b2c3d4', origin: undefined, project_id: '' })
    expect(shape(sources({ sessions: [stage], loops: [loop()] }))).toEqual({
      'p-garden': [['loop:a1b2c3d4', 'loop']],
    })
  })

  it('each thread lands under its own project, and none crosses to another', () => {
    const src = sources({
      sessions: [chat(), chat({ key: 'chat-2', title: 'Tax return', project_id: 'p-taxes' })],
      loops: [loop({ project_id: 'p-taxes' })],
    })
    expect(shape(src)).toEqual({
      'p-garden': [['chat:chat-1', 'chat']],
      'p-taxes': [['chat:chat-2', 'chat'], ['loop:a1b2c3d4', 'loop']],
    })
  })

  it('a chat and a loop with no project go under the "No project" group, not a built-in project', () => {
    const src = sources({
      sessions: [chat({ project_id: '' }), chat({ key: 'chat-2', project_id: undefined })],
      loops: [loop({ project_id: undefined })],
    })
    expect(NO_PROJECT).toBe('')
    expect(shape(src)).toEqual({
      [NO_PROJECT]: [['chat:chat-1', 'chat'], ['chat:chat-2', 'chat'], ['loop:a1b2c3d4', 'loop']],
    })
  })

  it('a chat that came in on a channel is a chat thread', () => {
    const phone = chat({ key: 'chat-9', origin: 'channel', source_id: 'C1', source_label: 'Telegram' })
    expect(shape(sources({ sessions: [phone] }))).toEqual({ 'p-garden': [['chat:chat-9', 'chat']] })
  })

  it('a chat is named as every other surface names one, never by its key', () => {
    const untitled = chat({ key: 'chat-3-1700000000', title: 'chat-3-1700000000', prompt_preview: 'Which roses take shade?' })
    const [group] = toThreads(sources({ sessions: [untitled] }))
    expect(group.threads[0].title).toBe('Which roses take shade?')
  })

  it('a malformed row is skipped, never thrown on', () => {
    const src = sources({
      sessions: [null, 'x', { key: '' }, chat()] as unknown as ThreadChat[],
      loops: [null, { id: '' }, loop()] as unknown as ThreadLoop[],
    })
    expect(() => toThreads(src)).not.toThrow()
    expect(shape(src)).toEqual({ 'p-garden': [['chat:chat-1', 'chat'], ['loop:a1b2c3d4', 'loop']] })
  })

  it('nothing to list is no groups at all', () => {
    expect(toThreads(sources())).toEqual([])
  })
})
