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
// 🪤 A RAIL MEASURES THE PROGRAM, NOT THE EXPLANATION OF IT — and not the code NEAR the program either.
// The two scanners below used to read raw source inside a fixed character window after
// `useCachedData(`, which made them wrong in two compounding ways:
//
//   1. The first adopter to DOCUMENT the swallow it removed tripped them: `ArchivePanel`'s comment
//      quotes the `.catch(() => [] as SessionArchive[])` it deleted, three lines under the call.
//      (Fourth time a ratchet here has counted its own prose as code — a `<button>` in comment text
//      broke the primitive-adoption ratchet twice.)
//   2. 🔴 Worse, THE COMMENTS WERE LOAD-BEARING. Stripping them alone made `#/discover` fail, because
//      its `dismiss` MUTATION's `.catch(() => {})` sits 2 lines below its fetcher and had been pushed
//      out of the 220-char window by the comment between them. The window never measured "the fetcher
//      swallows"; it measured "nothing that looks like a swallow happens to be nearby".
//
// So the scan is structural now: paren-match the call and look ONLY at its argument list. A comment
// cannot pad it, and a mutation two lines down cannot be mistaken for the fetcher.
const codeOf = (abs: string) =>
  readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const SWALLOW = /\.catch\(\(\)\s*=>\s*(\[\]|null|undefined|\{\})/

/** Every `useCachedData('key', …)` call in a file, as `{ key, args, line }` — `args` is the call's own
 *  argument list, paren-matched from the opening paren to its partner. */
function cachedCalls(src: string): { key: string; args: string; line: number }[] {
  const out: { key: string; args: string; line: number }[] = []
  for (const m of src.matchAll(/useCachedData(?:<[^>]*>)?\(/g)) {
    const start = (m.index ?? 0) + m[0].length
    let i = start
    let depth = 1
    while (i < src.length && depth > 0) {
      const c = src[i]
      if (c === '(') depth++
      else if (c === ')') depth--
      i++
    }
    const args = src.slice(start, i - 1)
    const key = args.match(/^\s*'([^']+)'/)?.[1]
    if (key) out.push({ key, args, line: src.slice(0, m.index).split('\n').length })
  }
  return out
}
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
    // `#/discover` is the sharpest instance of the family so far: its `.catch(() => null)` made `data`
    // falsy, which the render reads as "Discover is off" — so a 500 did not merely stay silent, it made
    // a FALSE CLAIM ABOUT A SETTING and offered "Open Settings" to turn tips back on. Measured against
    // a 500 on `/api/legibility/discover`: "Discover is off — … Turn them back on in Settings ›
    // Legibility." A confident wrong answer beats a silent one for damage.
    'pages/discover/DiscoverPage.tsx',
    // `#/apps` swallowed TWICE — the installed list (`() => []`) and the Store catalog (`() => null`).
    // Measured against a 500 on `/api/apps*`: "No apps installed — Browse the Store to add apps, or
    // install one from a local path or git URL" plus a Browse Store CTA, and the Store tab renders as
    // an empty shelf. Both guards are per-fetch, so a catalog outage never claims your Library is empty.
    'pages/apps/AppsSection.tsx',
    // `#/settings/inbox` swallowed to `null`, which the hook PERSISTED — `sessionStorage
    // ['cache:settings:inbox'] === "null"` — so all THREE consumers of that key seeded null from cache and
    // read it as loaded. Its own gate then rendered `<FormSkeleton>` forever: measured with the GET at 500,
    // 0 editable controls, 22 shimmering skeleton nodes, no error, no retry. Adding it here also puts the
    // key-poisoning check below over `'settings:inbox'`.
    'pages/settings/InboxSettingsPanel.tsx',
    // `#/chat` joins late and deliberately. #1162 gave the two `chat:sessions` readers an error
    // branch but left SIX other reads swallowing, so this file could not satisfy the
    // no-swallow-anywhere bar and got a resource-scoped rail instead
    // (`pages/chat/sessionLoadHonesty.test.ts`). Those six are now gone:
    //   • `chat:suggestions` · `chat:starters` — persisted decoration strips that hide when empty.
    //     A swallowed rejection resolved to `[]`, which the hook then persists as though it were an
    //     answer. No error UI: a strip that quietly does not appear claims nothing.
    //   • `chat:stream-reveal` — a persisted CONFIG VALUE fabricated as `'smooth'`. The default
    //     belongs at the use site (`streamRevealCfg === 'immediate'`), where it is a default rather
    //     than a stored answer.
    //   • `chat:artifact-picker` — its empty state TEACHES ("Ask in chat for a widget…"), so a 500
    //     told a user with artifacts to go make their first one. Now a `FieldError`, error branch
    //     first.
    //   • `chat:folders` · `chat:tags` — feed a menu whose empty state INSTRUCTS ("Create a folder
    //     or tag first"). The failure is threaded to it as `orgLoadFailed` so it says so instead.
    'pages/ChatPage.tsx',
    // The `#/settings/*` async lists, taken as a FAMILY rather than a surface: a census of every
    // settings reader whose data renders as a list found ten, of which five conflated a failed load
    // with an empty one. These two are the page-body-scale members and fit this rail unchanged.
    // `#/settings/audit` is the sharpest case in the whole family — "No matching events" is also what a
    // tamper-evident security log says when nothing happened, so a read failure rendered as silence.
    // Measured at 500 with a cold sessionStorage: no `role="alert"`, no retry, and the server's own
    // message nowhere on the page. The other three members (the memory Audit tab, the remote-provider
    // region, the custom-rules list) are pinned per site in
    // `pages/settings/settingsListHonesty.test.ts` — see its header for why they cannot live here.
    'pages/settings/ArchivePanel.tsx',
    'pages/settings/AuditPanel.tsx',
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
      //     is what `#/workflows` does with its `Promise.all`;
      //   • or it is destructured plainly as `error` — the most direct form, and the one a surface with
      //     a single fetch should use (`#/discover`).
      // Substituting data (`.catch(() => [])` / `(() => null)`) satisfies none of them, which is the point.
      //
      // ⚠️ THIRD WIDENING. This matcher has now been wrong about three separate adopters: it demanded the
      // alias `loadErr` (#1127 widened it), then an alias containing "err" at all (#1132's plain `error`),
      // and its sibling reachability check demanded source order (#1129 replaced it). Each time it had
      // encoded an accident of whoever adopted first. **When a rail rejects a new adopter, check the rail
      // before the adopter.**
      expect(src, 'must capture the rejection, not discard it').toMatch(
        /\berror\s*[,}]|error:\s*\w*(?:err|Err)\w*|catch\(\(\w+\)\s*=>\s*\{[^}]*[Ee]rr\w*\(/,
      )
      // And the error branch must precede the skeleton/empty branches, or it never runs.
      // REACHABILITY, not source order. The first two adopters put `<LoadError>` textually before their
      // skeleton, so an earlier version of this rail asserted exactly that — and it rejected
      // `#/workflows`, whose error branch is perfectly reachable while sitting AFTER its `<Loading />`
      // because a separate `loading` flag is cleared in a `finally` and therefore opens on failure.
      // Source order was a proxy for the real property; these are the two shapes that satisfy it:
      const errAt = src.search(/<LoadError\b/)
      // Three loading primitives ship: `ListSkeleton` (a shaped list placeholder), `Loading` (a spinner)
      // and `FormSkeleton` (a shaped form placeholder, used by the settings panels). The rail knew the
      // first two because the first adopters used them — the same accident this file has now corrected
      // four times. The vocabulary is what widens; the property being checked does not.
      const loadAt = Math.min(...[/<ListSkeleton\b/, /<Loading\s*\/>/, /<FormSkeleton\b/].map((re) => {
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

  it('no OTHER consumer of an adopter\'s cache key swallows either', () => {
    // 🔴 THE ONE THAT ACTUALLY BIT. `useCachedData` caches by KEY, so a swallow at ANY call site
    // resolves with a substitute value that the hook then persists — and every other consumer of that
    // key reads it as a success, making their own `data === undefined && error` branch unreachable.
    // Measured on `#/apps`: the `'apps'` key has FOUR consumers (the shell's nav badge, two settings
    // panels, and the Store), three of which swallowed. With all `/api/apps*` calls at 500 and the
    // Store's own swallow already removed, `sessionStorage['cache:apps']` was `"[]"` and the page still
    // rendered "No apps installed". Fixing one file was not enough; fixing the key was.
    const files: string[] = []
    for (const abs of walk(SRC)) files.push(abs)
    // key → [file:line, swallows?]
    const consumers = new Map<string, { at: string; swallows: boolean }[]>()
    for (const abs of files) {
      for (const c of cachedCalls(codeOf(abs))) {
        const at = `${abs.slice(SRC.length + 1)}:${c.line}`
        consumers.set(c.key, [...(consumers.get(c.key) ?? []), { at, swallows: SWALLOW.test(c.args) }])
      }
    }
    // Sanity: the scan must actually see the multi-consumer key it was written for.
    const appsConsumers = consumers.get('apps') ?? []
    expect(appsConsumers.length, "the scan must find the 'apps' key's consumers").toBeGreaterThanOrEqual(3)

    const adopterKeys = new Set<string>()
    for (const rel of ADOPTERS) {
      for (const c of cachedCalls(codeOf(join(SRC, rel)))) adopterKeys.add(c.key)
    }
    expect(adopterKeys.size, 'the adopters must declare at least one cache key').toBeGreaterThan(0)

    const poisoners: string[] = []
    for (const key of adopterKeys) {
      for (const c of consumers.get(key) ?? []) if (c.swallows) poisoners.push(`${c.at} (key '${key}')`)
    }
    expect(poisoners, 'a swallow here makes every other consumer of the key unable to see the failure').toEqual([])
  })

  it('no adopter swallows the rejection inside its fetcher', () => {
    // `.catch(() => [])` inside the fetcher makes the error branch unreachable by construction: the
    // hook is handed a successful empty list. A surface that renders LoadError while still swallowing
    // is asserting a state it can never enter.
    for (const rel of ADOPTERS) {
      const swallowing = cachedCalls(codeOf(join(SRC, rel))).filter((c) => SWALLOW.test(c.args))
      expect(swallowing.map((c) => `${c.key}:${c.line}`), `${rel} swallows a fetch rejection`).toEqual([])
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
