import { describe, it, expect, vi, beforeEach } from 'vitest'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Plus, Brain } from 'lucide-react'
import { EmptyState } from '../ui/ListScaffold'
import { workflowPresets } from './workflows/workflowPresets'

// ── ONE empty-state primitive, rolled out to the seven surfaces (OU-6) ──────────────────
//
// 🔑 THE DECISION THIS FILE PINS. OU-6 is written as "web/src/ui/EmptyState.tsx exists", which
// reads as a greenfield primitive. It is not one: `EmptyState` has existed since long before
// this atom, exported from the LIST KIT (`ui/ListScaffold.tsx`) beside its two siblings
// `LoadError` and `ListSkeleton`, and it is already the answer at ~30 call sites. Creating a
// second component at `ui/EmptyState.tsx` would be a dual path — two components answering one
// condition — which this repo forbids, and it would contradict an existing rail that pins the
// co-location ON PURPOSE:
//
//   loadErrorState.test.tsx › "the primitive is exported from the list kit, beside EmptyState"
//     "Co-located on purpose: the two are alternative answers to the same condition, and a
//      surface reaching for one should see the other."
//
// So the atom's FILENAME clause is satisfied by not honouring it, and its PRODUCT clause —
// every one of the seven surfaces explains itself and offers one working action — is what this
// file holds. Recorded as a DEVIATION in the plan's execution log.
//
// The two other empty-shaped components are NOT dual paths; each answers a different condition
// and says so in its own doc:
//   · `PresetEmptyState` (ui/PresetEmptyState.tsx) — the preset-first ON-RAMP for a surface
//     whose create flow front-loads its whole ontology. Offers finished examples that SEED the
//     existing form. Its doc names the distinction: "Distinct from EmptyState (ListScaffold),
//     which states a fact and offers one CTA." Triggers uses it, which is why Triggers is
//     allowed to satisfy the rollout through that primitive instead.
//   · `SlotEmptyState` (pages/dashboard/widgets/kit.tsx) — an inline one-line dashed strip
//     sized for a DASHBOARD WIDGET SLOT, not a page or panel body.
//
// ── AND: the honesty precondition ──────────────────────────────────────────────────────
//
// An empty state that renders when the data merely FAILED TO LOAD is not an empty state, it is
// a confident wrong answer. Auditing the seven for this atom found `#/loops` doing exactly
// that (`.catch(() => [] as GoalLoop[])` in its fetcher → "No loops yet — Start a loop" on a
// 500). That fix joined the existing family rail rather than growing a second one; see
// `ui/loadErrorState.test.tsx`'s ADOPTERS entry for `pages/loops/LoopsListPage.tsx`.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

/** Every `action={…}` JSX attribute in a file, as the expression BETWEEN its braces —
 *  brace-matched from the opening `{` to its partner.
 *
 *  ⚠️ Written this way after the rail rejected a correct surface. The first version matched
 *  `action={{ … onClick`, i.e. only the object-literal shape, and failed `#/skills` — whose
 *  `action={!q ? { label: 'Browse skills', onClick: onBrowse, … } : undefined}` is the
 *  CONDITIONAL shape, and is the better one: it encodes "no CTA while filtered" in the prop
 *  itself. The rail had encoded an accident of whichever call site was written first. Matching
 *  the whole expression admits both shapes and cannot be padded by a comment or a long label. */
function actionExprs(src: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(/\baction=\{/g)) {
    const start = (m.index ?? 0) + m[0].length
    let i = start
    let depth = 1
    while (i < src.length && depth > 0) {
      const c = src[i]
      if (c === '{') depth++
      else if (c === '}') depth--
      i++
    }
    out.push(src.slice(start, i - 1))
  }
  return out
}

/** The seven surfaces OU-6 names, each with the file that owns its empty branch.
 *
 *  Two of the seven are not what the plan's prose implies, and the names are kept honest here
 *  rather than quietly re-scoped:
 *    · "Loops" has no nav tile — loops launch from within Projects — but `#/loops` is routable
 *      and `LoopsListPage` is the list a user lands on.
 *    · "Memory" is not a page at all. It is `#/settings/memory`, a multi-tab PANEL, so its
 *      empty branches live in `settings/MemoryPanel.tsx`. `EmptyState`'s own doc covers this:
 *      "The uniform empty state for a list/PANEL". */
const SURFACES: { name: string; file: string; primitive: 'EmptyState' | 'PresetEmptyState' }[] = [
  { name: 'Loops', file: 'pages/loops/LoopsListPage.tsx', primitive: 'EmptyState' },
  { name: 'Workflows', file: 'pages/workflows/WorkflowsListPage.tsx', primitive: 'EmptyState' },
  { name: 'Knowledge', file: 'pages/knowledge/KnowledgeListPage.tsx', primitive: 'EmptyState' },
  { name: 'Memory', file: 'pages/settings/MemoryPanel.tsx', primitive: 'EmptyState' },
  { name: 'Skills', file: 'pages/skills/SkillsPage.tsx', primitive: 'EmptyState' },
  { name: 'Tasks', file: 'pages/tasks/TasksListPage.tsx', primitive: 'EmptyState' },
  { name: 'Triggers', file: 'pages/triggers/TriggersListPage.tsx', primitive: 'PresetEmptyState' },
]

describe('exactly one general empty-state primitive', () => {
  it('does NOT ship a second one at ui/EmptyState.tsx', () => {
    // The dual path this atom could most easily have created. If a future change genuinely wants
    // the primitive in its own file, that is a MOVE — delete it from the kit in the same commit
    // and update `loadErrorState.test.tsx`'s co-location rail — never an addition beside it.
    expect(
      existsSync(join(SRC, 'ui/EmptyState.tsx')),
      'EmptyState lives in the list kit (ui/ListScaffold.tsx); a second file beside it would be two components for one condition',
    ).toBe(false)
  })

  it('exports it from the list kit, with its siblings', () => {
    const kit = read('ui/ListScaffold.tsx')
    expect(kit).toMatch(/export function EmptyState\b/)
    // The siblings that make the empty/failed/loading distinction expressible at all.
    expect(kit).toMatch(/export function LoadError\b/)
    expect(kit).toMatch(/export function ListSkeleton\b/)
  })

  it('keeps the preset on-ramp a documented SIBLING, not a rival', () => {
    // PresetEmptyState may coexist only while it declares what makes it different. Strip that
    // sentence from its doc and the two become indistinguishable — which is the moment they are
    // a dual path and one of them should be deleted.
    const doc = read('ui/PresetEmptyState.doc.ts')
    expect(doc, 'the on-ramp must state how it differs from EmptyState').toMatch(
      /Distinct from EmptyState/,
    )
  })

  it('documents the primitive where uiDocs.drift looks', () => {
    // `EmptyState` is documented in the KIT's doc file because that is where the component is.
    // A rename/extraction that forgot this would ship an undocumented primitive.
    const doc = read('ui/ListScaffold.doc.ts')
    expect(doc).toMatch(/name: 'EmptyState'/)
  })
})

describe('the seven OU-6 surfaces route their empty case through the primitive', () => {
  for (const { name, file, primitive } of SURFACES) {
    it(`${name} (${file}) renders <${primitive}>`, () => {
      const src = read(file)
      expect(src, `${name} must import the shared primitive from the kit`).toMatch(
        primitive === 'EmptyState'
          ? /import \{[^}]*\bEmptyState\b[^}]*\} from '[^']*ListScaffold'/
          : /import \{[^}]*\bPresetEmptyState\b[^}]*\} from '[^']*PresetEmptyState'/,
      )
      expect(src, `${name} must actually render it`).toMatch(new RegExp(`<${primitive}\\b`))
    })

    it(`${name} offers a working action on the genuinely-empty case`, () => {
      const src = read(file)
      // "One seeded working action each" is the atom's product clause. For six surfaces that is
      // an `action={{ … onClick … }}` on an EmptyState; for Triggers it is the preset grid,
      // whose cards ARE the actions (each seeds the create flow with a prefill).
      if (primitive === 'PresetEmptyState') {
        expect(src, 'the preset grid must be fed a catalog and hand picks back').toMatch(/presets=\{/)
        expect(src).toMatch(/onPick=\{/)
      } else {
        const wired = actionExprs(src).filter((e) => /onClick/.test(e))
        expect(
          wired.length,
          `the empty state on ${name} must offer a next step, not just state a fact`,
        ).toBeGreaterThan(0)
      }
    })
  }

  it('scans the real files (not vacuously green)', () => {
    // Every assertion above is a regex over a file read by relative path. A typo'd path, a moved
    // page, or a renamed primitive would make `read` throw — but a SHRUNKEN list would silently
    // assert less while still reporting green, which is the failure this floor catches.
    expect(SURFACES).toHaveLength(7)
    for (const { file } of SURFACES) {
      expect(existsSync(join(SRC, file)), `${file} must exist to be scanned`).toBe(true)
      expect(read(file).length, `${file} must be non-trivial`).toBeGreaterThan(500)
    }
  })
})

// ── PEP-2: the CROSS-SURFACE sweep ──────────────────────────────────────────────────────
//
// OU-6 (above) rolled ONE primitive out to seven surfaces. PEP-2 asks a different question of
// every list surface in the app: does a user who lands on it EMPTY find a way in?
//
// The census below is the atom's deliverable, and it is a census rather than a spot-check on
// purpose: "I swept everything" with no per-surface verdict is not evidence, and the failure mode
// of a sweep is a surface nobody classified. It was built by extracting every `<EmptyState>` and
// `<PresetEmptyState>` element from `src/pages/**` — 57 sites across 30 files — and reducing each
// file to the branch a user meets when the collection is GENUINELY empty (not filtered).
//
// Four verdicts, and only the first is a defect:
//
//   'on-ramp'    the genuinely-empty branch carries an action that reaches an existing create
//                flow. Most surfaces already did; three did not and were fixed by this atom.
//   'produced'   nothing for the user to create — the collection fills itself (agent proposals,
//                digests, extracted entities) or filling it IS the good news (no contradictions,
//                nothing to review). Manufacturing a CTA here would invent work.
//   'derived'    the rows are a projection of another collection the user does own, so the
//                on-ramp belongs to that collection's surface, not this one.
//   'degenerate' only reachable on an install missing its own bundled data. Kept honest (the
//                fact plus whatever path exists) but not worth a preset grid.
//
// 🚧 THE TRIGGERS FENCE, NARROWED 2026-08-28 — its original reason has EXPIRED. It read:
// "`pages/triggers/*` and the Automations surface were being changed concurrently by TSE-4, so this
// atom did not touch them." TSE-4 is ✅ done (`docs/roadmap/atomic/TSE.md`) and no open PR touches
// `pages/triggers/*`, so concurrency is no longer a reason to leave two files unclassified — and an
// `offLimits` entry whose stated cause is gone is indistinguishable from an unexamined surface.
//
// Of the two findings it recorded, ONE is now classified and one still cannot be:
//
//   (b) `WeekGridView`'s "No fires this week" has no on-ramp → **classified 'derived' below, which
//       means it is NOT a defect.** Only the 'on-ramp' verdict is. The grid plots projected fires of
//       triggers the user owns on the sibling List view, one `Segmented` away — the same shape as
//       `Tasks › Graph`. The finding was recorded before the taxonomy could be applied to it,
//       precisely because the file was fenced.
//   (a) A fresh home is NOT trigger-empty — `reconcile_digest_cron` registers
//       `system:notification-digest` at every boot, so a newcomer's first visit is machine-named
//       system rows and NO empty state, since the preset grid is gated on `counts.all === 0`.
//       `TriggersListPage.tsx` therefore stays fenced, but for a DIFFERENT reason than before: its
//       code does carry an on-ramp (`PresetEmptyState` + `TRIGGER_PRESETS`), so 'on-ramp' would pass
//       this file's own assertion while being unreachable in practice. Classifying it either way
//       would assert something false, and whether those rows are hidden, counted separately, or
//       renamed is an owner scope call (tracked as TC-8). **Do not classify it to close the hole.**
const PEP2_CENSUS: {
  surface: string
  file: string
  verdict: 'on-ramp' | 'produced' | 'derived' | 'degenerate'
  why: string
}[] = [
  // ── fixed by PEP-2 ──
  { surface: 'Workflows › Runs', file: 'pages/workflows/WorkflowsListPage.tsx', verdict: 'on-ramp',
    why: 'Runs is the DEFAULT tab, so this is the newcomer\'s first view of Workflows. Its one CTA went to the definitions LIST — twenty-odd machine names. Now a PresetEmptyState of bundled-template cards that seed the existing start() flow, with browse kept as the footer.' },
  { surface: 'Knowledge › Intents', file: 'pages/knowledge/KnowledgeListPage.tsx', verdict: 'on-ramp',
    why: 'Named the "New intent" control in prose and left the user to find it in the top bar. Now carries it, sharing blankIntent() with the header so there is one create seed — and its loader stopped swallowing, because a CTA over a failed read is the worse bug.' },
  { surface: 'Artifacts', file: 'pages/artifacts/ArtifactGrid.tsx', verdict: 'on-ramp',
    why: 'Hint named the Files page ("save a file as an artifact") with no way to get there. Now a Browse-files action into that existing flow; the prop is required so a call site cannot ship the fact without the way in.' },
  // ── already had one ──
  { surface: 'Tasks', file: 'pages/tasks/TasksListPage.tsx', verdict: 'on-ramp',
    why: 'New task → onCreate. PEP-2\'s scope asked for TEMPLATE cards here; there is no task-template catalog in the backend to source them from (src/personalclaw/tasks/ ships models+handlers, no templates), and authoring card copy would be the drift the scope forbids. Nothing changed.' },
  { surface: 'Loops', file: 'pages/loops/LoopsListPage.tsx', verdict: 'on-ramp', why: '"Start a loop" — the same handler the header CTA uses.' },
  { surface: 'Knowledge › Library', file: 'pages/knowledge/KnowledgeListPage.tsx', verdict: 'on-ramp', why: '"Add knowledge" — onCreate, the shared create route.' },
  { surface: 'Knowledge › Decisions', file: 'pages/knowledge/DecisionJournal.tsx', verdict: 'on-ramp',
    why: '"Open chat" — the ONLY surface that can create a decision. Logging one also mints its one-shot review trigger, so handlers/knowledge.py deliberately refuses to create a `decision` from the library create picker: an item authored there would be a decision that never comes back. Naming chat in prose and leaving the user to find it is the exact defect this census flagged on Knowledge › Intents, so `onOpenChat` is a REQUIRED prop threaded from KnowledgeSection (the ArtifactGrid rule — a call site cannot ship the fact without the way in).' },
  { surface: 'Memory', file: 'pages/settings/MemoryPanel.tsx', verdict: 'on-ramp', why: '"Add a fact" — opens the memory editor in place.' },
  { surface: 'Skills', file: 'pages/skills/SkillsPage.tsx', verdict: 'on-ramp', why: 'Browse skills — conditional on `!q`, the shape this file\'s actionExprs() comment defends.' },
  { surface: 'Agents', file: 'pages/agents/AgentsListPage.tsx', verdict: 'on-ramp', why: '"New agent" — onCreate, the same flow as the header.' },
  { surface: 'Projects', file: 'pages/projects/ProjectsSection.tsx', verdict: 'on-ramp', why: '"New project" — onCreate, the same flow as the header.' },
  { surface: 'Prompts', file: 'pages/prompts/PromptsListPage.tsx', verdict: 'on-ramp', why: '"New prompt" — onCreate, the same flow as the header.' },
  { surface: 'Chat', file: 'pages/ChatPage.tsx', verdict: 'on-ramp', why: '"New chat" — the same route the composer opens.' },
  { surface: 'Code', file: 'pages/code/CodeSection.tsx', verdict: 'on-ramp', why: '"New code project" — the existing clone/open flow.' },
  { surface: 'Apps', file: 'pages/apps/AppsSection.tsx', verdict: 'on-ramp', why: '"Browse the Store" — switches to the Store tab, the install flow.' },
  { surface: 'Terminal', file: 'pages/terminal/TerminalPage.tsx', verdict: 'on-ramp', why: '"New session" — spawns a shell, the only way one exists.' },
  { surface: 'Watched sources', file: 'pages/knowledge/SourcesPage.tsx', verdict: 'on-ramp', why: '"Add a source" — the existing SourceCreatePage flow.' },
  { surface: 'Scheduled reports', file: 'pages/knowledge/ReportsPage.tsx', verdict: 'on-ramp', why: '"New report" — the inline create form on the same page.' },
  { surface: 'Knowledge › Graph', file: 'pages/knowledge/KnowledgeGraph.tsx', verdict: 'on-ramp', why: 'Extraction is a per-item action, and the empty state offers it.' },
  { surface: 'Discover', file: 'pages/discover/DiscoverPage.tsx', verdict: 'on-ramp', why: 'Open Settings — the off case is a SETTING, and the action reaches it.' },
  { surface: 'Devices', file: 'pages/settings/DevicesPanel.tsx', verdict: 'on-ramp',
    why: '"Pair your first device" — the same startPairing() the section above calls, so there is one pairing flow with two entrances. The label deliberately differs from that section\'s "Pair a device" button: identical accessible names on one screen make the action ambiguous to name-based navigation. A paired device is user-created, so "produced" would be dishonest here.' },
  // ── nothing for the user to create ──
  { surface: 'Inbox', file: 'pages/inbox/InboxPage.tsx', verdict: 'produced',
    why: 'Inbox zero is the good news. Items arrive from connected providers; the connect flow is Settings › Inbox, not a create button on an empty queue.' },
  { surface: 'Notifications', file: 'pages/notifications/NotificationsPage.tsx', verdict: 'produced', why: 'Emitted by the system. Zero is success.' },
  { surface: 'Companion', file: 'pages/companion/CompanionPage.tsx', verdict: 'produced', why: '"Nothing waiting on you" is success.' },
  { surface: 'Learning', file: 'pages/learning/LearningPage.tsx', verdict: 'produced', why: '"Nothing to review" is success; the queue fills from captured signals.' },
  { surface: 'Skill proposals', file: 'pages/skills/SkillProposals.tsx', verdict: 'produced', why: 'The agent proposes these. A user cannot author a proposal to itself.' },
  { surface: 'Inbox › Proposals', file: 'pages/inbox/ProposalsLens.tsx', verdict: 'produced', why: 'Same: agent-authored.' },
  { surface: 'Knowledge › Conflicts', file: 'pages/knowledge/ConflictPanel.tsx', verdict: 'produced', why: '"No contradictions recorded" is the outcome a user wants.' },
  { surface: 'Knowledge › Tags', file: 'pages/knowledge/TagManager.tsx', verdict: 'produced',
    why: 'A tag comes into existence by tagging a saved item; there is no create-tag flow to link to, and the hint teaches the mechanism. Only reachable when the library is NON-empty — the shared "Knowledge base is empty" state preempts every view.' },
  { surface: 'Memory › Digests', file: 'pages/settings/MemoryPanel.tsx', verdict: 'produced', why: 'Digests are generated on a cadence.' },
  // ── a projection of a collection owned elsewhere ──
  { surface: 'Tasks › Graph', file: 'pages/tasks/TaskGraph.tsx', verdict: 'derived', why: 'A view of tasks. The create flow lives on the list, one segmented control away.' },
  { surface: 'Companion › sections', file: 'pages/companion/CompanionSections.tsx', verdict: 'derived',
    why: 'Four projections (running loops, open tasks, pending inbox, recent notifications) of collections owned by #/loops, #/tasks, #/inbox and #/notifications. A phone triages what already exists; the create flows belong to those surfaces, and the page footer links out to them rather than growing four CTAs that would each be a second entrance to someone else\'s flow.' },
  { surface: 'Files', file: 'pages/files/FilesSection.tsx', verdict: 'derived', why: '"No file open" is a selection state, not an empty collection.' },
  { surface: 'Automations › Week', file: 'pages/triggers/WeekGridView.tsx', verdict: 'derived',
    why: 'A projection of the schedules owned by the sibling List view, one `Segmented` control away — the same shape as Tasks › Graph. Its hint already teaches why the grid can be empty while triggers exist (only enabled INTERVAL schedules are plotted; a cron expression is not projected yet; a disabled one has no fires), so a CTA here would offer to create a trigger in the one place that cannot show whether the new one will appear.' },
  // ── only reachable on a broken install ──
  { surface: 'Tools', file: 'pages/tools/ToolsPage.tsx', verdict: 'degenerate',
    why: 'Built-in action tools always exist, so a successful index read cannot be empty — and the failed read already branches to LoadError (the swallow was removed earlier). When importable MCP servers exist, ImportSuggestions is the on-ramp.' },
  { surface: 'Knowledge › Add source', file: 'pages/knowledge/SourceCreatePage.tsx', verdict: 'degenerate', why: '"No source kinds are available" means the backend registered no providers.' },
  // ── EI-10 ──
  { surface: 'Settings › Secrets', file: 'pages/settings/SecretsPanel.tsx', verdict: 'on-ramp',
    why: '"Add your first secret" focuses the add form\'s name field. This collection\'s create surface IS that form, already on the page, so the on-ramp is a focus rather than a navigation — there is nowhere to navigate to. The hint sentence is the SERVER\'s (`empty_hint`), so the CLI and the dashboard cannot drift on what an empty vault means.' },
]

describe('PEP-2 · every list surface\'s genuinely-empty branch is classified', () => {
  it('classifies every file that renders an empty state (no surface goes unswept)', () => {
    // THE VACUITY FLOOR, and the one that matters most here. A census is only evidence if it
    // covers the population, so this derives the population FROM THE TREE — every file under
    // src/pages that renders either primitive — and fails on any file the table does not name.
    // A new list surface therefore cannot ship without a verdict, and a shrunken table cannot
    // read green.
    const rendering = new Set<string>()
    const walk = (dir: string) => {
      for (const entry of readdirSync(join(SRC, dir), { withFileTypes: true })) {
        const rel = `${dir}/${entry.name}`
        if (entry.isDirectory()) walk(rel)
        else if (/\.tsx$/.test(entry.name) && !/\.test\.tsx$/.test(entry.name)) {
          if (/<(Preset)?EmptyState\b/.test(read(rel))) rendering.add(rel)
        }
      }
    }
    walk('pages')
    // The population is real, not an empty set that would make the diff below vacuous.
    expect(rendering.size, 'files rendering an empty state').toBeGreaterThanOrEqual(28)
    const classified = new Set(PEP2_CENSUS.map((r) => r.file))
    // 🚧 The Automations/triggers surfaces are deliberately out of this atom's fence (see the
    // header): they are named here so the diff is honest rather than silently short.
    // Narrowed 2026-08-28: `WeekGridView` is classified above. Only the file with an open owner
    // question (TC-8 — the preset grid is unreachable because a fresh home is never trigger-empty)
    // is still fenced, and the header says why.
    const offLimits = new Set(['pages/triggers/TriggersListPage.tsx'])
    const unswept = [...rendering].filter((f) => !classified.has(f) && !offLimits.has(f))
    expect(unswept, 'every empty-state file needs a PEP-2 verdict').toEqual([])
  })

  it('every "on-ramp" verdict is backed by a real action in the file', () => {
    for (const row of PEP2_CENSUS.filter((r) => r.verdict === 'on-ramp')) {
      const src = read(row.file)
      // Either an `action={… onClick …}` (the EmptyState shape, conditional or object-literal —
      // `actionExprs` admits both) or a preset grid whose cards ARE the actions.
      const wired = actionExprs(src).some((e) => /onClick/.test(e))
      const grid = /<PresetEmptyState\b/.test(src) && /presets=\{/.test(src) && /onPick=\{/.test(src)
      expect(wired || grid, `${row.surface} (${row.file}) claims an on-ramp but offers no action`).toBe(true)
    }
  })

  it('records a reason for every non-defect, and no verdict is a bare label', () => {
    // The atom's rule that a surface with nothing to create is NOT a defect only holds if the
    // reason is written down — otherwise "produced" is indistinguishable from "unexamined".
    for (const row of PEP2_CENSUS) {
      expect(row.why.length, `${row.surface} needs a stated reason`).toBeGreaterThan(15)
      expect(existsSync(join(SRC, row.file)), `${row.file} must exist`).toBe(true)
    }
    // Each verdict class is actually populated — a census where every row says 'on-ramp' would
    // pass the assertions above while having examined nothing.
    for (const v of ['on-ramp', 'produced', 'derived', 'degenerate'] as const) {
      expect(PEP2_CENSUS.filter((r) => r.verdict === v).length, `no ${v} rows`).toBeGreaterThan(0)
    }
  })
})

describe('PEP-2 · the three surfaces this atom changed', () => {
  it('Workflows sources its preset cards from the bundled templates, not from new copy', () => {
    // The scope's constraint: "preset source = the bundled workflow templates surfaced as cards,
    // no new copy that drifts from the templates". So the card's summary and description must be
    // READ OFF the definition. A catalog that hardcoded either would satisfy "shows cards" while
    // reintroducing the drift the constraint exists to prevent.
    const cat = read('pages/workflows/workflowPresets.ts')
    expect(cat, 'summary is the template name, from data').toMatch(/summary:\s*def\.name/)
    expect(cat, 'description is the template\'s own, from data').toMatch(/description:\s*def\.description/)
    // And the kind→template mapping is the SHARED alias table, not a second one. A duplicate
    // table is exactly the drift `KIND_TO_TEMPLATE`'s backend-parity test exists to catch.
    expect(cat).toMatch(/import \{[^}]*\btemplateForKind\b[^}]*\} from '\.\/containerKey'/)
    expect(cat, 'must not name a template literally').not.toMatch(/'(code|design|general)-project'|'deep-research'|'goal-pursuit-/)
  })

  it('Workflows offers no card for a template this install does not ship', () => {
    const defs = [{ name: 'code-project', description: 'Ship a code change.', source: 's', version: 1, tags: [], provider: 'p' }]
    const presets = workflowPresets(defs)
    // One of the five kinds resolves into `defs`; the other four must be dropped, not rendered
    // as dead cards whose failure only appears on click.
    expect(presets.map((p) => p.id)).toEqual(['code'])
    expect(presets[0].summary).toBe('code-project')
    expect(presets[0].description).toBe('Ship a code change.')
    expect(presets[0].prefill).toBe('code-project')
    // An install with no bundled templates yields NO presets — which is why the page keeps a
    // plain-EmptyState branch for that case instead of rendering an empty grid.
    expect(workflowPresets([])).toEqual([])
  })

  it('Workflows keeps a non-preset branch for the no-templates install', () => {
    const src = read('pages/workflows/WorkflowsListPage.tsx')
    expect(src, 'the preset grid must be gated on there BEING presets').toMatch(/presets\.length > 0/)
    expect(src, 'and the pre-PEP-2 empty state must survive as the fallback').toMatch(/title="No workflow runs yet"[\s\S]{0,400}?action=\{\{ label: 'Browse definitions'/)
  })

  it('Knowledge intents share ONE create seed between the header and the empty state', () => {
    const src = read('pages/knowledge/KnowledgeListPage.tsx')
    // Two call sites, one definition. A second inline `{ id: '', goal: '', … }` literal would be
    // two create paths one edit apart from diverging.
    expect(src).toMatch(/function blankIntent\(\): KnowledgeIntent/)
    // Anchored on `blankIntent())` — the seed being HANDED TO a setter — not on the bare name,
    // which also occurs in the declaration and in a comment. Exactly two such call sites: the
    // header control's `setSelectedIntent(…)` and the empty state's `onSelect(…)`. (A THIRD site,
    // the `?intent=__new__` deep-link restore, calls it bare and is counted by the literal check
    // below — it was a third inline copy of the blank shape until this atom found it.)
    expect((src.match(/blankIntent\(\)\)/g) ?? []).length, 'header + empty state').toBe(2)
    // And the THIRD consumer — asserted by what it IS, not by a count a comment could shift.
    expect(src, 'the ?intent=__new__ deep-link uses the same seed').toMatch(
      /intentTok === '__new__'\s*\n\s*\? blankIntent\(\)/,
    )
    // The blank shape is written down exactly ONCE — inside `blankIntent`. A second occurrence is
    // an inline literal at a call site, which is how the two paths drift.
    expect((src.match(/\{ id: '', goal: '',/g) ?? []).length, 'the blank shape has one definition').toBe(1)
  })

  it('Knowledge intents branch on the failed read BEFORE the empty state', () => {
    // THE INVERSE DEFECT. Adding a create CTA to an empty state that also renders on a failed
    // read makes the lie worse: "you have none, make your first" to a user who has some. This
    // loader used to `.catch(() => setIntents([]))`.
    const src = read('pages/knowledge/KnowledgeListPage.tsx')
    expect(src, 'the rejection must be captured').toMatch(/catch\(\(e\) => \{ setIntentsErr\(e\)/)
    expect(src, 'no swallow may remain on the intents read').not.toMatch(/knowledgeIntents\(\)[\s\S]{0,120}catch\(\(\) =>/)
    // REACHABILITY: the error branch is an early return, so the empty branch is unreachable while
    // it holds. Asserted as an ordering over the two sites rather than as a source-order regex —
    // the same widening `loadErrorState.test.tsx` had to make three times.
    const errAt = src.indexOf('if (intentsErr) return <LoadError what="intents"')
    const emptyAt = src.indexOf('title="No intents yet"')
    expect(errAt, 'the intents error branch must exist').toBeGreaterThan(-1)
    expect(emptyAt).toBeGreaterThan(-1)
    expect(errAt, 'the error branch must precede the empty one').toBeLessThan(emptyAt)
  })

  it('Artifacts cannot ship the fact without the way in', () => {
    const src = read('pages/artifacts/ArtifactGrid.tsx')
    // REQUIRED, not `onBrowseFiles?:` — an optional on-ramp is one a call site drops silently.
    expect(src).toMatch(/onBrowseFiles: \(\) => void/)
    expect(src).not.toMatch(/onBrowseFiles\?:/)
    expect(read('pages/artifacts/ArtifactsSection.tsx'), 'the only call site wires it to Files').toMatch(
      /onBrowseFiles=\{\(\) => navigate\('files'\)\}/,
    )
  })
})

describe('PEP-2 · the expert path is unchanged when the list is not empty', () => {
  // The clause that is easiest to claim and hardest to prove: a change gated on emptiness must be
  // INVISIBLE to a user with data. Both directions are asserted from the same render, because a
  // test that only checks the empty case cannot tell a gate from an unconditional render.
  const defs = [
    { name: 'code-project', description: 'Ship a code change.', source: 'bundled', version: 1, tags: [], provider: 'core' },
    { name: 'deep-research', description: 'Investigate a topic.', source: 'bundled', version: 1, tags: [], provider: 'core' },
  ]
  const run = {
    id: 'r1', workflow_name: 'code-project', status: 'running', started_at: 0, elapsed_seconds: 3,
  }

  async function renderWorkflows(runs: unknown[]) {
    vi.doMock('../lib/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        workflowDefs: () => Promise.resolve({ defs, total: defs.length }),
        workflowRuns: () => Promise.resolve({ runs, total: runs.length }),
        workflowSurfacing: () => Promise.reject(new Error('not needed')),
      },
    }))
    const { WorkflowsListPage } = await import('./workflows/WorkflowsListPage')
    render(<WorkflowsListPage sub="" navEpoch={0} query={{}} setQuery={() => {}} navigate={() => {}} />)
  }

  beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

  it('EMPTY: the preset cards are there and each names its bundled template', async () => {
    await renderWorkflows([])
    // MOUNTED-NESS FLOOR: assert the surface's own empty headline first. Without it a render that
    // failed to mount at all would satisfy every `queryBy…(…)` absence check below.
    expect(await screen.findByRole('heading', { name: 'No workflow runs yet' })).toBeInTheDocument()
    // The card's accessible name is `${title} — ${summary}`, i.e. the human label AND the machine
    // template name, which is the vocabulary this on-ramp exists to teach.
    expect(screen.getByRole('button', { name: /Work on code — code-project/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Research a topic — deep-research/ })).toBeInTheDocument()
    // The expert path is still one click away, under the grid.
    expect(screen.getByRole('button', { name: /Browse all definitions/ })).toBeInTheDocument()
  })

  it('NON-EMPTY: the run renders and not one preset card appears', async () => {
    await renderWorkflows([run])
    // MOUNTED-NESS FLOOR for the other direction: the row must actually be on screen, or "no
    // cards" is just an unmounted page. `ListRow`'s `label` is the accessible name of its overlay
    // button (the wrapper carries no role on purpose — nested-interactive), so the row is asked
    // for by NAME rather than by text, which is split across three elements.
    //
    // 🪤 IT IS ALSO THE SYNC POINT, and that is not decoration. Measured while falsifying this
    // test: with the emptiness gate mutated open AND this line deleted, every `queryBy…` below
    // returned null and the test reported GREEN — the assertions had simply run before the load
    // resolved. Deleting this `await` does not weaken the test, it empties it.
    expect(await screen.findByRole('button', { name: 'code-project — run r1' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No workflow runs yet' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Work on code/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Research a topic/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Browse all definitions/ })).toBeNull()
  })
})

describe('an empty state is a launchpad, not a dead end', () => {
  // Behaviour, not source: the properties the seven inherit BY using the primitive. If these
  // regress in the kit, all seven regress at once, which is the point of there being one.
  it('names itself with a heading, so the surface is not silently blank', () => {
    render(<EmptyState icon={Brain} title="No memories yet" hint="Add one, or let a chat record them." />)
    expect(screen.getByRole('heading', { name: 'No memories yet' })).toBeInTheDocument()
    expect(screen.getByText('Add one, or let a chat record them.')).toBeInTheDocument()
  })

  it('exposes the action as a real keyboard-reachable button', async () => {
    const onClick = vi.fn()
    render(<EmptyState icon={Brain} title="No memories yet" action={{ label: 'Add a fact', onClick, icon: Plus }} />)
    const btn = screen.getByRole('button', { name: /Add a fact/ })
    // Reachable by Tab and activatable from the keyboard — not a click-only div.
    await userEvent.tab()
    expect(btn).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('does not opt its action out of the app-wide focus ring', () => {
    // ⚠️ MECHANISM, not vibes. The first draft of this test asserted a `focus-visible:ring-*`
    // utility on the button and failed — correctly. `Button` carries no such utility, because
    // this app's keyboard ring is a GLOBAL rule in `design/tokens.css`:
    //     :focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px }
    // written precisely so controls do not each need one. jsdom applies no stylesheet, so the
    // painted ring is not observable here at all; what IS observable — and what actually
    // regresses — is a control opting OUT with `outline-none` and supplying no replacement,
    // which is the exact failure the global rule's own comment says it exists to prevent.
    const ring = read('design/tokens.css')
    expect(ring, 'the app-wide keyboard ring must exist').toMatch(
      /:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--color-primary\)/,
    )
    render(<EmptyState title="t" action={{ label: 'Go', onClick: () => {} }} />)
    const cls = (screen.getByRole('button', { name: 'Go' }).className || '').split(/\s+/)
    const optsOut = cls.some((c) => c === 'outline-none' || c === 'focus:outline-none')
    const replaces = cls.some((c) => c.startsWith('focus-visible:ring') || c.startsWith('focus-visible:outline'))
    expect(optsOut && !replaces, 'the CTA suppressed the global ring without replacing it').toBe(false)
  })

  it('is NOT a live region — "you have none" is a normal answer', () => {
    // The contrast with `LoadError`, which IS role=alert. An empty state that interrupted on
    // every render would announce absence as though it were bad news.
    const { container } = render(<EmptyState title="No loops yet" />)
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container.querySelector('[role="status"]')).toBeNull()
  })

  it('renders no button at all when there is no next step', () => {
    // The filtered-to-nothing case relies on this: `action` omitted must mean no CTA, so a
    // narrowed list cannot accidentally pitch a create flow.
    const { container } = render(<EmptyState title="No matching loops" hint="Try another filter." />)
    expect(container.querySelector('button')).toBeNull()
  })
})
