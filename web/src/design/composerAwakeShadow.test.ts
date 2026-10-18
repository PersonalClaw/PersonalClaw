import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { DROP_GROW_PX, RISE_GROW_PX } from '../ui/composer/rise'

// ── The composer's own awake shadow fits inside the page gutter it sits in ─────────────────────
//
// The composer lives in the page gutter (`px-l`, 16px at the default density): docked 16px above the
// chat page's bottom, and 16px from the rail at the shipped `full` width, in every chat state. The
// page box clips whatever crosses that gutter. It used to animate to the cards' `--shadow-lift`
// (`0 22px 55px -14px rgb(0 0 0 / .6), 0 0 48px -6px coral 50%`), which reaches ~63px down — so on
// an ongoing chat the focused composer's shadow was cut into a straight line at the page bottom.
// Measured on a light canvas, 1440×900: (185,165,163) in the last row against a (240,244,248)
// canvas, and a coral step at the rail in both themes (red 31 → 70, dark).
//
// So this rail resolves the shadow the Composer ACTUALLY animates to when awake — read out of
// `Composer.tsx`, not assumed — and measures every layer's outward reach from the token itself:
// offset + spread + blur radius (the blur radius is ~2σ of the Gaussian, where a layer is down to
// ~2% of its alpha). That reach must fit in what is left of the gutter after the capped rise
// (`ui/composer/rise.ts`). The WIDE glow is the halo's job (`ui/DotGlow`), which fades out before
// every edge instead of being clipped.

const WEB = process.cwd()
const composer = readFileSync(join(WEB, 'src/ui/Composer.tsx'), 'utf8')
const tokens = readFileSync(join(WEB, 'src/design/tokens.css'), 'utf8')
/** The page gutter the composer sits in: `px-l` = `--spacing-l` = 16px at the default density. */
const GUTTER = 16

/** The token the composer surface animates its box-shadow to when awake (focused or receiving a
 *  drop, under the decorative light — Claw Arcade's, `web/DESIGN.md` §7). */
function awakeToken(): string {
  const m = composer.match(/boxShadow:\s*awake\s*\?\s*'var\((--[\w-]+)\)'/)
  expect(m, 'Composer.tsx no longer animates its awake box-shadow to a single var() token — re-point this rail').not.toBeNull()
  return m![1]
}

/** How far the default look's focus ring reaches past the composer: its outline width plus its
 *  offset, read out of `Composer.tsx`'s `FOCUS_RING`. */
function focusRingReach(): number {
  const ring = composer.match(/const FOCUS_RING = \{([^}]*)\}/)
  expect(ring, 'Composer.tsx no longer declares FOCUS_RING — re-point this rail').not.toBeNull()
  const px = (prop: string) => Number(ring![1].match(new RegExp(`${prop}:\\s*'(\\d+(?:\\.\\d+)?)px'`))?.[1] ?? NaN)
  return px('outlineWidth') + px('outlineOffset')
}

/** Every declaration of `name` in tokens.css (the dark @theme default plus any scheme override). */
function declarations(name: string): string[] {
  const out: string[] = []
  const re = new RegExp(`${name.replace(/-/g, '\\-')}\\s*:\\s*([^;]+);`, 'g')
  for (const m of tokens.matchAll(re)) out.push(m[1].replace(/\s+/g, ' ').trim())
  return out
}

/** Split a box-shadow value into its layers, on commas that are not inside parentheses. */
function layers(value: string): string[] {
  const out: string[] = []
  let depth = 0, cur = ''
  for (const ch of value) {
    if (ch === '(') depth++
    if (ch === ')') depth--
    if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = '' } else cur += ch
  }
  if (cur.trim()) out.push(cur.trim())
  return out
}

/** A layer's outward reach below and beside its box, in px. Reads the layer's leading lengths
 *  (`x y blur spread`, the order every shadow token in tokens.css is written in); the colour that
 *  follows is where the reading stops. */
function reach(layer: string): { down: number; side: number } {
  const lengths: number[] = []
  for (const tok of layer.split(/\s+/)) {
    if (tok === 'inset') continue
    const m = tok.match(/^(-?\d*\.?\d+)px$|^0$/)
    if (!m) break
    lengths.push(Number(m[1] ?? 0))
  }
  expect(lengths.length, `could not read the lengths of shadow layer "${layer}"`).toBeGreaterThanOrEqual(2)
  const [x = 0, y = 0, blur = 0, spread = 0] = lengths
  return { down: y + spread + blur, side: Math.abs(x) + spread + blur }
}

describe('the composer’s awake shadow stays inside its gutter', () => {
  it('every layer reaches no further than the gutter the capped rise leaves', () => {
    const name = awakeToken()
    const values = declarations(name)
    expect(values.length, `${name} is not declared in tokens.css`).toBeGreaterThan(0)
    const budget = GUTTER - Math.max(RISE_GROW_PX, DROP_GROW_PX)
    for (const v of values) {
      for (const layer of layers(v)) {
        const r = reach(layer)
        expect(r.down, `${name}: "${layer}" reaches ${r.down}px below the composer; the gutter leaves ${budget}px`).toBeLessThanOrEqual(budget)
        expect(r.side, `${name}: "${layer}" reaches ${r.side}px beside the composer; the gutter leaves ${budget}px`).toBeLessThanOrEqual(budget)
      }
    }
  })

  it('so does the default look’s plain focus ring, which never rises', () => {
    const reach = focusRingReach()
    expect(reach, 'FOCUS_RING has no readable outline width and offset').toBeGreaterThan(0)
    expect(reach, `the focus ring reaches ${reach}px past the composer; the gutter has ${GUTTER}px`).toBeLessThanOrEqual(GUTTER)
  })

  it('the measuring stick is not vacuous: the cards’ `--shadow-lift` it replaced does NOT fit', () => {
    const lift = declarations('--shadow-lift')
    expect(lift.length).toBeGreaterThan(0)
    const worst = Math.max(...lift.flatMap((v) => layers(v).map((l) => reach(l).down)))
    expect(worst).toBe(63)
  })
})
