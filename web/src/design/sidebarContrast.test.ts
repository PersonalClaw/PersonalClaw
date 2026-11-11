// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import { SCHEMES } from './schemes'
import { tokenValue } from './tokenSheet'
import { readSource } from '../test/sourceTree'
import { SELECTED_PILL } from '../ui/NavRail'
import { EDGE } from '../ui/SearchField'

// ── The chat list's controls are told from their ground at 3:1 or more (one list for both hosts) ──
//
// 🔴 Red before: the chat list's search field, its menu trigger and the open row's lit pill were
// fills alone, 1.00 to 1.12:1 against the sidebar's rail and the panel's surface, so nothing but the
// placeholder told the owner where the field was, or which row was open. Now each carries one edge,
// `border-on-surface-low/70`, which this file measures against both grounds, in both modes, with the
// edge composited over the control's own fill as it renders. The list's text and icons are measured
// too: the app's name and the row marks in `on-surface-low`, the rows in `on-surface-var`, the
// sidebar's section heading (`on-surface` at 0.65), the unread dot, and the open menu's text and
// icons on its glass (`surface-container` at 0.72 over the ground).
//
// No colour scheme overrides a surface or an ink token (a test below pins that), so the default
// scheme's numbers hold in all of them for those rows. The rows drawn in `--color-primary`, which
// every scheme sets, are measured in each scheme: the focus ring, the chosen option on its tint, and
// the trigger's set dot.

const lum = (hex: string): number => {
  const h = hex.replace('#', '')
  const ch = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * ch(0) + 0.7152 * ch(2) + 0.0722 * ch(4)
}
const contrast = (a: string, b: string): number => {
  const la = lum(a), lb = lum(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}
/** Composite `hex` over `base` at `alpha`: what a translucent edge renders as. */
const overlay = (hex: string, base: string, alpha: number): string => {
  const p = (h: string, i: number) => parseInt(h.replace('#', '').slice(i, i + 2), 16)
  return '#' + [0, 2, 4]
    .map((i) => Math.round(p(hex, i) * alpha + p(base, i) * (1 - alpha)))
    .map((v) => v.toString(16).padStart(2, '0'))
    .join('')
}

type Mode = 'dark' | 'light'
const MODES: Mode[] = ['dark', 'light']
const token = (name: string, mode: Mode) => tokenValue(`--color-${name}`, mode)
/** The open menu's glass over *ground*: its `surface-container` fill at 0.72. */
const GLASS_ALPHA = 0.72
const glassOver = (ground: string, mode: Mode) => overlay(token('surface-container', mode), token(ground, mode), GLASS_ALPHA)
/** The list's two grounds: the sidebar's rail and the panel's dock. */
const GROUNDS = { rail: 'rail', panel: 'surface' } as const
const EDGE_ALPHA = 0.7

/** Each boundary row: the control's own fill, under the edge. */
const BOUNDARIES = {
  'the search field': 'surface-container',
  'the Sort and filter trigger': 'surface-container',
  'the lit pill': 'surface-low',
} as const

/** Each text and icon row: its ink, and its floor. */
const INKS = {
  "an app's name on a row (text)": { ink: 'on-surface-low', floor: 4.5 },
  'a row title at rest (text)': { ink: 'on-surface-var', floor: 4.5 },
  'a row mark: a kind, a pin, an archive (icon)': { ink: 'on-surface-low', floor: 3 },
} as const

describe('the chat list on its grounds', () => {
  for (const mode of MODES) {
    for (const [host, ground] of Object.entries(GROUNDS)) {
      for (const [row, fill] of Object.entries(BOUNDARIES)) {
        it(`${row} is told from the ${host} at 3:1 or more, in ${mode}`, () => {
          const g = token(ground, mode)
          const edge = overlay(token('on-surface-low', mode), token(fill, mode), EDGE_ALPHA)
          expect(contrast(edge, g), `${row} on the ${host} (${mode})`).toBeGreaterThanOrEqual(3)
        })
      }
      for (const [row, { ink, floor }] of Object.entries(INKS)) {
        it(`${row} reads at ${floor}:1 or more on the ${host}, in ${mode}`, () => {
          expect(contrast(token(ink, mode), token(ground, mode)), `${row} on the ${host} (${mode})`).toBeGreaterThanOrEqual(floor)
        })
      }
    }
  }

  for (const mode of MODES) {
    it(`the sidebar's section heading (text) reads at 4.5:1 or more on the rail, in ${mode}`, () => {
      const rail = token('rail', mode)
      expect(contrast(overlay(token('on-surface', mode), rail, 0.65), rail)).toBeGreaterThanOrEqual(4.5)
    })
    it(`the unread dot (icon) is 3:1 or more on both grounds and on the lit pill, in ${mode}`, () => {
      for (const ground of ['rail', 'surface', 'surface-low']) {
        expect(contrast(token('state-quiet', mode), token(ground, mode)), `on ${ground} (${mode})`).toBeGreaterThanOrEqual(3)
      }
    })
    for (const [host, ground] of Object.entries(GROUNDS)) {
      it(`the open menu's text and icons read on its glass over the ${host}, in ${mode}`, () => {
        const glass = glassOver(ground, mode)
        expect(contrast(token('on-surface', mode), glass), 'an option and the Show all row (text)').toBeGreaterThanOrEqual(4.5)
        expect(contrast(token('on-surface-low', mode), glass), 'a section title and a count (text)').toBeGreaterThanOrEqual(4.5)
        expect(contrast(token('on-surface-var', mode), glass), "an option's icon").toBeGreaterThanOrEqual(3)
      })
    }
  }

  for (const s of SCHEMES) {
    for (const mode of MODES) {
      it(`in ${s.id}, ${mode}: the ring, the chosen option and the set dot, drawn in its primary`, () => {
        const p = tokenValue('--color-primary', mode, s.id)
        for (const [host, ground] of Object.entries(GROUNDS)) {
          const g = token(ground, mode)
          const glass = glassOver(ground, mode)
          expect(contrast(p, g), `the focus ring on the ${host}`).toBeGreaterThanOrEqual(3)
          expect(contrast(p, overlay(p, glass, 0.12)), `the chosen option on its tint, over the ${host}`).toBeGreaterThanOrEqual(4.5)
        }
        expect(contrast(p, token('surface-container', mode)), "the trigger's set dot").toBeGreaterThanOrEqual(3)
      })
    }
  }

  it('without the edge, the fills alone are not told from the ground: what the edge is for', () => {
    for (const mode of MODES) {
      for (const fill of Object.values(BOUNDARIES)) {
        expect(contrast(token(fill, mode), token('rail', mode)), `${fill} on the rail (${mode})`).toBeLessThan(3)
      }
    }
  })

  it('no colour scheme overrides a surface or an ink this file measures, so the numbers hold in all', () => {
    const measured = new Set(['rail', 'surface', 'on-surface', 'on-surface-low', 'on-surface-var', 'state-quiet', ...Object.values(BOUNDARIES)].map((n) => `--color-${n}`))
    expect(SCHEMES.length).toBeGreaterThanOrEqual(12)
    for (const s of SCHEMES) {
      const overrides = Object.keys(s.colors).filter((k) => measured.has(k))
      expect(overrides, `scheme ${s.id}`).toEqual([])
    }
  })
})

describe('the edge is where the list draws it', () => {
  const read = (rel: string) => readSource(join(process.cwd(), 'src', rel))

  it('is one value, on the lit pill and the list controls', () => {
    expect(EDGE).toBe('border border-on-surface-low/70')
    expect(SELECTED_PILL).toContain(EDGE)
  })

  it('is measured over the fills the controls draw', () => {
    expect(SELECTED_PILL).toContain('bg-surface-low')
    expect(read('ui/FilterMenu.tsx')).toMatch(/size === 'sm'[\s\S]{0,700}bg-surface-container/)
    expect(read('ui/SearchField.tsx')).toMatch(/container: 'bg-surface-container'/)
  })

  it("the list's search passes edge, and the small menu trigger and both lit pills use the constants", () => {
    const rail = read('ui/ThreadRail.tsx')
    expect(rail).toMatch(/<SearchField[^>]*\sedge(?=[\s/])[^>]*\/>/)
    expect(rail.match(/className=\{SELECTED_PILL\}/g)).toHaveLength(2)
    expect(read('ui/FilterMenu.tsx')).toMatch(/size === 'sm'[\s\S]{0,700}EDGE,/)
    expect(read('ui/NavRail.tsx')).toMatch(/\{active && <span className=\{SELECTED_PILL\} \/>\}/)
  })
})
