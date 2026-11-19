// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { SCHEMES } from './schemes'
import { tokenValue, type Mode } from './tokenSheet'
import { readSource } from '../test/sourceTree'

// ── The turn line's words meet AA on the chat's ground, at rest, in every scheme and mode ───────
//
// The a11y scans that run after scripted turns wait for the turn line to go (`settleTurnLine`,
// web/e2e/helpers.ts), because a scan taken while it fades reads its words blended toward the
// ground and reds on contrast the line does not have (#6274, #6236). That leaves the line's own
// contrast scanned by no e2e test: no other scan drives a turn, and the line is shown at full
// opacity for only about 1.3 s once a turn ends. So it is held here, from the tokens, where it
// can't flake: the row's words are drawn in `on-surface` (what the turn is doing, or how it
// ended) and `on-surface-var` (the turning word, the time, the quiet line and the tokens), on the
// chat's `canvas`, with no fill of its own and no opacity at rest.
//
// Measured in Chromium on a real turn's ending, while it holds: the state text is 14.9:1 in light
// (#1f1f1f on #f0f4f8) and dark (#e3e3e3 on #0f0f0f); the elapsed label is 5.47:1 in light
// (#5f6368) and 11.25:1 in dark (#c4c7c5). Light's `on-surface-var` is the one near the floor.

const AA = 4.5
const MODES: Mode[] = ['light', 'dark']

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

const ROW = readSource(join(process.cwd(), 'src/ui/TurnStatusRow.tsx'))

describe('the turn line’s words meet AA on the chat’s ground', () => {
  it('its words are drawn in on-surface and on-surface-var, with no fill or opacity of their own at rest', () => {
    const inks = new Set([...ROW.matchAll(/\btext-(on-[a-z-]+)/g)].map((m) => m[1]))
    expect([...inks].sort()).toEqual(['on-surface', 'on-surface-var'])
    // Anywhere in the component, not only on the row's root: a word drawn at part opacity, or on a
    // fill, renders at a contrast the tokens don't say.
    expect(ROW.match(/(?<![\w-])(?:[a-z-]+:)*(?:bg-|opacity-)[\w/().[\]-]+/g) ?? []).toEqual([])
    // The one opacity the row takes is the leaving fade's, while it goes: one inline opacity in the
    // whole component, and it is that one.
    expect(ROW.match(/\bopacity\s*:/g) ?? []).toHaveLength(1)
    expect(ROW).toMatch(/stage === 'leaving' && !reduced \? \{ opacity: 0 \}/)
  })

  for (const mode of MODES) {
    for (const { id } of SCHEMES) {
      it(`${mode}, ${id}: on-surface and on-surface-var on canvas are ${AA}:1 or more`, () => {
        const ground = tokenValue('--color-canvas', mode, id)
        for (const ink of ['--color-on-surface', '--color-on-surface-var']) {
          const ratio = contrast(tokenValue(ink, mode, id), ground)
          expect(ratio, `${ink} ${tokenValue(ink, mode, id)} on ${ground}: ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA)
        }
      })
    }
  }

  it('the measured numbers are the tokens’ own', () => {
    expect(contrast(tokenValue('--color-on-surface', 'light'), tokenValue('--color-canvas', 'light'))).toBeCloseTo(14.9, 1)
    expect(contrast(tokenValue('--color-on-surface', 'dark'), tokenValue('--color-canvas', 'dark'))).toBeCloseTo(14.9, 0)
    expect(contrast(tokenValue('--color-on-surface-var', 'light'), tokenValue('--color-canvas', 'light'))).toBeCloseTo(5.47, 1)
    expect(contrast(tokenValue('--color-on-surface-var', 'dark'), tokenValue('--color-canvas', 'dark'))).toBeCloseTo(11.25, 1)
  })
})
