import { describe, expect, it } from 'vitest'
import type { RunAct } from './api'
import { ORB_SCENE_OF, ORB_STAGES, TOOL_SCENE_OF_ACT, orbSceneOf } from './orbState'
import { RUN_ACTS, WORK_PHASES, runActOf } from './workPhase'

// ── A tool call is drawn as what it does ────────────────────────────────────────────────────────
//
// The orb drew every tool call as running a command: the gateway's `work_phase` frame said `tool`
// and nothing more, so a file being read, one being changed and a command being run looked alike,
// and two of the orb's twenty states, reading and editing, were drawn by nothing. The frame says
// now what the call does (`act`, `read`, `edit` or `run`, only while the turn is in `tool`), and the
// orb draws it: a call that reads is reading, one that edits is editing, one that runs is running a
// command, and one the gateway says nothing of is running, as every call was drawn before.

describe('what a tool call does', () => {
  it('is one of three words, the gateway’s own (`work_phase.Act`)', () => {
    expect([...RUN_ACTS]).toEqual(['read', 'edit', 'run'])
  })

  it('is read from the wire as one of them, or as nothing: never a guess at a nearby one', () => {
    for (const act of RUN_ACTS) expect(runActOf(act)).toBe(act)
    for (const stray of [null, undefined, '', 'Read', 'reading', 'delete', 'execute', 3, {}, ['read']]) {
      expect(runActOf(stray), String(stray)).toBeNull()
    }
  })
})

describe('the orb draws a tool call as what it does', () => {
  const drawn: [RunAct | null, string][] = [['read', 'reading'], ['edit', 'editing'], ['run', 'running'], [null, 'running']]

  it.each(drawn)('a call that says %s is drawn %s', (act, scene) => {
    expect(orbSceneOf('tool', null, act)).toBe(scene)
  })

  it('each act has its own state, and a call that says nothing is drawn as one that runs', () => {
    expect(TOOL_SCENE_OF_ACT).toEqual({ read: 'reading', edit: 'editing', run: 'running' })
    expect(orbSceneOf('tool')).toBe(ORB_SCENE_OF.tool)
    expect(ORB_SCENE_OF.tool).toBe('running')
  })

  it('an act draws nothing outside a tool step: every other phase is drawn as it was', () => {
    for (const phase of WORK_PHASES.filter((p) => p !== 'tool')) {
      for (const act of RUN_ACTS) expect(orbSceneOf(phase, null, act), `${phase} ${act}`).toBe(ORB_SCENE_OF[phase])
    }
  })

  it('a stage a page names still wins over the call', () => {
    for (const stage of ORB_STAGES) expect(orbSceneOf('tool', stage, 'read')).toBe(stage)
  })
})
