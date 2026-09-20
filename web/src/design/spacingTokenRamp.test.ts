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
// 🔑 THE CENSUS SPLITS IN TWO, AND ONLY ONE HALF IS A DEFECT. Measured 2026-09-20 on `a9c03d57e`:
// **4911 raw spacing utilities** in `src`, of which **3135 (64%) land exactly on a rung**
// (4/8/12/16/20/24/28px → xs/s/m/l/xl/2xl/3xl) and **1776 (36%) do not** — the app leans hard on
// half-steps the ramp has never had, dominated by **6px ×996 and 2px ×510**. A value with an exact
// token is a straight defect: same pixels at the default density, and it starts obeying the slider.
// A 6px value cannot be converted without either adding rungs to the ramp or changing the spacing,
// and BOTH are the owner's call — so this rail gates only the first half and merely records the
// second.
//
// 🔴 THIS RAIL IS THE CEILING ONLY; IT DELIBERATELY SHIPS WITHOUT A CONVERSION SWEEP. The point of
// a shrink-only ratchet is that it is useful the moment it exists: 3135 is today's floor, a new
// `gap-2` reds it, and converting any utility lowers it. Landing the number first means the sweep
// can be done in whatever slices fit, by whoever, without the count silently drifting back up in
// between — which is exactly what happened to the earlier attempt at this, whose 2963 was measured
// 745 commits ago and had already been overtaken by 172.
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

/** px → rung. Tailwind's numeric scale is n × 4px, so the mapping is arithmetic, not taste. */
const RUNG: Record<number, string> = { 4: 'xs', 8: 's', 12: 'm', 16: 'l', 20: 'xl', 24: '2xl', 28: '3xl' }

/** 🔴 SHRINK-ONLY. Measured 2026-09-20 on `a9c03d57e` — today's actual floor, not an aspiration.
 *  A new `gap-2` reds this; converting one lowers it. It may never be RAISED: a ceiling that moves
 *  up on demand is not a ratchet, it is a comment. (An earlier attempt at this rail carried 2963,
 *  measured 745 commits earlier; by the time it was read the tree was at 3135. That is the failure
 *  mode this number is dated and sha-stamped to avoid — re-measure and re-state, never bump.) */
const MAPPABLE_CEILING = 3135

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
