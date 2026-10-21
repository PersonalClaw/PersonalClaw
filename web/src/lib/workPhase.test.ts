// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import {
  SYSTEM_STATE_FAMILIES, WORK_PHASES, WORK_PHASE_LOOKS, idleRunState, isStepPhase, livePhaseOf, phaseFamily,
  phaseFeedLine, phaseLabel, phaseWorld, workPhaseOf, type WorkPhase,
} from './workPhase'
import { filesUnder, readSource } from '../test/sourceTree'

// ── ONE phase vocabulary, every surface ──────────────────────────────────────────────────────────
//
// What an agent is doing reaches the web as one phase of a closed set (`work_phase` frames, a
// chat's `run_state`). `workPhase.ts` is the one table of what each phase looks like: its tone, which
// of the four shared families it reads as, the copy row its line is written from, the state an
// ambient world shows it as, and the word the chat says for it. `tests/test_work_phase_vocabulary.py`
// pins this table to the gateway's in both directions. This file holds the web half: every phase is
// mapped on every surface, the families are the settled ones, and no surface keeps a phase table of
// its own — the way "working" came to be green in Agent World and coral in the chat.

const FAMILIES = ['quiet', 'active', 'waiting', 'blocked']
const WORLDS = ['working', 'blocked', 'needs_input', 'waiting_approval', 'idle', 'error']

/** The families the owner settled. */
const SETTLED: Record<string, string> = {
  queued: 'active', thinking: 'active', searching: 'active', tool: 'active', planning: 'active',
  delegating: 'active', streaming: 'active', listening: 'active', retrying: 'active', waiting_model: 'active',
  approval: 'waiting', input: 'waiting',
  error: 'blocked',
  done: 'quiet', idle: 'quiet',
}

/** A table keyed by phase that gives it a tone or a family: what only this module may hold. Read off
 *  the phases no other vocabulary uses, so a loop's `planning` label or a job's `queued` state is not
 *  taken for one. */
const PHASE_TABLE_ROW = /\b(?:queued|thinking|searching|delegating|streaming|waiting_model|retrying|listening)\s*:\s*(?:\{\s*(?:tone|family)\b|'(?:primary|ok|warn|info|danger|neutral|active|quiet|waiting|blocked)')/

const SRC = join(process.cwd(), 'src')
const REGISTRY = join(SRC, 'lib', 'workPhase.ts')

describe('every phase has its look on every surface', () => {
  it('lists the fifteen phases the gateway sends', () => {
    expect([...WORK_PHASES].sort()).toEqual(Object.keys(SETTLED).sort())
  })

  it.each([...WORK_PHASES])('%s has a tone, one family, a line key, a world state and a chat label', (phase) => {
    const look = WORK_PHASE_LOOKS[phase]
    expect(look.tone).toMatch(/^(primary|ok|warn|info|danger|neutral)$/)
    expect(FAMILIES).toContain(look.family)
    expect(look.lineKey).toMatch(/^[a-z_]+$/)
    expect(WORLDS).toContain(look.world)
    expect(look.label.trim()).not.toBe('')
    expect([phaseFamily(phase), phaseWorld(phase), phaseLabel(phase)])
      .toEqual([look.family, look.world, look.label])
  })

  it('reads each phase as the family the owner settled', () => {
    const families = Object.fromEntries(WORK_PHASES.map((p) => [p, phaseFamily(p)]))
    expect(families).toEqual(SETTLED)
  })

  it('shows live work held up as blocked in a world, apart from working (#3889)', () => {
    const worlds = Object.fromEntries(WORK_PHASES.map((p) => [p, phaseWorld(p)]))
    expect(Object.keys(worlds).filter((p) => worlds[p] === 'blocked').sort()).toEqual(['queued', 'retrying', 'waiting_model'])
  })

  it('reads the gateway\'s own states as their settled families', () => {
    expect(SYSTEM_STATE_FAMILIES).toEqual({ idle: 'quiet', restarting: 'active', offline: 'blocked' })
  })

  it('keeps coral for live work, and the microphone in the system\'s own warn', () => {
    const tone = (phase: WorkPhase) => WORK_PHASE_LOOKS[phase].tone
    expect(tone('queued')).toBe('primary')
    expect(tone('thinking')).toBe('primary')
    expect(tone('listening')).toBe('warn')
    expect([tone('approval'), tone('input'), tone('done'), tone('error')])
      .toEqual(['warn', 'info', 'ok', 'danger'])
  })

  it('reads a wire value as a phase only when it is one', () => {
    expect(workPhaseOf('streaming')).toBe('streaming')
    expect(workPhaseOf('Thinking…')).toBeNull()
    expect(workPhaseOf(undefined)).toBeNull()
  })
})

describe('a turn\'s own view reads its frames', () => {
  const turn = { message_id: '2026-01-01T09:00:00+00:00', started_at: '2026-01-01T09:00:00+00:00' }

  it('reads what a running turn is doing, and nothing once it has ended', () => {
    expect(livePhaseOf({ phase: 'searching', line: 'Searching the web', turn })).toEqual({ phase: 'searching', line: 'Searching the web' })
    // Its last frame says how it ended, and names no turn: nothing is running.
    expect(livePhaseOf({ phase: 'done', line: 'Done in 4 seconds', turn: null })).toBeNull()
    // A phase the gateway does not send is no phase at all.
    expect(livePhaseOf({ phase: 'Thinking…', line: 'Thinking…', turn })).toBeNull()
    expect(livePhaseOf(undefined)).toBeNull()
  })

  it('knows the phases a step\'s own card says', () => {
    expect(WORK_PHASES.filter(isStepPhase)).toEqual(['searching', 'tool'])
  })

  it('gives a feed of steps a phase\'s line once per change, and none for a step', () => {
    const thinking = { phase: 'thinking', line: 'Thinking', turn }
    const first = phaseFeedLine(thinking, '')
    expect(first.line).toBe('Thinking')
    // Said again every fifteen seconds while the model is silent: nothing new.
    expect(phaseFeedLine(thinking, first.said)).toEqual({ said: first.said, line: null })
    // A step's own card says it; the feed still knows it was said, so the next phase is news.
    const searching = phaseFeedLine({ phase: 'searching', line: 'Searching the web', turn }, first.said)
    expect(searching.line).toBeNull()
    const approval = { phase: 'approval', line: 'Needs your OK: web_search', turn }
    expect(phaseFeedLine(approval, searching.said).line).toBe('Needs your OK: web_search')
    // A feed that shows a phase its own way skips that one too.
    expect(phaseFeedLine({ phase: 'streaming', line: 'Writing', turn }, '', (p) => p === 'streaming').line).toBeNull()
    // The turn's last frame adds nothing, and the next turn's first phase is news again.
    const ended = phaseFeedLine({ phase: 'done', line: 'Done in 4 seconds', turn: null }, first.said)
    expect(ended).toEqual({ said: '', line: null })
    expect(phaseFeedLine(thinking, ended.said).line).toBe('Thinking')
  })

  it('adds no line for the thinking the gateway says after each step', () => {
    // The gateway says thinking (planning, in plan mode) after every result: a line for it would put
    // a row after every step's card, and a feed would hold half as many steps.
    const between = [{ phase: 'thinking', line: 'Thinking', turn }, { phase: 'planning', line: 'Planning', turn }]
    const thought = phaseFeedLine(between[0], '').said
    for (const step of [{ phase: 'tool', line: 'shell', turn }, { phase: 'searching', line: 'Searching the web', turn }]) {
      const card = phaseFeedLine(step, thought)
      for (const after of between) {
        const said = phaseFeedLine(after, card.said)
        expect(said.line, `${after.phase} after ${step.phase}`).toBeNull()
        // It is what the feed last heard, so the gateway saying it again is no news either.
        expect(phaseFeedLine(after, said.said)).toEqual({ said: said.said, line: null })
      }
    }
    // Thinking after anything else is still news: the helpers it handed off to are done.
    const tool = phaseFeedLine({ phase: 'tool', line: 'shell', turn }, thought).said
    const handed = phaseFeedLine({ phase: 'delegating', line: 'Handing off to 1 helper', turn }, tool)
    expect(handed.line).toBe('Handing off to 1 helper')
    expect(phaseFeedLine(between[0], handed.said).line).toBe('Thinking')
  })

  it('holds a chat it has just made as one that has never run', () => {
    const idle = idleRunState('chat-1')
    expect([idle.work, idle.session, idle.phase, idle.turn, idle.seq]).toEqual(['chat:chat-1', 'chat-1', 'idle', null, 0])
  })
})

describe('no surface keeps a phase table of its own', () => {
  it('the row pattern matches the one registry (positive control)', () => {
    expect(PHASE_TABLE_ROW.test(readSource(REGISTRY))).toBe(true)
    expect(PHASE_TABLE_ROW.test(`const MINE = {\n  streaming: 'ok',\n}`)).toBe(true)
    expect(PHASE_TABLE_ROW.test(`planning: { label: 'Planning', accent: 'var(--color-primary)' }`)).toBe(false)
  })

  it('finds no phase-to-tone or phase-to-family table outside workPhase.ts', () => {
    const others = filesUnder(SRC, (name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))
      .filter((path) => path !== REGISTRY && PHASE_TABLE_ROW.test(readSource(path)))
      .map((path) => path.slice(SRC.length + 1))
    expect(others).toEqual([])
  })
})
