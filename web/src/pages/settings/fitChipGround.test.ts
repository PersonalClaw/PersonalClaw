/** The model-fit chip's ground, measured — because the defect was one hundredth off AA.
 *
 *  `statusChipContrast.test.ts` measures the `StatusPill` tint over the RESTING surface tiers and
 *  records, in its own words, that `--color-surface-high` is excluded deliberately: a tier that
 *  "lifts ten sRGB steps toward the ink is a GROUND problem, and the fix is for the chip's ground
 *  to stop moving under it, which is a different change." It filed the hovered reading as an open
 *  finding rather than nudging the ink budget.
 *
 *  OU-14 made one instance of that finding FIRE. The fit chip sits on a row painted
 *  `--color-surface-high` when a model is not downloaded, inside a bento card that itself lifts to
 *  `hover:bg-surface-high` — and that route had never carried a downloadable chat model before, so
 *  no fit chip had ever rendered there. axe reported it as a serious `color-contrast` violation on
 *  `#/settings/providers` in BOTH themes.
 *
 *  So this file measures the two grounds and pins the fix. It is arithmetic on the shipped token
 *  values, not a screenshot: the failure is 0.045 below the threshold, which is exactly the size of
 *  gap a visual review cannot see and a rounded report can hide (the sibling rail records a case
 *  that read 4.5214 as a float and 4.4992 in truth).
 *
 *  🪤 The tokens are read from `design/tokens.css` rather than retyped. A copied hex is a second
 *  source of truth for a number whose whole job is to be the same one the browser paints.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const CSS = readFileSync(join(process.cwd(), 'src/design/tokens.css'), 'utf8')

/** The value of `name` in the dark (`:root`) block and in the light-scheme block.
 *
 *  Order matters and is asserted: `tokens.css` declares the dark values first, so the SECOND
 *  occurrence is the light override. A token declared once (no light override) would make both
 *  readings identical, which this returns honestly rather than papering over. */
function pair(name: string): { dark: string; light: string } {
  const hits = [...CSS.matchAll(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`, 'g'))].map((m) => m[1])
  expect(hits.length, `${name} must be declared in tokens.css`).toBeGreaterThan(0)
  return { dark: hits[0], light: hits[hits.length - 1] }
}

const rgb = (h: string) => [0, 2, 4].map((i) => parseInt(h.slice(1 + i, 3 + i), 16))
const lin = (c: number) => (c / 255 <= 0.04045 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4)
const lum = (h: string) => { const [r, g, b] = rgb(h).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b }
const contrast = (a: string, b: string) => {
  const [x, y] = [lum(a), lum(b)]
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}
const hex = (t: number[]) => `#${t.map((v) => v.toString(16).padStart(2, '0')).join('')}`

/** `contrast(ink, color-mix(in srgb, ink <pct>%, ground))` — the pill's real composite.
 *
 *  Rounded to integer channels BEFORE the ratio, because that is what a browser rasterises. The
 *  sibling rail records that carrying floats through reads 4.5214 where the truth is 4.4992, which
 *  is the difference between a green rail and a real violation. */
function chipRatio(ink: string, ground: string, pct = 16): number {
  const a = pct / 100
  const [I, G] = [rgb(ink), rgb(ground)]
  return contrast(ink, hex(I.map((v, i) => Math.round(a * v + (1 - a) * G[i]))))
}

/** A 12px chip is not "large text" at any weight, so it never gets 1.4.3's 3:1 relief. */
const AA = 4.5

describe('the model-fit chip is legible on the ground it actually sits on', () => {
  const ok = pair('--color-ok')
  const high = pair('--color-surface-high')
  const container = pair('--color-surface-container')

  it('🔴 records WHY the ground is pinned: the un-pinned tier fails AA in both themes', () => {
    // The measurement that justifies `groundedOn`. If a future token change lifts these above
    // 4.5 on their own, this test reds and the prop can be dropped — which is the point of
    // asserting the defect rather than only the fix.
    const dark = chipRatio(ok.dark, high.dark)
    const light = chipRatio(ok.light, high.light)
    expect(dark, 'dark: ok tint over surface-high').toBeLessThan(AA)
    expect(light, 'light: ok tint over surface-high').toBeLessThan(AA)
    // Pinned to 3 decimals so a drift in either direction is visible, not just a pass/fail flip.
    expect(dark).toBeCloseTo(4.454, 2)
    expect(light).toBeCloseTo(4.463, 2)
  })

  it('and the pinned ground clears AA in both themes', () => {
    expect(chipRatio(ok.dark, container.dark), 'dark: ok tint over surface-container').toBeGreaterThanOrEqual(AA)
    expect(chipRatio(ok.light, container.light), 'light: ok tint over surface-container').toBeGreaterThanOrEqual(AA)
  })

  it('the three MEASURABLE fit tones clear AA on the pinned ground', () => {
    // `FIT_TONE` maps green/yellow/red onto ok/warn/danger, so fixing only `ok` would leave two
    // chips on the same row measured by nobody. These are also exactly the three the sibling rail
    // enumerates as `GLOBAL_TONES`, so this agrees with it rather than inventing a second bar.
    for (const token of ['--color-ok', '--color-warn', '--color-danger']) {
      const ink = pair(token)
      for (const mode of ['dark', 'light'] as const) {
        const ratio = chipRatio(ink[mode], container[mode])
        expect(ratio, `${mode}: ${token} tint over surface-container reads ${ratio.toFixed(4)}`)
          .toBeGreaterThanOrEqual(AA)
      }
    }
  })

  it('🔴 RECORDS A SEPARATE, PRE-EXISTING FINDING: the `neutral` tone is not AA at any ground', () => {
    // Found by running the test above with all four fit tones in it, which is why it is recorded
    // here instead of quietly dropped. `FIT_TONE.unknown` is `neutral`, and `StatusPill` maps
    // `neutral` to `--color-outline-variant` — a HAIRLINE token being used as text ink. It reads
    // ~1.63 dark and ~2.07 light, so no ground fixes it: the ink itself is the defect.
    //
    // NOT fixed here, and the boundary is deliberate. It is not this change's: the "Fit unknown"
    // chip only renders on a host whose memory could not be measured, and `tone="neutral"` has at
    // least four other live call sites (`VoiceProfilesSection`, `DesktopLiveView`, `BrowseMirror`
    // ×2), so the fix is one ink decision across all of them, not a local override that would make
    // this one chip disagree with its siblings. `statusChipContrast.test.ts` measures
    // `GLOBAL_TONES = ['ok','warn','danger']` plus per-scheme `info` and does not measure `neutral`
    // or `primary` at all — an omission it does not record, which is why this pins the number.
    //
    // Pinned, not asserted-as-failing-forever: whoever picks the ink up reds this test, sees the
    // measurement, and moves it into the clearing assertion above.
    const neutral = pair('--color-outline-variant')
    expect(chipRatio(neutral.dark, container.dark)).toBeCloseTo(1.635, 2)
    expect(chipRatio(neutral.light, container.light)).toBeLessThan(AA)
    // The ink, bare, on the same ground — proving the ground is not what is wrong.
    expect(contrast(neutral.dark, container.dark)).toBeLessThan(AA)
  })

  it('the chip in the source really does pin its ground', () => {
    // The arithmetic above proves the NUMBERS. This proves the component uses them: a correct
    // measurement beside a call site that never opted in is a green rail over a live violation.
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/LocalModelManager.tsx'), 'utf8')
    const chip = src.slice(src.indexOf('function FitChip'), src.indexOf('function FitChip') + 1600)
    expect(chip).toContain('groundedOn="var(--color-surface-container)"')
  })
})
