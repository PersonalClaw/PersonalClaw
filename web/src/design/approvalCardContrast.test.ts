// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { tokenValue, type Mode } from './tokenSheet'
import { readSource } from '../test/sourceTree'

// ── The approval card's danger ink clears AA on the ground it is painted on ────────────────────
//
// A permission prompt paints its danger tone twice over: Deny (`ApprovalChoiceButton`'s danger
// palette) and the destructive risk chip (`pages/chat/ApprovalCard.tsx`'s `RiskChip`) each sit on
// a wash of the danger tone itself, and both washes sit on the card's own warn wash
// (`ui/ApprovalPrompt.tsx`), which sits on the page. `statusChipContrast.test.ts` composites a
// chip over a bare ground; nothing composited the card's layer under it, and that layer is what
// broke it. Measured in light at `#af2f29`: the risk chip read 4.12 over the canvas and Deny 4.23,
// and axe flagged both on `#/mission-control` and Deny on `#/companion`.
//
// The fix is the token, in each theme: the same hue, moved away from the mid-tone (darker in light,
// brighter in dark) to the nearest step that clears 4.5 on every resting ground (`tokens.css`,
// beside each `--color-danger`). Dark read 3.91 at worst before (the chip on surface-container). The
// strengths and the wash are read from the components, so a stronger tint or a heavier card wash
// reds here first.

const SRC = join(process.cwd(), 'src')

/** The resting tiers an approval card is painted on: Mission Control's lanes, the dashboard's
 *  action centre, a chat's transcript, a loop's and a run's approvals, the phone and the buddy. */
const GROUNDS = ['canvas', 'surface', 'surface-low', 'surface-container'] as const

// WCAG 2.1 relative luminance and contrast, kept local per this directory's convention.
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
const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
const hex = (c: number[]) => '#' + c.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')

/** `color-mix(in srgb, tone pct%, transparent)` over `ground`, rounded to 8 bits per channel as
 *  the framebuffer and axe-core's `flattenColors` round it. */
function over(tone: string, pct: number, ground: string): string {
  const a = pct / 100, t = rgb(tone), g = rgb(ground)
  return hex(t.map((v, i) => Math.round(a * v + (1 - a) * g[i])))
}

const token = (name: string, mode: Mode) => tokenValue(`--color-${name}`, mode)

const MIX = /color-mix\(in srgb, (var\(--color-[a-z-]+\)|\$\{m\.color\}) ([\d.]+)%, transparent\)/

/** The card's wash: the `background` of the prompt's own container. */
function cardWash(): { tone: string; pct: number } {
  const src = readSource(join(SRC, 'ui/ApprovalPrompt.tsx'))
  const style = src.match(/aria-label=\{`Permission needed to run \$\{tool\}`\}[\s\S]*?style=\{\{([^}]*)\}\}/)
  const bg = style?.[1].match(new RegExp(`background: '${MIX.source}'`))
  if (!bg) throw new Error("could not find the approval card's background wash in ui/ApprovalPrompt.tsx")
  return { tone: bg[1].slice(12, -1), pct: Number(bg[2]) }
}

/** Deny's tint, from `ApprovalChoiceButton`'s danger palette. */
function denyTint(): number {
  const src = readSource(join(SRC, 'ui/ApprovalPrompt.tsx'))
  const m = src.match(new RegExp(`tone === 'danger'\\s*\\?\\s*\\{ background: '${MIX.source}', color: 'var\\(--color-danger\\)' \\}`))
  if (!m || m[1] !== 'var(--color-danger)') throw new Error("could not find Deny's danger palette in ui/ApprovalPrompt.tsx")
  return Number(m[2])
}

/** The destructive risk chip's tint, and that its ink is the danger tone. */
function riskChipTint(): number {
  const src = readSource(join(SRC, 'pages/chat/ApprovalCard.tsx'))
  if (!/destructive: \{[^}]*color: 'var\(--color-danger\)'/.test(src)) throw new Error("RISK_META's destructive row is not painted in --color-danger")
  const chip = src.match(/function RiskChip[\s\S]*?\n\}/)?.[0] ?? ''
  const m = chip.match(MIX)
  if (!m || m[1] !== '${m.color}') throw new Error("could not find RiskChip's tint in pages/chat/ApprovalCard.tsx")
  return Number(m[2])
}

const PIECES = [['Deny', denyTint()], ['the destructive risk chip', riskChipTint()]] as const

/** Every piece under 4.5 on a ground of `mode`, as `<piece> on <ground>: <ratio>`. */
function shortfalls(mode: Mode, danger = token('danger', mode)): string[] {
  const wash = cardWash()
  const out: string[] = []
  for (const ground of GROUNDS) {
    const card = over(token(wash.tone, mode), wash.pct, token(ground, mode))
    for (const [piece, pct] of PIECES) {
      const ratio = contrast(danger, over(danger, pct, card))
      if (ratio < 4.5) out.push(`${piece} on ${ground}: ${ratio.toFixed(2)}`)
    }
  }
  return out
}

describe("the approval card's danger ink", () => {
  it('is read from the card as it paints: an 8% warn wash, Deny at 14%, the risk chip at 16%', () => {
    expect(cardWash()).toEqual({ tone: 'warn', pct: 8 })
    expect(PIECES.map(([, pct]) => pct)).toEqual([14, 16])
  })

  it.each(['light', 'dark'] as const)('clears 4.5:1 for Deny and the risk chip on every ground the card rests on, in %s', (mode) => {
    expect(shortfalls(mode)).toEqual([])
  })

  it('each theme moved its danger away from the mid-tone: darker in light, brighter in dark', () => {
    expect(token('danger', 'light')).toBe('#a62621')
    expect(luminance('#a62621')).toBeLessThan(luminance('#af2f29'))
    expect(token('danger', 'dark')).toBe('#ff8179')
    expect(luminance('#ff8179')).toBeGreaterThan(luminance('#f66c66'))
  })

  it('the old dark value is caught (positive control)', () => {
    expect(shortfalls('dark', '#f66c66')).toEqual([
      'the destructive risk chip on surface: 4.47',
      'Deny on surface-low: 4.26', 'the destructive risk chip on surface-low: 4.10',
      'Deny on surface-container: 4.03', 'the destructive risk chip on surface-container: 3.91',
    ])
  })

  it('the old light value is caught (positive control)', () => {
    expect(shortfalls('light', '#af2f29')).toEqual([
      'Deny on canvas: 4.23', 'the destructive risk chip on canvas: 4.12',
      'the destructive risk chip on surface: 4.49',
      'Deny on surface-low: 4.28', 'the destructive risk chip on surface-low: 4.15',
      'the destructive risk chip on surface-container: 4.49',
    ])
  })
})
