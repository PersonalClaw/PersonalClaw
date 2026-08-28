import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A thin vertical control needs a 24px band, not a 24px box ─────────────────────────────────
//
// The nav rail's resize handle is `w-1 h-full` — measured on `main` at **4×900 (desktop)** and
// **4×1112 (tablet)**, present on every surface at both, absent only at phone where the rail
// collapses. 20px short of WCAG 2.2 SC 2.5.8 in the one axis that is short.
//
// `.hit-24` (the idiom the previous change shipped) is the wrong SHAPE for it. That one insets a
// pseudo-element equally on all four sides, which is right for a small square button and wrong here
// twice over:
//
//   · it would add ~10px ABOVE the rail's top edge. Invisible, but hit-testable — and a pointer band
//     overhanging into the header is how a fix for one stolen target creates another. This program has
//     that exact defect on file (`Scratch` at 1024px firing `Open terminal`).
//   · it needs the control's drawn width restated at the call site, which then drifts from `w-1`.
//
// `.hit-24-x` pins the band to the element's own vertical extent (`top: 0; bottom: 0`) and centres
// `--hit-min` of width on it. `left: 50%` + `translateX(-50%)` centres a fixed-width box on a box of
// ANY width, so no call site has to know or repeat the 4px.
//
// 🔴 THE PRECONDITION THAT COST A MEASUREMENT. `.hit-24` sets `position: relative`; this variant must
// NOT. Its call site is already `absolute right-0 top-0`, and forcing `relative` drops the handle out
// of that placement — measured on `main` with nothing but `position: relative` injected, it moved from
// `left:192,top:0` to `left:0,top:900`, i.e. out of the rail. My first draft shipped exactly that, and
// the browser reachability measurement is what caught it: the reachable band went DOWN from 4px to 1px,
// which is the opposite of the change's whole purpose. A source-only rail would have passed it.
//
// ⚠️ AXE CANNOT SEE THIS, and that is not a defect in the fix. axe and every
// `getBoundingClientRect` check read the ELEMENT's box, which is deliberately unchanged, so a
// `target-size` report on this control does not go away. The criterion is about the area a person can
// press; that is what moves. Measured in a real browser with the rule applied — reachable width
// **4px → 24px** at 1440×900 and 834×1112, with `position` and `left` byte-identical. jsdom has no
// layout, so that number cannot be re-derived here; this file guards the contract that produces it.

const SRC = join(process.cwd(), 'src')
const css = readFileSync(join(SRC, 'design/tokens.css'), 'utf8')
const navRail = readFileSync(join(SRC, 'ui/NavRail.tsx'), 'utf8')

// Comments stripped BEFORE any matching, and this is load-bearing rather than tidiness: the
// `.hit-24-x` block carries a comment explaining why it must not set `position`, and that prose
// contains the literal string `position: relative`. Matching the raw slice made the
// does-not-set-position assertion fail on its own documentation — a text scanner reads comments,
// and a rail that cannot tell a declaration from a sentence about a declaration is measuring the
// wrong thing. (If a future edit drops this stripping, that test goes red rather than silently
// vacuous, which is the correct direction to fail.)
const cssCode = css.replace(/\/\*[\s\S]*?\*\//g, '')

/** The `.hit-24-x` rule bodies, sliced to the construct so a later utility cannot satisfy these. */
function ruleBody(selector: string): string {
  const at = cssCode.indexOf(`${selector} {`)
  expect(at, `${selector} is not declared in design/tokens.css`).toBeGreaterThan(-1)
  const end = cssCode.indexOf('}', at)
  expect(end, `${selector}'s body does not terminate`).toBeGreaterThan(at)
  return cssCode.slice(at, end)
}

describe('the thin-handle hit target', () => {
  it('reads its subjects (a rail over nothing asserts nothing)', () => {
    expect(css.length, 'tokens.css did not read').toBeGreaterThan(10_000)
    expect(navRail, 'the handle must still be a window-splitter').toContain('role="separator"')
    expect(navRail).toContain('aria-orientation="vertical"')
  })

  it('the band is 24px wide and pinned to the element\'s own height', () => {
    const before = ruleBody('.hit-24-x::before')
    expect(before, 'a generated box needs content').toMatch(/content:\s*""/)
    expect(before).toMatch(/position:\s*absolute/)
    expect(before, 'width comes from the floor, not a literal').toMatch(/width:\s*var\(--hit-min\)/)
    expect(before, 'centred, so no call site restates the control width').toMatch(/left:\s*50%/)
    expect(before).toMatch(/translateX\(-50%\)/)
    // Pinned vertically: this is what stops the band overhanging the rail's top edge.
    expect(before, 'top must pin to the element').toMatch(/top:\s*0/)
    expect(before, 'bottom must pin to the element').toMatch(/bottom:\s*0/)
    expect(before, 'and it must forward events, not swallow them').toMatch(/pointer-events:\s*auto/)
  })

  it('and the band must NEVER grow vertically', () => {
    // The distinction from `.hit-24`, asserted as a property rather than a comment: an `inset` or a
    // negative top/bottom here would reintroduce the overhang this variant exists to avoid.
    const before = ruleBody('.hit-24-x::before')
    expect(before, 'no symmetric inset — that is `.hit-24`').not.toMatch(/inset:/)
    expect(before, 'no negative vertical pull').not.toMatch(/top:\s*calc\(-|bottom:\s*calc\(-|top:\s*-|bottom:\s*-/)
  })

  it('🔴 the utility does NOT set position — the call site owns its containing block', () => {
    // The precondition. `.hit-24` sets `position: relative`; doing that here moves the handle out of
    // its absolute placement (measured: left:192,top:0 -> left:0,top:900).
    expect(
      ruleBody('.hit-24-x'),
      'setting `position` here drops an absolutely-placed call site out of its placement — the ' +
        'first draft of this change did exactly that and the reachable band went 4px -> 1px',
    ).not.toMatch(/position:/)
    // …which is only safe because the call site is positioned. If a future adopter is not, its
    // pseudo-element anchors to the wrong box silently.
    const handle = navRail.slice(navRail.indexOf('role="separator"'))
    const className = handle.slice(handle.indexOf('className='), handle.indexOf('/>') + 2)
    expect(className, 'the adopter must be positioned itself').toMatch(/\babsolute\b/)
    expect(className, 'and must carry the utility').toMatch(/\bhit-24-x\b/)
  })

  it('the handle keeps the geometry the drag maths depends on', () => {
    // The fix is a no-op on layout by construction; if the drawn strip or its full height changed,
    // the 1px seam and the resize arithmetic would both be a different question.
    const handle = navRail.slice(navRail.indexOf('role="separator"'))
    const className = handle.slice(handle.indexOf('className='), handle.indexOf('/>') + 2)
    expect(className, 'still a 4px drawn strip').toMatch(/\bw-1\b/)
    expect(className, 'still full height').toMatch(/\bh-full\b/)
    expect(className, 'still pinned to the rail\'s right edge').toMatch(/\bright-0\b/)
    // And the keyboard contract the separator promises is untouched.
    expect(handle).toMatch(/aria-valuenow=/)
    expect(handle).toMatch(/onKeyDown=/)
  })

  it('`.hit-24` is left alone — it is a different shape for a different problem', () => {
    // Its one adopter (`ui/BoardCollapse`) must keep behaving identically; this change adds a sibling
    // rather than editing a shipped primitive, so that call site is byte-identical.
    const base = ruleBody('.hit-24')
    expect(base, '`.hit-24` still establishes its own containing block').toMatch(/position:\s*relative/)
    expect(base).toMatch(/--hit-size:\s*21px/)
    expect(
      readFileSync(join(SRC, 'ui/BoardCollapse.tsx'), 'utf8'),
      'BoardCollapse must still use the symmetric idiom, not this one',
    ).toMatch(/\bhit-24\b(?!-x)/)
  })
})
