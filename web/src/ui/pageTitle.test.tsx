import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { PageTitle } from './PageTitle'

// ── Every destination names itself with an h1 ──────────────────────────────────────────
//
// Measured across 20 nav destinations on the running app, before this primitive existed:
//
//   17 of 20 had NO h1 at all.
//   13 of 20 rendered ZERO headings of any level.
//   The 3 that had one got it from a greeting/headline (the dashboard's "Good afternoon",
//   chat's and the loop composer's display-s), never from the page's own name.
//   #/dashboard additionally skipped h1 -> h3 across all seven of its section headings.
//
// Heading navigation — the H key in NVDA/JAWS, the rotor in VoiceOver — is how a screen-reader
// user skims an unfamiliar page. On 13 of 20 surfaces it landed on nothing, so there was no way
// to orient beyond reading the whole DOM in order.
//
// The title was never missing; only its semantics were. Every page rendered
// `<span data-type="title-l" className="text-on-surface">Tasks</span>` into the TopBar left
// slot, hand-rolled at ~30 sites. `PageTitle` is that span with the tag it should have had.
//
// 🪤 THE INTERESTING PART IS WHAT IS *NOT* IN THIS LIST. Three exclusions, each deliberate:
//
//   · `ChatPage`'s "Chat history" — a DOCKED PANEL header. A side panel is not the page, and
//     giving it an h1 would claim the document's title for a drawer.
//   · ~~`AppFrame` and the DETAIL pages~~ — **SETTLED IN CYCLE 162, and the URL is what settles it.**
//     Driven across five surfaces: when the URL's PATH identifies the entity (`#/workflows/runs/<id>`,
//     `#/projects/<id>`, `#/app/<name>`) the route rendered **NO h1 at all** — axe
//     `page-has-heading-one`. When the entity is a QUERY PARAM on the list route (`?item=`, `?open=` —
//     the peek) the list kept its own h1, which is right: a docked panel is not the page. So the entity
//     takes the h1 exactly where the entity is the destination, and that is the row lesson from cycle
//     161 one level up — **a destination is named by its identity, not by its category.**
//     `#/artifacts` with an artifact open swaps its `PageTitle` for a Back button and renders the
//     artifact name as a bare span; the h1 measured on that route comes from the artifact's own
//     CONTENT, not from the page title. So the question is genuinely open, not settled by
//     precedent. Measured h1-less today: `#/workflows/runs/<id>` (loses "Workflows" the moment a
//     run opens — axe `page-has-heading-one`), `WorkflowDefDetail`, `KnowledgeDetailPage`,
//     `PromptViewPage`, `CodeSection`/`CodeCockpitPage`/`CodePlanReview`, `AppFrame`,
//     `ProjectsSection`'s project view, and `SkillsPage`'s browse branch.
//
// ── Cycle 150: the CREATE pages, which are not that subset ───────────────────────────────────
//
// "New task" is no more an entity name than "Tasks" is — a create destination has a static name,
// exactly like the 20 converged above. All five measured h1-less, driven at 1440x900:
//
//   #/tasks/new      no h1; headings began at **H3** ("Basics")
//   #/prompts/new    no h1, and **ZERO headings of any level**
//   #/agents/new     no h1, ZERO headings — under 9,441 characters of form
//   #/triggers/new   no h1, ZERO headings
//   #/knowledge/new  no h1 (both steps: the type picker and the chosen-type form)
//
// Verified pixel-identical after conversion: 8/8 captures at 0.00% across both themes, which is
// what `PageTitle`'s own doc claims and this pass re-measured rather than trusted.
//
// ⚠️ `#/tasks/new` still skips **h1 → h3**: `TaskForm`'s `Section` renders an `h3`, and it has TWO
// hosts — `TaskCreatePage` (a full page) and `TaskDetail` (a docked panel). Levelling it is a
// shared-component change with a second consumer to reason about, so it is deliberately NOT in
// this pass; the skip existed before (from nothing straight to h3) and now at least has an anchor
// above it.
//   · `topBarTitleTruncates.test.tsx` — a fixture that must keep exercising the RAW idiom, or
//     it stops testing what it claims to.

describe('PageTitle', () => {
  it('renders an h1', () => {
    render(<PageTitle>Tasks</PageTitle>)
    expect(screen.getByRole('heading', { level: 1, name: 'Tasks' })).toBeTruthy()
  })

  it('carries the title-l type role, not a per-component size', () => {
    render(<PageTitle>Tasks</PageTitle>)
    expect(screen.getByRole('heading', { level: 1 }).getAttribute('data-type')).toBe('title-l')
  })

  it('keeps the ink token and merges extra classes', () => {
    render(<PageTitle className="flex items-center gap-s">Inbox</PageTitle>)
    const h = screen.getByRole('heading', { level: 1 })
    expect(h.className).toContain('text-on-surface')
    expect(h.className).toContain('flex items-center gap-s')
  })

  it('lets the title own trailing chrome (a count badge reads as part of the name)', () => {
    render(<PageTitle>Notifications <span>3</span></PageTitle>)
    expect(screen.getByRole('heading', { level: 1, name: 'Notifications 3' })).toBeTruthy()
  })
})

// ── The call-site half ────────────────────────────────────────────────────────────────

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.doc\.tsx$/.test(n) ? [p] : []
  })

/** The destinations converged in this pass: a nav destination whose TopBar left slot is a
 *  STATIC page name. Every one of these measured h1-less before the change. */
const DESTINATIONS = [
  'pages/tasks/TasksListPage.tsx',
  'pages/tools/ToolsPage.tsx',
  'pages/triggers/TriggersListPage.tsx',
  'pages/terminal/TerminalPage.tsx',
  'pages/agents/AgentsListPage.tsx',
  'pages/knowledge/KnowledgeListPage.tsx',
  'pages/prompts/PromptsListPage.tsx',
  'pages/loops/LoopsListPage.tsx',
  'pages/settings/SettingsPage.tsx',
  'pages/inbox/InboxPage.tsx',
  'pages/notifications/NotificationsPage.tsx',
  'pages/projects/ProjectsSection.tsx',
  'pages/skills/SkillsPage.tsx',
  'pages/learning/LearningPage.tsx',
  'pages/artifacts/ArtifactsSection.tsx',
  'pages/apps/AppsSection.tsx',
  'pages/files/FilesSection.tsx',
  'pages/discover/DiscoverPage.tsx',
  'pages/workflows/WorkflowsListPage.tsx',
  'ui/ListScaffold.tsx',
  // Cycle 162 — the entity-detail destinations (a path segment IS the entity).
  'pages/workflows/WorkflowRunDetail.tsx',
  'pages/workflows/WorkflowDefDetail.tsx',
  'pages/apps/AppFrame.tsx',
  // Cycle 150 — the create destinations.
  'pages/tasks/TaskCreatePage.tsx',
  'pages/triggers/TriggerCreatePage.tsx',
  'pages/agents/AgentCreatePage.tsx',
  'pages/prompts/PromptCreatePage.tsx',
  'pages/knowledge/KnowledgeCreatePage.tsx',
]

describe('every converged destination names itself', () => {
  for (const rel of DESTINATIONS) {
    it(`${rel} titles itself with PageTitle`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must render the primitive').toMatch(/<PageTitle[\s>]/)
      expect(src, 'must import it').toMatch(/import \{ PageTitle \}/)
    })
  }

  it('none of them kept a bare title-l span in the TopBar left slot', () => {
    // The specific idiom this pass replaces. A file that renders BOTH has been half-migrated,
    // which is the state that looks converged in a diff and is not.
    const holdouts: string[] = []
    for (const rel of DESTINATIONS) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      if (/left=\{<span data-type="title-l"/.test(src)) holdouts.push(rel)
    }
    expect(holdouts, `still hand-rolling the page title:\n  ${holdouts.join('\n  ')}`).toEqual([])
  })

  it('renders exactly ONE PageTitle per destination (a page has one name)', () => {
    // Two files render more than one, both in mutually exclusive branches, never both at once:
    // SkillsPage (proposals view vs installed view) and KnowledgeCreatePage (the type picker step
    // vs the chosen-type form). Named rather than waved through, so a THIRD one has to justify
    // itself here.
    // `ProjectsSection` renders three, one per mutually exclusive branch: the LIST page's title, the
    // shared detail shell's default, and the project view's own `titleNode`. 🪤 Converting the shell's
    // default alone left `#/projects/<id>` still h1-less — the project view passes `titleNode`, so the
    // `??` default never renders. Follow the value that reaches the slot, not the first one you find.
    const TWO_STEP = ['pages/skills/SkillsPage.tsx', 'pages/knowledge/KnowledgeCreatePage.tsx',
      'pages/projects/ProjectsSection.tsx']
    const offenders: string[] = []
    for (const rel of DESTINATIONS) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      const n = [...src.matchAll(/<PageTitle[\s>]/g)].length
      if (n > 1 && !TWO_STEP.includes(rel)) offenders.push(`${rel} (${n})`)
    }
    expect(offenders, `more than one page title:\n  ${offenders.join('\n  ')}`).toEqual([])
  })

  it('does NOT give a docked panel header an h1', () => {
    // ChatPage's "Chat history" is a SidePanel header. If a future pass converts it, heading
    // navigation gets two competing document titles on one route.
    const chat = readFileSync(join(SRC, 'pages/ChatPage.tsx'), 'utf8')
    expect(
      /left=\{<PageTitle[^>]*>Chat history/.test(chat),
      'the Chat history panel header must stay a span — a drawer is not the page',
    ).toBe(false)
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})

// ── The level below the title ─────────────────────────────────────────────────────────
//
// An h1 fixes orientation only if what follows it is an h2. The dashboard rendered its page
// greeting as h1 and then EVERY one of its seven sections as h3 — measured `h1 -> h3 "Needs you"`
// — so a screen-reader user skipping by level fell straight past a missing tier. Both section
// headers (the shared `Section` and the separately-authored `PinnedTiles`) are now h2.

describe('an entity is the destination when the URL says so', () => {
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  it('the path-segment detail routes carry the entity as their h1', () => {
    expect(read('pages/workflows/WorkflowRunDetail.tsx'), 'the run')
      .toMatch(/<PageTitle className="truncate">\{run\.workflow\}<\/PageTitle>/)
    expect(read('pages/workflows/WorkflowDefDetail.tsx'), 'the definition')
      .toMatch(/<PageTitle className="truncate">\{name\}<\/PageTitle>/)
    expect(read('pages/projects/ProjectsSection.tsx'), "the project view's own titleNode")
      .toMatch(/<PageTitle className="truncate">\{project\.name\}<\/PageTitle>/)
    expect(read('pages/apps/AppFrame.tsx'), 'the installed app')
      .toMatch(/<PageTitle className="flex items-center gap-s">/)
    // Named in this file's own prose as "measured h1-less today" but never converted: the last
    // inventory surface still without a heading of ANY level. Its path identifies the entity
    // (`#/knowledge/item/<id>`), so the settled rule gives it the h1. Driven before the change:
    // 48 surfaces, 46 with an h1, and this one had ZERO headings at 1s/2.5s/5s/9s — persistent,
    // not a loading frame. (`#/settings/models` LOOKED h1-less in the same census and is not: it
    // returns a bare `ListSkeleton` while models load, so a 1.1s sample caught it before its
    // `PanelHeader`. Sample a heading census past the skeleton, or it lies.)
    expect(read('pages/knowledge/KnowledgeDetailPage.tsx'), 'the knowledge item')
      .toMatch(/<PageTitle className="truncate min-w-0">/)
  })

  it('a peek does NOT take one — the list is still the destination', () => {
    // Driven: `?item=` on knowledge, `?open=` on prompts and apps all keep the LIST's h1 and give the
    // panel none. `PageTitle`'s doc already forbids an h1 on a docked panel; this pins the surfaces that
    // could most easily drift, because their peek and their full page look the same in a diff.
    const knowledge = read('pages/knowledge/KnowledgeListPage.tsx')
    expect(knowledge).toMatch(/<PageTitle[\s>]/)
    expect((knowledge.match(/<PageTitle[\s>]/g) ?? []).length, 'one per destination, not one per panel').toBe(1)
    const chat = read('pages/ChatPage.tsx')
    expect(/left=\{<PageTitle[^>]*>Chat history/.test(chat), 'the chat history drawer stays a span').toBe(false)
  })

  it('none of the four kept the bare span it replaced', () => {
    for (const rel of ['pages/workflows/WorkflowRunDetail.tsx', 'pages/workflows/WorkflowDefDetail.tsx',
      'pages/apps/AppFrame.tsx', 'pages/knowledge/KnowledgeDetailPage.tsx']) {
      expect(read(rel), `${rel} still hand-rolls a title`).not.toMatch(/<span data-type="title-l"/)
    }
    // ProjectsSection keeps ONE such span deliberately: the rename editor's input is not a heading.
    const projects = read('pages/projects/ProjectsSection.tsx')
    expect((projects.match(/<span data-type="title-l"/g) ?? []).length, 'only the peek-panel header').toBeLessThanOrEqual(1)
  })
})

describe('no destination skips a heading level', () => {
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  // ── Cycle 163: the LAST skip in the app, and the sweep that proves it was the last ──────────────
  //
  // Every destination has an h1 now (cycles 150 and 162), so the next question is whether what follows
  // it is an h2. Swept 20 surfaces, reading the visible heading sequence on each:
  //
  //   #/tasks/new   h1 → **h3** ×5 ("Basics", "Classification", …)   🔴 the only skip
  //   everything else   a lone h1, or a clean 1→2 sequence (dashboard 12222222, settings 12222, …)
  //
  // `TaskForm`'s local `Section` rendered the h3, and cycle 150 deferred it because that component has
  // TWO hosts. Measured, both want h2: on `#/tasks/new` the page h1 is "New task"; in the task detail
  // panel on `#/tasks` the page h1 is "Tasks" and the panel carries no heading of its own (a docked
  // panel is not the page — `PageTitle`'s own rule), so the sections sit one level under the page either
  // way. After: **0 skips across all 20 surfaces**, and 6/6 captures pixel-identical (Tailwind's
  // preflight resets heading margins, so the tag swap moves nothing).

  it("the task form's section headers are h2, directly under the page h1", () => {
    const src = read('pages/tasks/TaskForm.tsx')
    expect(src).toMatch(/<h2 className="text-on-surface text-\[0\.8125rem\]" style=\{fvs\(550\)\}>\{title\}<\/h2>/)
    expect(src, 'no h3 section header left to skip a level').not.toMatch(/<h3/)
  })

  it('nothing in pages/tasks renders an h3 at all', () => {
    // The surface this cycle measured, held closed. Other areas still have h3s (workflows' drawers,
    // settings' panels, DiscoverPage) — they sit under an h2 or in a panel, and the sweep found no skip
    // in any of them, so they are deliberately untouched rather than swept on principle.
    const { readdirSync, statSync } = require('node:fs') as typeof import('node:fs')
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const offenders = walk(join(SRC, 'pages/tasks')).filter((abs) => /<h3[\s>]/.test(readFileSync(abs, 'utf8')))
      .map((abs) => abs.slice(SRC.length + 1))
    expect(offenders, `these would skip h1 → h3:\n${offenders.join('\n')}`).toEqual([])
  })
})

describe('the dashboard does not skip a heading level', () => {
  const files = ['pages/dashboard/DashboardPage.tsx', 'pages/dashboard/PinnedTiles.tsx']

  it.each(files)('%s renders its section header as h2', (rel) => {
    const src = readFileSync(join(SRC, rel), 'utf8')
    expect(src, 'section header must be an h2, directly under the page h1').toMatch(/<h2 data-type="label-l"/)
    expect(src, 'no h3 section header left to skip a level').not.toMatch(/<h3 data-type="label-l"/)
  })

  it('finds the shared Section component (not vacuously green)', () => {
    // If `Section` is ever renamed or inlined, this assertion is what notices before the
    // heading level silently drifts back.
    const src = readFileSync(join(SRC, 'pages/dashboard/DashboardPage.tsx'), 'utf8')
    expect(src).toMatch(/function Section\(/)
  })
})

// ── 2026-08-19: two more skips, and the reason this file's checks did not see them ────────────────
//
// `#/settings/providers` reported `h1 → h3 — "Agent providers"` at BOTH themes and at phone width, and
// `#/settings/voice` renders `H1 > H2 > H2 > H2 > H2 > H4` in the live DOM. Both are the defect this
// file already exists to prevent, in a third area.
//
// 🔑 THE TWO SKIP CHECKS ABOVE ARE SCOPED TO THE AREAS THAT WERE FIXED — `pages/tasks` and the
// dashboard's shared `Section`. Neither looks anywhere else, so `pages/settings` was never in scope.
// A rail scoped to the area of the last fix cannot find the next instance; the rule is what should be
// scoped, not the folder. The check below derives its own population instead.
//
// 🪤 AND `ux-audit` REPORTED ONLY ONE OF THE TWO. It caught providers (whose panel reads a
// `persist: true` key, so its headings paint immediately) and missed voice's h4 entirely — measured
// four consecutive runs, `headings: []` each time, while the h4 sits in the DOM at 1196×20 with no
// hidden ancestor. Its settle waits for the panel's own skeleton to clear, and voice's "Learned
// corrections" block belongs to a NESTED, separately-fetched section that arrives after that. So a
// heading inside a late sub-fetch is invisible to the audit — which is precisely why this check reads
// the SOURCE and cannot race anything.

describe('a panel that owns its h1 does not skip a level', () => {
  const PAGES = join(process.cwd(), 'src/pages')
  const walkPages = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walkPages(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })
  const strip = (s: string) => s
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

  /** Only files that render their OWN h1 — via `PanelHeader` or `PageTitle`. A child component whose
   *  h1 lives in its parent route (settings' own hub, `TaskForm`, `PinnedTiles`, …) cannot be judged
   *  from one file, and guessing would fill this rail with false positives: 15 of 25 heading-bearing
   *  files look wrong that way and almost all are fine.
   *
   *  ⚠️ UNVERIFIED, deliberately out of scope: `code/CodeCockpitPage`, `workflows/IntrospectPanel`
   *  (×6), `workflows/NodeInspectorDrawer`, `workflows/OutboxPanel` (×2) and
   *  `workflows/WorkspacePanel` each render an `h3` with no `h2` in the file. Those are panels and
   *  drawers the default surface does not render, so neither the audit nor a source scan can settle
   *  them — they need a drive-open pass, not an assertion. */
  function ownsItsH1() {
    return walkPages(PAGES)
      .map((abs) => ({ rel: abs.slice(PAGES.length + 1), src: strip(readFileSync(abs, 'utf8')) }))
      .filter((f) => /<h[1-6]\b/.test(f.src) && /<PanelHeader\b|<PageTitle\b/.test(f.src))
  }

  /** The primitives the rule leans on. If either changes level, the rule's arithmetic is wrong and
   *  these two assertions fail before the sweep can report a false verdict. */
  it('the premises hold: PanelHeader is an h1 and Section is an h2', () => {
    const ui = readFileSync(join(process.cwd(), 'src/pages/settings/settingsUI.tsx'), 'utf8')
    expect(ui, 'PanelHeader must render the panel title as h1').toMatch(/<h1 className="text-on-surface"/)
    expect(ui, 'Section must render its heading as h2').toMatch(/<h2 className=\{`mb-s text-on-surface/)
  })

  it('finds the population (not vacuously green)', () => {
    const files = ownsItsH1().map((f) => f.rel)
    expect(files.length, 'the scan must resolve the self-titled panels').toBeGreaterThanOrEqual(3)
    // 🪤 `settings/ProvidersPanel` is deliberately NOT here any more: fixing its level exposed that it
    // hand-rolled a section title at all, so it now renders `Section` and owns no heading tag — which
    // takes it out of a population defined by "files with their own <hN>". The panel that still owns
    // one, and the reason this check exists, is VoicePanel.
    expect(files).toContain('settings/VoicePanel.tsx')
  })

  it('every own heading has the level above it available', () => {
    const bad: string[] = []
    for (const { rel, src } of ownsItsH1()) {
      // What the file's own primitives put on the page before its hand-rolled headings.
      const available = new Set<number>()
      if (/<PanelHeader\b|<PageTitle\b/.test(src)) available.add(1)
      if (/<Section\b/.test(src)) available.add(2)
      for (const m of src.matchAll(/<h([1-6])\b/g)) {
        const level = Number(m[1])
        if (level > 1 && !available.has(level - 1)) bad.push(`${rel}: h${level} with no h${level - 1} above it`)
        available.add(level)
      }
    }
    expect(bad, `these skip a heading level:\n${bad.join('\n')}`).toEqual([])
  })

  it('the two fixed panels keep what replaced their skipped headings', () => {
    // Pinned by name as well as by the sweep. VoicePanel's fix is a TAG whose size comes from its
    // class, so an edit could "restore" the h4 without moving a pixel and nothing visual would object.
    // 🪤 Asserted by the PROPS present, not by their order: the first version of this pinned
    // `<Section title={label} hint={hint} icon={Icon}` and broke the moment the call was rewritten to
    // put the count inside the title. Attribute order is incidental; what matters is which props the
    // panel hands the primitive.
    const providers = readFileSync(join(PAGES, 'settings/ProvidersPanel.tsx'), 'utf8')
    const sectionCall = providers.slice(providers.indexOf('<Section'), providers.indexOf('<Section') + 400)
    expect(sectionCall, 'the entity group renders the shared Section').toContain('<Section')
    for (const prop of ['icon={Icon}', 'hint={hint}', '{label}']) {
      expect(sectionCall, `it must still hand Section its ${prop}`).toContain(prop)
    }
    expect(sectionCall, 'with the muted tone — coral on nine decorative glyphs is a system violation')
      .toContain('iconTone="muted"')
    expect(/<h[1-6]\b/.test(providers), 'and writes no heading tag of its own').toBe(false)
    const voice = readFileSync(join(PAGES, 'settings/VoicePanel.tsx'), 'utf8')
    expect(voice, 'Learned corrections nests under its Section h2').toMatch(/<h3 [^>]*>Learned corrections<\/h3>/)
  })
})
