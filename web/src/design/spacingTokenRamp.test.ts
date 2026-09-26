import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The density slider only moves TOKENISED spacing ────────────────────────────────────────────
//
// `tokens.css` builds the spacing ramp as `calc(<px> * var(--space-scale))`, and
// `[data-ui="dense"]` / `[data-ui="cli"]` re-scale that one variable to 0.8 / 0.68. That is the
// entire mechanism behind Appearance → Density, a shipped user-facing control. Tailwind's DEFAULT
// numeric scale (`p-6` = 1.5rem) is not wired to it and cannot be — there is no `--spacing` base
// override in the `@theme` block, only the named rungs.
//
// So a raw numeric spacing utility is inert under a control the app advertises. Measured in a real
// browser by injecting two probes into the live document and toggling `data-ui` on `<html>`:
//
//                      comfortable   dense (0.8)   cli (0.68)
//   py-2xl / px-m       24px / 12px   19.2 / 9.6    16.32 / 8.16     ← tracks the slider
//   py-6   / px-3       24px / 12px   24   / 12     24    / 12       ← FROZEN
//
// 🔑 THE CENSUS SPLITS IN TWO, AND ONLY ONE HALF IS A DEFECT. Measured 2026-09-20 on `a96ec3d0c`,
// after the first conversion slice: **4845 raw spacing utilities** in `src`, of which
// **3069 (63%) land exactly on a rung** (4/8/12/16/20/24/28px → xs/s/m/l/xl/2xl/3xl) and
// **1776 (37%) do not** — the app leans hard on half-steps the ramp has never had, dominated by
// **6px ×996 and 2px ×510**. A value with an exact token is a straight defect: same pixels at the
// default density, and it starts obeying the slider. A 6px value cannot be converted without either
// adding rungs to the ramp or changing the spacing, and BOTH are the owner's call — so this rail
// gates only the first half and merely records the second.
//
// 🔴 THE CEILING IS THE GATE; THE SWEEP RUNS IN SLICES BEHIND IT. The point of a shrink-only
// ratchet is that it is useful the moment it exists: a new `gap-2` reds it, and converting any
// utility lowers it. It landed first (`a9c03d57e`, ceiling 3135) precisely so the sweep could be
// done in whatever slices fit, by whoever, without the count silently drifting back up in between —
// which is exactly what happened to the earlier attempt at this, whose 2963 was measured 745
// commits ago and had already been overtaken by 172. Slice 1 (`a96ec3d0c`) converted **66
// utilities across 29 whole files** and re-stated the ceiling at 3069. Slice 2 (`2e007601b`)
// converted **14 utilities across 2 files** and re-stated it at 3055 — 2 files, but NOT *whole* ones:
// it left `py-0.5` in `chat/WorkflowProgressCard` and `gap-y-1.5` in `settings/ChatPanel`, one each,
// **both sitting on an element it had just converted** (`px-2 py-0.5` → `px-s py-0.5`,
// `gap-x-2 gap-y-1.5` → `gap-x-s gap-y-1.5`). Neither survivor is rung-equivalent, so the ceiling and
// the per-file pin were both satisfied while two elements ran split across the two spacing regimes —
// that is exactly the shape #3220 was filed for, and the last leg below is what now guards it.
// Slice 3 (`53a80172d`) converted **40 utilities in `settings/DoctorPanel.tsx`** and re-stated it
// at 3015.
//
// 🪤 SLICE 3'S FINDING — A SECOND RATCHET OWNS PART OF THIS SWEEP'S TERRITORY, so a whole-file pass
// is not always reachable. DoctorPanel held 48 mappable values, not 40. The other 8 are the four
// `px-4 py-3` container slabs (`:159`, `:239`, `:455`, `:608`), and converting them is pixel-identical
// yet reds a DIFFERENT rail: `pages/settings/rowGroupPadding.test.tsx` counts the literal string
// `px-l py-m` across `pages/settings` and caps it at **7** ("converging one LOWERS this, adding an
// eighth reds CI"). Converting all four would make it 11. That ceiling may only fall, exactly as
// this one may only fall, so the two rails genuinely conflict here and neither may be relaxed to
// let the other advance — the four slabs stay raw and this ceiling stops 8 short of the file.
// The resolution is the open question `rowGroupPadding` already records (those groups belong in
// `RowGroup`, whose padding is `px-l py-xs`), NOT a bump on either side. Any future slice touching
// `pages/settings` must check that rail first; a half-conversion to `px-l py-3` would dodge the
// string match while leaving one class mixing the token scale with Tailwind's frozen defaults.
//
// Two hazards a converting pass will hit, recorded here because they are the reason a sweep is a
// separate change and not a one-line regex replace:
//
// 🔴 **A PADDING PAIRED WITH A NEGATIVE MARGIN MUST NOT BE CONVERTED ALONE.** `ui/TextLink` ships
//    `py-1 -my-1` — the app's margin idiom for a 24px hit target, where the padding grows the box
//    and the margin pulls the layout back. The ramp has no negative utilities, so the regex below
//    deliberately skips `-my-1`; converting only the `py-1` leaves the pair mismatched at any
//    non-default density (py 4→3.2px while -my stays -4px, a net -0.8px pull that did not exist).
//    `nestedTargetSize.test.tsx` reds on exactly that, and it is right to. Same hazard in
//    `ui/TokenControls` (`-mx-1`) and `pages/settings/SettingsHome` (`-mx-2`).
//
// 🪤 **SOME RAILS PIN THE LITERAL CLASS STRING** of components a sweep would touch —
//    `app/IncidentBanner` (*"the exact pre-change chrome"*), `ui/FormFooter` (`py-3`),
//    `ui/TextLink` (`gap-1`), `pages/workflows/WorkflowRunDetail` (`gap-1`). Those pins measure the
//    SPELLING of something whose property is unchanged, which is a known repair in this repo — but
//    retargeting a rail to accommodate a sweep is a separate concern from the sweep, and doing both
//    in one change is how a gate gets quietly weakened. Fix the pin first, then convert.
//
// ⚠️ AND A CONVERTED CLASS MUST ACTUALLY EXIST. A Tailwind utility that is not generated silently
// drops the declaration — the padding just disappears (`lg:pr-xs` emits `.lg\:pr-xs` inside a media
// query, so a naive search for `.pr-xs` reads as absent). jsdom cannot see the build, so the check
// that belongs here is the one that makes the mapping TRUE: the rung exists in `tokens.css` at the
// px value the arithmetic assumes — asserted first, below, as this rail's premise.

const SRC = join(process.cwd(), 'src')
const tokens = readFileSync(join(SRC, 'design/tokens.css'), 'utf8')

/** Spacing utilities that take a numeric Tailwind scale value. Negative forms (`-mx-1`) are
 *  excluded by the lookbehind: they are a different concern (pulling layout back), and the ramp
 *  has no negative utilities. */
const RAW = new RegExp(
  String.raw`(?<![-\w])((?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y|space-x|space-y))-(\d+(?:\.\d+)?)(?![-\w])`,
  'g',
)

/** The tokenised counterpart of `RAW`, used ONLY as the per-file pin's non-vacuity control. A file
 *  with zero raw values is indistinguishable from a file with no spacing at all, and the second one
 *  satisfies a "stays converted" pin forever. */
const TOKENISED = new RegExp(
  String.raw`(?<![-\w])((?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y|space-x|space-y))-(xs|s|m|l|xl|2xl|3xl)(?![-\w])`,
  'g',
)

/** px → rung. Tailwind's numeric scale is n × 4px, so the mapping is arithmetic, not taste. */
const RUNG: Record<number, string> = { 4: 'xs', 8: 's', 12: 'm', 16: 'l', 20: 'xl', 24: '2xl', 28: '3xl' }

/** 🔴 SHRINK-ONLY. Measured 2026-09-20 on `2e007601b` — today's actual floor, not an aspiration.
 *  A new `gap-2` reds this; converting one lowers it. It may never be RAISED: a ceiling that moves
 *  up on demand is not a ratchet, it is a comment. (An earlier attempt at this rail carried 2963,
 *  measured 745 commits earlier; by the time it was read the tree was at 3135. That is the failure
 *  mode this number is dated and sha-stamped to avoid — re-measure and re-state, never bump.)
 *  History, each re-stated DOWN by a landed slice: 3135 (`a9c03d57e`, ceiling only) → 3069 → 3055
 *  → 3015 → 3002 (onboarding's import step moved whole onto the rungs; measured on `c2a599568`
 *  plus that change) → 2977 (the rewritten install-consent module, `pages/apps/installConsent`,
 *  put every rung value on its rungs — its two remaining raw values share an element with a
 *  `gap-1.5` half-step, so they stay in one regime — and the Store's new saved-credentials row
 *  took `gap-s` beside its `p-m`; measured on `ab2536010` plus that change). */
const MAPPABLE_CEILING = 2977

/** NOT a gate. The half-step population, recorded so the owner question has a number attached and
 *  so a later pass can see whether it moved. Adding rungs to the ramp would convert most of it. */
const NO_TOKEN_MEASURED = 1776

function strip(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')
}

function tsxFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) tsxFiles(p, acc)
    else if (entry.endsWith('.tsx') && !entry.includes('.test.')) acc.push(p)
  }
  return acc
}

/** 🔴 EVERY LITERAL `className` SPELLING, BECAUSE THE LEG BELOW IS ONLY AS WIDE AS THIS MATCH.
 *  Matching the double-quoted form alone put the bypass one stylistic keystroke away *inside the file
 *  the leg guards*: `chat/WorkflowProgressCard` already writes two of its classNames as template
 *  literals, one of them carrying a rung, so re-spelling the fixed element as
 *  ``className={`shrink-0 rounded-pill px-s py-0.5 tabular-nums`}`` scored 6 passed (6) — the
 *  reintroduction the leg exists to catch, going green. A ratchet that a reformat walks out of is the
 *  defect, not the guard, so all three literal forms are matched.
 *
 *  Two shapes are deliberately NOT matched, and both are safe: a bare expression
 *  (`className={look.spin ? 'animate-spin' : ''}`) has no literal class list to inspect, and a
 *  template literal containing a NESTED backtick terminates the `[^`]*` run early and simply misses
 *  rather than mis-reporting — neither guarded file has one. */
const CLASS_ATTR = /className=(?:"([^"]*)"|\{`([^`]*)`\}|\{'([^']*)'\})/g

/** The ceiling above and the per-file pin below both classify at file level, so one `className`
 *  carrying BOTH a tokenised and a raw spacing utility is invisible to either: the raw side can be
 *  a half-step the ceiling never counted (e.g. `py-0.5`, 2px, no rung), and the tokenised side keeps
 *  the file's TOKENISED-not-empty pin satisfied. #3220 found this shape twice — `px-s py-0.5` and
 *  `gap-x-s gap-y-1.5` — each element split across two different spacing regimes on one line.
 *
 *  ⚠️ A ZERO IS NOT A SECOND REGIME. `0 × var(--space-scale)` is 0 at every density, so `p-0` beside
 *  `gap-s` is not "density-scale one side, frozen-default the other" — there is nothing to diverge.
 *  The PM census counted **32** `gap-0`/`p-0` sites as correctly raw forever, and neither swept file
 *  has one today, so flagging them would be a latent wrong-reason CI red rather than a finding. Zero
 *  is excluded from the raw side only HERE; the aggregate ceiling already ignores it a different way
 *  (0px has no rung, so it lands in the recorded half-step population, not the gated one). */
function sameElementMixedSpacing(src: string): string[] {
  const mixed: string[] = []
  for (const m of src.matchAll(CLASS_ATTR)) {
    const cls = m[1] ?? m[2] ?? m[3]
    const raw = [...cls.matchAll(RAW)].filter((r) => Number(r[2]) !== 0)
    if (raw.length > 0 && [...cls.matchAll(TOKENISED)].length > 0) mixed.push(cls)
  }
  return mixed
}

function census() {
  const mappable: string[] = []
  const noToken: string[] = []
  for (const abs of tsxFiles(SRC)) {
    const rel = abs.slice(SRC.length + 1)
    for (const m of strip(readFileSync(abs, 'utf8')).matchAll(RAW)) {
      const px = Number(m[2]) * 4
      ;(Number.isInteger(px) && RUNG[px] ? mappable : noToken).push(`${rel}: ${m[0]}`)
    }
  }
  return { mappable, noToken }
}

describe('spacing rides the density scale', () => {
  it('the ramp is built from --space-scale, which is what makes any of this matter', () => {
    // If a future edit hard-codes the rungs, the slider stops working and this rail's premise dies
    // with it — so the premise is asserted first.
    expect(tokens, 'the density multiplier must exist').toMatch(/--space-scale:\s*1\s*;/)
    for (const [px, rung] of Object.entries(RUNG)) {
      expect(
        tokens,
        `--spacing-${rung} must be ${px}px × the scale, or the px→rung mapping this rail uses is wrong`,
      ).toMatch(new RegExp(String.raw`--spacing-${rung.replace('2xl', '2xl')}:\s*calc\(${px}px\s*\*\s*var\(--space-scale\)\)`))
    }
    // And the levels the slider actually selects.
    expect(tokens, 'dense must re-scale the spine').toMatch(/\[data-ui="dense"\][\s\S]{0,120}--space-scale:\s*0?\.8/)
    expect(tokens, 'cli must re-scale the spine').toMatch(/\[data-ui="cli"\][\s\S]{0,120}--space-scale:\s*0?\.68/)
  })

  it('reads the tree, and the matcher matches a real utility (a vacuous census passes forever)', () => {
    const { mappable, noToken } = census()
    expect(mappable.length + noToken.length, 'the scan found no spacing utilities at all').toBeGreaterThan(500)
    // Positive and negative controls on the matcher itself.
    expect([...'className="gap-2 px-3"'.matchAll(RAW)].length, 'must catch a raw utility').toBe(2)
    expect([...'className="gap-s px-m"'.matchAll(RAW)].length, 'must not flag a tokenised one').toBe(0)
    expect([...'className="-mx-1"'.matchAll(RAW)].length, 'negative margins are out of scope').toBe(0)
    expect([...'className="grid-cols-2 w-4 top-2"'.matchAll(RAW)].length, 'not every -N is spacing').toBe(0)
    expect(strip('/* gap-2 */ x'), 'comments stripped before matching').not.toContain('gap-2')
  })

  it('🔴 a spacing value with an exact rung must USE it — shrink-only', () => {
    const { mappable } = census()
    expect(
      mappable.length,
      `${mappable.length} raw spacing utilities land exactly on a rung and so are inert under the ` +
        `density slider for no reason. Ceiling is ${MAPPABLE_CEILING} and may only fall. The mapping ` +
        'is arithmetic — n × 4px → xs/s/m/l/xl/2xl/3xl — so the conversion is pixel-identical at the ' +
        `default density.\nFirst few:\n  ${mappable.slice(0, 6).join('\n  ')}`,
    ).toBeLessThanOrEqual(MAPPABLE_CEILING)
  })

  it('the files a slice fully converted stay converted', () => {
    // 🔑 THE CEILING ABOVE STRUCTURALLY CANNOT CATCH THIS. It caps the AGGREGATE, so a converted
    // file reverting is invisible whenever anything else converts as much in the same change — the
    // count lands under the ceiling and the rail stays green while a file the sweep already paid
    // for goes back. The sweep took WHOLE files, so that is the shape a regression here actually
    // has. Sampled across the areas the slices touched, including two primitives; each one measured
    // at zero rung-equivalent raw values on `bfe18b58a`.
    for (const rel of [
      'pages/settings/ChatPanel.tsx',
      'pages/knowledge/TagManager.tsx',
      'pages/workflows/OutboxPanel.tsx',
      'ui/FindBar.tsx',
      'ui/Composer.tsx',
    ]) {
      const code = strip(readFileSync(join(SRC, rel), 'utf8'))
      // Non-vacuity FIRST: stripping a file's layout would satisfy the pin below, not fail it.
      expect(
        [...code.matchAll(TOKENISED)].length,
        `${rel} carries no tokenised spacing at all — the pin below would pass vacuously`,
      ).toBeGreaterThan(0)
      const raw = [...code.matchAll(RAW)]
        .filter((m) => {
          const px = Number(m[2]) * 4
          return Number.isInteger(px) && RUNG[px]
        })
        .map((m) => m[0])
      expect(raw, `${rel} reintroduced a rung-equivalent raw value the density slider cannot move`).toEqual([])
    }
  })

  it('🔴 no className on these two swept elements mixes a tokenised and a raw spacing utility', () => {
    // Positive controls: the exact two pairs #3220 found, replanted as literal strings — proves the
    // detector still catches them even though the real files below no longer carry either.
    expect(
      sameElementMixedSpacing('<span className="shrink-0 rounded-pill px-s py-0.5 tabular-nums" />'),
      'must catch px-s mixed with raw py-0.5',
    ).toEqual(['shrink-0 rounded-pill px-s py-0.5 tabular-nums'])
    expect(
      sameElementMixedSpacing('<ul className="grid items-center gap-x-s gap-y-1.5" />'),
      'must catch gap-x-s mixed with raw gap-y-1.5',
    ).toEqual(['grid items-center gap-x-s gap-y-1.5'])
    // Third positive control: the SAME mix, re-spelled as a template literal. This is the form that
    // scored 6 passed (6) against the `className="…"`-only matcher, and it is the form
    // `WorkflowProgressCard` already uses twice — so the bypass was a reformat away in the guarded
    // file itself. Interpolation must not defeat it either, which is the realistic shape there.
    expect(
      sameElementMixedSpacing('<span className={`shrink-0 rounded-pill px-s py-0.5 tabular-nums`} />'),
      'must catch the same mix re-spelled as a template literal',
    ).toEqual(['shrink-0 rounded-pill px-s py-0.5 tabular-nums'])
    expect(
      sameElementMixedSpacing('<span className={`inline-flex items-center gap-xs py-0.5 ${look.tone}`} />'),
      'an interpolation must not hide the mix',
    ).toEqual(['inline-flex items-center gap-xs py-0.5 ${look.tone}'])
    expect(
      sameElementMixedSpacing("<ul className={'grid items-center gap-x-s gap-y-1.5'} />"),
      'must catch the same mix inside a braced single-quoted string',
    ).toEqual(['grid items-center gap-x-s gap-y-1.5'])
    // Negative controls: the same two elements in their fixed, fully-tokenised form — must not fire.
    expect(
      sameElementMixedSpacing('<span className="shrink-0 rounded-pill px-s py-xs tabular-nums" />'),
      'must not flag an element that is fully tokenised',
    ).toEqual([])
    expect(
      sameElementMixedSpacing('<ul className="grid items-center gap-x-s gap-y-s" />'),
      'must not flag an element that is fully tokenised',
    ).toEqual([])
    // An all-raw element is the aggregate ceiling's job, not this leg's.
    expect(
      sameElementMixedSpacing('<div className="px-3 py-2" />'),
      "all-raw is the ceiling rail's job, not this leg's",
    ).toEqual([])
    // And a ZERO is scale-invariant, so it is not the second regime this leg names — without this,
    // any of the 32 `gap-0`/`p-0` sites landing beside a rung would red CI for a reason that is
    // arithmetically false.
    expect(
      sameElementMixedSpacing('<div className="m-0 grid items-center gap-x-s gap-y-s" />'),
      'a zero utility is 0 at every density — nothing diverges, so it is not a mixed regime',
    ).toEqual([])
    expect(
      sameElementMixedSpacing('<div className="gap-0 p-0 px-s" />'),
      'gap-0 / p-0 are correctly raw forever',
    ).toEqual([])
    // But a zero must not shield a real half-step on the same element.
    expect(
      sameElementMixedSpacing('<div className="m-0 gap-x-s gap-y-1.5" />'),
      'excluding zero must not swallow a genuine half-step beside it',
    ).toEqual(['m-0 gap-x-s gap-y-1.5'])

    for (const rel of ['pages/chat/WorkflowProgressCard.tsx', 'pages/settings/ChatPanel.tsx']) {
      const code = strip(readFileSync(join(SRC, rel), 'utf8'))
      expect(
        sameElementMixedSpacing(code),
        `${rel} mixes a tokenised and a raw spacing utility on one element — density-scale one side, ` +
          'frozen-default the other',
      ).toEqual([])
    }
  })

  it('records the half-step population the ramp cannot express — an owner question, not a gate', () => {
    // Deliberately NOT an upper bound: until the ramp gains 2/6/10/14px rungs, a 6px need has no
    // tokenised form and forbidding it would push authors to a WORSE value. The number is asserted
    // loosely so a large move is visible without the rail dictating a design decision.
    const { noToken } = census()
    expect(noToken.length, 'the half-step population vanished — did the ramp gain rungs? update this rail')
      .toBeGreaterThan(NO_TOKEN_MEASURED * 0.5)
    // The distribution is the argument: 6px and 2px dominate, so this is a ramp gap, not sloppiness.
    const six = noToken.filter((s) => /-1\.5(?![-\w])/.test(s)).length
    const two = noToken.filter((s) => /-0\.5(?![-\w])/.test(s)).length
    expect(six + two, '6px + 2px should still be the bulk of the untokenisable half').toBeGreaterThan(
      noToken.length * 0.5,
    )
  })
})
