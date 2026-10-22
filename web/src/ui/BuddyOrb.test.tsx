import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, render } from '@testing-library/react'
import { BuddyOrb } from './BuddyOrb'
import { FAMILIES, FAMILY_STATES, wordsOf } from '../buddyRig/poseSet'
import { POSE_KEYS } from './buddy/useOrbPose'
import { transitionOf, type BuddyState } from './buddy/orbPoses'
import { WORK_PHASES } from '../lib/workPhase'

// ── The buddy orb (#3891) ───────────────────────────────────────────────────────────────────────
//
// Every family's pose, and the focused card's, carries its exact line beside the art, so the art
// never carries the state alone. Its dots and its badge are tokens, so a scheme change recolours
// it in the paint that change causes. Reduced motion swaps the moving pose for its still in place:
// the same orb, the same state and line, nothing started again.

const STATES: readonly BuddyState[] = [...WORK_PHASES, 'restarting', 'offline']

/** Every colour painted since the last `reset`. */
let fills: string[] = []
const getContext = HTMLCanvasElement.prototype.getContext
const ctx = new Proxy({} as Record<string, unknown>, {
  get: (target, key: string) => (key in target ? target[key] : () => {}),
  set: (target, key: string, value) => {
    if (key === 'fillStyle') fills.push(String(value))
    target[key] = value
    return true
  },
}) as unknown as CanvasRenderingContext2D

/** The platform's media queries, as a test sets them, with their change events. */
const matching = new Set<string>()
const listeners = new Set<() => void>()
const originalMatchMedia = window.matchMedia
function setMedia(query: string, on: boolean): void {
  if (on) matching.add(query)
  else matching.delete(query)
  act(() => { for (const cb of [...listeners]) cb() })
}

beforeEach(() => {
  fills = []
  matching.clear()
  listeners.clear()
  HTMLCanvasElement.prototype.getContext = (() => ctx) as never
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      get matches() { return [...matching].some((q) => query.includes(q)) },
      media: query, onchange: null,
      addEventListener: (_: string, cb: () => void) => listeners.add(cb),
      removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  })
})

afterEach(() => {
  HTMLCanvasElement.prototype.getContext = getContext
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: originalMatchMedia })
  document.documentElement.style.removeProperty('--color-orb-alive')
  document.documentElement.style.removeProperty('--color-orb-rest')
})

const orbOf = (container: HTMLElement) => container.querySelector('[data-buddy-orb]') as HTMLElement

/** The line beside the art: the text node that follows the orb's canvas in the same row, or null
 *  when there is none to read. */
function lineBesideArt(container: HTMLElement): string | null {
  const art = container.querySelector('[data-buddy-orb] canvas[aria-hidden="true"]')
  const line = art?.parentElement?.nextElementSibling
  if (!art || !line?.hasAttribute('data-buddy-line')) return null
  return line.textContent?.trim() || null
}

describe('the exact line sits beside every family pose and the focused card', () => {
  for (const family of FAMILIES) {
    it.each(FAMILY_STATES[family])(`${family}: %s`, (state) => {
      const line = `Release notes: ${wordsOf(state)}`
      const { container } = render(<BuddyOrb state={state} line={line} size={48} />)
      expect(orbOf(container).dataset.buddyFamily).toBe(family)
      expect(lineBesideArt(container)).toBe(line)
      // The drawing is decoration (#3902): the line beside it says its state.
      expect(container.querySelector('canvas')?.getAttribute('aria-hidden')).toBe('true')
      expect(container.querySelector('canvas')?.getAttribute('role')).toBeNull()
    })
  }

  it.each([18, 24, 96] as const)('and at %i px', (size) => {
    const { container } = render(<BuddyOrb state="approval" line="Release notes: Needs your OK" size={size} />)
    expect(lineBesideArt(container)).toBe('Release notes: Needs your OK')
  })

  it('a pose rendered without its line fails the check: the control', () => {
    const { container } = render(<BuddyOrb state="approval" line="" />)
    expect(lineBesideArt(container)).toBeNull()
  })
})

describe('without a line', () => {
  it('draws the orb alone, with nothing beside it to take room, so an orb in a slot is in its middle (#5216)', () => {
    const { container } = render(<BuddyOrb state="thinking" line="" size={48} />)
    const row = orbOf(container)
    const laidOut = [...row.children].filter((el) => !el.classList.contains('sr-only'))
    expect(laidOut.length, 'only the art takes part in the row').toBe(1)
    expect(row.querySelector('[data-buddy-words]')?.textContent, 'its state still said in words').toBe(wordsOf('thinking'))
  })

  it('with a line, keeps the line beside the art and says nothing twice', () => {
    const { container } = render(<BuddyOrb state="thinking" line="Release notes: Thinking" size={48} />)
    expect(lineBesideArt(container)).toBe('Release notes: Thinking')
    expect(orbOf(container).querySelector('[data-buddy-words]')).toBeNull()
  })
})

describe('its colour is the orb tokens', () => {
  it('alive states take --color-orb-alive and the rest --color-orb-rest', () => {
    for (const state of STATES) {
      const { container, unmount } = render(<BuddyOrb state={state} line={wordsOf(state)} />)
      const alive = ['active', 'waiting'].includes(orbOf(container).dataset.buddyFamily!)
      expect(orbOf(container).dataset.buddyInkToken, state).toBe(alive ? '--color-orb-alive' : '--color-orb-rest')
      unmount()
    }
  })

  it('under more contrast the dots take the words’ ink, and under forced colours the system’s', () => {
    setMedia('prefers-contrast: more', true)
    const { container, unmount } = render(<BuddyOrb state="tool" line="Working" />)
    expect(orbOf(container).dataset.buddyInkToken).toBe('--color-on-surface')
    unmount()
    setMedia('forced-colors: active', true)
    const forced = render(<BuddyOrb state="tool" line="Working" />)
    expect(orbOf(forced.container).dataset.buddyInkToken).toBe('CanvasText')
  })

  it('names no colour of its own in its source', () => {
    // Its comments cite issues by number, which read as colours; the code is what is checked.
    const src = readFileSync(join(process.cwd(), 'src/ui/BuddyOrb.tsx'), 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '')
    expect(src).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(\s*\d/i)
    expect(src).not.toMatch(/coral/i)
  })

  it('a scheme change recolours a still orb in the paint the change causes', async () => {
    document.documentElement.style.setProperty('--color-orb-rest', '#3366cc')
    const { container } = render(<BuddyOrb state="idle" line="Nothing running" />)
    expect(orbOf(container).dataset.buddyMoving).toBe('false')
    expect(fills.some(isRampOf({ r: 0x33, g: 0x66, b: 0xcc })), 'the first paint did not use the ink').toBe(true)
    fills = []
    document.documentElement.style.setProperty('--color-orb-rest', '#228844')
    await act(async () => { await Promise.resolve() })
    expect(fills.length, 'the change painted nothing').toBeGreaterThan(0)
    expect(fills.some(isRampOf({ r: 0x22, g: 0x88, b: 0x44 })), 'the repaint did not use the new ink').toBe(true)
    expect(fills.some(isRampOf({ r: 0x33, g: 0x66, b: 0xcc })), 'the repaint still used the old ink').toBe(false)
  })
})

/** Whether a painted colour is one of the painter's shades of `tint`: toward black in the dark,
 *  toward white in the light. */
function isRampOf(tint: { r: number; g: number; b: number }): (fill: string) => boolean {
  return (fill) => {
    const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(fill)
    if (!m) return false
    const [r, g, b] = [Number(m[1]), Number(m[2]), Number(m[3])]
    const k = r / tint.r
    const dark = Math.abs(g - tint.g * k) <= 1.5 && Math.abs(b - tint.b * k) <= 1.5
    const w = (r - tint.r) / (255 - tint.r)
    const light = w >= 0 && Math.abs(g - (tint.g + (255 - tint.g) * w)) <= 1.5 && Math.abs(b - (tint.b + (255 - tint.b) * w)) <= 1.5
    return dark || light
  }
}

describe('reduced motion', () => {
  it('turns every transition into its instant answer: no spring, nothing over 50 ms', () => {
    setMedia('prefers-reduced-motion', true)
    for (const state of STATES) {
      for (const key of POSE_KEYS) {
        const t = transitionOf(state, key) as { type?: string; duration?: number }
        expect(t.type, `${state}.${key}`).not.toBe('spring')
        expect((t.duration ?? 0) * 1000, `${state}.${key}`).toBeLessThanOrEqual(50)
      }
    }
  })

  it('toggled while an alert shows, swaps the pose for its still in place and back', () => {
    const line = 'Release notes: Needs your OK'
    const { container } = render(<BuddyOrb state="approval" line={line} />)
    const canvas = container.querySelector('canvas')
    expect(orbOf(container).dataset.buddyOrb).toBe('animated')

    setMedia('prefers-reduced-motion', true)
    expect(orbOf(container).dataset.buddyOrb).toBe('still')
    expect(container.querySelector('canvas'), 'the orb was drawn again from scratch').toBe(canvas)
    expect(orbOf(container).dataset.buddyState).toBe('approval')
    expect(orbOf(container).dataset.buddyBadge).toBe('approval')
    expect(lineBesideArt(container)).toBe(line)
    expect(orbOf(container).dataset.buddyMoving, 'the switch started a move').toBe('false')

    setMedia('prefers-reduced-motion', false)
    expect(orbOf(container).dataset.buddyOrb).toBe('animated')
    expect(container.querySelector('canvas')).toBe(canvas)
    expect(orbOf(container).dataset.buddyState).toBe('approval')
    expect(lineBesideArt(container)).toBe(line)
    expect(orbOf(container).dataset.buddyMoving, 'switching back started a move').toBe('false')
  })
})
