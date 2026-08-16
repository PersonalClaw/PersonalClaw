/**
 * PERSONALITY-THEMES §S2 (T2.3) — the error-treatment closed map, and its AA floor.
 *
 * An error surface is the one place in the product where a legibility regression
 * is not a cosmetic complaint: it is the message that tells a user what broke and
 * how to recover, drawn at the moment they are least able to guess. So a
 * personality is allowed to restyle it only under a measured contrast bar.
 *
 * `schemeContrast.test.ts` cannot cover this. It sweeps the per-scheme ACCENT
 * tokens out of `schemes.ts`; the tokens a treatment paints with
 * (`--color-surface-*`, `--color-on-surface`, `--color-danger`) are global values
 * in `tokens.css`, identical under every scheme and swept by nothing. This file is
 * that population's guard, read from the token file so a retint cannot drift it.
 */

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ERROR_TREATMENTS, getErrorTreatment, treatmentPaint, type ErrorTreatmentId } from './errorTreatments'
import { PERSONALITIES } from './personalities'

// WCAG 2.1 relative luminance + contrast ratio (sRGB) — same math as
// schemeContrast.test.ts, kept local per this directory's convention.
function luminance(hex: string): number {
  const h = hex.replace('#', '')
  const chan = (i: number) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * chan(0) + 0.7152 * chan(2) + 0.0722 * chan(4)
}
function contrast(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/** AA for normal-size text. The glyph only needs 3:1 as a non-text element, but
 *  it is held to the text bar too — an error icon that is merely "detectable" is
 *  not the standard an error surface should ship at. */
const AA = 4.5

/** tokens.css split into the dark default block and the `.light` override, so a
 *  token's value is read per mode rather than restated here. */
function blocks(): { dark: string; light: string } {
  const src = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  // Match the `.light` RULE BLOCK, not the first mention of the string: the file
  // says ".light mode" in prose earlier (the trap schemeContrast.test.ts records).
  const at = src.search(/\.light\s*\{/)
  if (at < 0) throw new Error('could not find the .light rule block in tokens.css')
  return { dark: src.slice(0, at), light: src.slice(at) }
}

function tokenValue(block: string, name: string): string {
  const m = block.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`))
  if (!m) throw new Error(`${name} has no direct hex value in this tokens.css block`)
  return m[1]
}

const BLOCKS = blocks()
const MODES = ['dark', 'light'] as const

describe('the treatment map is closed, complete, and non-vacuous', () => {
  it('every id in the map keys its own entry (no copy-paste id drift)', () => {
    for (const [id, t] of Object.entries(ERROR_TREATMENTS)) expect(t.id).toBe(id)
  })

  it('at least one personality declares a treatment, so the skin tests are not vacuous', () => {
    // Without this the whole feature could be dead code and every "same copy under
    // every personality" assertion would pass by never rendering a treatment.
    const declared = PERSONALITIES.filter((p) => p.behavior.errorTreatment)
    expect(declared.length).toBeGreaterThan(0)
  })

  it('every declared treatment id resolves in the closed map', () => {
    for (const p of PERSONALITIES) {
      const id = p.behavior.errorTreatment
      if (id) expect(getErrorTreatment(id), `${p.id} → ${id}`).not.toBeNull()
    }
  })

  it('an unknown or absent id resolves to no treatment, never a throw', () => {
    // The resolver runs while an error surface is rendering. A throw there escapes
    // the boundary drawing it and blanks the app.
    expect(getErrorTreatment(undefined)).toBeNull()
    expect(getErrorTreatment('removed-in-a-later-release')).toBeNull()
    expect(getErrorTreatment('')).toBeNull()
    expect(treatmentPaint(null)).toBeNull()
  })

  it('an INHERITED key is not a treatment either, and does not throw downstream', () => {
    // Found while closing the same hole in `getShellElement` (PT-3). `map[id] ?? null`
    // reads the prototype chain, so these ids resolved to real objects — `Object`
    // itself — and `treatmentPaint` then threw `Cannot read properties of undefined
    // (reading 'bg')` while the ErrorBoundary was mid-render. Exactly the failure the
    // test above exists to prevent, reached by a key nobody thought to try.
    for (const inherited of ['constructor', 'toString', 'hasOwnProperty', '__proto__', 'valueOf']) {
      expect(getErrorTreatment(inherited), inherited).toBeNull()
      expect(() => treatmentPaint(getErrorTreatment(inherited)), inherited).not.toThrow()
    }
  })

  it('a treatment carries presentation ONLY — no copy, action or role slot', () => {
    // The skin-only guarantee as a structural check: a treatment that could carry
    // text or a handler could reword or disarm a failure.
    const ALLOWED = new Set(['id', 'label', 'surfaceClass', 'iconClass', 'paint'])
    for (const t of Object.values(ERROR_TREATMENTS)) {
      for (const key of Object.keys(t)) expect(ALLOWED.has(key), `${t.id}.${key}`).toBe(true)
      expect(Object.keys(t.paint).sort()).toEqual(['bg', 'icon', 'ink'])
    }
  })

  it('paints with design tokens, never colour literals', () => {
    for (const t of Object.values(ERROR_TREATMENTS)) {
      for (const [slot, token] of Object.entries(t.paint)) {
        expect(token, `${t.id}.paint.${slot}`).toMatch(/^--color-[a-z-]+$/)
      }
    }
  })

  it('renders paint as var() references so the token indirection survives', () => {
    const t = ERROR_TREATMENTS['terminal-frame']
    expect(treatmentPaint(t)).toEqual({
      background: `var(${t.paint.bg})`,
      color: `var(${t.paint.ink})`,
    })
  })
})

describe('every treatment meets WCAG AA in BOTH modes', () => {
  it('the token file yielded real, per-mode values (not vacuously green)', () => {
    // Guards the guard: if the block split silently produced the same text twice,
    // every ratio below would be computed against the wrong mode and still pass.
    expect(tokenValue(BLOCKS.dark, '--color-surface-container')).not.toBe(
      tokenValue(BLOCKS.light, '--color-surface-container'),
    )
    expect(tokenValue(BLOCKS.dark, '--color-danger')).not.toBe(tokenValue(BLOCKS.light, '--color-danger'))
  })

  for (const id of Object.keys(ERROR_TREATMENTS) as ErrorTreatmentId[]) {
    const t = ERROR_TREATMENTS[id]
    describe(`treatment '${id}'`, () => {
      for (const mode of MODES) {
        const bg = () => tokenValue(BLOCKS[mode], t.paint.bg)
        const ink = () => tokenValue(BLOCKS[mode], t.paint.ink)
        const icon = () => tokenValue(BLOCKS[mode], t.paint.icon)

        it(`${mode}: body ink on the treated surface ≥ AA`, () => {
          expect(contrast(ink(), bg())).toBeGreaterThanOrEqual(AA)
        })
        it(`${mode}: alert glyph on the treated surface ≥ AA`, () => {
          expect(contrast(icon(), bg())).toBeGreaterThanOrEqual(AA)
        })
      }
    })
  }
})
