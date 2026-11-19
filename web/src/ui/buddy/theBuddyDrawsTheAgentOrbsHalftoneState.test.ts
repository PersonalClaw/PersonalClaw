import { describe, expect, it } from 'vitest'
import { WORK_PHASES } from '../../lib/workPhase'
import { ORB_SCENE_OF, ORB_SCENES, SCENE_MOVES } from '../../lib/orbState'
import { buddyMoves, buddySceneOf, type BuddyState } from './buddyHalftone'
import { TRAY_STATES, stillOf } from '../../buddyRig/poseSet'

// ── The buddy draws the agent orb's halftone state for what it shows ───────────────────────────
//
// The buddy is the agent orb on the desktop (`docs/design/buddy/character.md`): every phase is the
// agent orb's own state, but for the buddy's listening, which is a push-to-talk capture and so the
// recording dot, and the two system states, a restart (retrying: it comes back) and the gateway
// away (offline). The tray's six stills are the forms that read at 18 px (`docs/design/agent-orb.md`,
// "Rows and the tray").

describe('every state the buddy shows is one of the agent orb’s twenty', () => {
  it('a phase is the agent orb’s own state, but listening, which is the recording dot', () => {
    for (const phase of WORK_PHASES) {
      expect(buddySceneOf(phase), phase).toBe(phase === 'listening' ? 'recording' : ORB_SCENE_OF[phase])
    }
  })

  it('a restart is retrying, never offline, and the gateway away is offline', () => {
    expect(buddySceneOf('restarting')).toBe('retrying')
    expect(buddySceneOf('offline')).toBe('offline')
  })

  it('it moves exactly when its state does, and its still is that state, the whole orb at rest', () => {
    const states: BuddyState[] = [...WORK_PHASES, 'restarting', 'offline']
    for (const state of states) {
      expect(ORB_SCENES).toContain(buddySceneOf(state))
      expect(buddyMoves(state), state).toBe(SCENE_MOVES[buddySceneOf(state)])
      expect(stillOf(state), state).toEqual({ scene: buddySceneOf(state), moving: false, lift: 0, sway: 0, swell: 1, dim: 1 })
    }
  })
})

describe('the tray’s six stills', () => {
  it('idle rests, working runs a command, needs-you needs your OK, error is the error mark, listening the recording dot, offline the offline mark', () => {
    const scenes = Object.fromEntries(Object.entries(TRAY_STATES).map(([still, state]) => [still, buddySceneOf(state)]))
    expect(scenes).toEqual({ idle: 'ready', working: 'running', 'needs-you': 'approval', error: 'error', listening: 'recording', offline: 'offline' })
  })
})
