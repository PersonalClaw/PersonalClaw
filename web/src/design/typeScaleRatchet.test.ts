// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'

// ── Arbitrary font sizes are frozen debt, not a pattern (AUD-NZ13, the ratchet) ────────
//
// tokens.css ships the type scale as data-type roles (display-* … caption): each role sets
// font-size + line-height + variable-font weight TOGETHER, so a size never travels without
// its pairing. Raw Tailwind arbitrary values like `text-[0.8125rem]` bypass all of that —
// they pin a size with no role, no weight, no line-height, and they are why the scale
// drifted in the first place (see typeScaleRoles.test.ts for the off-scale shapes that
// already crept in). The wholesale migration is mechanical but large, so this rail
// ratchets instead of banning: the count of raw sub-1rem arbitrary sizes may only FALL.
//
// CEILING is the measured count on the day the ratchet landed. New code must use the
// data-type roles from tokens.css. When you migrate existing sites onto roles, LOWER the
// ceiling to the new count in the same change — never raise it.
// 669 → 653: onboarding's import step moved whole onto the roles. `c2a599568` measured 671 — two
// over this ceiling already, from elsewhere — so 653 is the tree's measured count, not 669 − 18.
// 653 → 643: the surfaces added since moved onto the roles (the callback panel, the heartbeat queue,
// the attachment chips and their preview, the agent's model and hook notes, the palette's empty
// state and footer). `ecaf8b673` measured 674, 21 over, because those surfaces landed on raw sizes;
// 643 is the tree's measured count after moving 31.
// 643 → 641: the line under each of the agents page's runtime groups is on `body-s`. The two lines
// added for a runtime that is not ready and for a failed lookup had landed on raw sizes
// (`5fa5fef9a` measured 644), and the empty-group line that shares their slot moved with them.
const CEILING = 641

const RAW_SIZE = /text-\[0?\.[0-9]+rem\]/g

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] => filesUnder(d, (n) => /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n))

describe('raw arbitrary font sizes only ever decrease', () => {
  it(`stays at or under the ${CEILING} frozen on ratchet day`, () => {
    let count = 0
    for (const abs of walk(SRC)) {
      count += readSource(abs).match(RAW_SIZE)?.length ?? 0
    }
    expect(
      count,
      [
        `${count} raw arbitrary font sizes (ceiling ${CEILING}). New text sits on the type`,
        'scale via data-type roles from design/tokens.css, not text-[…rem]:',
        '  · 0.75rem   → data-type="caption"  (chip/badge/timestamp micro-text)',
        '  · 0.8125rem → data-type="body-s" for prose, data-type="label-s" for chip/meta',
        '  · 0.9375rem → data-type="body-m" / "label-m" / "title-m" by intent',
        'The role also carries line-height + weight — drop the leading-*/font-* utilities',
        'it makes redundant. If you migrated sites AWAY from raw sizes, lower CEILING to',
        'the new count in this same change.',
      ].join('\n'),
    ).toBeLessThanOrEqual(CEILING)
  })
})
