import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { PRELAUNCH_RUN_STATUSES, isPrelaunch, isTerminal } from './workflowMeta'

// ── A run's lifecycle has THREE phases and the header had TWO branches (#372) ─────────────────
//
// `isTerminal` is a binary split, and `draft` is on neither side of it: a run that has not started
// is not terminal, so the header's `!isTerminal(run.status) ? <live controls> : <Fork>` routed a
// forked run into the LIVE branch. It rendered **Steer + Pause + Cancel** — controls for work in
// flight — on a run with no work in flight and no way to start any. The only outcome the fork's
// author was offered was cancelling something that never ran, and Pause on a draft was doubly
// inert (#370: `pause_requested` has no reader).
//
// Measured on the shipped file before the fix: `grep -n draft WorkflowRunDetail.tsx` returned ZERO
// matches, so nothing on the page knew the state existed, while `workflowMeta` had already named it
// (`PRELAUNCH_RUN_STATUSES`, added for the policy-overrides editor). The vocabulary was there; the
// header just never asked.
//
// 🪤 THE FAKE VERSION of this test asserts the word "Start" appears in the file. That passes on a
// Start button rendered in the wrong branch, or on one gated behind `=== 'draft'` (which a future
// prelaunch status would not inherit). So the assertions below are about the BRANCH: prelaunch is
// asked first, it is asked through `isPrelaunch`, and the live-only controls stay out of it.

const SOURCE = readFileSync(join(__dirname, 'WorkflowRunDetail.tsx'), 'utf8')

/** The header's cluster, from `<HeaderActions>` to its close. Sliced rather than searched whole-file,
 *  because `Pause` and `Cancel` also appear in imports, callbacks and the node rows, and a
 *  whole-file grep would read those as header controls. */
function cluster(): string {
  const start = SOURCE.indexOf('<HeaderActions>')
  const end = SOURCE.indexOf('</HeaderActions>', start)
  expect(start, 'the run header has no action cluster').toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return SOURCE.slice(start, end)
}

/** The run's next step, last in the row (the one order): the branch on its lifecycle phase. */
function nextStep(): string {
  const block = cluster()
  const start = block.indexOf('{isPrelaunch(run.status) ? (')
  expect(start, 'the header does not branch on isPrelaunch — draft still falls into a two-way split').toBeGreaterThan(-1)
  return block.slice(start)
}

/** The prelaunch arm alone — the branch's first consequent. */
function prelaunchArm(): string {
  const block = nextStep()
  return block.slice(0, block.indexOf('isTerminal(run.status)'))
}

describe('the run-detail header branches on all three lifecycle phases', () => {
  it('asks PRELAUNCH before the terminal split', () => {
    const block = nextStep()
    const prelaunch = block.indexOf('isPrelaunch(run.status)')
    const terminal = block.indexOf('isTerminal(run.status)')
    expect(prelaunch).toBeGreaterThan(-1)
    expect(terminal).toBeGreaterThan(-1)
    // Order matters: `!isTerminal` is true for a draft, so asking it first swallows the phase.
    expect(prelaunch).toBeLessThan(terminal)
  })

  it('offers Start in the prelaunch arm', () => {
    expect(prelaunchArm()).toContain('Start')
    // The button has to reach the new route, not `startWorkflowRun` (which takes a def name and
    // would mint a SECOND run, leaving the fork stranded exactly as before).
    expect(SOURCE).toContain('api.startDraftWorkflowRun(runId)')
  })

  it('does NOT offer Pause or Steer on a run that has not started', () => {
    // Both are controls for work in flight. Pause on a draft was the reported symptom.
    expect(prelaunchArm()).not.toContain('Pause')
    expect(prelaunchArm()).not.toContain('Steer')
    // Steer leads the row (the one order), on a run in flight only.
    expect(cluster()).toMatch(/\{!isPrelaunch\(run\.status\) && !isTerminal\(run\.status\) && \(\s*<HeaderControl icon=\{MessageSquarePlus\} label="Steer"/)
  })

  it('still offers Cancel before launch, because a draft is abandonable', () => {
    // `service.cancel_run` finalizes a PRELAUNCH run itself (it has no controller to see the
    // intent), so this button works — and without it a draft would be undeletable. Cancel leads
    // the row on any run that has not ended, a draft among them.
    expect(cluster()).toMatch(/\{!isTerminal\(run\.status\) && \(\s*<HeaderControl icon=\{X\} label="Cancel"/)
    expect(isTerminal('draft')).toBe(false)
  })

  it('keeps Fork on the terminal arm and the live controls on the active arm', () => {
    // A regression here would mean the prelaunch arm was wired by REPLACING one of the two arms
    // above and break the two phases that already worked.
    const afterTerminal = nextStep().slice(nextStep().indexOf('isTerminal(run.status)'))
    expect(afterTerminal).toContain('Pause')
    expect(afterTerminal).toContain('Resume')
    expect(afterTerminal).toContain('Fork')
  })
})

describe('the phase sets stay disjoint', () => {
  it('draft is prelaunch and is not terminal', () => {
    expect(isPrelaunch('draft')).toBe(true)
    expect(isTerminal('draft')).toBe(false)
  })

  it('no status is both prelaunch and terminal', () => {
    for (const s of PRELAUNCH_RUN_STATUSES) expect(isTerminal(s)).toBe(false)
  })

  it('a running run is neither — it falls to the active arm', () => {
    for (const s of ['running', 'paused', 'needs_input']) {
      expect(isPrelaunch(s)).toBe(false)
      expect(isTerminal(s)).toBe(false)
    }
  })
})
