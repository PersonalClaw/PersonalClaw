import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The skills mode toggle must FOLD, not be crushed ──────────────────────────────────────────
//
// `Segmented`'s own comment already diagnosed this and prescribed the fix:
//
//   "Tabs are `shrink-0`. `size-8` / `px-m` set a tab's size but NOT its floor, so in a constrained
//    slot the flex parent squeezed them: measured on the #/skills header at 390px … the two icon tabs
//    rendered 15.3 × 32 instead of 32 × 32 — under the 24px SC 2.5.8 minimum … Overflow is the job of
//    `collapse` ('scroll' / 'menu'), not of silently crushing every target: a strip that cannot fit
//    should scroll or fold."
//
// This call site never passed the prop, so it kept crushing. Measured on the seeded `demo-home`
// header, strip box vs its two `shrink-0` 32px tabs, worst reachable width per option:
//
//              BEFORE (no collapse)              AFTER (collapse='menu')
//     1440px   strip 197×40   61px               strip 197×40   61px      ← unchanged
//      390px   strip  41×40   16px  ✗            folded pill    33px  ✓
//      320px   strip   8×40    1px  ✗✗           folded pill     1px  ✗   ← see below
//
// The tabs are `shrink-0` and the wrapper is `min-w-0`, so the strip absorbed the entire squeeze while
// its children kept their size and overflowed it — 8px of container holding 66px of tabs.
//
// 🔴 320px IS STILL BROKEN AND THIS CHANGE IS NOT THE FIX FOR IT. Once folded, the pill (32×32 at
// x=110) is COVERED by `button[aria-label="More actions"]` (40×40) — the overflow menu. That is the
// sixth measured instance of the header-overlap family, whose other members are `knowledge-detail`'s back button,
// `apps`' listbox trigger, `loops`/`loop`'s Granularity and `code`'s Project kind. **Do not "finish"
// this locally**: the family needs a decision on what the header row does when it cannot fit, and this file
// only removes the crush that was stacked on top of it.
//
// 🪤 The measurement that produced those numbers is easy to get wrong twice over, and I got it wrong
// both ways before it was right:
//   · `collapse` makes `Segmented` render a HIDDEN COPY of itself FIRST in DOM order (a `-z-10
//     invisible pointer-events-none aria-hidden` probe that measures intrinsic width). A plain
//     `querySelector` returns the PROBE, and every hit test then reads 1×1 — including at 1440px,
//     which looked like this change had broken the desktop case. The harness's ancestor walk
//     (display/visibility/opacity/inert/aria-hidden up the chain) is what selects the real strip.
//   · `elementFromPoint` returns the deepest PAINT node, which for an icon button is an `svg`/`circle`.
//     Resolve upward to the nearest `<button>` before deciding who owns a pixel.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const stripComments = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

/** `ModeToggle` only, comments stripped — the block below quotes `collapse` while explaining it. */
function modeToggle(): string {
  const src = stripComments(read('pages/skills/SkillsPage.tsx'))
  const at = src.indexOf('function ModeToggle(')
  expect(at, 'ModeToggle must still exist').toBeGreaterThan(-1)
  const end = src.indexOf('\nfunction ', at + 1)
  expect(end, 'ModeToggle must terminate before the next top-level function').toBeGreaterThan(at)
  return src.slice(at, end)
}

describe('the skills mode toggle folds instead of being crushed', () => {
  // It is the header's view switch now, a `HeaderSegmented` in the header's cluster, which passes the
  // collapse strategy and the icon-only density itself, so neither can be dropped at this call site.
  const member = () => {
    const src = stripComments(read('ui/HeaderActions.tsx'))
    const at = src.indexOf('export function HeaderSegmented(')
    expect(at, 'HeaderSegmented must still exist').toBeGreaterThan(-1)
    return src.slice(at, src.indexOf('\nexport function ', at + 1))
  }

  it('reads its subject (a rail over nothing asserts nothing)', () => {
    const fn = modeToggle()
    expect(fn.length, 'the slice is empty — every assertion below is vacuous').toBeGreaterThan(120)
    expect(fn, 'it must be the header view switch').toMatch(/<HeaderSegmented\b/)
    expect(fn, 'with both options').toMatch(/'installed'[\s\S]*'browse'/)
  })

  it('passes a collapse strategy, so a constrained slot folds rather than squeezing', () => {
    expect(
      member(),
      "without `collapse` the strip absorbs the whole squeeze: measured 8×40 of container holding 66px " +
        'of `shrink-0` tabs at 320px, with the second tab 1×1 reachable',
    ).toMatch(/<Segmented\b[^>]*collapse="menu"/)
  })

  it('keeps icon-only density, which is what makes the folded pill fit a narrow header', () => {
    // `CollapsedSegmented`'s docstring is explicit: labelled the pill is ~119px and "still overflowed a
    // phone header's ~42px control rail"; icon-only it is ~32-40px. Dropping `iconOnly` would fold into
    // something that does not fit, which looks like a fix and is not. The cluster sets it from its
    // TEXT tier on, so a phone header always has it, for a switch whose options all carry icons.
    expect(member(), 'the folded pill must inherit icon-only density').toMatch(/const iconOnly = canIcon && tier !== 'full'/)
    expect(modeToggle(), 'every option carries its icon').toMatch(/'installed', label: 'Installed', icon:[\s\S]*'browse', label: 'Browse', icon:/)
    expect(member()).toMatch(/<Segmented\b[^>]*iconOnly=\{iconOnly\}/)
  })

  it("the primitive's prescription survives — it is the reason this prop is here", () => {
    // If that comment is deleted, the next reader sees an unexplained `collapse` prop and may drop it.
    const seg = read('ui/Segmented.tsx')
    // The noun moved from "tab" to "option" when #3472 made Segmented a radiogroup; the diagnosis
    // this pins — that the size utilities set a size and not a FLOOR — is the same sentence.
    expect(seg, 'the crush diagnosis must stay in Segmented').toMatch(/set an option's size but NOT its floor/)
    expect(seg, 'and its prescription').toMatch(/should scroll or fold/)
  })

  it('🔴 records that 320px remains a header overlap, not a crush', () => {
    // The residual failure has a different cause and a different owner. Without this, a later pass
    // measures 1×1 at 320px, concludes `collapse` did not work, and reverts it.
    const src = read('pages/skills/SkillsPage.tsx')
    expect(src, 'the 320px measurement must stay').toMatch(/320px/)
    expect(src, 'and that the strip was crushed to 8px').toMatch(/8×40|8x40/)
  })
})
