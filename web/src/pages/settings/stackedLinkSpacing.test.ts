import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Stacked TextLinks must not overlap when their row wraps ──────────────────────────────────────
//
// `TextLink` deliberately grows its hit box with `py-1 -my-1`: the padding takes it to 26px tall for
// SC 2.5.8, and the negative margin hands the 4px back so a link inside a sentence keeps the line's
// rhythm (cycle 115 measured that switching to `inline-flex` instead moved 0.83% of the pixels on
// `#/tasks`). The consequence for LAYOUT is that a TextLink bleeds 4px past its box on both sides, so
// a flex container that stacks two of them needs `gap-y > 8px` before there is any real space at all.
//
// `ManageLink` had `gap-y-1` (4px). At 390px the row wraps — the two links do not fit side by side —
// and 4px minus 8px of bleed left the two 26px targets OVERLAPPING:
//
//     first  top=504.5  bottom=530.5
//     second top=526.5  bottom=552.5     ⇒ gap = -4px
//
// which is the `target-size` failure axe reports as serious. Swept every adjacent TextLink pair in the
// app at 390×844: 2 of 2 overlapped, both from this component (it renders once for STT and once for
// TTS); at 1440×900, 0 of 2, because `gap-y` only applies between wrapped lines. So this is the whole
// family, not a sample. `gap-y-3` gives 12 − 8 = 4px of real separation, and the targets pass SC 2.5.8
// on SIZE (26px ≥ 24px) as soon as they stop overlapping.
//
// Desktop rendering is unchanged by construction: at 1440px these sit on one line, where only
// `gap-x-4` applies.

const SRC = join(process.cwd(), 'src')
const voicePanel = () => readFileSync(join(SRC, 'pages/settings/VoicePanel.tsx'), 'utf8')
const textLink = () => readFileSync(join(SRC, 'ui/TextLink.tsx'), 'utf8')

describe('ManageLink stacks its links without overlapping them', () => {
  it('the wrapping row leaves more vertical gap than TextLink bleeds', () => {
    const src = voicePanel()
    const row = src.match(/<div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-\d+">/)?.[0] ?? ''
    expect(row, 'the ManageLink row must exist').toContain('flex-wrap')
    const gap = Number(row.match(/gap-y-(\d+)/)?.[1] ?? 0)
    // Tailwind's scale is 4px per step; TextLink bleeds 4px top AND bottom, so 8px is break-even.
    expect(gap * 4, `gap-y-${gap} = ${gap * 4}px must exceed TextLink's 8px of -my-1 bleed`).toBeGreaterThan(8)
  })

  it('the bleed this guards against is still real', () => {
    // Vacuity guard: if TextLink ever stops bleeding, the reason for the wider gap disappears and this
    // rail should be re-derived rather than left asserting a stale premise.
    expect(textLink(), 'TextLink still grows its hit box with py-1 -my-1').toMatch(/py-1 -my-1/)
  })

  it('the exact pre-fix value does not come back', () => {
    expect(/gap-x-4 gap-y-1"/.test(voicePanel()), 'gap-y-1 reintroduces the -4px overlap').toBe(false)
  })

  it('both links still route through the shared primitive', () => {
    // The overlap was a spacing bug, not a reason to hand-roll: the row must keep rendering TextLinks,
    // whose py-1 -my-1 is what makes them 26px in the first place.
    // Slice to the NEXT top-level function rather than a fixed char count — a comment added inside
    // ManageLink must not be able to push its second link out of the window and fake a pass/fail.
    const src = voicePanel()
    const start = src.indexOf('function ManageLink')
    const next = src.indexOf('\nfunction ', start + 1)
    const row = src.slice(start, next === -1 ? undefined : next)
    expect((row.match(/<TextLink/g) || []).length, 'both links are TextLinks').toBe(2)
    expect(row).toMatch(/size="xs"/)
  })
})
