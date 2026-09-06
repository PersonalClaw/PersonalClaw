import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

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
const CEILING = 789

const RAW_SIZE = /text-\[0?\.[0-9]+rem\]/g

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })

describe('raw arbitrary font sizes only ever decrease', () => {
  it(`stays at or under the ${CEILING} frozen on ratchet day`, () => {
    let count = 0
    for (const abs of walk(SRC)) {
      count += readFileSync(abs, 'utf8').match(RAW_SIZE)?.length ?? 0
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
