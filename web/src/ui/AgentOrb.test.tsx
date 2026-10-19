import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AgentOrb, ORB_LOOKS, orbLookOf } from './AgentOrb'
import { WORK_PHASES, WORK_PHASE_LOOKS, phaseFamily, phaseLabel, type WorkPhase } from '../lib/workPhase'

// ── The agent orb: one look per phase, from the phase contract and the orb tokens ────────────────
//
// The chat's thinking indicator guessed what a turn was doing from the words of its status line and
// rotated seven stock phrases under it. The orb is handed a phase of the contract (`lib/workPhase`)
// and nothing else: each phase is one drawing in one of the four families, the active family moves
// and the rest are still frames, and a waiting or failed turn carries a badge whose shape, not its
// colour, says which (`docs/design/agent-orb.md`, "Phase to state").

/** The drawings `docs/design/agent-orb.md` names for the active family. */
const ACTIVE_DRAWINGS: Partial<Record<WorkPhase, string>> = {
  queued: 'breathing', thinking: 'breathing', searching: 'searching', tool: 'working', planning: 'solving',
  delegating: 'connecting', streaming: 'composing', listening: 'listening', retrying: 'working', waiting_model: 'breathing',
}
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

describe('every phase of the contract maps to exactly one orb look', () => {
  it('the table covers the whole contract and nothing else', () => {
    expect(Object.keys(ORB_LOOKS).sort()).toEqual([...WORK_PHASES].sort())
  })

  it.each([...WORK_PHASES])('%s: one drawing, moving only in the active family, alive only while an agent is', (phase) => {
    const look = ORB_LOOKS[phase]
    const family = phaseFamily(phase)
    expect(look.moves, 'it moves exactly when its family is active').toBe(family === 'active')
    expect(look.ink, 'the dots are alive while an agent works or waits on her, at rest otherwise').toBe(family === 'active' || family === 'waiting' ? 'alive' : 'rest')
    if (family === 'active') expect(look.drawing).toBe(ACTIVE_DRAWINGS[phase])
    expect(look.badge ?? null, 'a badge only where the owner is needed or the turn failed').toBe(phase in BADGED ? phase : null)
  })

  it('no orb look is reachable from a raw string: only a phase of the contract names one', () => {
    for (const raw of ['Thinking…', 'Working', 'working', 'composing', 'breathing', 'Needs your OK', '', null, undefined, 3]) {
      expect(orbLookOf(raw), String(raw)).toBeNull()
    }
    for (const phase of WORK_PHASES) expect(orbLookOf(phase)).toBe(ORB_LOOKS[phase])
  })
})

describe('what it draws for each phase', () => {
  it.each([...WORK_PHASES])('%s: an image named for the phase, in its family', (phase) => {
    const { container } = render(<AgentOrb phase={phase} />)
    const orb = orbOf(container)
    expect(orb.dataset.orbPhase).toBe(phase)
    expect(orb.dataset.orbFamily).toBe(WORK_PHASE_LOOKS[phase].family)
    expect(orb.dataset.orbDrawing).toBe(ORB_LOOKS[phase].drawing)
    // One image, named for the phase, so a screen reader that reaches it hears the exact phase.
    expect(screen.getAllByRole('img')).toHaveLength(1)
    expect(screen.getByRole('img')).toHaveAccessibleName(phaseLabel(phase))
  })

  it.each(Object.entries(BADGED))('%s carries the "%s" badge in its tone, as decoration', (phase, glyph) => {
    const { container } = render(<AgentOrb phase={phase as WorkPhase} />)
    const badge = container.querySelector('[data-orb-badge]') as HTMLElement
    expect(badge.dataset.orbBadge).toBe(phase)
    expect(badge.textContent).toBe(glyph)
    expect(badge.getAttribute('aria-hidden')).toBe('true')
    // The phase's own tone from the contract: warn for her OK, info for her answer, danger on error.
    expect(badge.style.color).toBe(`var(--color-${WORK_PHASE_LOOKS[phase as WorkPhase].tone})`)
    expect(badge.style.borderColor).toBe(`var(--color-${WORK_PHASE_LOOKS[phase as WorkPhase].tone})`)
  })

  it('every other phase has no badge', () => {
    for (const phase of WORK_PHASES.filter((p) => !(p in BADGED))) {
      const { container, unmount } = render(<AgentOrb phase={phase} />)
      expect(container.querySelector('[data-orb-badge]'), phase).toBeNull()
      unmount()
    }
  })

  it('is drawn at 20 px by default and at 64 px when asked, each its own tuned design', () => {
    const small = render(<AgentOrb phase="thinking" />)
    expect(orbOf(small.container).dataset.orbSize).toBe('20')
    expect((small.container.querySelector('canvas') as HTMLCanvasElement).style.width).toBe('20px')
    small.unmount()
    const large = render(<AgentOrb phase="thinking" size={64} />)
    expect(orbOf(large.container).dataset.orbSize).toBe('64')
    expect((large.container.querySelector('canvas') as HTMLCanvasElement).style.width).toBe('64px')
  })
})

describe('its colour is the orb tokens, never a literal', () => {
  it('alive phases take --color-orb-alive and the rest --color-orb-rest, as the scheme resolves them', () => {
    document.documentElement.style.setProperty('--color-orb-alive', '#3366cc')
    document.documentElement.style.setProperty('--color-orb-rest', '#777777')
    for (const phase of WORK_PHASES) {
      const { container, unmount } = render(<AgentOrb phase={phase} />)
      const alive = ORB_LOOKS[phase].ink === 'alive'
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

  it('with every author colour removed, each phase still names itself and its badge keeps its shape and ring', () => {
    MATCHING = ['forced-colors: active']
    for (const phase of WORK_PHASES) {
      const { container, unmount } = render(<AgentOrb phase={phase} />)
      for (const el of container.querySelectorAll<HTMLElement>('*')) {
        el.style.removeProperty('background')
        el.style.removeProperty('border-color')
        el.style.removeProperty('color')
      }
      expect(screen.getByRole('img'), phase).toHaveAccessibleName(phaseLabel(phase))
      const badge = container.querySelector('[data-orb-badge]') as HTMLElement | null
      if (phase in BADGED) {
        expect(badge!.textContent, phase).toBe(BADGED[phase])
        // A border survives forced colours in the system's text colour; a fill alone would vanish.
        expect(badge!.className, phase).toMatch(/\bborder\b/)
      } else {
        expect(badge, phase).toBeNull()
      }
      unmount()
    }
  })
})
