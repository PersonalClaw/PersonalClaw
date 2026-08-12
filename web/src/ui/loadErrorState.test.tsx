import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { LoadError, EmptyState } from './ListScaffold'

// ── A failed load is not an empty collection ──────────────────────────────────────────
//
// `useCachedData` returns `{ data, loading, error, refresh }`. Measured: **3 of 106 call
// sites read `error`.** The other 103 branch on `data === undefined` only, so a failed fetch
// falls through to the same branch as a genuinely empty result — the user is told "you have
// none" when the truth is "we could not load it", with no retry and nothing announced.
//
// Driven, not inferred. Intercepting `/api/projects` with a 500 (and letting boot succeed, so
// the app does not fall back to onboarding) rendered:
//
//   before:  "No projects yet"   + the New-project CTA, no alert, no retry
//   after:   alert → heading "Couldn't load your projects"
//                  → paragraph "probe-induced failure"      ← the server's own message
//                  → button "Retry"
//
// and Retry re-fetches: with the route restored it cleared the alert and rendered 5 rows.
//
// 🔑 WHY `role="alert"` HERE AND NOT ON `EmptyState`. A load failure is unrequested bad news
// that changes what the screen MEANS — it has to interrupt. "You have none" is a normal
// answer to a normal question, so `EmptyState` deliberately has no live region. Same
// distinction the toast host draws between assertive errors and polite confirmations.
//
// 🪤 ORDER MATTERS AND IS EASY TO GET WRONG. `data === undefined` is true for the loading,
// error AND empty branches, so the error test must come FIRST or it is unreachable:
//
//     {data === undefined && error ? <LoadError … />
//      : data === undefined      ? <ListSkeleton />
//      : data.length === 0       ? <EmptyState … />
//      : rows}

describe('LoadError announces and offers recovery', () => {
  it('is an alert — a load failure interrupts', () => {
    const { container } = render(<LoadError what="projects" />)
    expect(container.querySelector('[role="alert"]'), 'a failed load must be announced').not.toBeNull()
  })

  it('EmptyState is NOT an alert — "you have none" is a normal answer', () => {
    // The contrast is the point: if both were alerts, every empty list would interrupt.
    const { container } = render(<EmptyState title="No projects yet" />)
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it("names what failed, so the message isn't generic", () => {
    render(<LoadError what="projects" />)
    expect(screen.getByRole('heading', { name: /Couldn't load your projects/ })).toBeTruthy()
  })

  it("surfaces the server's own message when there is one", () => {
    // A bare "something went wrong" hides the one detail that helps.
    render(<LoadError what="projects" error={new Error('gateway timed out')} />)
    expect(screen.getByText('gateway timed out')).toBeTruthy()
  })

  it('falls back to a reassuring line when the error has no message', () => {
    render(<LoadError what="projects" error={{}} />)
    expect(screen.getByText(/Your projects are safe/)).toBeTruthy()
  })

  it('offers a retry that re-runs the fetch', () => {
    const onRetry = vi.fn()
    render(<LoadError what="projects" error={new Error('x')} onRetry={onRetry} />)
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('omits the retry button when the surface cannot retry', () => {
    render(<LoadError what="projects" error={new Error('x')} />)
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull()
  })

  it('hides the decorative icon from assistive tech', () => {
    // The heading already carries the meaning; an announced glyph is noise.
    const { container } = render(<LoadError what="projects" />)
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })
})

// ── The call-site half ────────────────────────────────────────────────────────────────
// The primitive existing is not the fix; a surface has to USE it. This pins the two that do,
// and deliberately does NOT assert the other ~100 — converting them is a per-surface product
// decision (what the retry re-runs, whether a cached copy should still show), logged as a
// follow-up rather than swept.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

describe('the migrated surfaces read the error', () => {
  // `#/learning` joined after a measured failure: with both learning endpoints returning 500 and a
  // COLD sessionStorage (a warm cache masks this entirely), the page rendered "Nothing to review —
  // proposals appear here when the system notices a pattern worth offering" with **no error text
  // anywhere**, and its capture-week panel — the whole point of the surface, the days capture never
  // ran — simply vanished. The most confident possible way to say the opposite of what happened.
  const ADOPTERS = [
    'pages/projects/ProjectsSection.tsx',
    'pages/code/CodeSection.tsx',
    'pages/learning/LearningPage.tsx',
    // `#/prompts` is the harsher variant: its fetchers carried `.catch(() => [])`, so the rejection
    // never reached the hook and `error` could not have been read even if someone tried. Measured
    // against a 500 with a cold sessionStorage, it rendered "No user prompts — user prompts are
    // invoked in chat with filled-in {{variables}}" plus a New-prompt CTA, with no error text and no
    // live region. Removing the swallow is half the fix; the branch is the other half.
    'pages/prompts/PromptsListPage.tsx',
    // `#/workflows` does not use the hook at all — it hand-rolls `useState` + `Promise.all` with a
    // `.catch` per read. Measured with all three workflow endpoints at 500: "No workflow runs yet —
    // start one from the Definitions tab", no error text, no live region. Its THIRD read keeps its
    // fallback on purpose (surfacing is a freshness column; a startable list beats an error for it),
    // which is why the swallow check below is scoped to the hook's own fetchers.
    'pages/workflows/WorkflowsListPage.tsx',
  ]

  for (const rel of ADOPTERS) {
    it(`${rel} branches on the load error before the empty state`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must render the shared primitive').toMatch(/<LoadError\b/)
      // The property that makes the branch possible is that the rejection is CAPTURED rather than
      // discarded. Two shapes qualify, and both ship here:
      //   • `useCachedData` consumers destructure it — `error: somethingErr` (the alias is free-form
      //     because a surface can guard more than one fetch; `#/learning` has two and cannot name both
      //     `loadErr`);
      //   • a hand-rolled loader catches into state — `.catch((e) => { setSomethingErr(e); … })`, which
      //     is what `#/workflows` does with its `Promise.all`.
      // Substituting data (`.catch(() => [])`) satisfies neither, which is the whole point.
      expect(src, 'must capture the rejection, not discard it').toMatch(
        /error:\s*\w*(?:err|Err)\w*|catch\(\(\w+\)\s*=>\s*\{[^}]*[Ee]rr\w*\(/,
      )
      // And the error branch must precede the skeleton/empty branches, or it never runs.
      // REACHABILITY, not source order. The first two adopters put `<LoadError>` textually before their
      // skeleton, so an earlier version of this rail asserted exactly that — and it rejected
      // `#/workflows`, whose error branch is perfectly reachable while sitting AFTER its `<Loading />`
      // because a separate `loading` flag is cleared in a `finally` and therefore opens on failure.
      // Source order was a proxy for the real property; these are the two shapes that satisfy it:
      const errAt = src.search(/<LoadError\b/)
      const loadAt = Math.min(...[/<ListSkeleton\b/, /<Loading\s*\/>/].map((re) => {
        const i = src.search(re)
        return i === -1 ? Number.POSITIVE_INFINITY : i
      }))
      expect(loadAt, 'the surface must have a loading state at all').toBeLessThan(Number.POSITIVE_INFINITY)
      const errorBranchFirst = errAt < loadAt
      const loadingClearedOnFailure = /finally\s*\{[^}]*setLoading\(false\)/.test(src)
      expect(
        errorBranchFirst || loadingClearedOnFailure,
        'the error branch must be reachable: either it precedes the loading branch, or the loading flag is cleared in a finally so a failure gets past it',
      ).toBe(true)
    })
  }

  it('no adopter swallows the rejection inside its fetcher', () => {
    // `.catch(() => [])` inside the fetcher makes the error branch unreachable by construction: the
    // hook is handed a successful empty list. A surface that renders LoadError while still swallowing
    // is asserting a state it can never enter.
    for (const rel of ADOPTERS) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      const swallowing = [...src.matchAll(/useCachedData[\s\S]{0,220}?\.catch\(\(\)\s*=>\s*(\[\]|null|undefined|\{\})/g)]
      expect(swallowing.map((m) => m[0].slice(0, 60)), `${rel} swallows a fetch rejection`).toEqual([])
    }
  })

  it('the primitive is exported from the list kit, beside EmptyState', () => {
    // Co-located on purpose: the two are alternative answers to the same condition, and a
    // surface reaching for one should see the other.
    const kit = readFileSync(join(SRC, 'ui/ListScaffold.tsx'), 'utf8')
    expect(kit).toMatch(/export function LoadError\b/)
    expect(kit).toMatch(/export function EmptyState\b/)
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length, 'the walker must find the tree').toBeGreaterThan(200)
  })
})
