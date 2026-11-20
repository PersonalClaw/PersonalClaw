import { describe, expect, it } from 'vitest'
import { WORK_PHASES, type WorkPhase } from './workPhase'
import {
  MARK_SCENE_OF, ORB_SCENES, ORB_SCENE_OF, ORB_STAGES, ORB_STAGE_LABELS, ORB_STATE_OF_AGENT_ROW, ORB_STATE_OF_PIP, SCENE_MOVES,
  TOOL_SCENE_OF_ACT, markSceneOf, markSceneOfPhase, orbSceneOf, type OrbScene,
} from './orbState'
import { voiceOrbStage } from '../ui/composer/voiceOrb'

// ── The halftone orb's twenty states (`docs/design/agent-orb.md`, "Phase to state") ─────────────
//
// Every phase of the contract is drawn as one of the twenty states, and the few things that drive
// the orb without being a phase (a push-to-talk capture, a piece of voice being turned into text, a
// reply read aloud, the gateway out of reach) are a closed set of typed stages. Nothing else
// reaches a state: no status line, no raw string.

describe('every phase is one of the twenty states', () => {
  it('the table covers the whole contract, and every state it names is one of the twenty', () => {
    expect(Object.keys(ORB_SCENE_OF).sort()).toEqual([...WORK_PHASES].sort())
    for (const phase of WORK_PHASES) expect(ORB_SCENES).toContain(ORB_SCENE_OF[phase])
    expect(ORB_SCENES).toHaveLength(20)
  })

  it.each<[WorkPhase, OrbScene]>([
    ['idle', 'ready'],
    ['listening', 'listening'],
    ['queued', 'queued'],
    // held behind other calls on a local model: it waits behind another's turn, as a queued turn does
    ['waiting_model', 'queued'],
    ['thinking', 'thinking'],
    ['planning', 'planning'],
    ['searching', 'searching'],
    // any other tool, whose call says nothing of what it does, runs as a command does
    ['tool', 'running'],
    ['delegating', 'delegating'],
    ['streaming', 'answering'],
    ['retrying', 'retrying'],
    ['approval', 'approval'],
    ['input', 'question'],
    ['done', 'done'],
    ['error', 'error'],
  ])('%s is %s', (phase, scene) => {
    expect(ORB_SCENE_OF[phase]).toBe(scene)
    expect(orbSceneOf(phase)).toBe(scene)
  })

  it('every state is driven: by a phase, by what a tool step’s call does, or by a stage', () => {
    const byPhase = new Set<OrbScene>([...WORK_PHASES.map((p) => ORB_SCENE_OF[p]), ...ORB_STAGES])
    expect(ORB_SCENES.filter((s) => !byPhase.has(s)), 'reading and editing are a tool step’s alone').toEqual(['reading', 'editing'])
    const driven = new Set<OrbScene>([...byPhase, ...Object.values(TOOL_SCENE_OF_ACT)])
    expect(ORB_SCENES.filter((s) => !driven.has(s))).toEqual([])
  })

  it('the table cannot be written to', () => {
    expect(Object.isFrozen(ORB_SCENE_OF)).toBe(true)
  })
})

describe('a stage wins over the phase while a page names it', () => {
  it('each stage is its own state, with its own words', () => {
    expect([...ORB_STAGES]).toEqual(['recording', 'transcribing', 'speaking', 'offline'])
    for (const stage of ORB_STAGES) {
      expect(orbSceneOf('thinking', stage)).toBe(stage)
      expect(ORB_STAGE_LABELS[stage].length).toBeGreaterThan(0)
    }
    expect(orbSceneOf('thinking', null)).toBe('thinking')
  })

  it('voice mode is listening while it hears the owner, speaking while it reads a reply, transcribing while it turns the last piece into text', () => {
    expect(voiceOrbStage({ heard: true, transcribing: true, speaking: true })).toBeNull()
    expect(voiceOrbStage({ heard: false, transcribing: false, speaking: true })).toBe('speaking')
    expect(voiceOrbStage({ heard: false, transcribing: true, speaking: true })).toBe('speaking')
    expect(voiceOrbStage({ heard: false, transcribing: true, speaking: false })).toBe('transcribing')
    expect(voiceOrbStage({ heard: false, transcribing: false, speaking: false })).toBeNull()
  })
})

describe('what moves', () => {
  it('rest, the two waits on the owner, done and the two stops hold still; every other state moves', () => {
    expect(ORB_SCENES.filter((s) => !SCENE_MOVES[s])).toEqual(['ready', 'approval', 'question', 'done', 'error', 'offline'])
  })
})

describe("a row's 12 px mark is one of the sphere's own forms", () => {
  const FORMS: OrbScene[] = ['ready', 'thinking', 'approval', 'question', 'done', 'error', 'offline']
  it('every orb state and badge a row can carry is a sphere form, never a scene', () => {
    for (const scene of Object.values(MARK_SCENE_OF)) expect(FORMS).toContain(scene)
    for (const look of [...Object.values(ORB_STATE_OF_PIP), ...Object.values(ORB_STATE_OF_AGENT_ROW)]) expect(FORMS).toContain(markSceneOf(look))
  })

  it('a running row is thinking, a waiting one needs your OK, a failed one is the error mark and an unread one done', () => {
    expect(markSceneOf(ORB_STATE_OF_PIP.running)).toBe('thinking')
    expect(markSceneOf(ORB_STATE_OF_PIP.waiting)).toBe('approval')
    expect(markSceneOf(ORB_STATE_OF_PIP.failed)).toBe('error')
    expect(markSceneOf(ORB_STATE_OF_PIP.unread)).toBe('done')
  })

  it('an orb drawn at 12 px takes the sphere form for its phase or stage', () => {
    for (const phase of WORK_PHASES) expect(FORMS, phase).toContain(markSceneOfPhase(phase))
    expect(markSceneOfPhase('idle')).toBe('ready')
    expect(markSceneOfPhase('listening'), 'a listening agent is alive').toBe('thinking')
    expect(markSceneOfPhase('tool')).toBe('thinking')
    expect(markSceneOfPhase('approval')).toBe('approval')
    expect(markSceneOfPhase('input')).toBe('question')
    expect(markSceneOfPhase('done')).toBe('done')
    expect(markSceneOfPhase('error')).toBe('error')
    expect(markSceneOfPhase('idle', 'offline')).toBe('offline')
    for (const stage of ['recording', 'transcribing', 'speaking'] as const) expect(markSceneOfPhase('idle', stage)).toBe('thinking')
  })

  it("Home's agents: working and blocked think, waiting needs your OK, failed and interrupted are errors, idle rests", () => {
    expect(markSceneOf(ORB_STATE_OF_AGENT_ROW.working)).toBe('thinking')
    expect(markSceneOf(ORB_STATE_OF_AGENT_ROW.blocked)).toBe('thinking')
    expect(markSceneOf(ORB_STATE_OF_AGENT_ROW.waiting)).toBe('approval')
    expect(markSceneOf(ORB_STATE_OF_AGENT_ROW.failed)).toBe('error')
    expect(markSceneOf(ORB_STATE_OF_AGENT_ROW.interrupted)).toBe('error')
    expect(markSceneOf(ORB_STATE_OF_AGENT_ROW.idle)).toBe('ready')
  })
})
