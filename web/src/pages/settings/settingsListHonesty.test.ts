import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── Five settings lists that answered a 500 with "you have none" ───────────────────────────
//
// The `LoadError` family, worked as a COHERENCE family this time: not one surface, but every async
// list under `#/settings/*`. Census — a `useCachedData` reader whose data is rendered as a list with
// an empty branch — found ten, and **five conflated a failed load with an empty collection**, all by
// the same mechanism: `.catch(() => [])` inside the fetcher, so the rejection never reached the hook
// and `error` could not be read even if someone tried.
//
// Driven at 1280×900 with a COLD sessionStorage (a warm cache masks this entirely) and the list's own
// endpoint at 500. What the user was told, before → after:
//
//   #/settings/archive      "No archived sessions yet"        → alert "Couldn't load your archived sessions
//                            no alert, no retry                       — probe-induced failure" + Retry
//   #/settings/audit        "No matching events"              → alert "Couldn't load your audit log …" + Retry
//   #/settings/providers    "No remote model providers yet"   → alert band "Couldn't load your remote
//                                                               model providers: …" + Retry
//   #/settings/tool-output  "No custom rules — the builtin …" → alert band "Couldn't load your projection
//                                                               rules: …" + Retry
//   #/settings/memory →     "No matching events"              → alert "Couldn't load your memory audit log …"
//     Audit tab                                                       + Retry
//
// In every before case the server's own message was NOWHERE on the page (`saidError: false`) and no
// element carried `role="alert"`. Retry was measured to recover: with the route restored, clicking it
// cleared the failure and rendered the real (genuinely empty) list.
//
// 🔑 THE AUDIT LOG IS THE ONE THAT MATTERS MOST, and it is worth saying why: "No matching events" is
// the same sentence a tamper-evident security log shows when nothing happened. A read failure that
// renders as silence is the one failure mode a security surface must never have.
//
// 🪤 THREE OF THE FIVE CARRY `{ persist: true }`, so the substituted `[]` was written to
// `sessionStorage` and survived a reload — the fiction outlived the outage.
//
// 🔑 SCALE PICKS THE PRIMITIVE, and both already exist:
//   • page-body scale (Archive, Audit, the memory Audit tab) → `LoadError`, the full-bleed form with
//     heading + server message + Retry. Under `PanelHeader`'s `h1`, its `h2` is level-correct.
//   • a region inside a panel (the remote-provider list, the custom-rules list inside a `Section`)
//     → the canonical `InlineError` band + a Retry button. `LoadError`'s `py-2xl` and second heading
//     would be wrong at that scale, and inventing a third error shape would make three.
//
// Archive and Audit join the shared ADOPTERS ratchet in `ui/loadErrorState.test.tsx`. The other three
// cannot: `MemoryPanel` is a 1200-line multi-tab file whose FIRST `<ListSkeleton>` belongs to a
// different tab (so that rail's source-order reachability proxy rejects it), and the two band adopters
// do not render `<LoadError>` at all. Same reason `#/chat` got `sessionLoadHonesty.test.ts` — a
// file-scoped rail over a multi-surface file measures the wrong thing, so the property gets pinned
// per site instead.

const HERE = join(process.cwd(), 'src', 'pages', 'settings')
const read = (f: string) => readFileSync(join(HERE, f), 'utf8')

/** file → the fetcher call that must NOT swallow, and the announced failure it must render. */
const SITES = [
  { file: 'MemoryPanel.tsx', fetch: 'api.memoryEvents({ limit: 100 })', announce: /<LoadError what="memory audit log"/ },
  { file: 'ModelBackends.tsx', fetch: 'api.modelProviders()', announce: /<InlineError icon className="mb-3">/ },
  { file: 'ProjectionRulesPanel.tsx', fetch: 'api.projectionRules()', announce: /<InlineError icon>/ },
]

describe('a settings list distinguishes a failed load from an empty one', () => {
  for (const s of SITES) {
    it(`${s.file} lets the rejection reach the hook`, () => {
      const src = read(s.file)
      expect(src, 'the fetcher must be bare — a `.catch` here makes the error branch unreachable')
        .toContain(`${s.fetch},`)
      const swallowed = new RegExp(`${s.fetch.replace(/[.()[\]{}]/g, '\\$&')}\\s*\\.catch`)
      expect(swallowed.test(src), `${s.file} still substitutes a value for the rejection`).toBe(false)
    })

    it(`${s.file} renders an ANNOUNCED failure, not a quiet empty line`, () => {
      expect(read(s.file)).toMatch(s.announce)
    })

    it(`${s.file} reads the error off the hook`, () => {
      // Both destructure shapes ship in this repo; either captures the rejection.
      expect(read(s.file)).toMatch(/\berror\s*[,}]|error:\s*\w*(?:err|Err)\w*/)
    })
  }

  it('the failure branch precedes the empty branch at each site', () => {
    // `data === undefined` is true for loading, failed AND empty, so a later test never runs. Measured
    // per site rather than per file: `MemoryPanel` has five tabs and eight readers, and a whole-file
    // search would compare branches that belong to different screens.
    const mem = read('MemoryPanel.tsx')
    const auditTab = mem.slice(mem.indexOf('function AuditTab()'), mem.indexOf('function AuditRow'))
    expect(auditTab.search(/<LoadError\b/), 'the memory Audit tab guards before it skeletons')
      .toBeLessThan(auditTab.search(/<ListSkeleton\b/))

    const backends = read('ModelBackends.tsx')
    expect(backends.search(/<InlineError\b/)).toBeLessThan(backends.search(/<RemoteProvidersSkeleton\s*\/>/))

    const rules = read('ProjectionRulesPanel.tsx')
    const chain = rules.slice(rules.indexOf('rules === undefined'))
    expect(chain.search(/<InlineError\b/)).toBeLessThan(chain.search(/<ListSkeleton\b/))
    expect(chain.search(/<ListSkeleton\b/), 'and the loading branch precedes the empty one')
      .toBeLessThan(chain.search(/No custom rules/))
  })

  it('the projection panel gained a loading state it never had', () => {
    // It rendered `rules ?? []`, so on every cold open it asserted "No custom rules" BEFORE the config
    // arrived — an empty state as the first thing a user sees, replaced a moment later.
    const src = read('ProjectionRulesPanel.tsx')
    expect(src).toMatch(/rules === undefined \? \(\s*<ListSkeleton/)
  })

  it('ModelBackends KEEPS the models catch — that one is a real distinction', () => {
    // 🪤 The two reads in that `Promise.all` are not the same kind of thing. `modelProviders` IS the
    // list, so its failure must surface. `modelsAvailable` only decorates each card with a model
    // count, so keeping its fallback degrades a card instead of blanking the panel. A future
    // no-swallow sweep would "fix" this into a regression, so it is pinned deliberately.
    expect(read('ModelBackends.tsx')).toMatch(/api\.modelsAvailable\(\)\.catch\(\(\) => \[\] as/)
  })

  it('the settings HUB does not poison the keys these panels now guard', () => {
    // 🔴 THE ONE THAT WOULD HAVE SHIPPED INERT. `useCachedData` caches by KEY, and the hub's bento
    // tiles read the same keys as the panels. While `settingsWidgets` swallowed, opening `#/settings`
    // primed `cache:settings:projection-rules` with `[]`, so the panel's brand-new error branch could
    // never fire on the path a user actually takes (hub → tile → panel). Driving the panel by URL with
    // a cold cache — which is how the fix was verified — never runs the tile, so the probe passed while
    // the real journey stayed broken. Measured after: hub-then-panel shows the alert.
    const widgets = readFileSync(join(HERE, 'settingsWidgets.tsx'), 'utf8')
    for (const key of ['settings:archives', 'settings:projection-rules']) {
      const call = widgets.slice(widgets.indexOf(`'${key}'`), widgets.indexOf(`'${key}'`) + 160)
      expect(call, `the hub tile for ${key} must not substitute a value`).not.toMatch(/\.catch\(\(\) =>/)
    }
  })

  it('the census that found these five is reproducible, and its population is stated', () => {
    // Not vacuous: the scan must see the whole panel directory, and the number of settings readers
    // still substituting a value is recorded here rather than left implied. These are config-form and
    // decoration reads, not list bodies — a separate family, and enforcing a blanket rule over them
    // now would make a red gate out of surfaces nobody has measured.
    const panels = readdirSync(HERE).filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
    expect(panels.length, 'the scan must find the settings panels').toBeGreaterThan(25)
    // 🪤 STRIP COMMENTS FIRST. Written without this, the scan flagged `ArchivePanel` — because the
    // comment ABOVE the fixed fetcher quotes the `.catch(() => [])` it removed. Third time a ratchet
    // in this repo has counted its own prose as code (a "<button>" in comment text broke the primitive
    // ratchet twice). A rail measures the program, not the explanation of it.
    const code = (n: string) => readFileSync(join(HERE, n), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const swallowers = panels.filter((n) =>
      /useCachedData[\s\S]{0,240}?\.catch\(\(\) =>\s*(\[\]|null|undefined|\{\})/.test(code(n)),
    )
    expect(swallowers, 'the five list bodies must no longer be among them').not.toContain('ArchivePanel.tsx')
    expect(swallowers).not.toContain('AuditPanel.tsx')
    expect(swallowers).not.toContain('ProjectionRulesPanel.tsx')
    // The remaining population, measured: **17 panels**, and two of them are here on purpose —
    // `ModelBackends` keeps its models-decoration fallback (above), and `MemoryPanel`'s other SEVEN
    // readers (stats, semantic, episodic, lessons, graph, settings, volunteer) still substitute. Those
    // feed config forms and counters rather than a list body, which is a different family with a
    // different right answer per site. A ceiling, not a target: it may only come down.
    expect(swallowers.length, 'if this moves, say which way and why in the PR').toBeLessThanOrEqual(17)
  })
})
