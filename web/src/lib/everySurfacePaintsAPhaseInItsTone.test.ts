import { describe, expect, it } from 'vitest'
import {
  RUN_STATUS_PHASES, WORLD_PHASE, WORK_PHASE_LOOKS, phaseAccent, phaseColor, phaseText, phaseToken,
  type WorkPhase,
} from './workPhase'
import { LOOP_STATUS_PHASES, loopStatusLook } from './loopStatus'
import { runLook } from '../pages/workflows/workflowMeta'
import { ITEM_KINDS } from '../pages/inbox/inboxMeta'
import { kindMeta } from '../pages/notifications/notificationMeta'
import { STATE_VISUAL } from '../pages/dashboard/world/worldScene'
import { NEEDS_YOU_DOT } from '../pages/projects/ProjectsSection'
import { MIC_TONE } from '../ui/MicCaptureChip'
import type { AgentActivityState } from './useAgentActivity'

// ── One tone per phase, on every surface that paints one (#3833) ───────────────────────────────
//
// Agent World, loops, workflow runs, the Inbox, notifications, the Projects board and the
// microphone chip each paint what an agent is doing. Each paints an item in its phase's tone, read
// from `workPhase.ts`: a loop or a run by the phase the gateway says its status is
// (`work_phase.py`'s `LOOP_STATUS_PHASES` and `RUN_STATUS_PHASES`, pinned equal by
// `tests/test_work_phase_vocabulary.py`), a world state by the phase it stands for, and a
// needs-you row by whether it waits on her yes (approval, warn) or her answer (input, info). No
// surface keeps a tone of its own for a phase.

/** Every row of `paint` that is not in its phase's tone, as `<row>: <tone> (its phase's: <tone>)`. */
function offTone<K extends string>(paint: Readonly<Record<K, string>>, phaseOf: (key: K) => WorkPhase, toneOf: (p: WorkPhase) => string): string[] {
  return (Object.keys(paint) as K[]).filter((k) => paint[k] !== toneOf(phaseOf(k))).map((k) => `${k}: ${paint[k]} (its phase's: ${toneOf(phaseOf(k))})`)
}

describe('a loop is painted in its phase\'s tone', () => {
  it('every loop status the gateway names has its phase', () => {
    expect(Object.keys(LOOP_STATUS_PHASES).length).toBeGreaterThanOrEqual(14)
  })

  it('each status\'s accent is its phase\'s', () => {
    const paint = Object.fromEntries(Object.keys(LOOP_STATUS_PHASES).map((s) => [s, loopStatusLook(s).accent]))
    expect(offTone(paint, (s) => LOOP_STATUS_PHASES[s], phaseAccent)).toEqual([])
  })

  it('a status a surface painted in a tone of its own is caught (positive control)', () => {
    const planted = { review: 'var(--color-info)', ready: phaseAccent('queued') }
    expect(offTone(planted, (s) => LOOP_STATUS_PHASES[s], phaseAccent)).toEqual([`review: var(--color-info) (its phase's: ${phaseAccent('approval')})`])
  })
})

describe('a workflow run is painted in its phase\'s tone', () => {
  it('each run status\'s look is its phase\'s, and a held run reads as queued', () => {
    const paint = Object.fromEntries(Object.keys(RUN_STATUS_PHASES).map((s) => [s, runLook(s).tone]))
    expect(offTone(paint, (s) => RUN_STATUS_PHASES[s], phaseText)).toEqual([])
    expect(runLook('running', 'Held: incident mode is on').tone).toBe(phaseText('queued'))
    // A run stopped at its budget ended early, as a loop that did: an error, said in words.
    expect(runLook('escalated', null, true).tone).toBe(phaseText(LOOP_STATUS_PHASES.ended_early))
    expect(runLook('escalated', null, true).label).toBe('Stopped at its budget')
  })
})

describe('the ambient world paints each state in its phase\'s tone', () => {
  it('each world state is the world of the phase it stands for', () => {
    for (const [state, phase] of Object.entries(WORLD_PHASE)) expect(WORK_PHASE_LOOKS[phase].world, state).toBe(state)
  })

  it('each world state\'s tone is its phase\'s: working is alive, not ok', () => {
    const paint = Object.fromEntries(Object.entries(STATE_VISUAL).map(([s, v]) => [s, v.tone])) as Record<AgentActivityState, string>
    expect(offTone(paint, (s) => WORLD_PHASE[s], phaseToken)).toEqual([])
    expect(STATE_VISUAL.working.tone).toBe('--color-primary')
  })
})

describe('a needs-you row is painted by what it waits on', () => {
  it('the Inbox: a request for her yes is approval, a question for her answer is input', () => {
    const tone = (key: string) => ITEM_KINDS.find((k) => k.key === key)!.tone
    expect(tone('agent_request')).toBe(phaseColor('approval'))
    expect(tone('needs_input')).toBe(phaseColor('input'))
  })

  it('notifications: an agent\'s request, a loop running, finishing or failing, a job failing', () => {
    expect(kindMeta('agent_request').tone).toBe(phaseColor('approval'))
    expect(kindMeta('loop').tone).toBe(phaseColor('thinking'))
    for (const k of ['complete', 'loop_complete']) expect(kindMeta(k).tone, k).toBe(phaseColor('done'))
    for (const k of ['loop_failed', 'cron_failed']) expect(kindMeta(k).tone, k).toBe(phaseColor('error'))
  })

  it('the Projects board: its needs-you group waits on her answer', () => {
    expect(NEEDS_YOU_DOT).toBe(phaseColor('input'))
  })

  it('the microphone chip is listening\'s tone, the system\'s own warn', () => {
    expect(MIC_TONE).toBe(phaseToken('listening'))
    expect(MIC_TONE).toBe('--color-warn')
  })
})
