/**
 * Layer 1, the raw palette: how a colour value gets its name.
 *
 * A palette token names a colour by what it is, never by what it is for: `--<family>-<step>`, where
 * the family is the colour's hue band and the step is its OKLCH lightness × 1000. So `--gray-0` is
 * black, `--gray-1000` is white, and `--red-709` is a red about as light as a mid-tone. The meaning
 * (canvas, primary, danger) lives one layer up, in the semantic tokens, which are written only in
 * terms of these (`web/DESIGN.md` §2, "The token layers").
 *
 * The values are the contrast-tuned ones the app already ships, so the scales are sparse: a step
 * exists because a semantic token needs exactly that value, and none is invented to fill a gap.
 *
 * One namespace for every sheet. The base sheet (`tokens.css`) and every scheme sheet
 * (`schemes/<id>.css`) name their raw values with this function, so one name always means one
 * value. A scheme sheet declares only the steps the base sheet lacks.
 */

export const PALETTE_FAMILIES = [
  'gray', 'red', 'orange', 'amber', 'yellow', 'lime', 'green', 'teal', 'cyan', 'blue', 'indigo', 'violet', 'pink',
] as const
export type PaletteFamily = (typeof PALETTE_FAMILIES)[number]

/** A layer-1 token name. Components never read one (`tokenLint.test.ts`). */
export const PALETTE_TOKEN = new RegExp(`^--(?:${PALETTE_FAMILIES.join('|')})-\\d{1,4}$`)

/** Below this OKLCH chroma a colour reads as a neutral, whatever its hue angle says. */
const GRAY_CHROMA = 0.025

/** Each hue band's upper bound in OKLCH degrees, exclusive. Pink wraps through 0. */
const HUE_BANDS: [PaletteFamily, number][] = [
  ['pink', 15], ['red', 40], ['orange', 65], ['amber', 90], ['yellow', 115], ['lime', 140],
  ['green', 170], ['teal', 195], ['cyan', 230], ['blue', 275], ['indigo', 295], ['violet', 335],
  ['pink', 360],
]

/** `#abc` / `#AABBCC` → `#aabbcc`. Throws on anything else: a palette value is always opaque hex. */
export function normalizeHex(hex: string): string {
  const h = hex.trim().toLowerCase()
  if (/^#[0-9a-f]{6}$/.test(h)) return h
  if (/^#[0-9a-f]{3}$/.test(h)) return `#${h[1]}${h[1]}${h[2]}${h[2]}${h[3]}${h[3]}`
  throw new Error(`not an opaque hex colour: ${hex}`)
}

function linear(c: number): number {
  const v = c / 255
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
}

/** OKLCH of an sRGB hex (Björn Ottosson's matrices). */
export function oklch(hex: string): { l: number; c: number; h: number } {
  const n = normalizeHex(hex)
  const r = linear(parseInt(n.slice(1, 3), 16))
  const g = linear(parseInt(n.slice(3, 5), 16))
  const b = linear(parseInt(n.slice(5, 7), 16))
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
  const h = (Math.atan2(B, A) * 180) / Math.PI
  return { l: L, c: Math.sqrt(A * A + B * B), h: h < 0 ? h + 360 : h }
}

export function paletteFamily(hex: string): PaletteFamily {
  const { c, h } = oklch(hex)
  if (c < GRAY_CHROMA) return 'gray'
  return HUE_BANDS.find(([, top]) => h < top)![0]
}

/**
 * The palette name for `hex`, given the names already taken (name → hex).
 *
 * A value already in `taken` keeps its name. A new one takes its own step (lightness × 1000,
 * rounded) when that is free, and otherwise the nearest free step, so two near-identical greys
 * (`#1e1f20`, `#1f1f1f`) get neighbouring names rather than one name for two values. Names are
 * assigned in the order the caller asks, base sheet first, so adding a scheme never renames a
 * value an existing sheet already uses.
 */
export function paletteName(hex: string, taken: Map<string, string>): string {
  const value = normalizeHex(hex)
  for (const [name, v] of taken) if (v === value) return name
  const family = paletteFamily(value)
  const exact = oklch(value).l * 1000
  const own = Math.max(0, Math.min(1000, Math.round(exact)))
  const candidates = Array.from({ length: 1001 }, (_, i) => i)
    .sort((a, b) => Math.abs(a - exact) - Math.abs(b - exact) || a - b)
  for (const step of [own, ...candidates]) {
    const name = `--${family}-${step}`
    if (!taken.has(name)) {
      taken.set(name, value)
      return name
    }
  }
  throw new Error(`no free ${family} step for ${value}`)
}
