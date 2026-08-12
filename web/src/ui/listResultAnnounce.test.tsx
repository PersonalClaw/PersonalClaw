import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ListControls } from './ListControls'

// ── Filtering a list changed the page and said nothing ───────────────────────────────
//
// Typing in a list filter rewrites the page under the user. The sighted cue is the list
// redrawing; for a screen-reader user there was no cue at all. Measured on six surfaces
// (knowledge, prompts, triggers, apps, skills, projects) — filtering 26 rows to 25, and
// filtering to a "No matching items" empty state — **zero live regions, every time**:
//
//   knowledge   noMatchShown=true   liveRegions=[]   NOT ANNOUNCED
//   prompts     noMatchShown=true   liveRegions=[]   NOT ANNOUNCED
//   …6 of 6
//
// axe reports nothing here, as in cycle 52: a missing announcement is not a rule violation.
//
// 🔑 THE CANONICAL FORM ALREADY EXISTED — `pages/chat/FindBar.tsx` announces its match count
// with `aria-live="polite"` on the `n/total` counter. This brings the 13 `ListControls`
// consumers onto that pattern instead of inventing one.
//
// ── The three that could not adopt it, and now can ───────────────────────────────────
//
// The coherence `layout` lens flagged `tasks`, `artifacts` and `files` as `ListControls`
// non-adopters. They are: each lays out its own controls bar and uses `SearchField` directly, so
// the announcement lived somewhere they could not reach. Re-measured with a probe that types into
// each list's own filter and diffs every live region, against `#/knowledge` as a KNOWN-adopter
// control (which proves the probe can see an announcement at all):
//
//   #/knowledge   "" → "No matching items"    ✅ announced   ← control
//   #/tasks       "" → ""                     🔴 silent
//   #/artifacts   "" → ""                     🔴 silent
//   #/files       "" → ""                     🔴 silent
//
// 🔑 SO THE FIX IS AN EXTRACTION, NOT A SECOND IMPLEMENTATION. `ResultAnnouncement` is the region
// `ListControls` always rendered, lifted out and exported beside it; `ListControls` now renders the
// extracted piece, and the three hand-laid bars render the same one. Migrate the IDIOM, not the page
// — copying the markup into three files is what turns one shared behaviour into four that drift.
//
// After, driven the same way: `"No matching tasks"` · `"No matching artifacts"` ·
// `"No matching lines"`.
//
// 🪤 THE NOUN IS PART OF THE COPY, AND `files` GOT IT WRONG FIRST: `noun="matches"` made the zero
// branch read **"No matching matches"**. A `ContentMatch` is one matching LINE (file + line + col +
// preview), so the honest noun is "lines" — which also reads correctly at 1 ("1 line").
//
// Verified live after the change:
//
//   idle                  ""                      (region MOUNTED, empty)
//   no match              "No matching items"
//   25 of 26 rows         "25 items"
//   1 row                 "1 item"                (singularised)
//   query cleared         ""

const opts = { value: '', onChange: () => {}, options: [] }

describe('a filtered list announces its result count', () => {
  it('mounts the live region even when idle', () => {
    // A live region created at the same moment its content appears is not reliably observed,
    // so it must already be in the DOM, empty, waiting. (Same lesson as the toast host.)
    const { container } = render(<ListControls search={{ value: '', onChange: () => {} }} />)
    const region = container.querySelector('[role="status"][aria-live="polite"]')
    expect(region, 'the region must exist before a filter is typed').not.toBeNull()
    expect(region!.textContent).toBe('')
  })

  it('says nothing while no filter is active', () => {
    // An idle list announcing its own length on mount is noise, not information.
    const { container } = render(
      <ListControls search={{ value: '', onChange: () => {} }}
        results={{ count: 26, noun: 'items', active: false }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('')
  })

  it('announces the count while filtering', () => {
    const { container } = render(
      <ListControls search={{ value: 'zfs', onChange: () => {} }}
        results={{ count: 25, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('25 items')
  })

  it('singularises a single result', () => {
    // "1 items" reads as a bug to anyone listening to it.
    const { container } = render(
      <ListControls search={{ value: 'x', onChange: () => {} }}
        results={{ count: 1, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('1 item')
  })

  it('names the empty result rather than announcing "0"', () => {
    // "No matching items" is the same sentence the visible EmptyState uses; "0 items" would
    // be technically true and useless.
    const { container } = render(
      <ListControls search={{ value: 'zzz', onChange: () => {} }}
        results={{ count: 0, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('No matching items')
  })

  it('is polite, not assertive — a result count is an update, not an interruption', () => {
    const { container } = render(
      <ListControls search={{ value: 'x', onChange: () => {} }}
        results={{ count: 3, noun: 'items', active: true }} />,
    )
    const region = container.querySelector('[role="status"]')!
    expect(region.getAttribute('aria-live')).toBe('polite')
  })

  it('keeps the announcement out of the visible bar', () => {
    // The count is already visible as the list itself; printing it would duplicate what a
    // sighted user can see and add a shifting element to the controls row.
    const { container } = render(
      <ListControls search={{ value: 'x', onChange: () => {} }}
        results={{ count: 3, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.className).toContain('sr-only')
  })

  it('still renders nothing at all when the bar has no controls', () => {
    // The early return must survive: an empty controls bar should not leave a stray region.
    const { container } = render(<ListControls />)
    expect(container.firstChild).toBeNull()
  })

  it('works on a filter-only bar (no search box)', () => {
    const { container } = render(
      <ListControls filter={{ ...opts, value: 'open' }}
        results={{ count: 4, noun: 'triggers', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('4 triggers')
  })
})

// ── The call-site half ────────────────────────────────────────────────────────────────
// The primitive existing is not the fix — a surface has to pass `results`. This pins the four
// migrated ones and deliberately does NOT assert the other nine: each needs its own filtered
// array and noun, and guessing which local variable holds the post-filter rows is how a sweep
// announces the wrong number.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

describe('the migrated list surfaces pass a result count', () => {
  const ADOPTERS = [
    'pages/projects/ProjectsSection.tsx',
    'pages/knowledge/KnowledgeListPage.tsx',
    'pages/skills/SkillsPage.tsx',
    'pages/prompts/PromptsListPage.tsx',
    // cycle 58 — the tail, each with its own post-filter array and noun
    'pages/triggers/TriggersListPage.tsx',
    'pages/workflows/WorkflowsListPage.tsx',
    'pages/notifications/NotificationsPage.tsx',
    'pages/loops/LoopsListPage.tsx',
    'pages/inbox/InboxPage.tsx',
  ]

  for (const rel of ADOPTERS) {
    it(`${rel} passes results to ListControls`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must pass a results prop').toMatch(/results=\{\{/)
      // `active` must be DERIVED from the query/filter, never hardcoded `true` — otherwise the
      // list announces its length on every mount. Search-driven surfaces derive it from the
      // query (`!!q.trim()`); filter-only ones compare against their own default value.
      const line = src.split('\n').find((l) => l.includes('results={{'))!
      expect(line, 'active must be derived, not hardcoded').not.toMatch(/active:\s*true/)
      expect(line, 'active must reference the query or the filter').toMatch(/active:\s*(!!|\w+\s*!==)/)
    })
  }

  it('never compares `active` to a filter default it does not use', () => {
    // 🪤 `filter !== 'all'` is WRONG on a surface whose filter DEFAULTS to something else:
    // inbox opens on 'open' and loops on 'active', so the comparison was true on mount and
    // the list announced its length before the user did anything (measured: inbox said
    // "39 items" at idle). `active` must compare against that surface's OWN default.
    const defaults: Record<string, string> = {
      'pages/inbox/InboxPage.tsx': 'open',
      'pages/loops/LoopsListPage.tsx': 'active',
    }
    for (const [rel, dflt] of Object.entries(defaults)) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      const line = src.split('\n').find((l) => l.includes('results={{'))!
      expect(line, `${rel} defaults its filter to '${dflt}'`).toContain(`!== '${dflt}'`)
    }
  })

  // ── The hand-laid bars: same idiom, reached through the extracted component ──────────
  const DIRECT: [string, string, RegExp][] = [
    // file, noun, the `active` expression that must be the surface's OWN definition of narrowed
    ['pages/tasks/TasksListPage.tsx', 'tasks', /active=\{query\.trim\(\)\.length > 0\}/],
    ['pages/artifacts/ArtifactsSection.tsx', 'artifacts', /active=\{!!\(q\.trim\(\) \|\| kind \|\| src \|\| col\)\}/],
    ['pages/files/FilesSection.tsx', 'lines', /active=\{showResults\}/],
  ]

  for (const [rel, noun, active] of DIRECT) {
    it(`${rel} renders the extracted announcement, not a copy of it`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must import the shared piece from the canonical module').toMatch(
        /import \{ ResultAnnouncement \} from '(\.\.\/)+ui\/ListControls'/,
      )
      const tag = src.match(/<ResultAnnouncement[\s\S]{0,200}?\/>/)?.[0] ?? ''
      expect(tag, `${rel} must render it`).toMatch(/<ResultAnnouncement/)
      expect(tag, `the noun must be "${noun}"`).toContain(`noun="${noun}"`)
      expect(tag, 'active must be this surface\'s own definition of narrowed').toMatch(active)
      expect(tag, 'active must never be hardcoded — it would announce at idle').not.toMatch(/active=\{true\}/)
      // And no second copy of the region: the whole point of the extraction.
      expect(src, 'a hand-rolled region here would be the drift this change removes')
        .not.toMatch(/role="status" aria-live="polite" className="sr-only"/)
    })
  }

  it('ListControls itself routes through the extracted component', () => {
    // If it kept its own inline copy, the two would drift the moment either changed — which is
    // exactly what "converge onto what already exists" is meant to prevent.
    const src = readFileSync(join(SRC, 'ui/ListControls.tsx'), 'utf8')
    expect(src).toMatch(/<ResultAnnouncement /)
    expect(src).toMatch(/export function ResultAnnouncement\b/)
    const body = src.slice(0, src.indexOf('export function ResultAnnouncement'))
    expect(body, 'the bar must not still hold an inline region').not.toMatch(/role="status" aria-live="polite"/)
  })

  it('the zero branch reads correctly for every noun in use', () => {
    // 🪤 "No matching matches" is what `files` printed first. The copy already carries the word
    // "matching", so a noun that repeats it is wrong — pinned per noun rather than per file.
    for (const noun of ['tasks', 'artifacts', 'lines', 'items']) {
      expect(`No matching ${noun}`, `"${noun}" must not restate the copy`).not.toMatch(/matching match/)
    }
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})
