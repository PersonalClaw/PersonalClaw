import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { LoadError, EmptyState } from './ListScaffold'

// ── A failed load is not an empty collection ──────────────────────────────────────────
//
// `useQuery` returns `{ data, loading, error, refresh }`. Measured: **3 of 106 call
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
    // The reassurance is now noun-free: it used to read "Your ${what} are safe", ungrammatical for
    // the many SINGULAR nouns callers pass ("Your project are safe"). The headline still names the
    // thing; the body no longer has to. See loadErrorSentence.test.ts for the tree-wide contract.
    render(<LoadError what="projects" error={{}} />)
    expect(screen.getByText(/this is just a load error, and nothing was lost/)).toBeTruthy()
  })

  it('the fallback reads grammatically for a SINGULAR noun too', () => {
    // The regression that motivated the rewrite: "project" (singular) must not produce "are safe".
    render(<LoadError what="project" error={{}} />)
    expect(screen.getByText(/Couldn't load your project/)).toBeTruthy()
    expect(screen.queryByText(/are safe/), 'the old plural-only copy is gone').toBeNull()
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
// `useQuery(`, which made them wrong in two compounding ways:
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
//
// 🪤 THE STRIPPER MUST NOT MOVE A LINE. Both replacements used to delete newlines, and every line
// number this file reports in a failure message is counted from the STRIPPED text — so the numbers
// drifted upward through any file with a block comment in it, and a reader sent to
// `MemoryPanel.tsx:214` found something else there. `^\s*//` was the worse of the two: `\s` matches
// `\n`, so a blank line followed by a comment line was consumed as ONE match and the blank line's
// newline went with it. Measured: with `^[ \t]*//` and block comments blanked rather than removed,
// the `lib/api.ts` control below resolves to its exact line.
const codeOf = (abs: string) =>
  readFileSync(abs, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^[ \t]*\/\/.*$/gm, '')
/** An absolute path as the repo-relative form the budget below is keyed on. */
const rel = (abs: string) => abs.slice(SRC.length + 1)

// 🪤 TWO WIDENINGS, both measured, both of which had been silently hiding real sites.
//
// 1. THE PARENTHESISED ARROW BODY. `(() => [])` was matched; `(() => ({}))` and `(() => (''))` were
//    not, because an arrow returning an object literal MUST wrap it in parens — so the single most
//    natural way to fabricate a config object was invisible to the one regex looking for
//    fabrication. Re-running the census with the paren admitted surfaced FIVE more files
//    (`app/usePlatform.ts`, `settings/AgentDefaultsPanel.tsx`, `settings/UpdatesPanel.tsx`,
//    `settings/VoicePanel.tsx`, and a second site in `skills/SkillInspector.tsx`) and took the
//    tree-wide count from 64 to 71. None of them was new; the scanner had never been able to see them.
// 2. `''` AND `""`. A fabricated empty STRING is the same lie for a scalar read that `[]` is for a
//    list — `.catch(() => '')` on a name read paints "—" and calls it an answer.
const SWALLOW = /\.catch\(\(\)\s*=>\s*\(?\s*(\[\]|null|undefined|\{\}|''|"")/

// ── the WHOLE-FILE scanner §B's census runs on ───────────────────────────────────────────────────
//
// 🔴 `SWALLOW` + `cachedCalls` CAN ONLY SEE INSIDE A `useQuery(` ARGUMENT LIST, and that is a
// structural blind spot, not a coverage gap: a file with ZERO `useQuery` calls is invisible to it
// however many rejections it fabricates a substitute for. Measured on this branch — 18 files and 25
// sites are in exactly that position, among them `ui/composer/MentionMenu.tsx` (the `@`-mention menu
// said "No matching files or knowledge" on a failed search AND cached that for 30s),
// `pages/files/browse/PathBar.tsx`, `pages/settings/DoctorPanel.tsx` and `lib/api.ts` itself. So the
// census below scans each production file WHOLE and `SWALLOW` is kept for the one property that is
// genuinely per-call: which consumer of a shared cache KEY poisons the others.
//
// 🪤 THERE ARE THREE SYNTACTIC FORMS AND THIS COUNTS TWO — deliberately, and the boundary is the
// point, because "widen the pattern" instructions in this family keep naming one form and reading as
// though they closed the gap:
//
//   A  `.catch(() => [] | null | undefined | '' | ({}))`   — expression body           93 sites
//   B  `.catch(() => { if (alive) setX([]) })`             — braced, assigns a literal 28 sites
//   C  `catch { }` / `.catch(() => {})`                    — empty body, no assignment  NOT counted
//
// C is NOT a swallow of this class. It discards a VOID rejection and fabricates nothing: localStorage
// reads, clipboard writes, socket teardown, fire-and-forget mutations. Counting it would add ~85
// sites that no surface renders a claim from, which is precisely the noise this file's paren-matching
// was introduced to escape. The distinction is syntactic and exact: an unparenthesised `{}` after
// `=>` is an empty BLOCK, a parenthesised `({})` is a fabricated object — so form A admits the second
// and not the first.
const SWALLOW_EMPTY = String.raw`\[\]|null\b|undefined\b|''|""|\{\s*\}`
/** 🪤 AND THE STRUCTURED ONE, which is how the scanner's OWN blind spot was found: fixing
 *  `settings/MemoryGraph.tsx` changed no count, because its fallback was
 *  `.catch(() => setSelfGraph({ nodes: [], edges: [] }))` — an object literal with PROPERTIES, which
 *  `\{\s*\}` does not match. A fabricated envelope is the same lie as a fabricated `[]` and a bigger
 *  one: it invents the whole shape of a response. Admitting it took `origin/main` 280c175f4 from
 *  66 files / 132 sites to 75 / 148 — 16 more sites in 9 more files, among them
 *  `knowledge/KnowledgeGraph.tsx`, a byte-for-byte twin of the MemoryGraph site.
 *
 *  This is deliberately a SYNTACTIC over-approximation — it counts `({ available: false })`, which is
 *  a capability probe's real answer. That is the design the whole section runs on: one wide rule, one
 *  semantic veto (`RECORDS_THE_ERROR`), and every correct site sitting in the budget with a written
 *  reason. Narrowing it instead would mean a rule that models control flow, and nobody can predict
 *  the verdict of one of those. */
const SWALLOW_SHAPE = String.raw`${SWALLOW_EMPTY}|\{\s*[\w'"]`
/** Form A, anchored at the first character after `=>`. `\[\]` is not followed by `\)`, which is what
 *  makes it CAST-TOLERANT: 21 of the 30 array-form sites in the tree are `catch(() => [] as Foo[])`,
 *  and a selector ending in `\)` would score 9 — a 70% false negative on the single commonest shape.
 *  Includes the inline setter (`catch(() => setX([]))`), which is the same fabrication via a state
 *  write rather than a return. A BARE `{` after `=>` is a block and is handled below, so the object
 *  shape is admitted only through the paren — form C stays out by construction. */
const FABRICATES = new RegExp(
  String.raw`^(?:\(\s*(?:${SWALLOW_SHAPE})|\[\]|null\b|undefined\b|''|""|set[A-Z]\w*\(\s*(?:${SWALLOW_SHAPE}))`,
)
/** Form B: somewhere in the braced body, a state setter is handed an empty or fabricated value. */
const FABRICATES_IN_BLOCK = new RegExp(String.raw`set[A-Z]\w*\(\s*(?:${SWALLOW_SHAPE})`)
/** …UNLESS the same body also records the rejection. This veto is load-bearing: without it the
 *  census counts `.catch((e) => { setSearchErr(e); setResults(null) })` — the exact shape §C's PINS
 *  below REQUIRE — as a swallow, and 15 of the tree's correct sites fire it, four of them fixes in
 *  this very commit. A detector that reds the code that clears the defect is worse than none. */
const RECORDS_THE_ERROR = /set\w*(?:Err|Error|Fail)\w*\(/i

/** The line number of every fabricating `.catch` in a whole file. */
function swallowSites(src: string): number[] {
  const lines: number[] = []
  // One arrow parameter at most: `.catch(() => …)` or `.catch((e) => …)`. A `.catch(fn)` reference
  // fabricates nothing here, and a multi-arg arrow is not a rejection handler.
  for (const m of src.matchAll(/\.catch\(\((\s*\w*\s*)\)\s*=>\s*/g)) {
    const at = (m.index ?? 0) + m[0].length
    let hit: boolean
    if (src[at] === '{') {
      let depth = 1
      let i = at + 1
      while (i < src.length && depth > 0) {
        if (src[i] === '{') depth++
        else if (src[i] === '}') depth--
        i++
      }
      const body = src.slice(at + 1, i - 1)
      hit = FABRICATES_IN_BLOCK.test(body) && !RECORDS_THE_ERROR.test(body)
    } else {
      // A bare expression body that fabricates is only form A when the handler ignored the
      // rejection — `(e) => e.message` is a transform, not a substitute. And the 64-char slice is
      // safe where this file's old 220-char window was not: `FABRICATES` is `^`-anchored, so it can
      // only ever read the expression that starts right here.
      hit = m[1].trim() === '' && FABRICATES.test(src.slice(at, at + 64))
    }
    if (hit) lines.push(src.slice(0, m.index).split('\n').length)
  }
  return lines
}

/** Every `useQuery(…)` call in a file, as `{ key, args, line }` — `args` is the call's own argument
 *  list, paren-matched from the opening paren to its partner.
 *
 *  🪤 `key` IS OPTIONAL, AND MAKING IT SO WAS THE THIRD BLIND SPOT. This used to `if (key)` —
 *  dropping any call whose first argument is not a single-quoted literal — so a template-literal key
 *  (`` `code:project:${id}` ``) or a constant (`useQuery(WEEK_KEY, …)`) made the whole call
 *  invisible to every check built on this function. Measured: **51 of 225** `useQuery` invocations,
 *  23% of the tree, including 11 that were swallowing. The file's own comment used to record one of
 *  those as a known exclusion; it was eleven. Callers that genuinely need a key now filter on it. */
function cachedCalls(src: string): { key?: string; args: string; line: number }[] {
  const out: { key?: string; args: string; line: number }[] = []
  for (const m of src.matchAll(/useQuery(?:<[^>]*>)?\(/g)) {
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
    out.push({ key: args.match(/^\s*'([^']+)'/)?.[1], args, line: src.slice(0, m.index).split('\n').length })
  }
  return out
}
// `.tsx?` — `app/usePlatform.ts` is a `.ts` module that calls `useQuery` and swallows, and a
// `.tsx`-only walker could never see it.
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

describe('the migrated surfaces read the error', () => {
  // `#/learning` joined after a measured failure: with both learning endpoints returning 500 and a
  // COLD sessionStorage (a warm cache masks this entirely), the page rendered "Nothing to review —
  // proposals appear here when the system notices a pattern worth offering" with **no error text
  // anywhere**, and its capture-week panel — the whole point of the surface, the days capture never
  // ran — simply vanished. The most confident possible way to say the opposite of what happened.
  const ADOPTERS = [
    // `#/tasks` joined last and was the sharpest remaining member: its fetcher carried
    // `.catch(() => [] as TaskItem[])`, the harsher variant — the rejection never reached the hook,
    // so `error` could not have been read even by a caller that tried, and the failure arrived as an
    // EMPTY ARRAY. Measured with `/api/tasks` at 500 and a cold sessionStorage, on all three views
    // and the peek: "No tasks — Break a goal into tracked work. Create a task, or let an agent plan
    // from a chat." plus the New-task CTA, no alert, no retry — a create-your-first-task pitch shown
    // to a user who may have a hundred. Both halves were needed: dropping the swallow alone would
    // have hung the surface on its skeleton forever, because `tasks === null` also satisfies the
    // skeleton branch. After: heading "Couldn't load your tasks", the server's own message, and a
    // Retry that recovers (driven: 0 rows -> 12, and the alert clears).
    'pages/tasks/TasksListPage.tsx',
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
    // `#/chat` joins late and deliberately. #1162 gave the `chat:sessions` readers an error
    // branch but left SIX other reads swallowing, so this file could not satisfy the
    // no-swallow-anywhere bar and got a resource-scoped rail instead
    // (`pages/chat/sessionLoadHonesty.test.ts`). Those six are now gone:
    //   • `chat:suggestions` · `chat:starters` — persisted decoration strips that hide when empty.
    //     A swallowed rejection resolved to `[]`, which the hook then persists as though it were an
    //     answer. No error UI: a strip that quietly does not appear claims nothing.
    //   • `chat:stream-reveal` — a persisted CONFIG VALUE fabricated as `'smooth'`. The default
    //     belongs at the use site (`streamRevealCfg === 'immediate'`), where it is a default rather
    //     than a stored answer.
    //   • `artifacts:chat-picker` — its empty state TEACHES ("Ask in chat for a widget…"), so a 500
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
    // `#/loops` joined with OU-6, found while auditing the seven surfaces that atom rolls the
    // empty state out to — which is the point worth keeping: the honesty of an empty state is a
    // PRECONDITION for shipping one, not a separate concern. Its fetcher carried
    // `.catch(() => [] as GoalLoop[])` — the harsher variant, so `error` was permanently null and
    // no caller could have read it. A failed `GET /api/loops` therefore rendered "No loops yet —
    // Describe a task and let an agent classify, plan, and pursue it autonomously" plus a
    // Start-a-loop CTA: a pitch to create your first loop, shown to someone whose loops were
    // merely unreachable. Same both-halves shape as `#/tasks`: dropping the swallow alone would
    // have pinned the page on its skeleton forever, because `loops === undefined` also satisfies
    // the skeleton branch, so the error branch is tested FIRST on that same condition.
    'pages/loops/LoopsListPage.tsx',
    // `#/code/:id` (the Code Cockpit) joined with DSC-12, which is the atom that named this file's
    // CodeSection↔CodeCockpitPage pair as "load-error twins" — the same failure, one surface using the
    // primitive and its own detail page hand-rolling it. The page-level twin was a 360px column with a
    // WARN-toned `AlertTriangle`, a `title-m` "Couldn't load this project", and a `secondary` Try again:
    // the right information in the wrong tone (a failed load is danger, not a caution) at the wrong type
    // scale, and — because it hand-rolled the block instead of reaching for `LoadError` — with **no
    // `role="alert"`**, so the one adopter class this rail exists for was silent on the page a user lands
    // on from every project row.
    //
    // Its loader is hand-rolled (`useState` + `.catch`), not `useQuery`, and it CAPTURES: the catch
    // routes 404/400 to a permanent `'missing'` state and everything else into `loadErr`, which is now the
    // rejection itself rather than a pre-flattened string, so the server's own message reaches the
    // primitive. Two things worth knowing before trusting this row:
    //   ⚠️ The capture matcher below is FILE-SCOPED, and this file also contains an unrelated
    //      `.catch((e) => { if (alive) setErr(…) })` (the terminal-session error, ~:2990) that satisfies it
    //      on its own. The real capture is `setLoadErr` in `load()`; the rail cannot tell the two apart.
    //   ⚠️ The `cachedCalls` key scan only matches SINGLE-QUOTED keys, so this file's one `useQuery`
    //      — `` `code:project:${id}` ``, a template literal — is invisible to both swallow checks. That
    //      call DOES `.catch(() => null)`, deliberately: it is an instant-paint seed (`persist:false`) that
    //      only ever *sets* `project` when it has data, so a failed seed cannot mask `loadErr`. Left as-is
    //      rather than widening the key regex, which would flag a correct swallow as a defect.
    'pages/code/CodeCockpitPage.tsx',
  ]

  for (const rel of ADOPTERS) {
    it(`${rel} branches on the load error before the empty state`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must render the shared primitive').toMatch(/<LoadError\b/)
      // The property that makes the branch possible is that the rejection is CAPTURED rather than
      // discarded. Two shapes qualify, and both ship here:
      //   • `useQuery` consumers destructure it — `error: somethingErr` (the alias is free-form
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
      // `<Loading />` gained a `what` prop in cycle 144 (it is a live region now), so match the TAG.
      //
      // FIFTH widening (DSC-12): a bare `<Loader2 className="animate-spin">` inside `<Centered>` counts
      // too. `#/code/:id`'s page gate is one, and there is NO spinner primitive to send it to — `Loading`
      // is a bare "Loading…" TEXT line whose own doc calls itself "THE LESSER IDIOM AND STAYS SO", so
      // swapping a centred spinner for it would be a redesign of the first paint to satisfy a regex,
      // which is the exact inversion this rail's own comment warns against. Verified non-weakening: all
      // ten prior adopters that use `<Loader2` at all use it AFTER their `<LoadError>` (projects 146/449,
      // code 326/367, apps 563/830), so the min does not move for any of them.
      const loadAt = Math.min(...[/<ListSkeleton\b/, /<Loading\b/, /<FormSkeleton\b/, /<Loader2\b/].map((re) => {
        const i = src.search(re)
        return i === -1 ? Number.POSITIVE_INFINITY : i
      }))
      expect(loadAt, 'the surface must have a loading state at all').toBeLessThan(Number.POSITIVE_INFINITY)
      const errorBranchFirst = errAt < loadAt
      const loadingClearedOnFailure = /finally\s*\{[^}]*setLoading\(false\)/.test(src)
      // THIRD proof, added by DSC-14. A surface whose `loading` comes from the one data layer does
      // not own a flag to clear: `useQuery` reports `loading` as "nothing to show AND a request is
      // in flight", and a rejection ends the flight — so the flag falls false on failure by
      // construction, and the error branch below it is reachable without a hand-written `finally`.
      // That is a STRONGER guarantee than the pattern it replaces, and it is asserted directly
      // rather than inferred from source shape: `lib/data/dataLayerContract.test.tsx` → "the layer
      // reports a rejection instead of resolving empty" pins `loading === false` on a rejected read.
      // `#/workflows` is the first surface here to hand-roll nothing at all.
      const loadingFromDataLayer = /loading(?::\s*\w+)?\s*[,}][^\n]*\n?[\s\S]{0,80}?useQuery\(/.test(src)
        || /useQuery\([\s\S]{0,400}?\bloading(?::\s*\w+)?\s*[,}]/.test(src)
      expect(
        errorBranchFirst || loadingClearedOnFailure || loadingFromDataLayer,
        'the error branch must be reachable: it precedes the loading branch, or the loading flag is cleared in a finally, or `loading` comes from the one data layer (which clears it on a rejection by construction) so a failure gets past it',
      ).toBe(true)
    })
  }

  it('no OTHER consumer of an adopter\'s cache key swallows either', () => {
    // 🔴 THE ONE THAT ACTUALLY BIT. `useQuery` caches by KEY, so a swallow at ANY call site
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
      // KEYED calls only. This check is about one cache KEY's consumers poisoning each other, so a
      // call whose key the scanner cannot resolve has nothing to contribute — and folding them in
      // collapsed every unkeyed call in the tree into a single literal `undefined` bucket, which then
      // read as eleven consumers of one shared key. A widening has to stop where the property it
      // feeds stops applying.
      for (const c of cachedCalls(codeOf(abs))) {
        const key = c.key
        if (!key) continue
        const at = `${abs.slice(SRC.length + 1)}:${c.line}`
        consumers.set(key, [...(consumers.get(key) ?? []), { at, swallows: SWALLOW.test(c.args) }])
      }
    }
    // Sanity: the scan must actually see the multi-consumer key it was written for.
    const appsConsumers = consumers.get('apps') ?? []
    expect(appsConsumers.length, "the scan must find the 'apps' key's consumers").toBeGreaterThanOrEqual(3)

    const adopterKeys = new Set<string>()
    for (const relPath of ADOPTERS) {
      // Keyed calls only, same reason as the census above: this check is about a KEY's consumers.
      for (const c of cachedCalls(codeOf(join(SRC, relPath)))) if (c.key) adopterKeys.add(c.key)
    }
    expect(adopterKeys.size, 'the adopters must declare at least one cache key').toBeGreaterThan(0)

    const poisoners: string[] = []
    for (const key of adopterKeys) {
      for (const c of consumers.get(key) ?? []) if (c.swallows) poisoners.push(`${c.at} (key '${key}')`)
    }
    expect(poisoners, 'a swallow here makes every other consumer of the key unable to see the failure').toEqual([])
  })

  it('no adopter swallows a rejection anywhere in the file', () => {
    // `.catch(() => [])` inside the fetcher makes the error branch unreachable by construction: the
    // hook is handed a successful empty list. A surface that renders LoadError while still swallowing
    // is asserting a state it can never enter.
    //
    // 🔑 MEASURED AGAINST §B's BUDGET, NOT AGAINST ZERO — and widening the key scan is what forced
    // that. `CodeCockpitPage`'s template-literal key made its one `.catch(() => null)` invisible
    // here, and this file's own comment recorded the exclusion AND the reason it is correct: that
    // call is an instant-paint seed (`persist: false`) which only ever SETS `project` when it has
    // data, so a failed seed cannot mask the page's real `loadErr`. With the key scan widened the
    // call became visible, and a flat `toEqual([])` would have demanded the removal of a swallow the
    // file had already reasoned was right. Deferring to the budget keeps the two halves from
    // contradicting each other: an adopter may carry exactly the swallows §B has written down, and
    // the number still may only fall.
    //
    // 🪤 WHOLE-FILE, on §B's scanner — it used to scan only `useQuery(` arguments, and that made this
    // assertion weaker than its own name. An adopter's `useQuery` reads could be spotless while an
    // effect three hundred lines down fabricated `[]` for the same surface, and the swallow it is
    // named after would not be counted here. Sharing one scanner with §B also means the budget has
    // ONE definition: an adopter row and a tree row can no longer drift apart by construction.
    for (const relPath of ADOPTERS) {
      const at = swallowSites(codeOf(join(SRC, relPath)))
      expect(
        at.length,
        `${relPath} swallows ${at.length} rejection(s) at line(s) ${at.join(', ')}; `
        + `§B's budget allows ${SWALLOW_BUDGET[relPath] ?? 0}`,
      ).toBe(SWALLOW_BUDGET[relPath] ?? 0)
    }
  })
})

// ── §B THE TREE-WIDE BUDGET — because every check above is scoped to a NAMED LIST ───────────────
//
// 🔴 THIS SECTION EXISTS BECAUSE THE ONE ABOVE COULD NOT SEE ITS OWN BLIND SPOT, and the numbers say
// how badly. Every swallow assertion in §A iterates `ADOPTERS` (16 files) or the keys those files
// own. So on the day this was written the tree held **71 fetcher swallows across 31 files** and CI
// was GREEN, because none of the 31 was an adopter. That is not a gap in the list; it is the wrong
// shape of rail. A list can only ever fail the files someone already thought of, which is why #532's
// own count went UP across four cycles — 73 → 115 → 122 — with a green rail the whole time.
//
// So this half is a CENSUS, not a list, and it is a budget rather than an allowlist:
//
//   · a file that swallows and is NOT in the map            → red   (the new-file hole §A had)
//   · a file that swallows MORE than its number             → red   (the new-site hole a bare
//                                                                    allowlist-of-names has)
//   · a file that swallows FEWER than its number            → red   (ratchet the number down in the
//                                                                    same commit; slack is a hole,
//                                                                    the ruling `primitiveAdoption`
//                                                                    already records)
//
// 🪤 THE MIDDLE ONE IS THE WHOLE POINT. An allowlist of FILE NAMES with no count is why a defect in
// this repo went 3 → 9 inside a single already-listed file with CI green. A name says "this file is
// known"; only a number says "this file is known AND has not got worse".
//
// 🔴 AND A CENSUS IS ONLY AS WIDE AS ITS SCANNER — the second time the same lesson had to be learnt
// here. The census above replaced a named list with a sweep of the whole tree, and then scanned each
// of those files through `cachedCalls`, which reads `useQuery(` argument lists ONLY. So it swept 604
// files and could only ever see a fraction of each: the 35 files the rebase alone added were invisible
// not because nobody had listed them but because they do not fetch through `useQuery`.
// Sweeping every file is not the same property as reading every file, and a rail that conflates them
// reports the tree it can parse as though it were the tree that exists.
//
// A NUMBER HERE IS A DEBT, NOT A DISPENSATION. Each entry is a fetcher that resolves a rejection
// into a value the server never sent, and the surface then renders it as an answer. The way to edit
// this map is downward.
//
// 🔑 RE-BASELINED AT `origin/main` 280c175f4 WHEN THE SCANNER STOPPED BEING `useQuery`-SCOPED (#532),
// and the four numbers below are the argument for the rebase — all four measured on that one commit,
// over the same 604 production files, so the only variable between them is how much of each file the
// scanner could read:
//
//   31 files /  51 sites   the OLD rail: whole-tree sweep, `useQuery(`-argument scan
//   66 files / 132 sites   the same sweep reading each file WHOLE          (form A 102, form B 30)
//   75 files / 148 sites   …and admitting the STRUCTURED literal           (form A 111, form B  37)
//   63 files / 127 sites   the map below: that baseline minus this commit  (form A  99, form B  28)
//
// Nothing the first two steps surfaced was a NEW defect. The old map dropped no file, gained 44, and
// eleven of the files it already named turned out to carry more than it could see (`ToolsPage` 1 → 5,
// `MemoryPanel` 4 → 8, `CodeCockpitPage` 1 → 4). Among the 44 is `lib/api.ts` — the exemplar #532
// itself cites, which the rail it was filed against could not read.
//
// The last line is this commit: 16 files reduced, 12 of them to zero, 21 sites removed. Every fix was
// a surface printing a positive claim about server state out of a failed read. 🪤 AND 15 OF THE 16 WERE
// INVISIBLE TO THE OLD SCAN — only `VoicePanel` appears in its 31-file map, and even there at 1 rather
// than the 3 it held. So a rail that was green on this commit was green about a tree it had never read.
// That is the measurement that makes this a rebase and not a re-count.
const SWALLOW_BUDGET: Record<string, number> = {
  // ── The error-PATH parse fallbacks. Correct code, and permanent: `r.json().catch(() => null)` is
  // read BEFORE `r.ok`, so the substitute feeds the error message, never a surface claim. These are
  // the sites #532's own comment warns against blanket-deleting, and they are why this map is a
  // budget rather than a target of zero.
  'app/appSdk.tsx': 3,
  'lib/api.ts': 5,
  'lib/errText.ts': 1,

  // Fails CLOSED, which is the one safe direction here: the readiness probe's substitute is
  // `{ needs_model: true, has_model_provider: false }`, so an unreadable probe shows the setup step
  // rather than telling a user with no provider that they are ready to chat.
  'app/Onboarding.tsx': 1,
  'app/usePlatform.ts': 1,
  'lib/agents.ts': 1,
  // The sixth is the RECORDS veto's blind edge, not a swallow: `setResultBody({ content: "(couldn't
  // load the full result: …)" })` puts the failure in the copy the user reads. The veto matches SETTER
  // names (`setSearchErr`), and this records into a FIELD — so widen the veto and it starts exempting
  // any `setX({ error })` that never renders; leave it, and one honest site sits here with a reason.
  'pages/ChatPage.tsx': 6,
  'pages/agents/AgentDetail.tsx': 3,
  'pages/artifacts/ArtifactCard.tsx': 1,
  'pages/chat/OrganizeChip.tsx': 1,
  'pages/chat/SessionSkillsReview.tsx': 1,
  // Includes the one this file already documented as a deliberate keep: an instant-paint seed that
  // only ever SETS `project` when it has data, so a failed seed cannot mask the page's real `loadErr`.
  'pages/code/CodeCockpitPage.tsx': 4,
  // A status-DISCRIMINATING branch, and the reason this map counts form B at all rather than trusting
  // a braced body: `if (e.status === 404) setResume(null)` reads a 404 as the answer "there is no
  // draft to resume", which is exactly what a 404 means. Left as debt rather than teaching the scanner
  // a third exemption — the rule for that would have to model status checks, and a rule that models
  // control flow is a rule nobody can predict the verdict of.
  'pages/code/CodeSection.tsx': 1,
  // Git semantics, documented at both sites: an untracked file has no HEAD blob, so the `''` fallback
  // is what makes it render as all-added; a file deleted from the working copy has no working blob, so
  // the same fallback renders it as all-removed. Both substitutes are the true content of a side that
  // does not exist — the only two sites in this map where an empty string is the server's real answer.
  'pages/code/DiffView.tsx': 2,
  'pages/dashboard/PinnedTiles.tsx': 2,
  // Path autocomplete. No sentence is composed from the result, so an empty suggestion list asserts
  // nothing — the narrow line the settings-hub keeps below are also on the right side of.
  'pages/files/browse/PathBar.tsx': 1,
  'pages/files/filesData.ts': 1,
  'pages/inbox/InboxPage.tsx': 1,
  'pages/knowledge/KnowledgeCreatePage.tsx': 1,
  'pages/knowledge/KnowledgeListPage.tsx': 1,
  'pages/loop/LoopComposer.tsx': 3,
  'pages/loops/DesignCockpitPage.tsx': 1,
  // The artifact tab's swallow is GONE (it printed an empty document for a failed read). What is left
  // is a per-item `api.task(id).catch(() => null)` behind a `.filter(Boolean)` — partial degradation
  // of a fan-out, deliberately — and a fire-and-forget `updateULoop` mutation, a different family.
  'pages/loops/LoopCockpitPage.tsx': 3,
  'pages/loops/LoopsSection.tsx': 1,
  // 🪤 THE FILE #532's BODY HELD UP AS THE EXEMPLAR, and it is in this map — which is the clearest
  // statement of what a number here means. `dirErrorMessage` is the pattern every fix in this commit
  // copies; the counted site is a different read in the same file, a peek section whose every consumer
  // is `length > 0`-gated, so it composes no sentence at all. Exemplary and over the line are not
  // opposites, and a census that scored its own exemplar at zero would be measuring the wrong thing.
  'pages/projects/ProjectsSection.tsx': 1,
  'pages/prompts/promptWidgets.tsx': 1,
  'pages/settings/AgentDefaultsPanel.tsx': 1,
  'pages/settings/AlwaysOnConventions.tsx': 1,
  'pages/settings/ChatPanel.tsx': 3,
  // The automation list swallow is FIXED (the panel said "You have no automations yet" when it could
  // not read them). The remaining one resets the REPORT, whose failure the panel already announces.
  'pages/settings/DoctorPanel.tsx': 1,
  'pages/settings/DurabilityPanel.tsx': 2,
  // `FeedbackPanel` is GONE from this map, not zeroed: its producers table printed "No feedback yet —
  // 👍/👎 appear on inbox classifications…" at a user whose every verdict was recorded and unreadable,
  // and the file's own docstring had written the swallow down as deliberate. It was the defect.
  'pages/settings/InboxSettingsPanel.tsx': 1,
  // 8 → 7. The daily-digest read is fixed; it printed "No digests yet" out of a failed fetch. The
  // seven left are stats/lint/observability decorations and a settings read with its own branch.
  'pages/settings/MemoryPanel.tsx': 7,
  'pages/settings/ModelBackends.tsx': 1,
  // 6 → 4. The two that LEFT were the panel's PRIMARY read, and it was the last surface in the
  // first-run defect set with no terminal state: `api.modelsAvailable().catch(() => [])` +
  // `api.modelsActive().catch(() => ({}))` inside one `Promise.all`, under a call site that bound
  // only `data`. A failed read therefore painted "No models discovered" — a confident claim about a
  // page that never loaded — and a read that never settled kept `<ListSkeleton>` up forever
  // (measured on a fresh home with every `/api` read held open: still "Loading…" with
  // `aria-busy=1` and no Retry at 9.5s). Both reads are REQUIRED to render a binding, so the
  // rejection now propagates and the site renders `LoadError` + Retry.
  //
  // 🪤 THIS NUMBER WAS BRIEFLY MIS-READ AS 2, AND THE CAUSE IS WORTH RECORDING because it is this
  // file's own scanner biting the hand that feeds it. The census strips block comments before
  // counting, with `/\/\*[\s\S]*?\*\//g` — and a `//` line comment citing the glob `/api/` followed
  // by two asterisks contains a `/*`, which opened a block the stripper then closed at a much later
  // `*/`, swallowing two real swallow sites along with it. Same family as the token-lint defect in
  // this release's notes (a comment-state scanner that is not comment-aware), and the reason no
  // comment added here spells that glob out.
  //
  // The four that remain are deliberate and different in kind: the reclaim-size read behind the
  // "Reclaim N" button, the per-provider breaker health that decorates the chain-entry dots, the
  // judge-benchmark tier recommendation whose absence is an honest "no chip" (the Learning page owns
  // reporting WHY), and a per-provider local-model health read. None of them is the panel.
  //
  // The RECORDS veto's other blind edge (see `ChatPage` above) also lives in this file: the reindex
  // failure IS recorded — `setReindex({ status: 'error', message })` — but into a FIELD of a state
  // object, and the veto keys off the SETTER's name. The surface tells the user the reindex failed;
  // only the scanner cannot see it.
  'pages/settings/ModelsPanel.tsx': 4,
  // 2 → 1, and the halving is the interesting part: this entry USED to read "Both read a provider's
  // JSON SCHEMA", and only one of the two ever did. The remaining site is the schema read, whose
  // substitute is `{ properties: {} }` — every caller turns that into `props.length === 0` → `return
  // null`, so an unreadable schema renders NOTHING and claims nothing. The generic fix for it is a form
  // that says it could not load its own shape, which is a design question about the multi-instance
  // provider surface, not a swallow to delete. The site that LEFT was `api.providerInstances()`, an
  // instance LIST: `[]` printed "No instances yet. Add one to start using this provider." over a
  // provider with five configured MCP servers, with "0 instances" in the header chip beside it. A
  // count and a sentence are claims; an unrendered form is not. Same file, opposite verdicts.
  'pages/settings/MultiInstanceCard.tsx': 1,
  'pages/settings/NotificationsPanel.tsx': 1,
  // 2 → 1. The installed-ledger read is fixed — and fixing it here required moving its TWIN in
  // `settingsWidgets` in the same commit, because the two share `settings:packs:installed`. The one
  // left is the bundled catalog, whose own `LoadError` the store section already renders beside it.
  'pages/settings/PacksPanel.tsx': 1,
  // `PromptsPanel` is GONE from this map, not zeroed — same call as `FeedbackPanel` above: its one
  // swallow WAS the defect. `api.promptBindings().catch(() => null)` substituted `null`, which is
  // exactly what "still loading" looks like under this panel's `!data` gate, so a failed read
  // rendered `<ListSkeleton>` forever and the error state was unreachable whether the read rejected
  // OR never settled (measured on a fresh home with the read held open: still "Loading…" with
  // `aria-busy=1` at 9.6s). The rejection propagates now and the site renders `LoadError` + Retry.
  // The same schema read `MultiInstanceCard` above keeps, same `{ properties: {} }`, same `return null`
  // — and the same open design question, which is why the two move together or not at all.
  'pages/settings/ProviderConfigForm.tsx': 1,
  'pages/settings/ProvidersPanel.tsx': 3,
  'pages/settings/RoutingPanel.tsx': 3,
  // 3 → 2. The providers read is fixed: a 500 on /api/search/providers told a user with three
  // registered providers "No search providers configured" and pointed them at the Store to install
  // their first one. The two left are the active BINDINGS (`{}` → every use-case reads "none — falls
  // back to General", which is a claim and is worth its own row in the issue) and the `/api/tools`
  // probe, whose `null` — not `[]` — is deliberate: it withholds the missing-tool note rather than
  // accusing the user of a missing app off an unreachable read.
  'pages/settings/SearchPanel.tsx': 2,
  'pages/settings/SecurityPanel.tsx': 2,
  'pages/settings/UpdatesPanel.tsx': 1,
  // 7 → 5. Both rollups are fixed — the pair the issue's ninth comment singled out, where a failing
  // `/api/usage/rollup` rendered "No model usage recorded this period." and "No usage recorded this
  // period." on a spend page whose own headline tiles, off a different endpoint, could be showing
  // $11.35 and 412 turns at the same moment. The five left are all `null`-substituting reads whose
  // surfaces are gated on the value's presence (`{t && …}`, `{sys && hasActivity && …}`, `dayCap > 0`,
  // `fold ?? null`), so an unread one costs a section rather than composing a sentence.
  'pages/settings/UsagePanel.tsx': 5,
  // 3 → 1. The two gone were the lexicon reads, which printed "0 in your lexicon", "No terms yet" and
  // "No learned corrections yet" out of a failed fetch. The one left is `modelsActive`, documented at
  // the site: it feeds a readiness CHIP, so losing it degrades a chip rather than inventing a setting.
  // 🪤 This is also the single file of the sixteen that the old arg-scoped rail DID list — at 1, while
  // it held 3. A number measured through a partial scanner is not a smaller truth, it is a wrong one.
  'pages/settings/VoicePanel.tsx': 1,
  // 3 → 2, and the one that left is worth reading as a lesson about shared keys. `usePacksInstalled`
  // was budgeted here as deliberate because it is byte-identical to `PacksPanel`'s ledger read and the
  // two SHARE `settings:packs:installed` — a divergent fetcher primes that key with a different
  // substitute and makes the panel's own error branch unreachable. That reasoning was sound and it was
  // an argument about ORDER, not about the swallow: "de-swallowing it is the panel's fix to make". So
  // both moved in one commit, and the byte-identity that made the entry defensible is what made it
  // impossible to fix either one alone. The two left make no CLAIM about the value they read:
  // `useAgentDefaults`' decorating read of the default agent's NAME renders as '—', and
  // `useToolsSavings` backs a meter whose absence is a designed state under a prior ruling with its
  // own rail (`dashboard/healthUnknown.test.ts`). That is the line, and it is narrower than "this read
  // is unimportant".
  'pages/settings/settingsWidgets.tsx': 2,
  'pages/skills/LearningSummaryBlock.tsx': 1,
  'pages/skills/SkillInspector.tsx': 2,
  'pages/skills/SkillsPage.tsx': 2,
  'pages/tasks/TasksListPage.tsx': 2,
  'pages/terminal/TerminalPage.tsx': 1,
  // The fifth is a capability probe whose substitute is `({ available: false })` — the widened scanner
  // counts a structured literal, and this is the shape it is deliberately wrong about. "We could not
  // reach the capability check" and "the capability is not available" are the same fact to a user who
  // cannot use it either way, so `available: false` IS the answer, not a stand-in for one.
  'pages/tools/ToolsPage.tsx': 5,
  'pages/triggers/TriggersListPage.tsx': 1,
  // TWO swallows fixed here, and the second is the reason a line citation is a poor spec: the ledger
  // read (`:150`, the line #532 and #2940 both name) said "No runs recorded yet", and the VERSION read
  // eleven lines above it said "No version history yet" — same defect, same file, outside the citation.
  // The two left reset values the page makes no claim about: a diff preview rendered only when
  // non-empty, and the definition itself, whose absence the page's own not-found branch owns.
  'pages/workflows/WorkflowDefDetail.tsx': 2,
  // A SECONDARY read on a page whose primary run fetch has a real error branch: the continuations list
  // is rendered only when non-empty, so a failed read costs a section, not a claim — and the page has
  // already told the user if the run itself could not be read.
  'pages/workflows/WorkflowRunDetail.tsx': 1,
  // Documented at the site: the freshness column is decoration on a row whose identity came from the
  // list read. An unreadable timestamp renders as unknown freshness, which is what it is.
  'pages/workflows/WorkflowsListPage.tsx': 1,
  'ui/DegradedChip.tsx': 1,
  'ui/PlanningWalkthrough.tsx': 3,
  'ui/chat/ChatPlanGate.tsx': 1,
  // A capability probe: the rejection IS the answer (`setAvailable(false)`), and it counts here only
  // because clearing the disabled-reason string reads as a fabrication to a syntactic scanner. Left
  // as debt rather than vetoed — one more special case in the scanner costs more than one row here.
  'ui/composer/useScreenShare.ts': 1,
}

describe('§B no fetcher swallows its own rejection, tree-wide and by COUNT', () => {
  /** file → how many rejections it resolves into a value the server never sent. Production only. */
  const census = (): Map<string, number> => {
    const out = new Map<string, number>()
    for (const abs of walk(SRC)) {
      const n = swallowSites(codeOf(abs)).length
      if (n > 0) out.set(rel(abs), n)
    }
    return out
  }

  it('VACUITY: the census still recognises every shape it counts', () => {
    // A regex that matches nothing reads exactly like a clean tree — the trap this file's sibling
    // states in its own header. Both floors are deliberately well under the live numbers so ordinary
    // progress does not trip them, and they are floors on the SCANNER, not on the defect.
    const c = census()
    expect(c.size, 'the swallow scanner found no site at all').toBeGreaterThanOrEqual(30)
    expect([...c.values()].reduce((a, b) => a + b, 0)).toBeGreaterThanOrEqual(60)

    // ── POSITIVE CONTROLS, AS SHAPES ─────────────────────────────────────────────────────────────
    // 🪤 SHAPES, NOT `file:line` PINS. The obvious way to write this control is to name the sites
    // #532 cited and assert they score — and it is wrong twice over: a pin rots on the next line
    // shift (this file's ghost-budget test exists because names rot), and worse, a pin on a site
    // someone FIXES inverts into a demand that the defect come back. Two of the issue's three cited
    // controls have already moved out from under their citations. A shape cannot rot that way.
    const counts = (src: string) => swallowSites(src).length
    expect(counts('api.x().catch(() => null)'), 'the bare null fallback').toBe(1)
    expect(counts('api.x().catch(() => setItems([]))'), 'the inline setter form').toBe(1)
    expect(counts('api.x().catch(() => ({}))'), 'the parenthesised object body').toBe(1)
    expect(counts(".catch(() => '')"), 'the fabricated empty string').toBe(1)
    expect(counts('.catch(() => { if (alive) setRows([]) })'), 'the braced setter form').toBe(1)
    // CAST TOLERANCE, the selector hazard #532 names explicitly: 21 of the tree's 30 array-form
    // sites are `catch(() => [] as Foo[])`, so a selector that closes on `\)` scores 9 of 30 — a 70%
    // false negative on the commonest shape in the class. Both spellings must count the same.
    expect(counts('.catch(() => [])'), 'the bare array fallback').toBe(1)
    expect(counts('.catch(() => [] as SessionArchive[])'), 'the SAME site, cast').toBe(1)
    expect(counts('.catch(() => [] as unknown as Row[])'), 'and double-cast').toBe(1)
    // THE STRUCTURED LITERAL, and this pair is here because its absence was not noticed by reading the
    // regex — it was noticed by fixing `settings/MemoryGraph.tsx` and watching the census not move. A
    // shape the scanner cannot see is a shape no count can be trusted about, so both spellings of a
    // fabricated ENVELOPE get a control: the returned one and the one handed to a setter.
    expect(counts('.catch(() => ({ nodes: [], edges: [] }))'), 'the fabricated envelope').toBe(1)
    expect(counts('.catch(() => setGraph({ nodes: [], edges: [] }))'), 'the SAME lie, via a setter').toBe(1)
    expect(counts(".catch(() => ({ 'a': 1 }))"), 'a quoted key is still a literal').toBe(1)

    // ── NEGATIVE CONTROLS ────────────────────────────────────────────────────────────────────────
    expect(counts('.catch(() => {})'), 'form C: an empty BLOCK fabricates nothing').toBe(0)
    expect(counts('.catch(() => setErr(e))'), 'a CAPTURE is not a swallow').toBe(0)
    expect(counts('.catch((e) => e.message)'), 'a TRANSFORM is not a swallow').toBe(0)
    expect(counts('.catch(reportToast)'), 'a handler REFERENCE is out of scope').toBe(0)
    // The veto, and the reason it is not optional: this is the exact shape §C's PINS below REQUIRE.
    // Without it the census reds the code that clears the defect.
    expect(
      counts('.catch((e) => { setSearchErr(e); setResults(null) })'),
      'record-then-reset is the CORRECT pattern, not a swallow',
    ).toBe(0)

    // The live file control: `lib/api.ts` carries the issue's own `:84` exemplar plus four siblings,
    // all `r.json().catch(() => null)` parse fallbacks read BEFORE `r.ok`. They are correct code and
    // will never be "fixed", which makes the file a permanent positive control on the scanner — if it
    // ever scores zero, the scanner broke, not the tree.
    expect(c.get('lib/api.ts') ?? 0, 'the live positive control stopped counting').toBeGreaterThan(0)
  })

  it('every swallowing file is in the budget — a NEW one turns CI red', () => {
    const unlisted = [...census().keys()].filter((f) => !(f in SWALLOW_BUDGET)).sort()
    expect(
      unlisted,
      'these files swallow a `useQuery` rejection and are not budgeted. Do not add them here — give '
      + 'the surface an error branch. The layer already reports the failure: `useQuery` returns '
      + '`error` and a `status` of loading|success|error, and `ui/ListScaffold`\'s `LoadError` (or '
      + '`BentoCard`\'s `failed`, for a settings-hub tile) renders it.',
    ).toEqual([])
  })

  it('and no file swallows MORE times than its budget', () => {
    const c = census()
    const over = [...c.entries()]
      .filter(([f, n]) => f in SWALLOW_BUDGET && n > SWALLOW_BUDGET[f])
      .map(([f, n]) => `${f}: ${n} > ${SWALLOW_BUDGET[f]}`)
    // 🪤 The check a name-only allowlist cannot make. Being listed is not a licence to add more.
    expect(over, 'a budgeted file grew a new swallow').toEqual([])
  })

  it('and no file swallows FEWER — fixing one ratchets the number down', () => {
    const c = census()
    const under = Object.entries(SWALLOW_BUDGET)
      .filter(([f, n]) => (c.get(f) ?? 0) < n)
      .map(([f, n]) => `${f}: ${c.get(f) ?? 0} < ${n} — lower it to ${c.get(f) ?? 0}`)
    // Exact equality, the house rule: "slack is not a safety margin here, it is a hole"
    // (`primitiveAdoption.baseline.json`). A budget that drifts above the actual re-opens exactly the
    // room this section closed.
    expect(under, 'ratchet these down in the same commit that fixed them').toEqual([])
  })

  it('and the budget names no file that has stopped existing', () => {
    const all = new Set(walk(SRC).map(rel))
    const ghosts = Object.keys(SWALLOW_BUDGET).filter((f) => !all.has(f))
    // A budget that outlives its files stops describing the app and starts describing its history —
    // the same rot §3 of `lib/data/dataLayerAdoption.test.ts` guards against for its named list.
    expect(ghosts, 'delete these entries').toEqual([])
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

describe('direct fetches keep their rejection too — the 2026-09-05 false-empty family', () => {
  // These slices fetch with a bare `api.*` call (an effect or a handler), not `useQuery`, so the
  // structural scan above cannot see them. Each once folded its rejection into an empty collection:
  // a failed intent-outcomes read said "Nothing gathered yet", a failed marketplace search said
  // "No results — try a different search term", and a failed task search / ready fetch said
  // "No tasks match this filter". Positive pins, per this file's own lesson about char windows:
  // each catch must RECORD the rejection, and the surface must render LoadError for that slice —
  // reverting either half turns this red.
  const PINS: Array<[string, RegExp, string]> = [
    ['pages/knowledge/KnowledgeListPage.tsx', /\.catch\(\(e\)\s*=>\s*\{\s*setOutcomesErr\(e\);\s*setOutcomes\(null\)\s*\}\)/, 'gathered matches'],
    ['pages/skills/SkillsPage.tsx', /catch\s*\(e\)\s*\{\s*setSearchErr\(e\);\s*setResults\(null\)/, 'skill search results'],
    ['pages/tasks/TasksListPage.tsx', /\.catch\(\(e\)\s*=>\s*\{\s*setReadyErr\(e\);\s*setReady\(null\)\s*\}\)/, 'ready tasks'],
    ['pages/tasks/TasksListPage.tsx', /\.catch\(\(e\)\s*=>\s*\{\s*if\s*\(alive\)\s*\{\s*setSearchErr\(e\);\s*setResults\(null\)\s*\}\s*\}\)/, 'search results'],
    // #532's daily-digest row. Pinned here rather than trusted to §B's count because the RECORDS veto
    // makes the fixed shape invisible to the census: reverting only the render half — keeping
    // `setDigestsErr` while deleting the branch that shows it — moves no number in this file.
    ['pages/settings/MemoryPanel.tsx', /\.catch\(\(e\)\s*=>\s*\{\s*setDigestsErr\(e\);\s*setDigests\(null\)\s*\}\)/, 'daily digests'],
  ]

  it('each slice records its rejection and renders LoadError for it', () => {
    for (const [rel, catchPin, what] of PINS) {
      const src = codeOf(join(SRC, rel))
      expect(catchPin.test(src), `${rel}: the catch must record the error, not fold it into an empty value (${catchPin})`).toBe(true)
      expect(src.includes(`<LoadError what="${what}"`), `${rel}: must render <LoadError what="${what}">`).toBe(true)
    }
  })
})
