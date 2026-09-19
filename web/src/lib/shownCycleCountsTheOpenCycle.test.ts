import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { ACTIVE_LOOP_STATUSES, PRELAUNCH_LOOP_STATUSES, shownCycle } from './loopStatus'

// ── The cycle number a user reads has ONE implementation ────────────────────────────────────
//
// `total_cycles` is the COMPLETED count, so "working on cycle N" is one more than it whenever a
// cycle is open. Three surfaces each derived that `+1` themselves and all three gated it on
// `status === 'running'` alone:
//
//   * `LoopCockpitPage.tsx`'s `cycleLabel` (the status bar);
//   * `LoopsListPage.tsx`'s row label + its ProgressRing aria-label;
//   * `LoopsListPage.tsx`'s `LoopPeek` (a THIRD copy, which the issue's own anchor missed).
//
// But the label is RENDERED for paused loops too, so pausing mid-cycle-1 took the non-running
// branch and the counter fell from "cycle 1/30" to "cycle 0/30" — reading as if the pause had
// discarded the work, and disagreeing with the cockpit's own "Cycle 1 · paused" detail row on the
// same screen (issue 274). A behavioural test on any one surface would have passed the day it landed
// while the other two stayed wrong, so this is a CENSUS as well as a behaviour check.

describe('shownCycle counts the open cycle', () => {
  it('adds the in-flight cycle for every ACTIVE status, not just running', () => {
    for (const status of ACTIVE_LOOP_STATUSES) {
      expect(shownCycle(status, 0), `${status} mid-cycle-1 must read 1`).toBe(1)
      expect(shownCycle(status, 4), `${status} mid-cycle-5 must read 5`).toBe(5)
    }
  })

  it('pausing does not change the number the user reads', () => {
    // The exact regression: the same loop, one second apart, 0 cycles completed.
    expect(shownCycle('paused', 0)).toBe(shownCycle('running', 0))
  })

  it('reads the raw completed count for PRELAUNCH and ENDED statuses', () => {
    for (const status of PRELAUNCH_LOOP_STATUSES) {
      expect(shownCycle(status, 0), `${status} has no open cycle`).toBe(0)
    }
    for (const status of ['complete', 'failed', 'stopped']) {
      expect(shownCycle(status, 7), `${status} has no cycle left open`).toBe(7)
    }
  })

  it('is the only place the +1 is derived', () => {
    const root = join(__dirname, '..')
    const offenders: string[] = []
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        const p = join(dir, entry)
        if (statSync(p).isDirectory()) { walk(p); continue }
        if (!/\.tsx?$/.test(p) || /\.test\.tsx?$/.test(p)) continue
        if (relative(root, p) === 'lib/loopStatus.ts') continue  // the one owner
        for (const line of readFileSync(p, 'utf8').split('\n')) {
          // A local re-derivation looks like `<something>total_cycles + 1`.
          if (/total_cycles\s*\+\s*1/.test(line)) offenders.push(`${relative(root, p)}: ${line.trim()}`)
        }
      }
    }
    walk(root)
    // The cockpit's Details row is the one legitimate unconditional `+1`: it renders only inside
    // the live cycle card, so it has no status to gate on — and agreeing with it is the fix's
    // whole point. Any OTHER site is a fourth copy of the gate that just went wrong three times.
    expect(offenders.filter((o) => !o.startsWith('pages/loops/LoopCockpitPage.tsx'))).toEqual([])
  })
})
