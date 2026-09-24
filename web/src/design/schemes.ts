/**
 * Curated color SCHEMES. A scheme is a named set of color-token overrides — it
 * retints the brand + glow/gradient identity (accent, primary, focus ring, the
 * spatial-backdrop glow) while leaving surfaces / content / layout coherent. The
 * user picks one; advanced users fork the current scheme into a saved custom one.
 *
 * Each scheme provides {dark, light} for the accent-driving tokens. The keys map
 * 1:1 to tokenRegistry color varNames; the appearance store applies them as
 * overrides (so a scheme = a known override set, and "reset" = the default
 * scheme 'lavender').
 */

export interface Scheme {
  id: string
  label: string
  /** decorative emoji / `icon:<LucideName>` token — only saved custom themes carry one */
  emoji?: string
  /** representative accent swatch (dark, light) for the picker tile */
  swatch: { dark: string; light: string }
  /** color-token overrides keyed by CSS varName → {dark, light} */
  colors: Record<string, { dark: string; light: string }>
}

/** Build the brand+glow override block from a small accent spec, so each scheme
 *  is defined by a few colors but drives the full accent identity coherently. */
function scheme(id: string, label: string, s: {
  primary: [string, string]          // [dark, light]
  primaryEmphasis: [string, string]
  onPrimary: [string, string]
  primaryContainer: [string, string]
  secondary: [string, string]
  gradient: [string, string, string, string]  // grad-1..4 (shared dark/light here for the wave)
  glowA: [string, string]
  glowB: [string, string]
  info: [string, string]
}): Scheme {
  const dl = (d: string, l: string) => ({ dark: d, light: l })
  return {
    id, label,
    swatch: { dark: s.primary[0], light: s.primary[1] },
    colors: {
      '--color-primary': dl(s.primary[0], s.primary[1]),
      '--color-primary-emphasis': dl(s.primaryEmphasis[0], s.primaryEmphasis[1]),
      '--color-on-primary': dl(s.onPrimary[0], s.onPrimary[1]),
      '--color-primary-container': dl(s.primaryContainer[0], s.primaryContainer[1]),
      '--color-secondary': dl(s.secondary[0], s.secondary[1]),
      '--color-info': dl(s.info[0], s.info[1]),
      '--grad-1': dl(s.gradient[0], s.gradient[0]),
      '--grad-2': dl(s.gradient[1], s.primary[1]),
      '--grad-3': dl(s.gradient[2], s.glowB[1]),
      '--grad-4': dl(s.gradient[3], s.gradient[3]),
      '--glow-a': dl(s.glowA[0], s.glowA[1]),
      '--glow-b': dl(s.glowB[0], s.glowB[1]),
    },
  }
}

/**
 * ── THE LIGHT ACCENT PAIR WAS RETUNED ACROSS ALL 12 SCHEMES (#3503) ────────────────────────────
 *
 * 🔴 THE OLD VALUES WERE CHOSEN AGAINST A GROUND NO ACCENT CHIP EVER HAS. The rule this file used
 * to state was "≥4.5:1 as white-text button fill AND as text on white" — both measured against the
 * BARE tier. A status chip paints its tone twice, as the ink and as a 16% wash of itself, so the
 * ground is a translucent tint of the ink and every percent drags the two together. Swept the way
 * `design/statusChipContrast.test.ts` sweeps every other tone (12 schemes × 2 modes × 4 resting
 * tiers, 8-bit quantized composite), `--color-primary` was under AA in **42 of 96 cells**, worst
 * **3.4870** (rose/light/canvas). **Every one of the 42 was LIGHT mode**; dark passed 48/48, and is
 * untouched here. `coral` — the default, live on Settings — read 3.5229. This is the same mistake
 * the `--color-info` retune corrected one token over, and `--color-primary-emphasis` was measured as
 * a substitute and rejected: it only reaches 20/96.
 *
 * THE METHOD IS THE `--color-info` PRECEDENT: OKLCH, hue angle held exactly, chroma held (0.908–1.000
 * of the original, given up only where sRGB cannot hold the requested lightness), lightness moved the
 * MINIMUM needed to clear 4.5 on the worst resting tier. Deliberately not a flattening: each scheme
 * moves by its own amount (ΔL −0.017 lavender to −0.074 rose; `mono` needed nothing), so the twelve
 * identities stay twelve.
 *
 *     scheme     primary          emphasis           chip ink on its own 16% tint, light
 *                                                    worst ground   before -> after
 *     coral      #c8452e->#b12e18  #a33922->#8d240b   canvas            3.5229 -> 4.5007
 *     honey      #9d6614->#885500  #985e10->#834e00   canvas            3.5816 -> 4.5367
 *     jade       #0b7f75->#006c64  #0a7268->#006057   canvas            3.5800 -> 4.5167
 *     ember      #c5482e->#af3218  #b0432c->#9a2e17   canvas            3.5201 -> 4.5055
 *     lavender   #6a4fd0->#6549ca  #563bbf->#5235b9   canvas            4.2072 -> 4.5178
 *     ocean      #1668d8->#005ac9  #0e4fa8->#004298   canvas            3.8291 -> 4.5078
 *     forest     #1a824a->#006f3b  #157a44->#006736   canvas            3.5832 -> 4.5188
 *     rose       #d22b6f->#b6005a  #bf2f6a->#a50b55   canvas            3.4870 -> 4.5258
 *     amber      #a5611c->#915000  #a15817->#8d4800   canvas            3.5818 -> 4.5178
 *     slate      #5a6a82->#53627a  #46556e->#3f4e66   canvas            4.0589 -> 4.5208
 *     mono       #3a3a3a  (kept)   #242424  (kept)    canvas            7.8001 -> 7.8001
 *     phosphor   #1a7f3c->#006f2f  #136230->#005324   canvas            3.7170 -> 4.5426
 *
 * 🔑 WHY `primaryEmphasis` MOVED TOO, BY THE SAME ΔL. Emphasis is the hover fill
 * (`Button.tsx`'s `bg-primary hover:bg-primary-emphasis`) and the accent-text ink for grounds plain
 * primary cannot carry, and the invariant behind both — stated in `accentOnCanvas.test.ts` — is that
 * it sits *further from the ground than primary in either mode*. Retuning primary ALONE breaks that:
 * it crosses emphasis outright in **6 of 12** schemes (honey, jade, ember, forest, rose, amber), so
 * hovering a filled accent button would make it *lighter*; and on `coral`, the default, the step
 * collapses from ΔL 0.0776 to 0.0122 — a hover nobody can see. No test catches either, which is why
 * they are stated here. Carrying emphasis by the same delta preserves every scheme's own step
 * exactly, and it is monotonically safe: every emphasis ground in `schemeContrast.test.ts` is a LIGHT
 * ground, so darkening only gains (worst light cell 4.70 → 6.06).
 *
 * COLLATERAL, MEASURED RATHER THAN ASSUMED — every surface reading this token moves:
 *
 *   · white-on-primary button fill        4.83 → 6.42 worst (it GAINS; `--color-on-primary` is
 *                                         `#ffffff` in light in all 12, so a darker fill only helps)
 *   · primary as text on white            4.83 → 6.42 worst
 *   · focus ring on every light tier      3.9986 → 5.1180 worst (SC 1.4.11 floor 3, pin 3.5)
 *   · session-map mark on `--color-rail`  4.3714 → 5.5952 worst (non-text floor 3)
 *   · `bg-primary/15` tonal ground under  12.07 → 11.44 worst at rest, 10.36 → 9.42 on hover.
 *     `--color-on-primary-container` ink   This is the ONE family that loses, because the ink is dark
 *                                         and the ground darkens with the token. Floor is 4.5 and the
 *                                         margin rail wants 6, so 9.42 is not close.
 *   · `::highlight(pc-find)` @34% and     7.99 → 7.14 and 9.54 → 8.84 worst. Same direction, same
 *     `.kl-highlight` @26%, on-surface ink verdict.
 *   · 50%-tint focus-ring counter-example 2.7454 → 2.7454 global max (the inverted assertion in
 *                                         `focusRingContrast.test.ts` needs it UNDER 3; the max is
 *                                         pinned by a dark `mono` cell and does not move)
 *   · `--grad-2`'s LIGHT stop             moves with `primary[1]` by construction (see `scheme()`
 *                                         below) — the brand wordmark, ClawMark and the composer's
 *                                         conic ring. Decorative, no text on any of them.
 *   · glow-a / glow-b                     deliberately NOT moved: this file's own rule is that
 *                                         glow/gradient stay brighter because they are decorative.
 *
 * There is no dark-in-light-mode ground carrying primary ink anywhere in the tree — every light
 * surface tier is #e6eaef–#ffffff, the xterm theme is mode-aware, and the only true black scrims
 * carry no text — so darkening is monotone for every ink site. Verified by census, not assumed.
 */
export const SCHEMES: Scheme[] = [
  // DEFAULT — PersonalClaw coral/terracotta. Warm, energetic, off-Gemini; the
  // ownable accent. Neutral surfaces stay from tokens.css; this drives only the
  // accent identity (primary/focus/loaders/glow), so the whole app re-tints warm.
  // Light primary/emphasis/info mirror tokenRegistry's AA-verified shades — keep in sync
  // (`schemeContrast.test.ts` now asserts all three declarations agree, rather than asking).
  // Glow/gradient stay on the brighter coral: decorative, not text-bearing.
  scheme('coral', 'Coral', {
    primary: ['#ff6b5b', '#b12e18'], primaryEmphasis: ['#ff9a86', '#8d240b'],
    onPrimary: ['#3f1008', '#ffffff'], primaryContainer: ['#5a1d12', '#ffe0d6'],
    secondary: ['#ffb454', '#cf7a23'], info: ['#629efe', '#1059bc'],
    gradient: ['#c85a48', '#ff6b5b', '#ff9a7a', '#ffb454'],
    glowA: ['#ff6b5b', '#e85a3f'], glowB: ['#ff9a7a', '#e07a54'],
  }),
  // Honey — warm like coral but golden/muted; cozy, understated.
  scheme('honey', 'Honey', {
    primary: ['#f2a93b', '#885500'], primaryEmphasis: ['#ffca7a', '#834e00'],
    onPrimary: ['#3a2504', '#ffffff'], primaryContainer: ['#523611', '#ffe9c2'],
    secondary: ['#e8785a', '#c2503a'], info: ['#50a1fa', '#0057c2'],
    gradient: ['#b0832f', '#f2a93b', '#ffca7a', '#e8785a'],
    glowA: ['#f2a93b', '#c17d18'], glowB: ['#ffca7a', '#d89a4a'],
  }),
  // Jade — calm, professional, cool-but-not-blue; the grounded counterpart.
  scheme('jade', 'Jade', {
    primary: ['#2dd4bf', '#006c64'], primaryEmphasis: ['#7fe8da', '#006057'],
    onPrimary: ['#04231f', '#ffffff'], primaryContainer: ['#0c3b35', '#cbf5ee'],
    secondary: ['#4e9ff8', '#1668d8'], info: ['#50a1fa', '#0057c2'],
    gradient: ['#2a9e90', '#2dd4bf', '#7fe8da', '#4e9ff8'],
    glowA: ['#2dd4bf', '#0d9488'], glowB: ['#7fe8da', '#3aa898'],
  }),
  // Ember — near-monochrome + a single warm spark (max restraint, ChatGPT-quiet).
  scheme('ember', 'Ember (mono + spark)', {
    primary: ['#ff7a5c', '#af3218'], primaryEmphasis: ['#ffa98f', '#9a2e17'],
    onPrimary: ['#2a0f08', '#ffffff'], primaryContainer: ['#3a2018', '#f0ddd6'],
    secondary: ['#9a9a96', '#5a5a56'], info: ['#99a0a8', '#565c64'],
    gradient: ['#6a6560', '#ff7a5c', '#b09a92', '#8a8580'],
    glowA: ['#ff7a5c', '#d1543a'], glowB: ['#b0a49e', '#8a7f78'],
  }),
  // Legacy lavender scheme (matches the pre-rebrand tokenRegistry defaults).
  scheme('lavender', 'Lavender', {
    primary: ['#9d8bff', '#6549ca'], primaryEmphasis: ['#b6bdff', '#5235b9'],
    onPrimary: ['#21134f', '#ffffff'], primaryContainer: ['#2e2168', '#e7deff'],
    secondary: ['#4e8ff8', '#1668d8'], info: ['#619eff', '#0057c2'],
    gradient: ['#8e75b2', '#9d8bff', '#c597ff', '#d8627e'],
    glowA: ['#9d8bff', '#6a4fd0'], glowB: ['#c597ff', '#9168c0'],
  }),
  scheme('ocean', 'Ocean', {
    primary: ['#4aa8ff', '#005ac9'], primaryEmphasis: ['#86c6ff', '#004298'],
    onPrimary: ['#04243f', '#ffffff'], primaryContainer: ['#0e3358', '#d8ecff'],
    secondary: ['#28c2c8', '#0a8f95'], info: ['#4aa8ff', '#0057c2'],
    gradient: ['#3a7bb0', '#4aa8ff', '#7fd0ff', '#28c2c8'],
    glowA: ['#4aa8ff', '#1668d8'], glowB: ['#7fd0ff', '#3a92c8'],
  }),
  scheme('forest', 'Forest', {
    primary: ['#4fc97f', '#006f3b'], primaryEmphasis: ['#86e0a6', '#006736'],
    onPrimary: ['#06280f', '#ffffff'], primaryContainer: ['#0f3a22', '#d6f3e0'],
    secondary: ['#9bcf3a', '#5f8f15'], info: ['#3ab0a0', '#02695e'],
    gradient: ['#3a8f5e', '#4fc97f', '#9bdf6f', '#d8c24a'],
    glowA: ['#4fc97f', '#1f9b58'], glowB: ['#9bdf6f', '#5f9f3a'],
  }),
  scheme('rose', 'Rose', {
    primary: ['#ff7eb0', '#b6005a'], primaryEmphasis: ['#ffb0cf', '#a50b55'],
    onPrimary: ['#3f0a22', '#ffffff'], primaryContainer: ['#5a1638', '#ffdcec'],
    secondary: ['#c597ff', '#8b5cd8'], info: ['#8496ff', '#4350c6'],
    gradient: ['#b0567e', '#ff7eb0', '#ffb0cf', '#c597ff'],
    glowA: ['#ff7eb0', '#d8407e'], glowB: ['#ffa6c8', '#c2607e'],
  }),
  scheme('amber', 'Amber', {
    primary: ['#ffb454', '#915000'], primaryEmphasis: ['#ffd08a', '#8d4800'],
    onPrimary: ['#3f2404', '#ffffff'], primaryContainer: ['#5a3810', '#ffe8c8'],
    secondary: ['#f55e57', '#c8362f'], info: ['#ffb454', '#8d4d01'],
    gradient: ['#b07a3a', '#ffb454', '#ffd08a', '#f55e57'],
    glowA: ['#ffb454', '#cf7a23'], glowB: ['#ffce7f', '#d89a4a'],
  }),
  scheme('slate', 'Slate', {
    primary: ['#9aa6b8', '#53627a'], primaryEmphasis: ['#c0c8d4', '#3f4e66'],
    onPrimary: ['#1a212e', '#ffffff'], primaryContainer: ['#2a3340', '#dde3ec'],
    secondary: ['#7f9cff', '#4f6fd8'], info: ['#7f9cff', '#3955bc'],
    gradient: ['#6a7588', '#9aa6b8', '#c0c8d4', '#7f8cb0'],
    glowA: ['#9aa6b8', '#5a6a82'], glowB: ['#c0c8d4', '#7a86a0'],
  }),
  scheme('mono', 'Mono', {
    primary: ['#d4d4d4', '#3a3a3a'], primaryEmphasis: ['#f0f0f0', '#242424'],
    onPrimary: ['#171717', '#ffffff'], primaryContainer: ['#333333', '#e4e4e4'],
    secondary: ['#a0a0a0', '#5a5a5a'], info: ['#a0a0a0', '#5a5a5a'],
    gradient: ['#8a8a8a', '#d4d4d4', '#f0f0f0', '#a0a0a0'],
    glowA: ['#d4d4d4', '#5a5a5a'], glowB: ['#f0f0f0', '#8a8a8a'],
  }),
  // PHOSPHOR — the mono-green CRT family, the base scheme for the retro-terminal
  // personality (PERSONALITY-THEMES §S1). Light-mode values are darkened greens
  // that hold ≥4.5:1 as a white-text button fill, as text on white, AND as chip
  // ink over their own 16% tint — the third ground is #3503's correction to this
  // rule, and it is the binding one (schemeContrast + statusChipContrast enforce
  // them). A personality carries no colour of its own, so this pair is the
  // retro-terminal accent too.
  scheme('phosphor', 'Phosphor', {
    primary: ['#3ddc74', '#006f2f'], primaryEmphasis: ['#7bf5a5', '#005324'],
    onPrimary: ['#062211', '#ffffff'], primaryContainer: ['#0f3d22', '#d4f7e0'],
    secondary: ['#8ce6a8', '#2b8b52'], info: ['#5cd6c0', '#00695b'],
    gradient: ['#0f6b38', '#3ddc74', '#7bf5a5', '#2b8b52'],
    glowA: ['#3ddc74', '#1a7f3c'], glowB: ['#7bf5a5', '#2b8b52'],
  }),
]

export const DEFAULT_SCHEME = 'coral'
export function getScheme(id: string): Scheme | undefined { return SCHEMES.find((s) => s.id === id) }

/** Token groups that constitute the COLOR scheme (everything else — layout,
 *  shape, 3D backdrop, motion — is a separate concern, not part of a scheme). */
export const COLOR_GROUPS = ['Brand', 'Surfaces', 'Content', 'Semantic', 'Glow & gradient']
/** Non-color customization groups, surfaced as their own controls. */
export const BACKDROP_GROUPS = ['3D surface', 'Motion', 'Elevation & glass']
export const TYPOGRAPHY_GROUPS = ['Typography']
export const LAYOUT_GROUPS = ['Layout', 'Shape']
