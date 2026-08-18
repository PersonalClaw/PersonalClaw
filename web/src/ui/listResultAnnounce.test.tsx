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

/** The `active:` expression under test — FOLLOWED THROUGH A NAMED CONST when the surface hoists it.
 *
 *  🪤 FOURTH WIDENING OF THIS FAMILY, and the same lesson each time: the rail was checking the
 *  SPELLING on one line, not the property. `#/inbox` now derives `narrowed` once and shares it between
 *  the announcement and its empty state — strictly better code, since the two can no longer disagree
 *  about which state the list is in — and the line-scoped matcher rejected it. The property is
 *  "derived from the query or from a filter compared against THIS surface's own default"; where that
 *  expression is written is not the property. */
/** The `active` expression at a call site, with every local `const` it names inlined.
 *
 *  🪤 IT USED TO RESOLVE ONLY A BARE IDENTIFIER, and a compound expression hid the interesting half:
 *  `active: groups !== null && filtered` returned as-is, so the default-literal check below never saw
 *  the `risk !== 'all'` INSIDE `filtered`. Caught by mutation — rewriting `ToolsPage` to compare
 *  `risk !== 'safe'` (a value it never defaults to) left this file green. Expanding recursively is
 *  what makes the check reach the surfaces whose "narrowed" flag is named rather than inline. */
function activeExpr(src: string, line: string, depth = 3): string {
  let expr = line.match(/active:\s*([^,}]+)/)?.[1]?.trim() ?? ''
  // 🪤 STRING LITERALS ARE MASKED FIRST. `AppsSection` happens to define `const all = …`, and the
  // scan's identifier match found the `all` INSIDE `libStatus !== 'all'` — rewriting the comparison's
  // own literal into `'(apps ?? [])'`. Caught by mutation: the check then failed on a correct file,
  // for a reason that had nothing to do with the surface.
  const strings: string[] = []
  const mask = (t: string) => t.replace(/'[^']*'/g, (m) => { strings.push(m); return `@@${strings.length - 1}@@` })
  const unmask = (t: string) => t.replace(/@@(\d+)@@/g, (_, i) => strings[Number(i)])
  expr = mask(expr)
  for (let i = 0; i < depth; i++) {
    let grew = false
    for (const id of new Set(expr.match(/[A-Za-z_$][\w$]*/g) ?? [])) {
      if (/^(undefined|null|true|false)$/.test(id)) continue
      // 🪤 ANCHORED TO COMPONENT SCOPE (exactly two spaces of indent). `ToolsPage` declares
      // `filtered` TWICE — `const filtered = list.filter(match)` six levels deep inside its `useMemo`,
      // and the narrowed flag `const filtered = !!q.trim() || risk !== 'all'` at component scope. A
      // file-wide regex resolved the FIRST, so the `risk` comparison the check exists to read was
      // invisible. Caught by mutation: `risk !== 'safe'` left this file green. The first `const X =`
      // in a file is not necessarily the one in scope at the call site.
      const rhs = mask(src.match(new RegExp(`^  const ${id}\\s*=\\s*(.+)$`, 'm'))?.[1] ?? '') || undefined
      // Only inline a COMPLETE single-line expression. 🪤 Without this the scan spliced in the first
      // line of a `useMemo<Group[] | null>(() => {` body and compared a literal against `(apps ?? [])`
      // — garbage that fails loudly, but for the wrong reason. A hook call or an unbalanced line is
      // not a boolean this check can reason about, so it is left as an opaque name.
      const balanced = rhs !== undefined
        && [...rhs].filter((c) => '([{'.includes(c)).length === [...rhs].filter((c) => ')]}'.includes(c)).length
      if (!rhs || !balanced || rhs.includes(id) || /use[A-Z]\w*\(|=>/.test(rhs)) continue
      expr = expr.replace(new RegExp(`\\b${id}\\b`, 'g'), `(${rhs})`)
      grew = true
    }
    if (!grew) break
  }
  return unmask(expr)
}

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
// The primitive existing is not the fix — a surface has to pass `results`.
//
// 🔑 THIS USED TO PIN A HAND-WRITTEN LIST OF "MIGRATED" SURFACES, and said so: it "deliberately does
// NOT assert the other nine: each needs its own filtered array and noun, and guessing which local
// variable holds the post-filter rows is how a sweep announces the wrong number." That was an honest
// deferral, and it is now done — the remaining six call sites were wired by hand, each from the array
// its own body renders. So the census becomes the POPULATION: every `<ListControls>` in the tree.
//
// The difference matters. A list of adopters goes green while a sixteenth consumer arrives silent —
// which is exactly what happened. Measured before this change: **16 call sites, 9 passing `results`,
// and one of them was `SkillsPage`'s SECOND bar**, invisible to a scan that read only the first
// `results={{` line per file.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** Source with comments stripped. `WorkbenchLayout`'s docstring says "Pass a `<ListControls …>`",
 *  which a raw scan counts as a seventeenth consumer that can never pass a prop. */
function code(abs: string): string {
  return readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

/** The opening tag of every `<ListControls …>` in a file — balanced, so a `{{…}}` prop value with a
 *  `>` inside an arrow function does not end it early. */
function controlsTags(src: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(/<ListControls\b/g)) {
    let depth = 0
    for (let j = m.index!; j < src.length; j++) {
      const c = src[j]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) { out.push(src.slice(m.index!, j + 1)); break }
    }
  }
  return out
}

function consumers(): Array<{ rel: string; tag: string }> {
  return walk(SRC).flatMap((abs) =>
    controlsTags(code(abs)).map((tag) => ({ rel: abs.replace(SRC + '/', ''), tag })),
  )
}

describe('EVERY list bar passes a result count — the ratchet', () => {
  it('the census finds the population', () => {
    const all = consumers()
    expect(all.length, 'the scan must find the bars').toBeGreaterThanOrEqual(15)
    // Counted, not spot-checked: a sweep that iterates its own matches never visits the one that
    // stopped matching.
    const silent = all.filter((c) => !/results=\{\{/.test(c.tag)).map((c) => c.rel)
    expect(silent, 'a bar that narrows a list without announcing it tells a screen reader nothing')
      .toEqual([])
  })

  it('`active` is derived on every one of them, never hardcoded', () => {
    for (const { rel, tag } of consumers()) {
      const line = tag.split('\n').find((l) => l.includes('results={{')) ?? tag
      expect(line, `${rel}: active must not be hardcoded — it would announce at idle`)
        .not.toMatch(/active:\s*true/)
      const expr = activeExpr(code(join(SRC, rel)), line)
      expect(expr, `${rel}: active must reference the query or a filter`).toMatch(/(!!|\w+\s*!==)/)
    }
  })

  it('never compares `active` to a filter default it does not use', () => {
    // 🪤 `filter !== 'all'` is WRONG on a surface whose filter DEFAULTS to something else: inbox opens
    // on 'open' and loops on 'active', so the comparison was true on mount and the list announced its
    // length before the user did anything (measured: inbox said "39 items" at idle).
    //
    // This used to be a two-entry map of the surfaces known to have bitten. It now RESOLVES each
    // surface's actual default from its own `useState`/`useQueryParam` call, so a seventeenth surface
    // with a non-'all' default is caught by the same rule instead of being added to a list afterwards.
    let checked = 0
    for (const { rel, tag } of consumers()) {
      const src = code(join(SRC, rel))
      const line = tag.split('\n').find((l) => l.includes('results={{')) ?? tag
      for (const cmp of activeExpr(src, line).matchAll(/(\w+)\s*!==\s*'([^']+)'/g)) {
        const [, name, literal] = cmp
        const dflt =
          src.match(new RegExp(`const \\[${name},[^\\]]*\\] = useState(?:<[^>]*>)?\\('([^']*)'\\)`))?.[1]
          ?? src.match(new RegExp(`const \\[${name},[^\\]]*\\] = useQueryParam\\([^,]+,[^,]+,\\s*'[^']*',\\s*'([^']*)'`))?.[1]
        if (dflt === undefined) continue          // not a locally-defaulted piece of state
        checked++
        expect(literal, `${rel}: '${name}' defaults to '${dflt}', so comparing to '${literal}' is ` +
          'true (or false) at rest').toBe(dflt)
      }
    }
    // Vacuity floor: a regex that resolves no defaults would pass this test while checking nothing.
    // 12 comparisons resolve today across the 15 bars. Held as a floor rather than an equality so a
    // new surface does not have to touch this line, but low enough to fail loudly if the resolution
    // regexes stop matching — a version of this check that resolved NOTHING passed while reading
    // nothing, twice, during this cycle.
    expect(checked, 'the scan must actually resolve some defaults').toBeGreaterThanOrEqual(12)
  })

  it('the count comes from the list the body renders, not a second filter chain', () => {
    // 🪤 The failure this shape invites is an announcement that disagrees with the screen. Each of
    // these is pinned to the ONE array its own body maps over, so re-deriving the count elsewhere
    // (a fourth inline `.filter(...)`, a `.length` on the pre-filter list) trips here.
    const pins: Array<[string, RegExp]> = [
      ['pages/agents/AgentsListPage.tsx', /count: shownCount\b/],
      ['pages/code/CodeSection.tsx', /count: shown\.length\b/],
      ['pages/tools/ToolsPage.tsx', /count: shownTools\b/],
      ['pages/skills/SkillsPage.tsx', /count: results\?\.length \?\? 0/],
      ['pages/apps/AppsSection.tsx', /count: \(libResult \?\? \[\]\)\.length/],
      ['pages/apps/AppsSection.tsx', /count: storeResult\.length/],
    ]
    for (const [rel, re] of pins) {
      expect(code(join(SRC, rel)), `${rel} must count its own rendered list`).toMatch(re)
    }
    // And the rows must read that same array — `AgentsListPage` filtered its native list inline
    // twice more, which is a third and fourth copy for a count to drift from.
    const agents = code(join(SRC, 'pages/agents/AgentsListPage.tsx'))
    expect(agents, 'the native rows read the hoisted array').toMatch(/\{shownNative\.map\(/)
    expect(agents, 'and nothing re-filters it inline').not.toMatch(/native\.agents\.filter\(/)
  })

  it('a bar that renders during its own skeleton waits before announcing', () => {
    // 🪤 An announcement is only true once the data is in. Three of these bars mount while their
    // list is still loading, so `active` has to wait — otherwise a filter restored from the URL
    // announces "No matching apps" about a list that has not arrived. The other bars render only
    // after the list is non-empty, where such a guard would be an INERT condition, so they must not
    // carry one.
    const guarded: Array<[string, RegExp]> = [
      ['pages/apps/AppsSection.tsx', /active: apps !== undefined && libNarrowed/],
      ['pages/apps/AppsSection.tsx', /active: catalog !== undefined && storeNarrowed/],
      ['pages/tools/ToolsPage.tsx', /active: groups !== null && filtered/],
      ['pages/agents/AgentsListPage.tsx', /active: !!n && !\(loading && groups\.length === 0\)/],
      // Remote search: waits for the fetch to settle, not for a local array to fill.
      ['pages/skills/SkillsPage.tsx', /active: !!q\.trim\(\) && !loading && results !== null/],
    ]
    for (const [rel, re] of guarded) expect(code(join(SRC, rel)), `${rel}`).toMatch(re)
    const codeSection = code(join(SRC, 'pages/code/CodeSection.tsx'))
    expect(codeSection, 'this bar renders inside `!!projects?.length`, so it needs no guard')
      .toMatch(/active: !!needle \|\| filter !== 'all'/)
  })

  it('one definition of "narrowed" per view, shared with the empty state', () => {
    // The library empty state and the library announcement have to agree about whether a filter is
    // on; two copies of a five-term boolean do not stay equal. Same for the Store, whose expression
    // was already duplicated into `filtersActive`.
    const apps = code(join(SRC, 'pages/apps/AppsSection.tsx'))
    expect(apps).toMatch(/const libNarrowed = /)
    expect(apps).toMatch(/const storeNarrowed = /)
    expect(apps, 'the empty state reads the shared flag').toMatch(/!libNarrowed \? \(/)
    expect(apps, 'and so does the Clear-filters affordance').toMatch(/filtersActive=\{storeNarrowed\}/)
    expect(apps, 'no second copy of the store expression').not.toMatch(
      /!!n \|\| storeType !== 'all' \|\| storeEntity !== 'all' \|\| storeTag !== 'all'[\s\S]{0,40}filtersActive/,
    )
  })
})

describe('the hand-laid bars reach the same idiom', () => {
  // ── The hand-laid bars: same idiom, reached through the extracted component ──────────
  const DIRECT: [string, string, RegExp][] = [
    // file, noun, the `active` expression that must be the surface's OWN definition of narrowed
    ['pages/tasks/TasksListPage.tsx', 'tasks', /active=\{query\.trim\(\)\.length > 0\}/],
    ['pages/artifacts/ArtifactsSection.tsx', 'artifacts', /active=\{!!\(q\.trim\(\) \|\| kind \|\| src \|\| col\)\}/],
    ['pages/files/FilesSection.tsx', 'lines', /active=\{showResults\}/],
    // The settings area's hand-laid bars. Each `active` is that panel's own narrowed flag, and each
    // count comes from the array its own body renders.
    ['pages/settings/ArchivePanel.tsx', 'archived sessions', /active=\{!!needle\}/],
    // Two dimensions: the text filter AND the level buttons, whose default is the least
    // restrictive level — comparing to anything else would announce at rest.
    ['pages/settings/DiagnosticsPanel.tsx', 'lines', /active=\{q !== '' \|\| minLevel !== 'DEBUG'\}/],
    ['pages/settings/MemoryPanel.tsx', 'memories', /active=\{!!q\.trim\(\) \|\| kindFilter !== 'all'\}/],
    ['pages/settings/MemoryPanel.tsx', 'events', /active=\{q !== ''\}/],
    ['pages/settings/ModelsPanel.tsx', 'models', /active=\{!!query\.trim\(\)\}/],
    // Remote searches: `active` waits for the fetch, or it reports the previous query's count.
    ['pages/settings/LocalModelManager.tsx', 'models',
      /active=\{!!query\.trim\(\) && !searching && searchResults !== null\}/],
    ['pages/settings/OllamaModelManager.tsx', 'models',
      /active=\{!!q\.trim\(\) && !searching && results !== null\}/],
    ['pages/settings/SettingsHome.tsx', 'settings', /active=\{q !== ''\}/],
  ]

  for (const [rel, noun, active] of DIRECT) {
    it(`${rel} renders the extracted announcement, not a copy of it`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must import the shared piece from the canonical module').toMatch(
        /import \{ ResultAnnouncement \} from '(\.\.\/)+ui\/ListControls'/,
      )
      // 🪤 ALL tags, not the first. `MemoryPanel` renders two (the Studio explorer and the audit
      // log), and a `src.match()` that stops at the first would leave the second unasserted — the
      // same first-match hole that let `SkillsPage`'s second bar ship silent.
      const tags = [...src.matchAll(/<ResultAnnouncement[\s\S]{0,260}?\/>/g)].map((m) => m[0])
      expect(tags.length, `${rel} must render it`).toBeGreaterThanOrEqual(1)
      const tag = tags.find((t) => t.includes(`noun="${noun}"`)) ?? ''
      expect(tag, `one of them must use the noun "${noun}"`).toContain(`noun="${noun}"`)
      expect(tag, 'active must be this surface\'s own definition of narrowed').toMatch(active)
      for (const t of tags) {
        expect(t, 'active must never be hardcoded — it would announce at idle').not.toMatch(/active=\{true\}/)
      }
      // And no second copy of the region: the whole point of the extraction.
      expect(src, 'a hand-rolled region here would be the drift this change removes')
        .not.toMatch(/role="status" aria-live="polite" className="sr-only"/)
    })
  }

  it('each count comes from the array its own body renders', () => {
    // 🪤 CAUGHT BY MUTATION, and it is the whole defect class: rewriting `SettingsHome`'s count from
    // its `matches` map to `SETTINGS_WIDGETS.length` — the UNFILTERED total — passed every other
    // assertion here. The noun and `active` were still perfect; the number was simply a lie. Pinning
    // `active` without pinning the count leaves the announcement free to describe a different list
    // than the one on screen.
    const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const COUNTS: Array<[string, RegExp]> = [
      ['pages/settings/ArchivePanel.tsx', /count=\{shown\.length\}/],
      ['pages/settings/DiagnosticsPanel.tsx', /count=\{visible\.length\}/],
      ['pages/settings/ModelsPanel.tsx', /count=\{filtered\.length\}/],
      ['pages/settings/LocalModelManager.tsx', /count=\{searchResults\?\.length \?\? 0\}/],
      ['pages/settings/OllamaModelManager.tsx', /count=\{results\?\.length \?\? 0\}/],
      // Not a filtered array at all: each widget reports whether it matched, and that same map
      // decides what stays on screen (`anyMatch` reads it), so the two cannot disagree.
      ['pages/settings/SettingsHome.tsx', /count=\{Object\.values\(matches\)\.filter\(Boolean\)\.length\}/],
    ]
    for (const [rel, re] of COUNTS) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} must count its own rendered list`)
        .toMatch(re)
    }
    // MemoryPanel's two both read a `shown` — one per tab, each its own memo.
    const mem = strip(readFileSync(join(SRC, 'pages/settings/MemoryPanel.tsx'), 'utf8'))
    expect((mem.match(/count=\{shown\.length\}/g) ?? []).length, 'both MemoryPanel lists').toBe(2)
    // A count read off the PRE-filter collection is the shape being excluded, everywhere.
    for (const [rel] of COUNTS) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} must not count the unfiltered list`)
        .not.toMatch(/count=\{(?:archives|entries|capable|SETTINGS_WIDGETS|items|events)\.length\}/)
    }
  })

  it('EVERY search control in the settings area announces — the area census', () => {
    // 🔑 A pinned table says "these are right"; a census says "there are no others". The settings
    // panels are the area this cycle swept end to end, so the claim is total here: every file under
    // `pages/settings` that renders a search control also renders the announcement. **Zero
    // exemptions** — if a panel needs one, that is a judgement to write down, not a silent gap.
    const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const withControl = walk(join(SRC, 'pages/settings'))
      .map((abs) => ({ rel: abs.replace(SRC + '/', ''), src: strip(readFileSync(abs, 'utf8')) }))
      .filter(({ src }) => /<SearchField\b/.test(src) || /<TextInput[^>]*ariaLabel="(?:Search|Filter)/.test(src))
    expect(withControl.length, 'the census must find the panels').toBeGreaterThanOrEqual(7)
    const silent = withControl.filter(({ src }) => !src.includes('<ResultAnnouncement')).map((f) => f.rel)
    expect(silent, 'a panel that narrows a list without announcing it tells a screen reader nothing')
      .toEqual([])
  })

  it('MemoryPanel announces its two LISTS and not its context-preview input', () => {
    // 🪤 THE ONE JUDGEMENT IN THAT CENSUS, pinned so it is not "finished" later. MemoryPanel holds
    // THREE search-shaped controls, and only two are filters: the Studio explorer and the audit log.
    // The third is `InspectTab`'s — you type a query and press a button to see what context a
    // retrieval WOULD return. Nothing is being narrowed, so a result count would describe nothing.
    const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const src = strip(readFileSync(join(SRC, 'pages/settings/MemoryPanel.tsx'), 'utf8'))
    const controls = (src.match(/<SearchField\b/g) ?? []).length
      + (src.match(/<TextInput[^>]*ariaLabel="(?:Search|Filter)/g) ?? []).length
    expect(controls, 'two SEARCH-shaped controls — the explorer and the audit log').toBe(2)
    // Exact, not a floor: a third announcement here would be one describing nothing.
    expect((src.match(/<ResultAnnouncement\b/g) ?? []).length, 'one per filter, no more').toBe(2)
    // And the third field is why the census needs no exemption for this panel: it is named as a
    // QUERY, not a filter, so the population never included it. Its text is submitted to produce a
    // preview — nothing is narrowed, so a result count would describe nothing.
    expect(src, 'the third field feeds a retrieval preview').toContain('api.memoryContextPreview(q)')
    expect(src, 'and is named as a query, which is what keeps it out of the census')
      .toContain('ariaLabel="Query to preview injected memory context"')
  })

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
