import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── The visual rail's INVOCATION contract ───────────────────────────────────────────────────
// `e2e/visual.spec.ts` compares 40 committed goldens, and those goldens were captured against a
// gateway with a FRESH home — `e2e/README.md` states that invariant explicitly, and it is the
// reason a data-backed route's EMPTY state is a valid baseline.
//
// The specs that drive real scripted chat turns (`a11y`, `chat`, `sessionMap`) write flywheel
// state into the ONE gateway every spec shares: `context.py` records an allocation sample per
// ambient render and `controller.py` calls `run_end.capture()` at run end. `#/learning` reads
// both back, so ITS rendering differs before and after a turn — and `fullyParallel` decides
// which side a mixed run sees. Measured 2026-09-19: visual-only is 40/40 zero-diff, while a
// mixed run reds `learning-light`/`learning-dark` at ~14,900 px each.
//
// So capture and verification must BOTH happen in a visual-only invocation. That is a property
// of two npm scripts, which nothing else in the tree checks — and a wrong `e2e:update` is how a
// contaminated golden gets committed in the first place. Hence this rail.
//
// It reads the scripts as TEXT rather than running them: the point is which tests each command
// selects, and a browser is neither needed nor affordable here. The pixel comparison stays where
// it belongs, in the visual suite.

const WEB = process.cwd()
const E2E = join(WEB, 'e2e')

const scripts = JSON.parse(readFileSync(join(WEB, 'package.json'), 'utf8')).scripts as Record<
  string,
  string
>

/** The tag `visual.spec.ts` declares and `e2e` selects on. A tag, not a title regex, so renaming
 *  a describe cannot silently fold the visual rail back in with the turn-driving specs. */
const TAG = '@visual'

describe('the visual rail runs as its own invocation', () => {
  it('recaptures visual-only, so a golden is captured under the invariant it is verified under', () => {
    expect(
      scripts['e2e:update'],
      `e2e:update must target e2e/visual.spec.ts. A bare \`playwright test --update-snapshots\`\n` +
        `also runs the turn-driving specs, so it regenerates #/learning's golden from its\n` +
        `POPULATED rendering — which \`npm run e2e:visual\` then reds, because that command sees\n` +
        `the empty one. That is not a stale baseline; it is two commands disagreeing about what\n` +
        `the surface is.`,
    ).toContain('e2e/visual.spec.ts')
  })

  it('verifies the whole suite without ever mixing the visual rail into a contaminated run', () => {
    const e2e = scripts.e2e
    expect(
      e2e,
      `npm run e2e must EXCLUDE the ${TAG} tests from the main pass (--grep-invert ${TAG})`,
    ).toContain(`--grep-invert ${TAG}`)
    expect(
      e2e,
      `npm run e2e must then run the visual rail as its own invocation, or excluding it above\n` +
        `silently DROPS all 40 goldens from the suite — a green run that compared nothing.`,
    ).toContain('e2e:visual')
  })

  it('tags visual.spec.ts, so the exclusion above actually selects it', () => {
    const spec = readFileSync(join(E2E, 'visual.spec.ts'), 'utf8')
    expect(
      spec,
      `e2e/visual.spec.ts must tag its describes '${TAG}'. Without the tag, e2e's\n` +
        `--grep-invert matches nothing, the main pass runs the goldens against a contaminated\n` +
        `gateway again, and the second invocation just runs them twice.`,
    ).toContain(`tag: '${TAG}'`)
  })

  // The premise that makes narrowing `e2e:update` LOSSLESS. If a second spec ever takes a
  // snapshot, a visual-only regenerate stops regenerating it — silently, because a missing
  // golden reds in the OTHER spec and reads as that spec's bug. Then either that spec joins
  // the visual rail's pristine-gateway contract, or `e2e:update` has to grow to cover it.
  it('is the ONLY snapshot-bearing spec — the reason a visual-only regenerate loses nothing', () => {
    const offenders = readdirSync(E2E)
      .filter((f) => f.endsWith('.spec.ts') && f !== 'visual.spec.ts')
      // `expectRouteScreenshot` too, not just the raw matchers: the repo's ONE snapshot call
      // site lives in `helpers.ts`, so a new spec would take a screenshot without ever
      // spelling `toHaveScreenshot` — the exact form this check would otherwise miss.
      .filter((f) =>
        /toHaveScreenshot|toMatchSnapshot|expectRouteScreenshot/.test(
          readFileSync(join(E2E, f), 'utf8'),
        ),
      )
    expect(
      offenders,
      `these specs take snapshots but are NOT covered by e2e:update's visual-only regenerate:\n  ` +
        `${offenders.join(', ')}\n` +
        `Either move the assertion into e2e/visual.spec.ts (and inherit its pristine-gateway\n` +
        `contract), or widen e2e:update — and then say how its goldens stay deterministic while\n` +
        `sharing a run with the specs that drive real chat turns.`,
    ).toEqual([])
  })
})
