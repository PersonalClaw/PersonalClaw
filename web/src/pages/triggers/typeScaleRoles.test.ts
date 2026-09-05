import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Text sits on the type scale, through data-type roles (AUD-NZ13, minimal slice) ────
//
// tokens.css defines the scale (1.75 / 1.5 / 1.25 / 1.0625 / 0.9375 / 0.8125 / 0.75rem)
// and the caption tier's own comment says it exists to eliminate "sub-0.8125rem drift".
// Two shapes had drifted in the triggers pages anyway:
//   · WeekGridView's day cells used text-[0.6875rem] — BELOW the 0.75rem caption floor;
//   · three detail values used text-[0.875rem] — a size BETWEEN body-s and body-m that is
//     on no tier at all, so it renders as almost-but-not body text.
// This pins the fixed slice. The wholesale raw-size → role migration across these pages
// is tracked separately (it is mechanical but large); this rail only keeps the two
// OFF-SCALE shapes from coming back.

const F = (rel: string) => readFileSync(join(process.cwd(), 'src', 'pages', 'triggers', rel), 'utf8')

describe('triggers pages carry no off-scale font sizes', () => {
  it('the week grid day cell rides the caption tier', () => {
    const src = F('WeekGridView.tsx')
    expect(src, 'the cell announces its role').toContain('data-type="caption"')
    expect(src, 'the sub-floor literal is gone').not.toContain('text-[0.6875rem]')
  })

  it('no triggers file uses a size that sits on no tier', () => {
    for (const rel of ['WeekGridView.tsx', 'TriggersListPage.tsx', 'StoreTriggerDetail.tsx']) {
      const src = F(rel)
      expect(src, `${rel}: 0.875rem is between body-s and body-m — on no tier`)
        .not.toContain('text-[0.875rem]')
      expect(src, `${rel}: nothing may dip under the caption floor`)
        .not.toMatch(/text-\[0\.(6|7[0-4])\d*rem\]/)
    }
  })
})
