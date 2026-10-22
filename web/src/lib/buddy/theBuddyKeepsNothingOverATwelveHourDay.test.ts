import { describe, it, expect } from 'vitest'
import type { PendingApproval, RunningList, RunningRow, WaitingList } from '../api'
import { waitingApproval, waitingList } from '../../test/waiting'
import { createBuddyHost, type HostDeps, type HostSnapshot } from './host'

// ── Twelve hours of the buddy's day keep nothing they no longer need (#3902) ───────────────────────
//
// The buddy runs all day, so anything it keeps per piece of work, per alert or per timer and never
// lets go is a leak that only a long day shows. This drives the live host (the one the buddy window
// runs) through a day on a fake clock: a piece of work starts, steps, asks for an approval, is
// answered, finishes and leaves, again and again, for twelve hours of the buddy's own time, in
// seconds of the test's. At the same point of every lap, with nothing running, the host must hold
// the same: the same timers, and a snapshot no larger than the second lap's. The renderer's own heap
// and listeners are the real-Electron tier's to measure (`desktop/test/buddyIdle.test.js`).

const LAP_MS = 180_000
const LAPS = (12 * 60 * 60 * 1000) / LAP_MS

/** A clock and a timer queue the host is handed, advanced by the test. */
function fakeClock() {
  let now = 1_780_000_000_000
  let seq = 0
  const pending = new Map<number, { at: number; fire: () => void }>()
  return {
    now: () => now,
    setTimer: (fire: () => void, ms: number) => { const id = ++seq; pending.set(id, { at: now + ms, fire }); return id },
    clearTimer: (handle: unknown) => { pending.delete(handle as number) },
    pending: () => pending.size,
    /** Fire every timer due by *ms* from now, in order, then stand at it. */
    advance(ms: number) {
      const until = now + ms
      for (;;) {
        const next = [...pending.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0]
        if (!next) break
        pending.delete(next[0])
        now = next[1].at
        next[1].fire()
      }
      now = until
    },
  }
}

const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve() }

const row = (lap: number, phase: RunningRow['phase'], waiting: string[] = []): RunningRow => ({
  work: `chat:s${lap}`, kind: 'chat', session: `s${lap}`, phase, line: phase === 'approval' ? 'Needs your OK' : 'Working', since: null,
  name: `Errand ${lap}`, agent: '', started_at: new Date(1_780_000_000_000 + lap * LAP_MS).toISOString(), route: `#/chat/s${lap}`, blocked: false, waiting,
} as RunningRow)

const approval = (lap: number): PendingApproval => ({
  id: `ap-${lap}`, request_id: `ap-${lap}`, source: 'chat', tool: 'Bash', tool_input: 'ls', tool_purpose: `List the folder, lap ${lap}`,
  session: `s${lap}`, ts: Math.round(1_780_000_000 + lap * 180), confirms: [], session_title: '', agent: '', risk: 'safe',
  grant_agent: '', source_label: 'chat', deny_effect: 'The turn stops here.',
} as PendingApproval)

describe('a twelve-hour day on a fake clock', () => {
  it('the host holds the same timers and no more state at the end of every lap', async () => {
    const clock = fakeClock()
    const lists: { running: RunningList; waiting: WaitingList } = {
      running: { running: [], finished: [] },
      waiting: { waiting: [], unreadable: [] } as WaitingList,
    }
    let snap: HostSnapshot | null = null
    const deps: HostDeps = {
      running: async () => lists.running,
      waiting: async () => lists.waiting,
      listening: async () => ({ on: false }),
      resolveApproval: async () => ({ ok: true }),
      answerChatQuestion: async () => ({ ok: true }),
      now: clock.now,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
    }
    const host = createBuddyHost(deps, (s) => { snap = s })
    const ring = async () => { host.ring(); await flush() }
    const at = async (ms: number, change: () => void) => { clock.advance(ms); change(); await ring() }

    const sizes: number[] = []
    const timers: number[] = []
    for (let lap = 1; lap <= LAPS; lap++) {
      await at(1_000, () => { lists.running = { running: [row(lap, 'thinking')], finished: [] } })
      await at(20_000, () => {
        lists.running = { running: [row(lap, 'approval', [`ap-${lap}`])], finished: [] }
        lists.waiting = waitingList(waitingApproval(approval(lap), { work: `chat:s${lap}`, consequence: 'changes' }))
      })
      // Answered elsewhere (the phone, the main window): the alert leaves saying so.
      await at(20_000, () => {
        lists.running = { running: [row(lap, 'thinking')], finished: [] }
        lists.waiting = { waiting: [], unreadable: [] } as WaitingList
      })
      await at(40_000, () => { lists.running = { running: [], finished: [row(lap, 'done')] } })
      await at(40_000, () => { lists.running = { running: [], finished: [] } })
      // The rest of the lap with nothing to do: every fold and hold has had its time.
      clock.advance(LAP_MS - 121_000)
      await flush()
      const s = snap as HostSnapshot | null
      expect(s, 'the host said what it holds').not.toBeNull()
      sizes.push(JSON.stringify({ machine: s!.machine, steps: s!.steps, rows: [...s!.rows], items: [...s!.items], beats: s!.beats }).length)
      timers.push(clock.pending())
    }
    host.stop()

    expect(sizes.length).toBe(LAPS)
    expect(LAPS, 'twelve hours of three-minute laps').toBe(240)
    // The first lap starts from nothing; from the second on, every lap ends holding the same.
    const steady = sizes[1]
    expect(Math.max(...sizes.slice(1)), 'no lap ends holding more than the second').toBeLessThanOrEqual(steady)
    expect(new Set(timers.slice(1)).size, 'the same timers at the end of every lap').toBe(1)
    expect(clock.pending(), 'stopped, it holds no timer').toBe(0)
  })
})
