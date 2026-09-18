import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
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
const sidePanel = readFileSync(join(SRC, 'ui/SidePanel.tsx'), 'utf8')
const sessionMapRail = readFileSync(join(SRC, 'pages/chat/SessionMapRail.tsx'), 'utf8')

/** Blank every comment, length-preserving, before any TSX scan below.
 *
 *  🪤 SAME LESSON THIS FILE ALREADY LEARNED FOR CSS, one language over: the adopter scan walks back
 *  from the first `hit-24-x` to its opening `<`, and an adopter that DOCUMENTS the utility it adopts
 *  (which every one of them should) puts that string in a sentence first — so the walk-back lands in
 *  prose and extracts a tag that does not exist. A source scan that cannot tell a declaration from a
 *  sentence about a declaration is measuring the wrong thing. */
function code(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:"'`])\/\/[^\n]*/g, (m, lead: string) => lead + ' '.repeat(m.length - lead.length))
}

/** 🔑 `.hit-24-x` HAS TWO ADOPTERS NOW, which is what makes it a primitive rather than speculative API.
 *
 *  Both are thin VERTICAL window-splitters, and the second was found by the reflow tier this repo did
 *  not have until #2270's finding earned it (`surfaces.json`'s `reflow` = 320px, WCAG SC 1.4.10):
 *
 *    `ui/NavRail.tsx`     4×900 desktop / 4×1112 tablet — reachable 4px → 24px  (a clean pass)
 *    `ui/SidePanel.tsx`   6×732 at 320px, on 46 consumers — reachable 6px → 15px (NOT a pass)
 *
 *  🪤 THE SECOND ONE DOES NOT REACH THE FLOOR, AND THAT IS RECORDED RATHER THAN ROUNDED UP. Its handle
 *  sits on the panel's INNER edge (`left-0`) with two `overflow: hidden` ancestors, so the band's
 *  outward half is clipped: growth is entirely rightward (R2→R11, L unchanged at 3). Reaching 24px was
 *  measured and REJECTED — a full inward band swallows clicks from four named controls at the panel's
 *  left edge (`Mark criterion incomplete`, `Mark step incomplete`, `Mark step done`, `Edit`), which is
 *  the stolen-target defect this repo already has on file. Getting to 24px means indenting panel
 *  content across all 46 consumers, which is the owner's call.
 *
 *  🔑 AND THE THIRD ONE IS NOT A SPLITTER, WHICH IS WHAT TURNED THIS RAIL FROM VACUOUS TO REAL.
 *  `pages/chat/SessionMapRail.tsx`'s mark (atom SSM-8) is a 4×4 tick on a hairline, not a window
 *  edge — and it was measured that the rail SAID it adopted `.hit-24-x` while this file scanned only
 *  `src/ui`, so "hitTargetThinHandle passes for the Session Map rail" was true of a file this test
 *  never opened. Two things had to change for that sentence to mean anything: the derivation below
 *  walks the whole `src` tree, and the per-adopter precondition asserts the property the CSS
 *  actually needs (a containing block + a thin drawn box) instead of the splitter-shaped proxy that
 *  the first two adopters happened to share.
 *
 *    `pages/chat/SessionMapRail.tsx`  4×4 tick, one per transcript mark — reachable 4px → 24px
 *                                    HORIZONTALLY ONLY, and that is the utility working as designed
 *                                    rather than a shortfall: `top: 0; bottom: 0` pins the band to
 *                                    the tick's own 4px height, and on a rail whose ticks sit a few
 *                                    px apart a 24px VERTICAL band would swallow its neighbours —
 *                                    the stolen-target defect recorded twice above. Recorded, not
 *                                    rounded up, exactly as SidePanel's 15px is. */
const ADOPTERS: [string, string][] = [
  ['ui/NavRail.tsx', code(navRail)],
  ['ui/SidePanel.tsx', code(sidePanel)],
  ['pages/chat/SessionMapRail.tsx', code(sessionMapRail)],
]

/** The two window-splitters, which additionally owe the resize contract. Kept separate from
 *  `ADOPTERS` so a non-splitter adopter is not asserted to be one. */
const SPLITTERS = ['ui/NavRail.tsx', 'ui/SidePanel.tsx']

/** Every `.tsx` under `src`, so a fourth adopter cannot appear in a directory this file forgot to
 *  look in — which is exactly how the Session Map rail hid. */
function allSources(dir: string, prefix = ''): string[] {
  const out: string[] = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name
    if (e.isDirectory()) { out.push(...allSources(join(dir, e.name), rel)); continue }
    if (/\.tsx$/.test(e.name) && !/\.test\./.test(e.name)) out.push(rel)
  }
  return out
}

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

  it('every adopter is positioned AND thin — the two preconditions the utility requires', () => {
    // `.hit-24-x` sets no `position`, so a statically-positioned adopter would anchor its
    // pseudo-element to the wrong box silently. And it centres a FIXED 24px on the element's own
    // width, which is only a fix at all for an element narrower than that. Both asserted per
    // adopter, on the property the CSS needs rather than on the splitter shape the first two
    // adopters happened to share — that proxy is what let the Session Map rail claim this rail's
    // green while sitting outside the directory it scanned.
    for (const [rel, src] of ADOPTERS) {
      const at = src.indexOf('hit-24-x')
      expect(at, `${rel} must actually adopt the utility`).toBeGreaterThan(-1)
      const openTag = src.lastIndexOf('<', at)
      const tag = src.slice(openTag, src.indexOf('>', at) + 1)
      expect(tag, `${rel}: an adopter must establish its own containing block`).toMatch(/\babsolute\b/)
      // Thin in the axis the band widens: a `w-1`/`w-1.5` strip or a `size-1` tick, never a box that
      // is already wider than the 24px floor.
      expect(tag, `${rel}: the band only fixes an element narrower than 24px`)
        .toMatch(/\b(w|size)-1(\.5)?\b/)
    }
  })

  it('the two splitters keep the resize affordance the band is pressed for', () => {
    // Split out from the precondition above when the third (non-splitter) adopter landed, so the
    // splitter contract is still asserted for the sites that owe it and is not silently dropped.
    for (const rel of SPLITTERS) {
      const src = ADOPTERS.find(([r]) => r === rel)![1]
      const at = src.indexOf('hit-24-x')
      const tag = src.slice(src.lastIndexOf('<', at), src.indexOf('>', at) + 1)
      expect(tag, `${rel}: still the thin window-splitter`).toMatch(/role="separator"|cursor-(col|ew)-resize/)
    }
  })

  it('the adopter list matches the tree — a new adopter must be named here', () => {
    // Derived, so a fourth adopter cannot appear without this rail noticing. The named list carries
    // the per-site measurement (one passes at 24px, one is clipped at 15px, one is horizontal-only);
    // a bare grep could not. Walks ALL of `src`: scanning only `src/ui` is how the Session Map rail
    // adopted the utility and stayed invisible to the file whose green it was credited with.
    const found = allSources(SRC).filter((rel) => code(readFileSync(join(SRC, rel), 'utf8')).includes('hit-24-x'))
    expect(found.sort(), 'an adopter of .hit-24-x exists that this rail does not measure')
      .toEqual(ADOPTERS.map(([rel]) => rel).sort())
  })

  it('the comment blanking is load-bearing, not hygiene', () => {
    // Proven on a real adopter rather than asserted: the Session Map rail documents the utility in
    // its header, so RAW text puts the first `hit-24-x` inside prose. If a future edit dropped
    // `code()`, the adopter scan would walk back from that sentence and this goes red — the correct
    // direction to fail, rather than silently extracting a tag that is not one.
    expect(sessionMapRail.indexOf('hit-24-x'), 'the rail must still document what it adopts')
      .toBeLessThan(sessionMapRail.indexOf('className='))
    const blanked = code(sessionMapRail)
    const at = blanked.indexOf('hit-24-x')
    expect(at, 'blanking removed the real adoption too').toBeGreaterThan(-1)
    expect(blanked.slice(blanked.lastIndexOf('<', at), at)).toContain('button')
  })

  it('SidePanel keeps the reason it stops at 15px, and the rejected option', () => {
    // Without this, the next pass reasonably "finishes the job" by widening the band to 24px — which
    // was measured to swallow four named controls. The rejection has to outlive the measurement.
    expect(sidePanel, 'the clipping caveat must stay').toMatch(/clipped at the panel boundary|overflow: hidden/)
    expect(sidePanel, 'and the four controls a 24px band would steal').toMatch(/Mark criterion incomplete/)
    expect(sidePanel, 'and that reaching 24px is the owner\'s call').toMatch(/owner call/i)
  })
})
