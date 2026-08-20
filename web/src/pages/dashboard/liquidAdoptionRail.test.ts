import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── The liquid state transition actually exists in the product (atom FM-4) ───────────────
//
// `ui/motion/LiquidShape.test.tsx` + `LiquidShape.reducedMotion.test.tsx` prove the primitive
// thoroughly — and every one of their `<LiquidShape>` call sites is a FIXTURE. Before this atom
// the census was: nine files mention the name, all nine under `ui/motion/`, and **zero product
// call sites**. A primitive whose only callers are its own tests is a feature the app does not
// have; a green primitive suite says nothing about that, and neither does the component test of
// the surface that adopts it (delete the adoption and both stay green). So the adopting surface
// is named here and the invariant is asserted against its source.
//
// This is a SOURCE scan for the reason `headerActionsAdoption.test.ts` records: the property is
// about CONSTRUCTION. Two extra reasons apply here. The morph is `aria-hidden` decoration by
// contract, so there is nothing in the accessibility tree to assert on; and jsdom runs no
// animation frames, so where the primitive SITS in the tree — the thing that decides whether it
// ever animates at all — is only visible in the source.
//
// Not adopted, and why (so a later pass does not re-derive it):
//   · `dashboard/world/AgentWorld.tsx` — the other live ambient surface, and the obvious second
//     candidate. It is a `<canvas>` painter ("clock, context, and pixels"); its scene nodes are
//     drawn, not mounted, so it structurally cannot host an SVG child. Giving it a liquid state
//     would mean porting the silhouette into `worldScene.ts`, which is a different atom.
//   · `loops/LoopCockpitPage.tsx` — a REAL candidate, not an absent one: it exists (1.3k lines)
//     and it has the phase/running state a composure morph depicts. The plan names both
//     consumers ("loop cockpits, ambient surfaces (20 — liquid state transitions)",
//     FLUID-MOTION.md §"Integration points"). It is left for a later pass on purpose — this
//     atom's clause is that the family reads as one system with a liquid transition SHIPPED,
//     and one honest adoption settles that. A second one is taste work, not coherence work.
//
// The plan's own C2 sketch is stale on two counts, verified against `ui/motion/LiquidShape.tsx`
// rather than trusted: `intensity={expr(1)}` (FLUID-MOTION.md:51) would scale the amplitude
// TWICE, because the primitive applies `expr()` to `intensity` itself, and `from="circle"`
// contradicts the primitive's own documented pairing for a load ("loading→loaded is
// blob→squircle rather than an arbitrary pair"). Both are asserted the correct way below.

const SRC = join(process.cwd(), 'src')

/** The surface that hosts the liquid state transition, relative to `src/`. */
const TILES = 'pages/dashboard/PinnedTiles.tsx'

/** The conditional that swaps the tile's BODY — `{<something> ? <WidgetFrame …> : …}`. Anchored
 *  on the element rather than on the condition's spelling, because the condition is a detail
 *  (`artifact?.content` or a `body` local hoisted out of it) and `WidgetFrame` is the artifact
 *  body itself. Matching it locates the conditional's OPENING BRACE, which is what the
 *  mounted-host claim below needs — "before `<WidgetFrame>`" would still admit a morph nested in
 *  the same branch. If the tile's body ever stops being one ternary on `WidgetFrame`, this stops
 *  matching and the rail goes red asking to be re-derived, which is the intended failure. */
const BODY_GATE = /\{\s*[\w$?.]+\s*\?\s*<WidgetFrame/

const sourceOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

/** True when `expression` is a number AT THE SOURCE LEVEL: a bare numeric literal, or a bare
 *  identifier the same module declares as one. The second form is the better call site — a named
 *  constant can be shared with the component test so both render the same amplitude — so the rail
 *  follows the identifier rather than demanding a magic literal. Anything else (a call, a member
 *  access, arithmetic) is something this rail cannot vouch for, and fails. */
function isPlainNumber(expression: string, src: string): boolean {
  if (/^-?\d*\.?\d+$/.test(expression)) return true
  if (!/^[A-Za-z_$][\w$]*$/.test(expression)) return false
  return new RegExp(
    `\\b(?:const|let|var)\\s+${expression}(?:\\s*:\\s*number)?\\s*=\\s*-?\\d*\\.?\\d+\\s*(?:;|$)`,
    'm',
  ).test(src)
}

/** The adoption's JSX tag, as a real scope rather than a character window: from `<LiquidShape`
 *  to the first `/>`. A failed match returns null (and is asserted, not silently skipped). */
function liquidTag(src: string): string | null {
  return src.match(/<LiquidShape\b[\s\S]*?\/>/)?.[0] ?? null
}

describe('the liquid state transition is adopted in the product', () => {
  it('finds the surface it scans, with the tile it scans for', () => {
    // The vacuity floor. Every "does NOT contain" assertion below is trivially true against a
    // file that moved or was renamed, and a scan of nothing passes perfectly — so a missing or
    // gutted surface has to throw HERE rather than read as a clean rail.
    const src = sourceOf(TILES)
    expect(src.split('\n').length, `${TILES} is too short to be the pinned-tiles band`)
      .toBeGreaterThan(100)
    // Positive markers: the band and the per-tile component this rail's claims are about.
    expect(src).toContain('data-testid="pinned-tiles"')
    expect(src).toMatch(/function PinnedTile\(/)
    expect(src, `${TILES} must still switch its body on one WidgetFrame ternary`)
      .toMatch(BODY_GATE)
  })

  it('reaches the primitive through the shared motion barrel', () => {
    const src = sourceOf(TILES)
    // Through `ui/motion`'s barrel, like every other shared motion primitive. This is not
    // pedantry: ALL 20 product imports of a motion primitive go through the barrel and the only
    // deep path in the tree is one test file, so a deep import here would be the first product
    // exception — and the barrel's own docstring is the rule ("Import from here so per-component
    // work is composition, not reinvention"). A deep path also survives the barrel dropping the
    // export, which is how a surface ends up holding a primitive the design system retired.
    expect(src, "import LiquidShape from '../../ui/motion', not from ui/motion/LiquidShape")
      .toMatch(/import \{[^}]*\bLiquidShape\b[^}]*\} from '\.\.\/\.\.\/ui\/motion'/)
    expect(src, `${TILES} must render <LiquidShape>, not merely import it`)
      .toMatch(/<LiquidShape\b/)
    // And it must not reach past the primitive into the vocabulary: a surface that hands its own
    // spring to a family member is the fifth dialect atom FM-4 exists to delete.
    expect(src, `${TILES} must not compose its own family timing`)
      .not.toMatch(/\b(familySpring|familyTween|MORPH_FAMILY)\b/)
  })

  it('hosts the morph OUTSIDE the branch that unmounts on the state it depicts', () => {
    // The one invariant a component test cannot see. A morph placed inside the `artifact?.content`
    // ternary remounts on the very transition it depicts: `active` is read fresh as the initial
    // value, the spring has no distance to travel, and the animation silently never plays. The
    // surface still looks correct in a screenshot of either end state.
    //
    // What a source scan can honestly prove is the NEGATIVE — that the call sits inside
    // `PinnedTile` and before the body conditional's opening brace, so it is in neither branch.
    // That is equivalent to "in the always-mounted header row" only because the conditional is
    // that component's LAST child; if a refactor reorders the tile, this goes RED and asks a human
    // to re-derive it. That is the intended failure: it does not quietly pass.
    const src = sourceOf(TILES)
    const component = src.indexOf('function PinnedTile(')
    const liquid = src.indexOf('<LiquidShape')
    const bodyGate = src.search(BODY_GATE)
    expect(liquid, 'the morph must be inside PinnedTile, not the band above it')
      .toBeGreaterThan(component)
    expect(liquid, 'the morph must precede the body-switching conditional')
      .toBeLessThan(bodyGate)
    // Stated as the negative too, so a SECOND call site added inside either branch also fails.
    expect(src.slice(bodyGate), 'no LiquidShape may sit inside the conditional body')
      .not.toMatch(/\bLiquidShape\b/)
  })

  it('depicts unsettled→settled as blob→squircle, off real state', () => {
    const tag = liquidTag(sourceOf(TILES))
    expect(tag, 'the <LiquidShape> tag must be self-closing so this rail can scope to it')
      .not.toBeNull()
    // Guards a runaway non-greedy match: if the `/>` it found belonged to some later element,
    // every assertion scoped to `tag` would be measuring the wrong text.
    expect(tag!.length, 'the matched tag ran past the call site').toBeLessThan(400)
    // Composure, not an arbitrary pair: `blob` is the unsettled form, `squircle` the settled one.
    expect(tag).toMatch(/\bfrom="blob"/)
    expect(tag).toMatch(/\bto="squircle"/)
    // `active` must be driven by state. A literal would make the morph a static graphic — the
    // exact "shipped inert" shape this whole file exists to prevent, one level down.
    expect(tag).toMatch(/\bactive=\{/)
    expect(tag, 'active must come from state, not a literal')
      .not.toMatch(/\bactive=\{\s*(?:true|false)\s*\}/)
  })

  it('passes a plain-number intensity and no hex tint', () => {
    const src = sourceOf(TILES)
    const tag = liquidTag(src)
    expect(tag, 'the <LiquidShape> tag must be self-closing so this rail can scope to it')
      .not.toBeNull()
    // The primitive scales the amplitude through `expr()` itself, so the call site passes a PLAIN
    // number. Pre-scaling here applies the knob twice — an extra factor of `0.35 + 0.65*e`, so 13%
    // low at the default expressiveness and 65% low at 0. Nearly invisible where anyone measures
    // it, worst for whoever dialled the knob DOWN. The plan's C2 sketch has this backwards.
    const intensity = tag!.match(/\bintensity=\{\s*([^}]+?)\s*\}/)?.[1]
    expect(intensity, 'intensity must be passed explicitly at this call site').toBeTruthy()
    expect(intensity, 'intensity must not be pre-scaled — the primitive applies expr() itself')
      .not.toMatch(/\bexpr/)
    expect(
      isPlainNumber(intensity!, src),
      `intensity={${intensity}} must be a number, or a name this module declares as one`,
    ).toBe(true)
    // Tint is a theme var or the primitive's own default; a hex would survive a theme flip.
    expect(tag!, 'no hex colour at a motion call site').not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })

  it('never leaves the depicted state to the decoration alone', () => {
    // The primitive is `aria-hidden` + `pointer-events-none` by contract, so it is invisible to
    // a screen reader and to a keyboard. The call site therefore owes the state in TEXT — the
    // primitive's own rule: "if the state it depicts matters, the CALL SITE must say so in text".
    const src = sourceOf(TILES)
    expect(src, 'the loading state must still be carried by text, not only by the blob')
      .toContain('Loading tile')
  })

  it('LiquidShape has at least one call site that is not a test fixture', () => {
    // The anti-inertness claim, measured across the whole frontend instead of asserted in a
    // comment above. Test files are excluded because they were the entire population before this
    // atom, and `ui/motion/` is excluded because a primitive demoing itself is not adoption.
    const hits: string[] = []
    const walk = (dir: string) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = join(dir, e.name)
        if (e.isDirectory()) { walk(p); continue }
        if (!/\.tsx?$/.test(e.name) || /\.(test|spec)\.tsx?$/.test(e.name)) continue
        if (p.startsWith(join(SRC, 'ui/motion'))) continue
        if (/<LiquidShape\b/.test(readFileSync(p, 'utf8'))) hits.push(p.slice(SRC.length + 1))
      }
    }
    walk(SRC)
    expect(hits, 'LiquidShape is inert again — no product surface renders it').not.toHaveLength(0)
    expect(hits).toContain(TILES)
  })
})
