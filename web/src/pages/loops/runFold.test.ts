import { describe, it, expect } from 'vitest'
import { foldRun, foldRunSnapshot, foldReducer, emptyRunFlags, type RunSnapshot } from './runFold'

// A minimal PHASE-TRACKED (code/design) run snapshot. `phase_tracked` is the kind
// strategy's `tracks_phases` declaration as the store puts it on the redacted view — a code
// loop is one of the two kinds that actually advances phase_status.
const phased = (over: Partial<RunSnapshot> = {}): RunSnapshot => ({
  id: 'r1', kind: 'code', status: 'running', total_cycles: 3, max_cycles: 30,
  phase_tracked: true,
  plan: [
    { stage: 'design', title: 'Design', min_cycles: 1 },
    { stage: 'build', title: 'Build', min_cycles: 2 },
    { stage: 'verify', title: 'Verify', min_cycles: 1 },
  ],
  phase_status: { design: 'done', build: 'active' },
  ...over,
})

const goal = (over: Partial<RunSnapshot> = {}): RunSnapshot => ({
  id: 'g1', kind: 'goal', status: 'running', total_cycles: 5, max_cycles: 30,
  kind_config: { goal_type: 'open_ended', sub_goals: ['find sources', 'synthesize'] },
  ...over,
})

// The completed research loop from #448, exactly as `/api/loops/f8f8ca9f` reported it: a
// real five-objective plan, `phase_status` never written, 20 cycles of findings, complete.
const research = (over: Partial<RunSnapshot> = {}): RunSnapshot => ({
  id: 'f8f8ca9f', kind: 'research', status: 'complete', total_cycles: 20, max_cycles: 30,
  phase_tracked: false,
  plan: [
    { title: 'Scope the comparison', min_cycles: 1 },
    { title: 'Gather RAIDZ2 sources', min_cycles: 1 },
    { title: 'Gather dRAID sources', min_cycles: 1 },
    { title: 'Compare resilver behaviour', min_cycles: 1 },
    { title: 'Write the report', min_cycles: 1 },
  ],
  phase_status: {},
  ...over,
})

describe('foldRunSnapshot — phased kinds', () => {
  it('derives stage progress + per-step done/active/todo from plan + phase_status', () => {
    const vm = foldRunSnapshot(phased())
    expect(vm.phased).toBe(true)
    expect(vm.phaseTotal).toBe(3)
    expect(vm.phaseDone).toBe(1) // only 'design' is done
    expect(vm.progressLabel).toBe('1/3 stages')
    expect(vm.steps.map((s) => s.state)).toEqual(['done', 'active', 'todo'])
    expect(vm.steps.map((s) => s.label)).toEqual(['Design', 'Build', 'Verify'])
  })

  it('keys phase_status by stage.trim() || title.trim() — a stageless row uses its title', () => {
    // The parity fix: a titled-but-stageless row (empty `stage`) must key on its TITLE,
    // not fall to an empty-string key that never matches phase_status (stuck 'todo').
    const vm = foldRunSnapshot(phased({
      plan: [{ stage: '', title: 'Kickoff', min_cycles: 1 }],
      phase_status: { Kickoff: 'done' },
    }))
    expect(vm.steps[0].state).toBe('done') // matched by title, not stuck todo
    expect(vm.steps[0].key).toBe('Kickoff')
  })

  it("treats 'running' phase_status the same as 'active'", () => {
    const vm = foldRunSnapshot(phased({ phase_status: { design: 'running' } }))
    expect(vm.steps[0].state).toBe('active')
  })

  it('empty plan → no progress label, no steps', () => {
    const vm = foldRunSnapshot(phased({ plan: [], phase_status: {} }))
    expect(vm.progressLabel).toBe('')
    expect(vm.steps).toEqual([])
  })
})

// #448: `phased = kind !== 'goal'` put research + general in the tracked set while only
// code + design have a phase writer, so a FINISHED run reported zero progress.
describe('foldRunSnapshot — a kind with no phase writer claims no stage progress', () => {
  it('a completed 20-cycle research run shows NO stage fraction (was "0/5 stages")', () => {
    const vm = foldRunSnapshot(research())
    expect(vm.phased).toBe(false)
    expect(vm.progressLabel).toBe('')       // the header falls back to "20 cycles"
    expect(vm.phaseTotal).toBe(0)           // → RunProgress hides the done/total bar
    expect(vm.phaseDone).toBe(0)
    expect(vm.progressLabel).not.toContain('stages')
  })

  it('still LISTS the plan — the objectives are real, only their done-state is unclaimed', () => {
    const vm = foldRunSnapshot(research())
    expect(vm.steps).toHaveLength(5)
    expect(vm.steps.map((s) => s.label)[4]).toBe('Write the report')
    // Not 'todo': that would assert "tracked, and not yet done" about a finished run's work.
    expect(vm.steps.every((s) => s.state === 'untracked')).toBe(true)
    expect(vm.steps.some((s) => s.state === 'todo')).toBe(false)
  })

  it('is decided by the kind DECLARATION, not by the kind name or by an empty map', () => {
    // A tracked kind mid-run legitimately has an empty phase_status → it keeps its fraction,
    // so "empty map ⇒ untracked" would have been the wrong derivation.
    const fresh = foldRunSnapshot(phased({ phase_status: {}, total_cycles: 0 }))
    expect(fresh.phased).toBe(true)
    expect(fresh.progressLabel).toBe('0/3 stages')
    // And a kind name the FE has never heard of gets no fraction rather than a guess.
    expect(foldRunSnapshot(research({ kind: 'brand-new-kind' })).progressLabel).toBe('')
    // Absent (not merely false) is also untracked — no snapshot gets a fraction invented.
    expect(foldRunSnapshot(research({ phase_tracked: undefined })).phased).toBe(false)
  })

  it('a research plan keeps its activePhase index (that is a plan index, not a done-claim)', () => {
    expect(foldRunSnapshot(research()).activePhase).toBe(foldRunSnapshot(research({ phase_tracked: true })).activePhase)
  })
})

describe('foldRunSnapshot — goal kind', () => {
  it('shows the goal-type label (not stage progress) + lists sub-goals as todo', () => {
    const vm = foldRunSnapshot(goal())
    expect(vm.phased).toBe(false)
    expect(vm.progressLabel).toBe('Open-ended')
    expect(vm.phaseTotal).toBe(0)
    expect(vm.steps.map((s) => s.label)).toEqual(['find sources', 'synthesize'])
    // 'untracked', not 'todo' — a goal advances by cycles, so no sub-goal has a done-state
    // to report. The render reads this state to pick a neutral dot over a checkbox.
    expect(vm.steps.every((s) => s.state === 'untracked')).toBe(true)
  })

  it('maps each goal_type to its label; unknown → empty', () => {
    expect(foldRunSnapshot(goal({ kind_config: { goal_type: 'verifiable' } })).progressLabel).toBe('Verifiable')
    expect(foldRunSnapshot(goal({ kind_config: { goal_type: 'monitor' } })).progressLabel).toBe('Monitoring')
    expect(foldRunSnapshot(goal({ kind_config: { goal_type: 'weird' } })).progressLabel).toBe('')
  })
})

describe('foldRunSnapshot — parked + scores', () => {
  it.each(['blocked', 'needs_input', 'stagnant', 'failed', 'stopped', 'ended_early'])(
    'status %s is parked', (status) => {
      expect(foldRunSnapshot(phased({ status })).parked).toBe(true)
    })
  it('a running status is not parked', () => {
    expect(foldRunSnapshot(phased({ status: 'running' })).parked).toBe(false)
  })
  it('reads best/last score from kind_config, marginals capped at 16', () => {
    const vm = foldRunSnapshot(goal({
      kind_config: { goal_type: 'open_ended', best_score: 0.9, last_score: 0.7 },
      marginal_scores: Array.from({ length: 20 }, (_, i) => i),
    }))
    expect(vm.bestScore).toBe(0.9)
    expect(vm.lastScore).toBe(0.7)
    expect(vm.marginals).toHaveLength(16)
    expect(vm.marginals[0]).toBe(4) // last 16 of 0..19
  })
})

describe('foldReducer — transient lifecycle flags', () => {
  it('gate_check ok:false records a failure; ok:true clears it', () => {
    let f = emptyRunFlags()
    f = foldReducer(f, 'gate_check', { ok: false, label: 'tests', command: 'npm test', output: 'boom' })
    expect(f.gate).toEqual({ label: 'tests', command: 'npm test', output: 'boom' })
    f = foldReducer(f, 'gate_check', { ok: true })
    expect(f.gate).toBeNull() // re-ran + passed clears the banner even with no stage_advance
  })

  it('stage_stalled records the stall; a forward event clears it', () => {
    let f = foldReducer(emptyRunFlags(), 'stage_stalled', { stage: 'build', title: 'Build', findings: 2 })
    expect(f.stall).toEqual({ stage: 'build', title: 'Build', findings: 2 })
    f = foldReducer(f, 'new_finding')
    expect(f.stall).toBeNull()
  })

  it('blocked KEEPS the stall (the stall is the reason for the block)', () => {
    let f = foldReducer(emptyRunFlags(), 'stage_stalled', { stage: 'build', title: 'Build', findings: 1 })
    f = foldReducer(f, 'blocked')
    expect(f.stall).not.toBeNull() // the block explanation survives
    expect(f.stall?.stage).toBe('build')
  })

  it('judge_error degrades; cycle_verdict clears + also clears stall/gate', () => {
    let f = foldReducer(emptyRunFlags(), 'judge_error')
    expect(f.judgeDegraded).toBe(true)
    f = foldReducer(f, 'stage_stalled', { stage: 'x', title: 'X', findings: 0 })
    f = foldReducer(f, 'cycle_verdict')
    expect(f.judgeDegraded).toBe(false)
    expect(f.stall).toBeNull()
  })

  it('deleted marks the run gone', () => {
    expect(foldReducer(emptyRunFlags(), 'deleted').deleted).toBe(true)
  })

  it('is pure — never mutates the input flags', () => {
    const f0 = emptyRunFlags()
    const f1 = foldReducer(f0, 'judge_error')
    expect(f0.judgeDegraded).toBe(false) // original untouched
    expect(f1).not.toBe(f0)
  })
})

describe('foldRun — merge snapshot + flags', () => {
  it('merges transient flags onto the snapshot derivation', () => {
    const flags = foldReducer(emptyRunFlags(), 'gate_check', { ok: false, label: 'lint', command: 'x', output: 'y' })
    const vm = foldRun(phased(), flags)
    expect(vm.gate?.label).toBe('lint')
    expect(vm.phaseDone).toBe(1) // snapshot fields still present
    expect(vm.judgeDegraded).toBe(false)
  })

  it('defaults to empty flags when none supplied', () => {
    const vm = foldRun(goal())
    expect(vm.gate).toBeNull()
    expect(vm.stall).toBeNull()
    expect(vm.judgeDegraded).toBe(false)
  })
})
