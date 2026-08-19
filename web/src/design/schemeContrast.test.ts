import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { SCHEMES } from './schemes'

// ── Scheme contrast guard (design-system consistency) ──────────────────────
// PRODUCT.md mandates WCAG 2.1 AA. The default 'coral' scheme's AA was verified
// numerically in tokenRegistry.ts and is enforced live by e2e/a11y.spec.ts —
// but axe only ever exercises the DEFAULT scheme. The other ten named schemes
// (honey/jade/ember/forest/rose/amber/…) carried pre-AA light-mode accents that
// no rail caught. This test closes that hole STRUCTURALLY: it iterates EVERY
// scheme and asserts each one's accent tokens meet AA in the contexts where they
// actually bear text/UI, in BOTH modes. A new scheme (or a regressed edit to an
// existing one) that dips below AA turns the build red — no scheme can ship
// below the bar the default is held to.

// WCAG 2.1 relative luminance + contrast ratio (sRGB).
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
  const hi = Math.max(la, lb), lo = Math.min(la, lb)
  return (hi + 0.05) / (lo + 0.05)
}

// AA for normal-size text / meaningful UI. Accent chips, links, and small labels
// are all ≤ large-text size, so 4.5:1 is the applicable threshold.
const AA = 4.5

// The dark neutral surface accent text realistically sits on. Kept in sync with
// tokens.css (--color-surface-container, dark block) — read from source so a
// token retint can't silently drift the guard. surface-highest is intentionally
// NOT the reference: even the AA-verified default coral is 4.4:1 there, so it's
// not a text-bearing accent background — testing it would invent a failure the
// default itself doesn't meet.
function darkSurfaceContainer(): string {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  // First occurrence = the default (dark) :root block, before the .light override.
  const m = css.match(/--color-surface-container:\s*(#[0-9a-fA-F]{3,8})/)
  if (!m) throw new Error('could not find --color-surface-container in tokens.css')
  return m[1]
}

/** `--color-surface-high` in a given mode — the ground the app's small accent CHIPS sit on (a tag
 *  pill, a status pill, a markdown citation/file chip). Read from source in both blocks so a retint
 *  cannot drift the guard, the same discipline as `darkSurfaceContainer` and `lightCanvas`. */
function surfaceHigh(mode: 'dark' | 'light'): string {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  const scope = mode === 'dark'
    ? css                                              // the default :root block comes first
    : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  const m = scope.match(/--color-surface-high:\s*(#[0-9a-fA-F]{3,8})/)
  if (!m) throw new Error(`could not find --color-surface-high for ${mode}`)
  return m[1]
}

/** `--color-surface-low` in a given mode — the ground a dashboard ROW paints (`bg-surface-low`), which
 *  is where a row action's 15px label sits. Not white in light: **#f4f6f9**, and that difference is the
 *  whole finding, so the value is read from source per mode rather than assumed. */
function surfaceLow(mode: 'dark' | 'light'): string {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  const scope = mode === 'dark' ? css : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  const m = scope.match(/--color-surface-low:\s*(#[0-9a-fA-F]{3,8})/)
  if (!m) throw new Error(`could not find --color-surface-low for ${mode}`)
  return m[1]
}

const WHITE = '#ffffff'

/** The LIGHT canvas — the shell paints it behind every route (`background: var(--color-canvas)`), and
 *  accent text lands on it wherever a panel does not paint its own surface.
 *
 *  🔴 THE DIMENSION THIS RAIL WAS MISSING. It guaranteed `primary` as accent text against WHITE, which
 *  passes in all 12 schemes (4.83-11.37). But two sites paint accent text straight onto the canvas —
 *  the Memory Studio tab and the inbox settings link — and there the SAME token measures **4.37:1**
 *  (axe and ux-audit agree). Computed across the curated set:
 *
 *      primary → canvas          FAILS in 7 of 12 schemes (4.37-4.41)
 *      primary-emphasis → canvas PASSES in all 12          (worst 4.82, coral 6.0)
 *
 *  So the fix is the pairing the design system already ships, not a new colour: **accent TEXT on the
 *  canvas uses `--color-primary-emphasis`.** Asserted below for every scheme, so a new scheme cannot
 *  land with a canvas-illegible accent, and the two call sites carry the number in a comment. */
function lightCanvas(): string {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  // 🪤 Match the RULE BLOCK, not the first occurrence of the string ".light": the file mentions
  // ".light mode" in a comment 380 characters in, so slicing from `indexOf('.light')` picked up the
  // DARK `--color-canvas: #0f0f0f` and made this rail red at 2.89:1 against the wrong backdrop.
  const block = /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1]
  if (!block) throw new Error('could not find the .light rule block in tokens.css')
  const m = block.match(/--color-canvas:\s*(#[0-9a-fA-F]{3,8})/)
  if (!m) throw new Error('could not find --color-canvas inside the .light block')
  return m[1]
}

describe('scheme contrast: every scheme meets WCAG AA (not just the default)', () => {
  const DARK_SURFACE = darkSurfaceContainer()
  const LIGHT_CANVAS = lightCanvas()
  const HIGH_LIGHT = surfaceHigh('light')
  const HIGH_DARK = surfaceHigh('dark')
  const LOW_LIGHT = surfaceLow('light')
  const LOW_DARK = surfaceLow('dark')

  it('has the full curated scheme set', () => {
    expect(SCHEMES.length).toBeGreaterThanOrEqual(11)
  })

  for (const s of SCHEMES) {
    const c = s.colors
    const primary = c['--color-primary']
    const emphasis = c['--color-primary-emphasis']
    const onPrimary = c['--color-on-primary']
    const info = c['--color-info']

    describe(`scheme '${s.id}'`, () => {
      // LIGHT — the dimension that was broken. primary/emphasis are used both as
      // a filled control (white/onPrimary text over the accent) and as accent
      // TEXT on the white surface; info is accent text on white.
      it('light: primary as filled control (onPrimary over primary) ≥ AA', () => {
        expect(contrast(primary.light, onPrimary.light)).toBeGreaterThanOrEqual(AA)
      })
      it('light: primary-emphasis as accent text on the CANVAS ≥ AA', () => {
        // The shipped rule: on the canvas, accent text uses the emphasis shade. Plain `primary` is
        // deliberately NOT asserted here — it fails in 7 of 12 schemes, which is the finding.
        expect(contrast(emphasis.light, LIGHT_CANVAS)).toBeGreaterThanOrEqual(AA)
      })

      // 🔴 THE THIRD GROUND. A small accent CHIP sits on `--color-surface-high`, not on white and not
      // on the canvas — a status pill, a markdown citation/file chip, a "proposes skill" tag. axe found
      // the knowledge Intents chip at **3.79 dark / 3.22 light** (it was `text-primary/80`, so the
      // alpha made it worse), and computing the whole curated set on that ground shows plain `primary`
      // cannot carry it either:
      //
      //     primary → surface-high            worst **4.26** light (fails in 10 of 12 schemes)
      //     primary-emphasis → surface-high   worst **4.70** light, 7.01 dark — passes all 12
      //
      // Same answer as the canvas dimension above, third ground. Dropping the `/80` alone would NOT
      // have fixed it, which is why the alpha was measured rather than assumed to be the whole story.
      it('light: primary-emphasis as accent text on SURFACE-HIGH ≥ AA', () => {
        expect(contrast(emphasis.light, HIGH_LIGHT)).toBeGreaterThanOrEqual(AA)
      })
      it('dark: primary-emphasis as accent text on SURFACE-HIGH ≥ AA', () => {
        expect(contrast(emphasis.dark, HIGH_DARK)).toBeGreaterThanOrEqual(AA)
      })

      // 🔴 THE FOURTH GROUND, and the one that caught a shipped defect. A dashboard ROW paints
      // `bg-surface-low` — **#f4f6f9** in light, not white — and a row action's label is 15px, so the
      // 4.5 floor applies. Measured on the rendered row: `text-primary` is **4.46:1**, and across the
      // curated set on that ground it fails in **6 of 12** schemes (4.46-4.49) while the emphasis shade
      // clears all twelve (worst 4.92 light, 8.38 dark). Every OTHER `RowAction` tone already passes
      // there (5.59-10.11) — `--color-primary` is the token tuned for brand presence, which is why the
      // emphasis shade exists.
      it('light: primary-emphasis as accent text on SURFACE-LOW ≥ AA', () => {
        expect(contrast(emphasis.light, LOW_LIGHT)).toBeGreaterThanOrEqual(AA)
      })
      it('dark: primary-emphasis as accent text on SURFACE-LOW ≥ AA', () => {
        expect(contrast(emphasis.dark, LOW_DARK)).toBeGreaterThanOrEqual(AA)
      })

      it('light: primary as accent text on white ≥ AA', () => {
        expect(contrast(primary.light, WHITE)).toBeGreaterThanOrEqual(AA)
      })
      it('light: emphasis (hover fill) over onPrimary ≥ AA', () => {
        expect(contrast(emphasis.light, onPrimary.light)).toBeGreaterThanOrEqual(AA)
      })
      it('light: info as accent text on white ≥ AA', () => {
        expect(contrast(info.light, WHITE)).toBeGreaterThanOrEqual(AA)
      })

      // DARK — already AA before this cycle; guard against regression. primary as
      // accent text on the neutral dark container (the common accent-text ground).
      it('dark: primary as accent text on surface-container ≥ AA', () => {
        expect(contrast(primary.dark, DARK_SURFACE)).toBeGreaterThanOrEqual(AA)
      })
      it('dark: info as accent text on surface-container ≥ AA', () => {
        expect(contrast(info.dark, DARK_SURFACE)).toBeGreaterThanOrEqual(AA)
      })
    })
  }
})

// ── The accent CONTAINER pair, across every scheme ─────────────────────────
//
// A selected chip / tinted accent surface is drawn with `--color-primary-container` as its
// background and `--color-on-primary-container` as its ink. Nothing above covered that pair, so
// it was guaranteed in exactly zero schemes — the same population gap the rest of this file exists
// to close, one token pair over.
//
// It matters more than it looks, because the two halves come from DIFFERENT places:
// every scheme supplies its own `primaryContainer`, but `--color-on-primary-container` is a single
// fixed value per mode in `tokens.css`. So the ink does NOT track the scheme. Picking a new accent
// changes the background under a constant foreground, and nothing was checking where that lands.
//
// (What sent me looking: a selected filter chip was drawing the accent as BOTH tint and ink —
// coral on 20% coral, 3.33:1 in light, axe `[serious]`. The fix moves it onto this pair, so the
// pair had better be sound in all 12 schemes rather than just the one I measured.)

/** `--color-on-primary-container` for a mode — read from tokens.css, not restated, so a retint
 *  cannot silently drift this guard. The FIRST occurrence is the default (dark) `:root` block; the
 *  one inside `.light` is the light override. */
function onPrimaryContainer(mode: 'dark' | 'light'): string {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  const all = [...css.matchAll(/--color-on-primary-container:\s*(#[0-9a-fA-F]{3,8})/g)].map((m) => m[1])
  if (all.length < 2) throw new Error(`expected a dark AND a light --color-on-primary-container, found ${all.length}`)
  return mode === 'dark' ? all[0] : all[all.length - 1]
}

describe('accent container: ink on the tinted accent surface meets AA in every scheme', () => {
  const INK = { dark: onPrimaryContainer('dark'), light: onPrimaryContainer('light') }

  it('found both mode values in tokens.css (not vacuously green)', () => {
    expect(INK.dark).toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(INK.light).toMatch(/^#[0-9a-fA-F]{6}$/)
    expect(INK.dark).not.toBe(INK.light)
  })

  for (const s of SCHEMES) {
    const container = s.colors['--color-primary-container']
    describe(`scheme '${s.id}'`, () => {
      it('light: on-primary-container over primary-container ≥ AA', () => {
        expect(contrast(INK.light, container.light)).toBeGreaterThanOrEqual(AA)
      })
      it('dark: on-primary-container over primary-container ≥ AA', () => {
        expect(contrast(INK.dark, container.dark)).toBeGreaterThanOrEqual(AA)
      })
    })
  }
})

// ── The TONAL TINT pair: a label on a TRANSLUCENT tint of the scheme's own primary ────────────
//
// 🔴 THE GROUND THIS FILE NEVER COMPOSITED. Everything above pairs an ink with an OPAQUE ground —
// a hex against a hex. `Button`'s `tonal` variant does something no such pair can describe: it
// paints `bg-primary/15` (and `hover:bg-primary/25`), so its ground is the ACTIVE scheme's primary
// composited over whatever surface the button sits on. The block above even names the failure mode
// in prose — "the ink does NOT track the scheme" — and then checks only `on-primary-container` over
// `primary-container`, the OPAQUE accent pair. The translucent one went unchecked in all 12 schemes,
// which is how `--color-on-primary-tint` shipped as a fixed literal (`#ff6b5b`, the DEFAULT scheme's
// primary) over eleven other schemes' tints.
//
// Composited: **58 of 144** combos below AA, worst **3.07:1** (Mono / dark / surface-container /
// hover), of which 8 were the resting state — two clicks from any dashboard (pick Amber or Mono,
// dark mode, look at "Open Chat").
//
// This rail closes it along all four axes at once: every scheme × both modes × every ground a tonal
// control legitimately sits on × BOTH alphas the variant paints. Three deliberate choices:
//
//  1. THE ALPHAS ARE PARSED FROM THE VARIANT STRING, not restated here. Change `bg-primary/15` and
//     this rail follows; add a third state and it is covered without an edit. A restated constant
//     would have silently kept measuring a tint the component no longer paints.
//  2. THE HOVER ALPHA COUNTS. Hover text is text — WCAG 1.4.3 exempts no state — and this repo
//     already settled the point one variant over: `ghost-accent` justifies its shade by naming
//     `hover:bg-surface-high` as "the HOVER ground" (ui/Button.tsx). Excluding it here would have
//     left 50 of the 58 failures behind a green rail.
//  3. THE INK DECLARATION IS RESOLVED, not restated. The token's VALUE is read from tokens.css and
//     interpreted (`var(…)` / `color-mix(in srgb, …)`) against each scheme, so this rail reds both
//     for a bad shade AND for the structural defect itself — an ink written as a literal resolves to
//     the same value in all 12 schemes and fails wherever a scheme's tint has drifted from coral's.

/** The `--color-on-primary-tint` declaration for a mode, verbatim from tokens.css. Read from source
 *  (never restated) so a retint cannot drift this guard — the discipline the whole file follows. */
function tintInkDecl(mode: 'dark' | 'light'): string {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  const scope = mode === 'dark'
    ? css.slice(0, css.search(/\.light\s*\{/))     // the @theme default block comes first
    : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  const m = scope.match(/--color-on-primary-tint:\s*([^;]+);/)
  if (!m) throw new Error(`could not find --color-on-primary-tint for ${mode}`)
  return m[1].trim()
}

/** The surfaces a tonal button legitimately sits on, per mode, read from tokens.css.
 *  `surface` (page/panel), `surface-low` (dashboard row, cockpit strip) and `surface-container`
 *  (card) are the three the 22 call sites land on. `surface-high`/`-highest` are excluded for the
 *  reason stated at the top of this file: even the AA-verified default coral does not clear them, so
 *  they are not text-bearing accent grounds and testing them would invent a failure. */
function tonalGrounds(mode: 'dark' | 'light'): Array<[string, string]> {
  const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
  const scope = mode === 'dark' ? css.slice(0, css.search(/\.light\s*\{/)) : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
  return ['--color-surface', '--color-surface-low', '--color-surface-container'].map((name) => {
    const m = scope.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`))
    if (!m) throw new Error(`could not find ${name} for ${mode}`)
    return [name.replace('--color-', ''), m[1]] as [string, string]
  })
}

/** Every `bg-primary/NN` alpha the `tonal` variant paints, parsed out of the variant string so the
 *  rail tracks the COMPONENT rather than a copy of it. `hover:`-prefixed = the hover ground. */
function tonalAlphas(): Array<[string, number]> {
  const btn = readFileSync(join(process.cwd(), 'src/ui/Button.tsx'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const tonal = /tonal:\s*'([^']+)'/.exec(btn)?.[1] ?? ''
  return [...tonal.matchAll(/(hover:)?bg-primary\/(\d+)\b/g)]
    .map((m) => [m[1] ? 'hover' : 'rest', Number(m[2]) / 100] as [string, number])
}

type Rgb = [number, number, number]
const toRgb = (hex: string): Rgb => {
  const h = hex.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as Rgb
}
function luminanceRgb([r, g, b]: Rgb): number {
  const chan = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4) }
  return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)
}
function contrastRgb(a: Rgb, b: Rgb): number {
  const la = luminanceRgb(a), lb = luminanceRgb(b)
  const hi = Math.max(la, lb), lo = Math.min(la, lb)
  return (hi + 0.05) / (lo + 0.05)
}
/** What `bg-primary/NN` actually paints: source-over of an alpha'd accent on an OPAQUE surface. */
const composite = (fg: Rgb, bg: Rgb, a: number): Rgb => fg.map((v, i) => a * v + (1 - a) * bg[i]) as Rgb
/** `color-mix(in srgb, C p%, other)`. `in srgb` interpolates the GAMMA-ENCODED channels, and both
 *  operands are opaque, so this is a plain per-channel lerp — no premultiplication, no linearizing.
 *  Kept in floating point (no hex round-trip) so the rail measures what the browser computes. */
const colorMixSrgb = (c: Rgb, p: number, other: Rgb): Rgb => c.map((v, i) => p * v + (1 - p) * other[i]) as Rgb

/** Resolve a tint-ink DECLARATION against one scheme. Understands the two shapes the token is
 *  allowed to take — a reference to a scheme token, or a `color-mix` of one — and falls back to
 *  parsing a bare literal so that a regression to a literal produces a real contrast number rather
 *  than a thrown error (a literal resolves identically in all 12 schemes, which is the defect). */
function resolveTintInk(decl: string, s: (typeof SCHEMES)[number], mode: 'dark' | 'light'): Rgb {
  /** Resolve a token the way the browser does: a scheme override if the scheme carries one (the
   *  appearance store sets those inline on <html>), otherwise the mode's tokens.css value. Both are
   *  legitimate — `--color-primary-emphasis` is per-scheme, `--color-on-primary-container` is one
   *  value per mode — and the difference is exactly what the two assertions below are about. */
  const resolveToken = (varName: string): Rgb => {
    const fromScheme = s.colors[varName]?.[mode]
    if (fromScheme) return toRgb(fromScheme)
    const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
    const scope = mode === 'dark' ? css.slice(0, css.search(/\.light\s*\{/)) : /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
    const m = scope.match(new RegExp(`${varName}:\\s*(#[0-9a-fA-F]{6})`))
    if (!m) throw new Error(`the tonal ink references ${varName}, which is neither a scheme token nor a ${mode} value in tokens.css`)
    return toRgb(m[1])
  }
  const schemeToken = resolveToken
  const ref = /^var\(\s*(--[a-z0-9-]+)\s*\)$/.exec(decl)
  if (ref) return schemeToken(ref[1])
  const mixed = /^color-mix\(\s*in srgb\s*,\s*var\(\s*(--[a-z0-9-]+)\s*\)\s+([\d.]+)%\s*,\s*(black|white)\s*\)$/.exec(decl)
  if (mixed) {
    return colorMixSrgb(schemeToken(mixed[1]), Number(mixed[2]) / 100, mixed[3] === 'black' ? [0, 0, 0] : [255, 255, 255])
  }
  const literal = /^(#[0-9a-fA-F]{6})$/.exec(decl)
  if (literal) return toRgb(literal[1])
  throw new Error(`--color-on-primary-tint (${mode}) is "${decl}" — not a var(), a color-mix(in srgb, var() N%, black|white), or a hex`)
}

describe('tonal tint: the ink clears AA over the scheme\'s OWN composited tint, every scheme × mode × ground × state', () => {
  const DECL = { dark: tintInkDecl('dark'), light: tintInkDecl('light') }
  const ALPHAS = tonalAlphas()
  const MODES = ['dark', 'light'] as const

  type Combo = { scheme: string; mode: 'dark' | 'light'; ground: string; state: string; ratio: number }
  const COMBOS: Combo[] = []
  for (const s of SCHEMES) {
    for (const mode of MODES) {
      const ink = resolveTintInk(DECL[mode], s, mode)
      const primary = toRgb(s.colors['--color-primary'][mode])
      for (const [ground, hex] of tonalGrounds(mode)) {
        for (const [state, alpha] of ALPHAS) {
          COMBOS.push({ scheme: s.id, mode, ground, state, ratio: contrastRgb(ink, composite(primary, toRgb(hex), alpha)) })
        }
      }
    }
  }

  // ── VACUITY FLOOR ──────────────────────────────────────────────────────────────────────────
  // Every input to the 144 assertions below is DERIVED (schemes imported, grounds and the ink
  // declaration parsed from tokens.css, alphas parsed from Button.tsx). Each of those parses can
  // come back empty, and an empty parse makes the loop above iterate NOTHING while every `it` still
  // reports green. So the population is pinned by COUNT here: deleting a scheme, dropping a ground,
  // removing the hover state, or renaming the token cannot turn this rail green by matching nothing.
  it('inspected the full population — 12 schemes × 2 modes × 3 grounds × 2 states', () => {
    expect(SCHEMES.length, 'the curated scheme set').toBe(12)
    expect(ALPHAS.map(([s]) => s), 'both tint states, parsed out of the tonal variant').toEqual(['rest', 'hover'])
    expect(ALPHAS.map(([, a]) => a), 'bg-primary/15 at rest, hover:bg-primary/25 on hover').toEqual([0.15, 0.25])
    for (const mode of MODES) {
      const grounds = tonalGrounds(mode)
      expect(grounds.length, `${mode}: three grounds a tonal control sits on`).toBe(3)
      for (const [, hex] of grounds) expect(hex, `${mode} ground is a real hex`).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(DECL[mode], `${mode}: the ink declaration was found`).toBeTruthy()
    }
    expect(COMBOS.length, '12 × 2 × 3 × 2 — the whole grid was walked').toBe(144)
    expect(new Set(COMBOS.map((c) => `${c.scheme}/${c.mode}/${c.ground}/${c.state}`)).size,
      'and every combo is distinct (no scheme silently measured twice)').toBe(144)
  })

  // ── The structural half: a FROZEN ink is the defect, and only margin excuses one ─────────────
  it('neither mode freezes a hex — the ink is a token reference', () => {
    // The 58 shipped failures were not a badly chosen shade. They were a shade that could not
    // follow the ground it was painted on: one scheme's `--color-primary` hard-coded as the ink for
    // all twelve schemes' tints. A hex that happens to pass today regresses the moment a scheme is
    // added or retinted, so the SHAPE is asserted here and the numbers below.
    for (const mode of MODES) {
      expect(DECL[mode], `${mode}: a frozen hex cannot track 12 schemes' tints`)
        .not.toMatch(/^#[0-9a-fA-F]{3,8}$/)
      expect(DECL[mode], `${mode}: must reference a palette token`).toMatch(/^var\(--color-[a-z-]+\)$/)
    }
  })

  it('dark TRACKS the scheme, because dark has no margin to spare', () => {
    // Dark's tint composites toward the dark surface, so the ground rises with the scheme's own
    // primary and a scheme-independent ink gets overtaken — that is exactly what happened. Even the
    // best fixed value would sit near the floor, so dark must be the scheme's own accent shade:
    // `--color-primary-emphasis`, the shade already shipped for accent text needing more contrast
    // than `--color-primary` (`ghost-accent` in ui/Button.tsx, and the canvas/surface-high/
    // surface-low grounds above). Worst across the grid: 5.40.
    expect(DECL.dark).toBe('var(--color-primary-emphasis)')
  })

  it('light may be scheme-INDEPENDENT only while it keeps a wide margin', () => {
    // 🔑 THE ASYMMETRY IS EARNED, NOT ASSUMED. Light's ink does NOT track the scheme: it is
    // `--color-on-primary-container`, one fixed value per mode. That is safe here for a reason this
    // assertion pins rather than trusts — at that depth the nearest light tint is still 2× the AA
    // floor away, so no scheme can catch it. Lighten it back toward the accent and this reds while
    // plain AA would still pass, which is the point: the next person is told to make it
    // scheme-tracking (as dark is) instead of spending the last of the margin.
    const MARGIN = 6
    const lightWorst = Math.min(...COMBOS.filter((c) => c.mode === 'light').map((c) => c.ratio))
    expect(lightWorst, `a scheme-independent light ink needs headroom, not just AA (got ${lightWorst.toFixed(2)})`)
      .toBeGreaterThanOrEqual(MARGIN)
  })

  it('the no-scheme-applied default is covered by the coral row', () => {
    // Nothing applies a scheme until the user picks one; the bare `@theme` + `.light` values are
    // what a first paint uses. They are coral's, so the coral row above measures the default state
    // too — but only while that stays true, hence this assertion.
    const css = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')
    const coral = SCHEMES.find((s) => s.id === 'coral')!
    const dark = css.slice(0, css.search(/\.light\s*\{/))
    const light = /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? ''
    for (const [mode, scope] of [['dark', dark], ['light', light]] as const) {
      for (const varName of ['--color-primary', '--color-primary-emphasis'] as const) {
        const m = scope.match(new RegExp(`${varName}:\\s*(#[0-9a-fA-F]{6})`))
        expect(m?.[1]?.toLowerCase(), `${mode} ${varName} default must equal coral's`)
          .toBe(coral.colors[varName][mode].toLowerCase())
      }
    }
  })

  for (const s of SCHEMES) {
    describe(`scheme '${s.id}'`, () => {
      for (const mode of MODES) {
        it(`${mode}: on-primary-tint over every tonal ground, at rest AND on hover, ≥ AA`, () => {
          const rows = COMBOS.filter((c) => c.scheme === s.id && c.mode === mode)
          expect(rows.length, 'three grounds × two states').toBe(6)
          const fails = rows.filter((c) => c.ratio < AA)
            .map((c) => `${c.ground}/${c.state} = ${c.ratio.toFixed(2)}`)
          expect(fails, `tonal label below AA on its own tint:\n  ${fails.join('\n  ')}`).toEqual([])
        })
      }
    })
  }
})
