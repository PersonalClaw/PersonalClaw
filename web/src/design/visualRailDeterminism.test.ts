import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The visual rail's DETERMINISM contract ──────────────────────────────────────────────────
// A golden comparison answers a question about the COMMIT. At head it could answer a question
// about the HOST instead, and it said so in the vocabulary of pixels.
//
// Two same-sha `npm run e2e:visual` invocations on a loaded box gave 26 passed / 15 failed and
// then 21 passed / 20 failed, with diffs of 0.02–0.04 against the 0.01 cap (28,130 px on
// `dashboard-light`) and a failing set that did not repeat between the two runs. The same
// goldens are 41/41 zero-diff on a quiet one. No golden byte changed between any of those runs;
// the machine's load did.
//
// Two mechanisms, and this rail pins the fix to each.
//
//  1. Every settle stage in `e2e/helpers.ts` was non-throwing with a SWALLOWED timeout, so an
//     exhausted budget did not degrade the measurement — it FABRICATED one. The screenshot was
//     taken mid-load and `toHaveScreenshot` attributed the difference to the pixels. A skeleton
//     is a different HEIGHT from the rows that replace it, and a `fullPage` capture's dimensions
//     are the page height, so a mid-load capture differs from a settled one by whole content
//     bands — which is what a 0.04 ratio on an unchanged golden actually was.
//
//  2. There was no barrier for "the content has arrived" at all. `waitForLoadState('networkidle')`
//     could never be one — the app holds long-lived `EventSource` streams open, so those routes
//     never go idle and its 5s cap always fell through — and `settleDom`'s 400ms quiet window is
//     a statement about the DOM, not the load: a pending fetch mutates nothing, so quiescence is
//     routinely reached mid-load. Measured with 2s of latency injected on every `/api/**`
//     response, `settleDom` returned on `#/tools` at 6053ms with 19 loading affordances still
//     rendered. `settleEntranceAnimations` was no help either: it was VACUOUS on every surface
//     (see the fade test below).
//
// It reads the harness as TEXT, like `visualRailInvocation.test.ts` next door, and for the same
// reason: the properties here are structural — a return type, an argument, a call order, a
// threshold — and a browser is neither needed nor affordable to check them. This rail cannot
// prove the suite is deterministic; only two zero-diff runs can, and they are the acceptance
// test. What it prevents is the one-line edit that silently restores the old behaviour while
// every helper still looks right.

const WEB = process.cwd()
const HELPERS = readFileSync(join(WEB, 'e2e', 'helpers.ts'), 'utf8')
const VISUAL_SPEC = readFileSync(join(WEB, 'e2e', 'visual.spec.ts'), 'utf8')
const PW_CONFIG = readFileSync(join(WEB, 'playwright.config.ts'), 'utf8')

/** Every stage `gotoRoute` runs, each of which used to swallow its own timeout. */
const STAGES = ['settleShellChrome', 'settleDom', 'settleEntranceAnimations']

/** The body of a top-level `export async function`, for the order-of-operations checks. */
function bodyOf(source: string, fn: string): string {
  const start = source.indexOf(`export async function ${fn}(`)
  expect(start, `${fn} must exist in e2e/helpers.ts`).toBeGreaterThan(-1)
  const rest = source.slice(start)
  // Top-level functions are closed by a `}` in the first column.
  const end = rest.indexOf('\n}')
  return rest.slice(0, end === -1 ? undefined : end)
}

describe('the visual rail measures the commit, not the host load', () => {
  it.each(STAGES)('%s reports whether it settled instead of swallowing its timeout', (stage) => {
    expect(
      HELPERS,
      `${stage} must exist and return Promise<boolean>. A stage that resolves void cannot tell\n` +
        `its caller that its budget ran out, which is how a starved page was screenshotted anyway\n` +
        `and the resulting whole-content-band difference was published as a 0.02-0.04 drift ratio.\n` +
        `The stage must still NOT throw — the a11y and walkthrough sweeps tolerate a late card and\n` +
        `must keep proceeding — so the signal has to travel by return value.`,
    ).toContain(`export async function ${stage}(`)
    const signature = bodyOf(HELPERS, stage)
    expect(
      signature.slice(0, signature.indexOf('{')),
      `${stage}'s signature must end in Promise<boolean>`,
    ).toContain('Promise<boolean>')
  })

  it('refuses to diff a page that never reached rest, rather than calling it drift', () => {
    expect(
      HELPERS,
      `expectRouteScreenshot must take a SettleReport and assert on it. Without that argument the\n` +
        `stages' return values have no consumer and the whole fix is inert: an exhausted budget\n` +
        `would again be laundered into a diff number about a frame that does not exist once the\n` +
        `page settles.`,
    ).toMatch(/export async function expectRouteScreenshot\([^)]*settle: SettleReport/s)
    const body = bodyOf(HELPERS, 'expectRouteScreenshot')
    const refusal = body.indexOf('settle.exhausted')
    const capture = body.indexOf('toHaveScreenshot')
    expect(refusal, 'expectRouteScreenshot must read settle.exhausted').toBeGreaterThan(-1)
    expect(capture, 'expectRouteScreenshot must still take the screenshot').toBeGreaterThan(-1)
    expect(
      refusal,
      `the refusal must come BEFORE toHaveScreenshot. After it, the screenshot is already taken\n` +
        `and compared — the failure would name the pixels first and the starved settle second,\n` +
        `which is the exact misattribution this rail exists to stop.`,
    ).toBeLessThan(capture)
  })

  it('wires the report from gotoRoute into the comparison', () => {
    expect(
      VISUAL_SPEC,
      `visual.spec.ts must pass gotoRoute's SettleReport to expectRouteScreenshot. Dropping it is\n` +
        `the one edit that silently restores the old behaviour while every helper still looks right.`,
    ).toMatch(/const settle = await gotoRoute\(/)
    expect(VISUAL_SPEC).toMatch(/expectRouteScreenshot\(page, [^)]*, settle\)/)
  })

  // Every stage is now load-BEARING for the pixel rail, which makes each one's SATISFIABILITY a
  // precondition of the rail working at all. Both halves of the fade predicate have already
  // shipped in a permanently-false form: first the infinite `.status-pulse`, which `SystemWidget`
  // renders on every connected surface, and then the static inline `opacity: 0.65` the nav rail
  // gives its section labels (`ui/NavRail.tsx:139`). The rail lives in the SHELL and
  // `assertShellMounted` requires it on every route, so `midFade` was permanently TRUE on all 40
  // surfaces and the stage was a 2s no-op everywhere — including for the a11y sweep, the consumer
  // whose composited-contrast flake it was written for. A predicate that can never resolve either
  // does nothing (when its timeout is swallowed) or refuses everything (once it is enforced).
  it('detects a fade by CHANGE, not by a translucent value', () => {
    const body = bodyOf(HELPERS, 'settleEntranceAnimations')
    expect(
      body,
      `settleEntranceAnimations must compare consecutive samples. A predicate that asks whether\n` +
        `any inline opacity merely IS between 0 and 1 is permanently true on every route — the nav\n` +
        `rail's section labels alone carry a static opacity: 0.65 — so it can never resolve.`,
    ).toContain('__pcFadeSample')
    expect(
      body,
      `the previous sample must be what decides midFade, or the comparison is decoration`,
    ).toMatch(/previous !== sample/)
  })

  it('treats quiet-AND-loaded as one barrier, not quiet alone', () => {
    // Each condition on its own is satisfied MID-LOAD, measured with 2s of injected API
    // latency: zero loading affordances is reached transiently (`#/dashboard` first reads zero
    // at 506ms, then raises skeletons again until 5755ms) and DOM quiescence is reached with
    // skeletons up (`#/tools`, 19 of them still rendered at 6053ms). Only the conjunction is a
    // resting state, which is why this is one predicate and not two sequential stages.
    const body = bodyOf(HELPERS, 'settleDom')
    expect(
      body,
      `settleDom's quiet window must also require that nothing is still loading. Quiet alone is\n` +
        `not rest: a pending fetch mutates nothing, so the window elapses mid-load and the\n` +
        `screenshot records the skeleton.`,
    ).toContain('querySelectorAll(selector).length === 0')
    expect(
      body,
      `and it must RE-ARM rather than resolve when an affordance is still up, or the check is\n` +
        `a one-shot that the next 400ms of quiet defeats`,
    ).toMatch(/else bump\(\)/)
    expect(
      HELPERS,
      `both halves of the skeleton kit must be matched. The bare atom (ui/ListScaffold.tsx:251)\n` +
        `carries .skeleton and NO aria state; the shaped wrappers carry aria-busy and no\n` +
        `.skeleton class. Either selector alone silently misses the routes that use the other.`,
    ).toMatch(/\.skeleton, \[aria-busy="true"\]/)
  })

  it('has no networkidle wait left to fall back on', () => {
    // The CALL, not the word: the docstrings name the mechanism they replaced, and deleting that
    // explanation to satisfy a grep would lose the reason it cannot come back.
    const code = HELPERS.replace(/\/\*\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
    expect(
      code,
      `the networkidle wait must be GONE, not kept beside the new load barrier. It cannot work in\n` +
        `this app — useConfigFsWatch, DiagnosticsPanel, ModelsPanel and useModelDownloads each hold\n` +
        `an EventSource open, so those routes never go idle and the wait always burned its cap and\n` +
        `fell through. Two barriers where one is known-unsatisfiable is a dual path whose weaker\n` +
        `half decides the outcome.`,
    ).not.toContain('networkidle')
  })

  it('did NOT buy determinism by loosening the comparison', () => {
    // The whole point: every number this change touched is a BUDGET (how long a precondition may
    // take), never a TOLERANCE (how much difference a comparison accepts). If that ever flips, the
    // rail is protecting nothing and the recorded evidence is meaningless.
    expect(
      PW_CONFIG,
      `maxDiffPixelRatio must stay 0.01. A determinism fix that raises the cap has not made the\n` +
        `rail deterministic, it has made it blind — and a failing set that differs on every run at\n` +
        `one sha is not even addressed by a bigger tolerance.`,
    ).toContain('maxDiffPixelRatio: 0.01')
    expect(
      VISUAL_SPEC,
      `the pixel rail must not exempt a route. Skipping a starved surface would let real drift\n` +
        `through on it forever, which is strictly worse than failing loudly on a busy host.`,
    ).not.toMatch(/test\.skip|test\.fixme|\.fail\(/)
  })
})
