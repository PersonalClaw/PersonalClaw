import { describe, expect, it } from 'vitest'
import { toThreads, type ThreadChat, type ThreadLoop, type ThreadSources } from '../lib/useThreads'
import type { ApprovalInput } from '../lib/attentionLanes'
import { runState, runningState } from '../test/runState'
import { railGroups, type RailProject } from './threadRail'

// ── The sidebar lists a project's threads in one named order (#3814) ───────────────────────────────
//
// One order serves chats, goal loops and code sessions alike: pinned first, then the latest
// activity, newest first, with ties broken by the thread's key. A chat's activity is the time its
// list row says it last did something (`sessionRecencyMs`); a loop's is its newest cycle's, which
// its row says (`last_cycle_at`). A row with no cycle time falls back to its workers' latest
// activity, else when it completed, started or was created.
//
// Status never moves a row: the pips say what needs the owner and ⌘⌥A jumps to it, so a row does not
// jump when a turn starts, an approval arrives or a run fails. The loops page's "running first" and
// the code section's status tiers do not carry into the sidebar.

const at = (hhmm: string) => `2026-10-20T${hhmm}:00+00:00`
const secs = (hhmm: string) => Date.parse(at(hhmm)) / 1000

function chat(key: string, activity: string, over: Partial<ThreadChat> = {}): ThreadChat {
  return { key, title: key, run_state: runState({ session: key }), origin: 'manual', project_id: 'p-garden', last_activity_ts: at(activity), ...over }
}

function loop(id: string, over: Partial<ThreadLoop> = {}): ThreadLoop {
  return {
    id, kind: 'goal', name: id, task: id, status: 'complete', total_cycles: 3, max_cycles: 10,
    started_at: null, session_key: `loop-${id}`, project_id: 'p-garden', ...over,
  } as ThreadLoop
}

function approval(session: string): ApprovalInput {
  return { id: 'req-1', request_id: 'req-1', tool: 'Bash', session, ts: secs('15:00'), risk: '', grant_agent: '', source_label: 'chat' } as ApprovalInput
}

function sources(over: Partial<ThreadSources> = {}): ThreadSources {
  return { sessions: [], loops: [], items: [], approvals: [], ...over }
}

const PROJECTS: RailProject[] = [
  { id: 'p-personal', name: 'Personal', status: 'active' },
  { id: 'p-garden', name: 'Garden', status: 'active' },
]

/** The keys of each group the rail shows, by project id. */
function order(src: ThreadSources): Record<string, string[]> {
  return Object.fromEntries(railGroups(toThreads(src), PROJECTS).map((g) => [g.projectId, g.threads.map((t) => t.key)]))
}

// Shuffled on purpose: chats, then loops, neither in the order they show.
const SHUFFLED = sources({
  sessions: [
    chat('chat-a', '10:00'),
    chat('chat-t2', '06:00'),
    // The worker of loop `w0rk3r55`: its latest activity is that loop's newest cycle.
    chat('loop-w0rk3r55', '14:00', { origin: 'loop', source_id: 'w0rk3r55' }),
    chat('chat-b', '12:00'),
    chat('chat-t1', '06:00'),
    chat('chat-p', '08:00', { pinned: true }),
  ],
  loops: [
    loop('l00pl00p', { started_at: secs('11:00'), created_at: secs('10:59') }),
    loop('c0dec0de', { kind: 'code', started_at: secs('09:00'), completed_at: secs('13:00'), created_at: secs('08:59') }),
    loop('w0rk3r55', { status: 'running', started_at: secs('07:00'), created_at: secs('06:59') }),
  ],
})

const NAMED_ORDER = [
  'chat:chat-p', // pinned, though its activity is older than all but the ties
  'loop:w0rk3r55', // 14:00, its worker's latest activity
  'loop:c0dec0de', // 13:00, when it completed
  'chat:chat-b', // 12:00
  'loop:l00pl00p', // 11:00, when it started
  'chat:chat-a', // 10:00
  'chat:chat-t1', // 06:00, a tie broken by key
  'chat:chat-t2',
]

describe('the threads of a project', () => {
  it('show pinned first, then by latest activity across chats, loops and code sessions, with ties by key', () => {
    expect(order(SHUFFLED)['p-garden']).toEqual(NAMED_ORDER)
  })

  it('show in the same order whatever order the lists arrive in', () => {
    const reversed = sources({ sessions: [...SHUFFLED.sessions].reverse(), loops: [...SHUFFLED.loops].reverse() })
    expect(order(reversed)['p-garden']).toEqual(NAMED_ORDER)
  })

  it('a status change moves no row: a turn starting, an approval arriving and a run failing', () => {
    const changed = sources({
      sessions: SHUFFLED.sessions.map((s) => (s.key === 'chat-a' ? { ...s, run_state: runningState({ session: 'chat-a' }) } : s)),
      loops: SHUFFLED.loops.map((l) => (l.id === 'l00pl00p' ? { ...l, status: 'failed' } : l.id === 'w0rk3r55' ? { ...l, status: 'complete' } : l)),
      approvals: [approval('chat-b')],
    })
    const pips = (src: ThreadSources) => Object.fromEntries(toThreads(src).flatMap((g) => g.threads).map((t) => [t.key, t.pip]))
    // The statuses did change, so the order below is held for the right reason.
    expect(pips(SHUFFLED)).toMatchObject({ 'chat:chat-a': null, 'chat:chat-b': null, 'loop:l00pl00p': null, 'loop:w0rk3r55': 'running' })
    expect(pips(changed)).toMatchObject({ 'chat:chat-a': 'running', 'chat:chat-b': 'waiting', 'loop:l00pl00p': 'failed', 'loop:w0rk3r55': null })
    expect(order(changed)['p-garden']).toEqual(NAMED_ORDER)
  })

  it("a loop with no cycle time is placed by its workers' activity, else when it completed, else started, else was created", () => {
    const src = sources({
      sessions: [chat('loop-w', '00:06', { origin: 'loop', source_id: 'w', project_id: 'p-garden' })],
      loops: [
        loop('x', { created_at: secs('00:01') }),
        loop('y', { created_at: secs('00:00'), started_at: secs('00:02') }),
        loop('z', { created_at: secs('00:00'), started_at: secs('00:01'), completed_at: secs('00:04') }),
        // Its worker's activity outranks the times its own row carries.
        loop('w', { created_at: secs('00:00'), started_at: secs('00:01'), completed_at: secs('00:03') }),
      ],
    })
    expect(order(src)['p-garden']).toEqual(['loop:w', 'loop:z', 'loop:y', 'loop:x'])
  })

  it("a loop whose row says when its newest cycle ran is placed by that time alone", () => {
    const src = sources({
      sessions: [
        // Loop r's worker, mid-turn after r's last cycle: a cycle is a worker's whole turn.
        chat('loop-r', '00:09', { origin: 'loop', source_id: 'r' }),
        chat('c', '00:05'),
      ],
      loops: [
        // Its last cycle ran at 00:04. Resumed at 00:08 and its worker busy at 00:09: a status
        // change and a turn under way move no row.
        loop('r', { status: 'running', created_at: secs('00:00'), started_at: secs('00:08'), last_cycle_at: secs('00:04') }),
        // Its last cycle ran at 00:06, a minute before it completed.
        loop('d', { created_at: secs('00:00'), started_at: secs('00:01'), completed_at: secs('00:07'), last_cycle_at: secs('00:06') }),
        // No cycle yet: placed as before, here by when it started.
        loop('n', { created_at: secs('00:00'), started_at: secs('00:03'), last_cycle_at: null }),
      ],
    })
    expect(order(src)['p-garden']).toEqual(['loop:d', 'chat:c', 'loop:r', 'loop:n'])
  })

  it('pinned threads come first among themselves by activity, in every group, "No project" included', () => {
    const src = sources({
      sessions: [
        chat('old-pin', '01:00', { pinned: true }),
        chat('new', '09:00'),
        chat('new-pin', '05:00', { pinned: true }),
        chat('loose', '09:00', { project_id: '' }),
        chat('loose-pin', '01:00', { project_id: '', pinned: true }),
      ],
    })
    expect(order(src)).toEqual({
      'p-personal': [],
      'p-garden': ['chat:new-pin', 'chat:old-pin', 'chat:new'],
      '': ['chat:loose-pin', 'chat:loose'],
    })
  })

  it('a thread with no time it can read sorts last, by key, and never throws', () => {
    const src = sources({
      sessions: [{ key: 'undated-b', title: 'b', run_state: runState({ session: 'undated-b' }), origin: 'manual', project_id: 'p-garden' }, chat('dated', '09:00')],
      loops: [loop('undated-a')],
    })
    expect(order(src)['p-garden']).toEqual(['chat:dated', 'chat:undated-b', 'loop:undated-a'])
  })
})
