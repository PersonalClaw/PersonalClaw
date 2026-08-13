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
      'pages/apps/AppFrame.tsx']) {
      expect(read(rel), `${rel} still hand-rolls a title`).not.toMatch(/<span data-type="title-l"/)
    }
    // ProjectsSection keeps ONE such span deliberately: the rename editor's input is not a heading.
    const projects = read('pages/projects/ProjectsSection.tsx')
    expect((projects.match(/<span data-type="title-l"/g) ?? []).length, 'only the peek-panel header').toBeLessThanOrEqual(1)
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
