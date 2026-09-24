import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SCHEMES } from './schemes'

// ── The app's keyboard focus indicator was invisible, and a rail was mandating it ──────────────────
//
// `tokens.css`'s global `:focus-visible { outline: 2px solid var(--color-primary) }` is compliant. It
// does not survive `outline-none`, which every input in the app carries — `focusRingSurvival.test.ts`
// already documents why, byte-for-byte from the BUILT stylesheet: `.outline-none` sits in
// `@layer utilities` and beats `@layer base`. The replacement each of those controls installs was
// `focus:ring-2 focus:ring-inset focus:ring-primary/50`, and **the alpha is what broke it**.
//
// Measured live at 127.0.0.1:10000 on a real `:focus-visible` text input (`el.matches(':focus-visible')`
// === true, `outlineStyle: "none"`, so the tint WAS the whole indicator):
//
//     --color-primary  #ff6b5b   surface behind the field  rgb(40,42,44)
//     ring at 50%      rgb(148,75,68)   → 2.30:1     ← the shipped indicator
//     ring at 100%     rgb(255,107,91)  → 5.15:1     ← after
//
// And swept across the whole token space (this file's first test), 12 schemes x 2 modes x 6 surface
// tiers: the worst 50% case is **1.89:1** (honey/light on surface-highest) and the worst FULL-opacity
// case is **4.00:1** (coral/light on surface-highest). So the fix is not a new colour and not a
// per-scheme judgement — opaque coral is what `tokens.css:311` already specifies for the global ring,
// and it clears the floor everywhere with headroom.
//
// 🔑 WHY 200+ REVIEW CYCLES WALKED PAST IT. The two rails that own this area are presence-only and one
// of them *enshrined* the failing value: `focusRingPerElement.test.ts` hardcoded
// `/focus:ring-2\s+focus:ring-inset\s+focus:ring-primary\/50/` and 17 controls were "fixed" INTO it,
// while `consistencyAudit` asserts only that the global rule exists. No test in the tree asserted a
// focus-indicator contrast floor — `git grep` for one returned nothing. A ratchet that mandates a
// specific string mandates whatever that string measures, so this file asserts the NUMBER.
//
// Deliberately not in scope: `ring-1 ring-primary/30` on `PlanStreamReview`'s pending row. That is a
// decorative tint on a non-focus state, not an indicator, so SC 2.4.11 does not reach it — and the
// spelling guard below is scoped to focus states so it cannot sweep it up. That scoping is asserted
// synthetically, because nothing in the tree exercises it today.

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
/** Composite `hex` over `base` at `alpha` — what a translucent ring actually renders as. */
const overlay = (hex: string, base: string, alpha: number): string => {
  const p = (h: string, i: number) => parseInt(h.replace('#', '').slice(i, i + 2), 16)
  return '#' + [0, 2, 4]
    .map((i) => Math.round(p(hex, i) * alpha + p(base, i) * (1 - alpha)))
    .map((v) => v.toString(16).padStart(2, '0'))
    .join('')
}

/** A surface token in a given mode, read from source so a retint cannot drift this guard — the same
 *  discipline (and the same `.light` rule-block trap) as `schemeContrast.test.ts`. */
function tier(mode: 'dark' | 'light', name: string): string {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  // 🪤 Match the RULE BLOCK, not `indexOf('.light')`: the file says ".light mode" in a comment first.
  const scope = mode === 'dark' ? css : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  const m = scope.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{3,8})`))
  if (!m) throw new Error(`no --color-${name} for ${mode}`)
  return m[1]
}

/** Every surface a focused control can sit on. A ring is drawn INSET, so the ground it must separate
 *  from is the control's own fill — which is one of these tiers at every site in the app. */
const TIERS = ['surface', 'surface-low', 'surface-container', 'surface-high', 'surface-highest', 'canvas']

/** SC 1.4.11 / 2.4.11: a focus indicator is a UI component boundary — 3:1, not 4.5:1. */
const FLOOR = 3

function primaryOf(s: (typeof SCHEMES)[number], mode: 'dark' | 'light'): string {
  const v = s.colors['--color-primary'] as unknown as { dark?: string; light: string } | string
  const hex = typeof v === 'string' ? v : (mode === 'dark' ? (v.dark ?? v.light) : v.light)
  if (typeof hex !== 'string' || !hex.startsWith('#')) throw new Error(`unparsed primary for ${s.id}/${mode}`)
  return hex
}

describe('the focus indicator clears the 3:1 floor in every scheme, mode and surface', () => {
  it('has the full curated scheme set — the sweep is not measuring a subset', () => {
    expect(SCHEMES.length).toBeGreaterThanOrEqual(11)
  })

  it('the floor is the standard, not a local preference', () => {
    // 🪤 Mutation-checked: lowering FLOOR from 3 to 2.5 left every other assertion in this file green,
    // because today's values clear both. The cheapest way to retire this rail is therefore to edit the
    // number — so the number is pinned to the spec it comes from.
    expect(FLOOR, 'SC 1.4.11 / SC 2.4.11 set 3:1 for a UI component boundary — not tunable').toBe(3)
  })

  it('opaque primary is >= 3:1 on every surface a ring can sit on', () => {
    const failures: string[] = []
    let worst = { ratio: Infinity, where: '' }
    for (const s of SCHEMES) {
      for (const mode of ['dark', 'light'] as const) {
        const prim = primaryOf(s, mode)
        for (const t of TIERS) {
          const base = tier(mode, t)
          const r = contrast(prim, base)
          if (r < worst.ratio) worst = { ratio: r, where: `${s.id}/${mode}/${t}` }
          if (r < FLOOR) failures.push(`${s.id}/${mode}/${t}: ${r.toFixed(2)}:1 (${prim} on ${base})`)
        }
      }
    }
    expect(failures, `a focus ring nobody can see:\n${failures.join('\n')}`).toEqual([])
    // The measured worst case, pinned: if a retint drops it below ~3.5 the margin is gone and the
    // next scheme edit will red this rail rather than surprise a keyboard user.
    expect(worst.ratio, `worst case is ${worst.where} at ${worst.ratio.toFixed(2)}:1`)
      .toBeGreaterThanOrEqual(3.5)
  })

  it('and the 50% tint it replaced could NOT have passed — the reason, as an assertion', () => {
    // Not decoration: this is what makes the rail explain itself to whoever reads it after a revert.
    let worst = Infinity
    for (const s of SCHEMES) {
      for (const mode of ['dark', 'light'] as const) {
        const prim = primaryOf(s, mode)
        for (const t of TIERS) {
          const base = tier(mode, t)
          worst = Math.min(worst, contrast(overlay(prim, base, 0.5), base))
        }
      }
    }
    expect(worst, 'a 50% tint of the accent cannot reach 3:1 against its own surface')
      .toBeLessThan(FLOOR)
  })
})

// ── The spelling guard: an alpha on a FOCUS ring is the defect, mechanically ───────────────────────

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    return statSync(p).isDirectory() ? walk(p) : (/\.(tsx?|css)$/.test(n) ? [p] : [])
  })

/** Any variant prefix containing `focus` — `focus:`, `focus-visible:`, `focus-within:` and the
 *  `has-[input:focus-visible]:` / `has-[>button:focus-visible]:` forms the row and picker idioms use.
 *  Keyed on "the prefix mentions focus" rather than a list, because the list was already incomplete
 *  once: a first cut named three prefixes and would have let `has-[…:focus-visible]:ring-primary/50`
 *  back in. A bare `ring-primary/30` (a decorative tint, not an indicator) is deliberately outside. */
const ALPHA_FOCUS_RING = /[\w[\]:>-]*focus[\w[\]:>-]*:ring-primary\/\d+/

describe('no focus ring may carry an alpha', () => {
  it('nowhere in src', () => {
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      // 🪤 Skip this file. Its scoping test below holds the counter-examples as literals, so a naive
      // walk reports the rail as its own top offender — which is how the first run failed.
      if (abs.endsWith('focusRingContrast.test.ts')) continue
      const src = readFileSync(abs, 'utf8')
      for (const m of src.matchAll(new RegExp(ALPHA_FOCUS_RING, 'g'))) {
        offenders.push(`${abs.slice(SRC.length + 1)}: ${m[0]}`)
      }
    }
    expect(offenders, `a translucent focus ring measures 1.89-2.44:1 — use ring-primary:\n${offenders.join('\n')}`)
      .toEqual([])
  })

  it('finds the population it guards (not vacuously green)', () => {
    // A MEASURED POPULATION, not a vacuity guard: **66 files** install their own focus ring today
    // (they carry `outline-none`, so the global rule cannot reach them). The floor sits at the
    // measurement per `railFloors.test.ts`'s taxonomy — if controls stop installing a ring, that is
    // either a migration worth noticing or a regression, and either way this should go red.
    const uses = walk(SRC).filter((f) => /(focus|focus-visible|focus-within):ring-primary\b/.test(readFileSync(f, 'utf8')))
    expect(uses.length, 'the app must still install its own focus ring').toBeGreaterThanOrEqual(66)
  })

  it('is scoped to focus states, and only to them', () => {
    // Synthetic, because the tree holds exactly one decorative alpha ring and it must stay legal.
    expect(ALPHA_FOCUS_RING.test('focus:ring-primary/50')).toBe(true)
    expect(ALPHA_FOCUS_RING.test('focus-within:ring-primary/50')).toBe(true)
    expect(ALPHA_FOCUS_RING.test('focus-visible:ring-primary/40')).toBe(true)
    expect(ALPHA_FOCUS_RING.test('has-[input:focus-visible]:ring-primary/50'),
      'the picker idiom — the prefix a narrower guard missed').toBe(true)
    expect(ALPHA_FOCUS_RING.test('has-[>button:focus-visible]:ring-primary/50'),
      'the list-row overlay idiom').toBe(true)
    expect(ALPHA_FOCUS_RING.test('ring-1 ring-primary/30'), 'a decorative tint is not an indicator').toBe(false)
    expect(ALPHA_FOCUS_RING.test('focus:ring-primary'), 'the compliant spelling must pass').toBe(false)
  })
})
