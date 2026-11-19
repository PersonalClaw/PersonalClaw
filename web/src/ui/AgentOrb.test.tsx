import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AgentOrb } from './AgentOrb'
import { ORB_SCENE_OF, ORB_STATE_OF, SCENE_MOVES } from '../lib/orbState'
import { WORK_PHASES, WORK_PHASE_LOOKS, phaseFamily, phaseLabel, type WorkPhase } from '../lib/workPhase'

// ── The agent orb: each phase drawn as its state of the halftone orb, in the orb tokens (#6117) ──
//
// The orb is handed a phase of the contract (`lib/workPhase`), and a typed stage where a page has
// one, and draws it as one of the halftone orb's twenty states through the one mapping
// (`lib/orbState.ts`): rest, the waits on the owner, done and the stops hold still and every other
// state moves, and a waiting or failed turn carries a mark on its plate whose shape, not its colour,
// says which (`docs/design/agent-orb.md`, "Phase to state"). The mapping's own cases, and that no raw
// string reaches it, are `lib/theOrbDrawsEachPhaseAsOneOfItsTwentyStates.test.ts`'s.
const BADGED: Partial<Record<WorkPhase, string>> = { approval: '!', input: '?', error: '×' }

/** What the platform says it wants, flippable per test: matching media queries by substring. */
let MATCHING: string[] = []
const ORIGINAL_MATCH_MEDIA = window.matchMedia
beforeEach(() => {
  MATCHING = []
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      get matches() { return MATCHING.some((q) => query.includes(q)) },
      media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
  })
})
afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
  for (const name of ['--color-orb-alive', '--color-orb-rest', '--color-on-surface']) document.documentElement.style.removeProperty(name)
})

const orbOf = (container: HTMLElement) => container.querySelector('[data-agent-orb]') as HTMLElement

describe('every phase of the contract is drawn as its one state', () => {
  it.each([...WORK_PHASES])('%s: its state, moving exactly when its state does, alive only while an agent is', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    const orb = orbOf(container)
    const look = ORB_STATE_OF[phase]
    const family = phaseFamily(phase)
    expect(orb.dataset.orbState).toBe(look.state)
    expect(orb.dataset.orbScene).toBe(ORB_SCENE_OF[phase])
    expect(orb.dataset.agentOrb, 'it moves exactly when its state does').toBe(SCENE_MOVES[ORB_SCENE_OF[phase]] ? 'animated' : 'still')
    expect(look.ink, 'alive while an agent works, listens or waits on the owner, at rest otherwise').toBe(family === 'active' || family === 'waiting' ? 'alive' : 'rest')
    expect(look.badge ?? null, 'a badge only where the owner is needed or the turn failed').toBe(phase in BADGED ? phase : null)
  })
})

describe('what it draws for each phase', () => {
  it.each([...WORK_PHASES])('%s: an image named for the phase, in its family', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    const orb = orbOf(container)
    expect(orb.dataset.orbPhase).toBe(phase)
    expect(orb.dataset.orbFamily).toBe(WORK_PHASE_LOOKS[phase].family)
    expect(orb.dataset.orbState).toBe(ORB_STATE_OF[phase].state)
    // One image, named for the phase, so a screen reader that reaches it hears the exact phase.
    expect(screen.getAllByRole('img')).toHaveLength(1)
    expect(screen.getByRole('img')).toHaveAccessibleName(phaseLabel(phase))
  })

  it.each(Object.entries(BADGED))('%s carries the "%s" mark on its plate, drawn in the orb’s own image', (phase, glyph) => {
    const { container } = render(<AgentOrb phase={phase as WorkPhase} />)
    const orb = orbOf(container)
    expect(orb.dataset.orbBadge).toBe(phase)
    expect(orb.dataset.orbGlyph).toBe(glyph)
    // The mark is part of the one image, which names the phase; no second element says it again.
    expect(screen.getAllByRole('img')).toHaveLength(1)
    expect(container.querySelectorAll('canvas')).toHaveLength(1)
  })

  it('every other phase has no badge', () => {
    for (const phase of WORK_PHASES.filter((p) => !(p in BADGED))) {
      const { container, unmount } = render(<AgentOrb phase={phase} />)
      expect(container.querySelector('[data-orb-badge]'), phase).toBeNull()
      unmount()
    }
  })

  it('is drawn at 20 px by default, at 64 px for an empty state and at 12 px as a list\'s still mark', () => {
    // Its box is its size; its canvas reaches past it on each side, where the burst mid-change and
    // the plate's shadow draw, and takes no pointer, so the box alone is what the layout holds.
    const small = render(<AgentOrb phase="thinking" />)
    expect(orbOf(small.container).dataset.orbSize).toBe('20')
    expect(orbOf(small.container).style.width).toBe('20px')
    expect((small.container.querySelector('canvas') as HTMLCanvasElement).style.width).toBe('28px')
    expect((small.container.querySelector('canvas') as HTMLCanvasElement).className).toMatch(/\bpointer-events-none\b/)
    small.unmount()
    const large = render(<AgentOrb phase="thinking" size={64} />)
    expect(orbOf(large.container).dataset.orbSize).toBe('64')
    expect(orbOf(large.container).style.width).toBe('64px')
    expect((large.container.querySelector('canvas') as HTMLCanvasElement).style.width).toBe('86px')
    large.unmount()
    // The list's mark never moves, whatever its state: shape and ink only.
    const mark = render(<AgentOrb phase="tool" size={12} />)
    expect(orbOf(mark.container).dataset.orbSize).toBe('12')
    expect(orbOf(mark.container).dataset.agentOrb).toBe('still')
  })
})

describe('its colour is the orb tokens, never a literal', () => {
  it('alive phases take --color-orb-alive and the rest --color-orb-rest, as the scheme resolves them', () => {
    document.documentElement.style.setProperty('--color-orb-alive', '#3366cc')
    document.documentElement.style.setProperty('--color-orb-rest', '#777777')
    for (const phase of WORK_PHASES) {
      const { container, unmount } = render(<AgentOrb phase={phase} />)
      const alive = ORB_STATE_OF[phase].ink === 'alive'
      expect(orbOf(container).dataset.orbInkToken, phase).toBe(alive ? '--color-orb-alive' : '--color-orb-rest')
      expect(orbOf(container).dataset.orbInk, phase).toBe(alive ? '#3366cc' : '#777777')
      unmount()
    }
  })

  it('a scheme change recolours an orb already on screen', async () => {
    document.documentElement.style.setProperty('--color-orb-alive', '#3366cc')
    const { container } = render(<AgentOrb phase="streaming" />)
    expect(orbOf(container).dataset.orbInk).toBe('#3366cc')
    document.documentElement.style.setProperty('--color-orb-alive', '#228844')
    await waitFor(() => expect(orbOf(container).dataset.orbInk).toBe('#228844'))
  })

  it('names no colour of its own in its source', () => {
    const src = readFileSync(join(process.cwd(), 'src/ui/AgentOrb.tsx'), 'utf8')
    expect(src).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(\s*\d/i)
    expect(src).not.toMatch(/coral/i)
  })
})

describe('high contrast', () => {
  it('under a request for more contrast, the dots take the words’ ink', () => {
    MATCHING = ['prefers-contrast: more']
    for (const phase of WORK_PHASES) {
      const { container, unmount } = render(<AgentOrb phase={phase} />)
      expect(orbOf(container).dataset.orbContrast, phase).toBe('more')
      expect(orbOf(container).dataset.orbInkToken, phase).toBe('--color-on-surface')
      unmount()
    }
  })

  it('in forced colours, the dots take the system’s text colour', () => {
    MATCHING = ['forced-colors: active']
    for (const phase of WORK_PHASES) {
      const { container, unmount } = render(<AgentOrb phase={phase} />)
      expect(orbOf(container).dataset.orbContrast, phase).toBe('forced')
      expect(orbOf(container).dataset.orbInkToken, phase).toBe('CanvasText')
      unmount()
    }
  })

  it('with every author colour removed, each phase still names itself and its mark keeps its shape', () => {
    MATCHING = ['forced-colors: active']
    for (const phase of WORK_PHASES) {
      const { container, unmount } = render(<AgentOrb phase={phase} />)
      for (const el of container.querySelectorAll<HTMLElement>('*')) {
        el.style.removeProperty('background')
        el.style.removeProperty('border-color')
        el.style.removeProperty('color')
      }
      expect(screen.getByRole('img'), phase).toHaveAccessibleName(phaseLabel(phase))
      // The mark is drawn with the dots, in the system's text colour, so its shape is what tells
      // the waits and the failure apart.
      const orb = orbOf(container)
      expect(orb.dataset.orbInkToken, phase).toBe('CanvasText')
      expect(orb.dataset.orbGlyph ?? null, phase).toBe(BADGED[phase] ?? null)
      unmount()
    }
  })
})
