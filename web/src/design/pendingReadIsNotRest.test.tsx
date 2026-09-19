import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TriageDigestCard } from '../pages/inbox/TriageDigestCard'
import { resetDataStore } from '../lib/data/store'
import type { TriageDigestView } from '../lib/api'

// ── A PENDING READ IS NOT A RESTING PAGE ─────────────────────────────────────────────────────────
//
// The companion rail to `visualRailDeterminism.test.ts`. That one proves the HARNESS measures the
// commit rather than the host; this one closes the hole the harness cannot see from the outside — a
// component that renders `null` while its GET is in flight.
//
// Why that single line is expensive out of all proportion to its size: it makes "still asking"
// byte-identical to "at rest with nothing to show". While the gap is on screen the DOM is quiet, no
// loading affordance exists, and no animation is running — so every independent observer of "has this
// page finished?" agrees that it has. A reader's eye. DOM quiescence. The skeleton sweep. Playwright's
// two-identical-screenshots stability protocol. All four say rest, and then the read lands and a whole
// content band appears, shifting everything around it.
//
// That is why it presents as pixel "drift" on a route nobody touched, and why recapturing is the one
// response that cannot work: `e2e:update` records whichever side of the race won that run and moves
// the failure to the next one. The fix is always to make the wait SAYABLE.
//
// 🔑 MEASURED, not reasoned about. At origin/main 398e6b7a6, two `npm run e2e:visual` runs on one
// pinned head:
//
//     #/inbox   ONE toHaveScreenshot call produced two consecutive screenshots 212,190px apart,
//               while the committed golden was 20px from the settled render.
//     #/chat    the same call produced two screenshots 501,409px apart — 54% of the image — so the
//               route never reached rest and yielded no verdict about its baseline at all.
//
// In both cases the BASELINE was right and the RUN never settled. Nothing here was fixed by touching
// a golden, a tolerance or a budget; three components were taught to say they were waiting.
//
// `#3169` found and fixed the first instance of this class (`IdentityReportPanel`, ~195px). These are
// the three it did not reach. The class is the finding — expect more, and add them here.

const proactiveDigest = vi.fn()

vi.mock('../lib/api', () => ({
  api: {
    proactiveDigest: () => proactiveDigest(),
    proactiveReply: () => Promise.resolve({ ok: true, outcome: 'acted', results: [] }),
    proactiveInstall: () => Promise.resolve({ ok: true, created: true }),
    autonomyUndo: () => Promise.resolve({ ok: true, code: 'reversed' }),
  },
}))
vi.mock('../app/appSdk', () => ({ notify: () => undefined }))

/** The settled "never installed" arm — the §5.4 pack card the golden actually holds. */
function uninstalled(): TriageDigestView {
  return { state: 'uninstalled', enabled: false, installed: false, error: '' }
}

/** What `e2e/helpers.ts`'s `LOADING_SELECTOR` counts. Kept character-identical to the harness on
 *  purpose: a rail that looked for a DIFFERENT affordance than the barrier waits on would pass while
 *  the barrier still stepped over the component. */
const LOADING_SELECTOR = '.skeleton, [aria-busy="true"]'
const affordances = () => document.querySelectorAll(LOADING_SELECTOR).length

beforeEach(() => {
  proactiveDigest.mockReset()
  resetDataStore()
  try { window.sessionStorage.clear() } catch { /* jsdom without storage */ }
})

describe('a component waiting on a read says so, so the settle barrier can see it', () => {
  it('TriageDigestCard renders an affordance while its digest read is in flight', () => {
    // A promise that never settles IS the pending state — no fake timers, no flushing, nothing that
    // could accidentally advance past the window under test.
    proactiveDigest.mockImplementation(() => new Promise<TriageDigestView>(() => {}))

    render(<TriageDigestCard />)

    expect(
      affordances(),
      'the pending card must render a loading affordance the page can be observed to be waiting on — '
        + 'rendering null here is what let #/inbox photograph itself mid-flight',
    ).toBeGreaterThan(0)

    // And it must not assert any of the five settled claims while it is still asking. "Morning
    // triage" is the heading every settled arm shares, so its absence covers all of them.
    expect(screen.queryByText('Morning triage')).toBeNull()
  })

  it('and stops once the read lands, so the SETTLED render is unchanged (no baseline moves)', async () => {
    proactiveDigest.mockImplementation(() => Promise.resolve(uninstalled()))

    render(<TriageDigestCard />)

    // The pack card, exactly as the committed golden holds it.
    await waitFor(() => expect(screen.getByText('Morning triage')).toBeTruthy())
    expect(screen.getByRole('button', { name: /Install/ })).toBeTruthy()

    // 🪤 THE HALF THAT MAKES THIS A RAIL RATHER THAN A DECORATION. If the affordance survived into
    // the settled state, `settleDom` would never clear and every #/inbox golden would time out
    // instead of comparing — trading a race for a permanent failure. Zero at rest is the property
    // that makes the fix free: the skeleton exists only while a request is open, so the pixels the
    // baseline was captured from are untouched.
    expect(
      affordances(),
      'the settled card must leave NO loading affordance behind, or settleDom can never clear',
    ).toBe(0)
  })
})

// ── The two #/chat strips ────────────────────────────────────────────────────────────────────────
//
// `SuggestionChips` and `StarterChips` are module-private inside a 3,000-line page, so they are
// asserted the way `pages/dashboard/suggestionCapMatchesProducer.test.ts` already asserts this same
// file — by source, with a vacuity floor. A source rail proves a STRING and not a reachable control,
// which is a real weakness and the reason the floor below is not optional: every anchor must be found,
// or the rail is answering a different question confidently.
//
// The property: each strip must consult `loading` and put an affordance on screen BEFORE it reaches
// the `return null` that used to cover both the pending and the empty answer. "Silent when none are
// available" stays true — that is a product choice about the EMPTY answer, and it was never meant to
// cover the pending one.
describe('#/chat’s two prompt strips distinguish "still asking" from "none available"', () => {
  // This file sits at `web/src/design/`, so the repo root is four levels up. Anchored on
  // `import.meta.dirname` rather than `process.cwd()`, which differs between a root-level
  // `npm run test:web` and a `cd web && vitest`.
  const REPO = join(import.meta.dirname, '..', '..', '..')
  const chat = readFileSync(join(REPO, 'web', 'src', 'pages', 'ChatPage.tsx'), 'utf8')

  /** The body of one strip: from its `function <name>(` to the next top-level `function` after it. */
  function strip(name: string): string | null {
    const at = chat.indexOf(`function ${name}(`)
    if (at < 0) return null
    const next = chat.indexOf('\nfunction ', at + 1)
    return chat.slice(at, next < 0 ? chat.length : next)
  }

  const strips = { SuggestionChips: strip('SuggestionChips'), StarterChips: strip('StarterChips') }

  it('both strips were located (vacuity floor)', () => {
    // Each assertion below is a regex against a file this rail does not own. If a rename makes one
    // stop matching, every property after it passes on an empty string and reports nothing.
    for (const [name, body] of Object.entries(strips)) {
      expect(body, `${name} was not found in ChatPage.tsx — this rail has gone vacuous, fix the anchor`).toBeTruthy()
      expect(body!.length, `${name}'s body is implausibly short (${body!.length} chars)`).toBeGreaterThan(400)
      // The anchor that proves we sliced a strip and not some neighbour: each one reads its list
      // through useQuery and caps it.
      expect(body).toMatch(/useQuery\(/)
    }
  })

  it('each strip gates on useQuery’s loading flag', () => {
    for (const [name, body] of Object.entries(strips)) {
      expect(
        body,
        `${name} must destructure \`loading\` — \`data\` alone cannot tell a pending read from an empty one`,
      ).toMatch(/\{\s*data\s*,\s*loading\s*\}\s*=\s*useQuery/)
    }
  })

  it('and puts the affordance BEFORE the return-null, not after it', () => {
    for (const [name, body] of Object.entries(strips)) {
      const guard = body!.search(/if\s*\(\s*loading\s*&&\s*!data\s*\)/)
      const silent = body!.search(/if\s*\(\s*!items\.length\s*\)\s*return null/)
      expect(guard, `${name} has no \`if (loading && !data)\` arm`).toBeGreaterThanOrEqual(0)
      expect(silent, `${name} no longer has its silent-when-empty arm — did the contract change?`).toBeGreaterThanOrEqual(0)
      expect(
        guard,
        `${name} checks \`loading\` only AFTER returning null, so the pending frame still renders nothing`,
      ).toBeLessThan(silent)
      // The affordance has to be the one the harness counts, not merely some placeholder div. Any of
      // the three forms satisfies `LOADING_SELECTOR`: the `Skeleton` primitive (which emits
      // `class="skeleton …"`), a hand-rolled `.skeleton`, or an explicit `aria-busy="true"` region.
      const arm = body!.slice(guard, silent)
      expect(
        /<Skeleton\b|className="[^"]*\bskeleton\b|aria-busy="true"/.test(arm),
        `${name}'s pending arm renders no \`Skeleton\`/\`.skeleton\`/\`aria-busy\`, so e2e/helpers.ts's `
          + `LOADING_SELECTOR cannot see it and settleDom will step straight over the wait`,
      ).toBe(true)
    }
  })
})
