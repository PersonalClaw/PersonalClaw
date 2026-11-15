import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { FIXED_CLOCK } from './fixedClock'

// ── A surface's read, answered with a fixed body (`STUBBED_READS` in `surfaces.spec.ts`) ─────────

/** A read a surface's row answers with a fixed body, and why. Only for a read whose answer depends
 *  on what other specs did first in the shared home or on state outside the run, so the surface
 *  would hold one of several states on any run, or one the run's home can only answer empty. Each
 *  state the stub hides is held by a unit test instead, named where the stub is. A `json` answers
 *  without asking the gateway, so nothing the live read says reaches the page. A `patch` answers
 *  with the live body and only the moving fields pinned, so the rest of the read is still the
 *  gateway's. */
export type StubbedRead = { url: RegExp; why: string } & ({ json: unknown } | { patch: (live: any) => unknown })

/** The part of a Playwright `Route` a stubbed read uses, `R` being its live response. */
export type StubRoute<R extends { json(): Promise<unknown> }> = {
  fetch(): Promise<R>
  fulfill(answer: { json: unknown; response?: R }): Promise<void>
}

/** Answer one stubbed read on `route`, as the matrix's rows do. */
export async function answerStubbedRead<R extends { json(): Promise<unknown> }>(stub: StubbedRead, route: StubRoute<R>): Promise<void> {
  if ('json' in stub) return route.fulfill({ json: stub.json })
  const live = await route.fetch()
  return route.fulfill({ response: live, json: stub.patch(await live.json()) })
}

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), 'fixtures')

/** The Store's catalogue: a snapshot of what the run's gateway answered on 2026-10-23
 *  (`fixtures/app-catalog.json`). The live read lists the app registry as it stands upstream
 *  (`apps/catalog.py`'s git sources), so an edit to a listing in another repository changed the
 *  Store's tree and turned core's main red with no core change (#3905: the Menu Bar Companion's
 *  text, PersonalClaw/PersonalClawApps#503). Re-take it with the Store's tree when the Store's
 *  cards change, never to follow the registry. What a card says is held by
 *  `src/pages/apps/storeCardProse.test.tsx` and its siblings, and this pin by
 *  `src/pages/apps/theStoreInTheUiCheckIsASnapshot.test.ts`. */
export const STORE_CATALOGUE: StubbedRead = {
  url: /\/api\/apps\/catalog(\?.*)?$/,
  json: JSON.parse(readFileSync(join(FIXTURES, 'app-catalog.json'), 'utf8')),
  why: 'the Store lists the app registry as it stands upstream, which another repository edits',
}

// ── What the visual spec's shots answer with fixed values (`VISUAL_STUBS` in `visual.spec.ts`) ──

const DAY = /^\d{4}-\d{2}-\d{2}$/

/** `day` (`YYYY-MM-DD`) moved by `by` calendar days. */
function shiftDay(day: string, by: number): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d + by)).toISOString().slice(0, 10)
}

/** The capture week as the live read gives it, moved so its last day is `today`: every day it
 *  names (each bucket's, the silent and errored days, the first pass's) moves by the same count,
 *  and every count stays the gateway's. The gateway buckets the week by its own local date, so
 *  the strip named the seven days that ended on the run's day. */
export function weekEndingOn(live: { buckets: { day: string }[]; silent_days?: string[]; error_days?: string[]; first_pass_day?: string }, today: string) {
  const last = live.buckets.at(-1)?.day
  if (!last || !DAY.test(last)) return live
  const by = Math.round((Date.parse(`${today}T00:00:00Z`) - Date.parse(`${last}T00:00:00Z`)) / 86_400_000)
  const move = (day: string) => (DAY.test(day) ? shiftDay(day, by) : day)
  return {
    ...live,
    buckets: live.buckets.map((b) => ({ ...b, day: move(b.day) })),
    ...(live.silent_days ? { silent_days: live.silent_days.map(move) } : {}),
    ...(live.error_days ? { error_days: live.error_days.map(move) } : {}),
    ...(live.first_pass_day ? { first_pass_day: move(live.first_pass_day) } : {}),
  }
}

/** Learning's capture week, ending on `FIXED_CLOCK.learning`'s day. Its states are held by
 *  `src/pages/learning/weekNeverRan.test.tsx`. */
export const LEARNING_WEEK: StubbedRead = {
  url: /\/api\/learning\/staging\/week(\?.*)?$/,
  patch: (live) => weekEndingOn(live, FIXED_CLOCK.learning.slice(0, 10)),
  why: "the gateway buckets the week by its own date, so the strip named the seven days that ended on the run's",
}

/** Settings' audit tile: the chain intact over a fixed count. The live count is how many events
 *  this run's gateway had logged by the shot (153 on one run, 155 on the next). The six keys
 *  `api_security_audit_verify` emits; its wording is held by
 *  `src/pages/settings/auditVerifyScope.test.tsx`. */
export const AUDIT_VERIFIED: StubbedRead = {
  url: /\/api\/security\/audit\/verify(\?.*)?$/,
  json: { checked: 150, valid: 150, ok: true, tampered: 0, windowed: true, window: 5000 },
  why: "the count is how many events the run's gateway had logged by then",
}

/** Settings' System tile: one fixed cpu and memory reading, every other field the gateway's. They
 *  are the runner's own, read live (3.3 and 3.5 of 15.6 GB a run apart). The readings are held by
 *  `src/pages/settings/systemPanel.test.tsx`. */
export const SYSTEM_READING: StubbedRead = {
  url: /\/api\/system(\?.*)?$/,
  patch: (live) => ({ ...live, cpu_pct: 12, mem_used_gb: 3.2, mem_total_gb: 15.6, mem_free_gb: 12.4 }),
  why: "the cpu and memory are the runner's own, read live",
}

/** The roots Files names: the two folders the run's home has at boot. The gateway lists a root only
 *  where its folder exists (`dashboard/handlers/files.py`), and a sibling spec's turn or upload in
 *  the shared home makes Uploads and each project's context along the way. So the shot named two
 *  roots on one run and five on another (PR CI's e2e-pinned, run 42190053229), and at 390 px the
 *  header's root switch folds to one pill when it cannot hold them all. The folded switch is held
 *  by `src/ui/aFoldedSwitchSaysItsValue.test.tsx`, and this pin by `src/design/visualPins.test.ts`. */
export const BOOT_ROOTS = ['Workspace', 'Outbox']

export const FILE_ROOTS: StubbedRead = {
  url: /\/api\/file-list$/,
  patch: (live: { roots?: { label: string }[] }) => ({ ...live, roots: (live.roots ?? []).filter((r) => BOOT_ROOTS.includes(r.label)) }),
  why: "a root is listed only where its folder exists, and other specs make Uploads and a project's context in the shared home",
}

// ── The Projects list (`projects` in both specs) ─────────────────────────────────────────────────

/** The Projects list's reads, each answered whole: the projects, what waits on you and what is
 *  running. The run's home makes Personal and Repeatable at boot, stamped with that moment, and a
 *  quiet project's "Active …" reads it, so a live list drew the run's own boot time against
 *  `FIXED_CLOCK.projects`; and it has nothing waiting or running, so no card drew a pulse. Garden
 *  carries its folder, a brief and one of each live row: an approval, its call and purpose, and a
 *  code session at work. What the list does with each is held by
 *  `src/pages/projects/theProjectsPageIsOneList.test.tsx` and its siblings. */
export const PROJECTS_LIST: StubbedRead[] = [{
  url: /\/api\/projects(\?.*)?$/,
  json: {
    projects: [
      { id: 'p-0b1c2d3e', name: 'Personal', is_builtin: true, status: 'active', workspace_dir: '', brief: '', task_list_count: 0, made_for: null, folder_state: '', created_at: '2026-10-01T09:00:00+00:00', updated_at: '2026-11-01T09:00:00+00:00' },
      { id: 'p-0c1d2e3f', name: 'Repeatable', is_builtin: true, status: 'active', workspace_dir: '', brief: '', task_list_count: 0, made_for: null, folder_state: '', created_at: '2026-10-01T09:00:00+00:00', updated_at: '2026-11-01T09:00:00+00:00' },
      { id: 'p-0a1b2c3d', name: 'Garden', is_builtin: false, status: 'active', workspace_dir: '/srv/code/garden', brief: 'Plan and grow the spring beds, and keep the soil sensors working. The beds go in by April.', task_list_count: 2, made_for: null, folder_state: 'ok', created_at: '2026-10-01T09:00:00+00:00', updated_at: '2026-11-02T11:57:00+00:00' },
    ],
  },
  why: "the run's home stamps its built-in projects with its own boot time, and has no project with a folder",
}, {
  url: /\/api\/waiting(\?.*)?$/,
  json: {
    waiting: [{
      id: 'approval:e2e-garden-ap', kind: 'approval', work: 'loop:e2e0beds', session: '', consequence: 'blocks', risk: '',
      brief: { tool: 'bash', tool_input: 'npm run sensor:calibrate', tool_purpose: 'Calibrate the soil sensors after the rain' },
      asked_at: '2026-11-02T11:54:00+00:00', age_secs: 360, expires_at: null, current: true, interrupt: 'none',
      answer: { method: 'POST', path: '/api/approvals/e2e-garden-ap/{answer}', takes: ['allow', 'deny'], body: {} },
      route: '#/loops/e2e0beds', project_id: 'p-0a1b2c3d', loop_kind: 'goal',
    }],
    unreadable: [],
  },
  why: "the run's home has nothing waiting on you",
}, {
  url: /\/api\/running(\?.*)?$/,
  json: {
    running: [{
      work: 'loop:e2e0fenc', kind: 'loop', session: '', phase: 'tool', line: '18 of 24 passed', since: '2026-11-02T11:48:00+00:00',
      name: 'Fix the irrigation sensor tests', agent: '', started_at: '2026-11-02T11:30:00+00:00', route: '#/code/e2e0fenc',
      blocked: false, waiting: [], project_id: 'p-0a1b2c3d', loop_kind: 'code',
    }],
    finished: [],
  },
  why: "the run's home has nothing running",
}]

// ── What waits on you, one of each shape (`FIXED_WAITING`), and Needs you's reads ─────────────────

/** One waiting item as `GET /api/waiting` serves it, with the list's common fields. */
const fixedWaiting = <T extends Record<string, unknown>>(over: T) => ({
  work: '', session: '', risk: '', expires_at: null, current: true, interrupt: 'deliver', age_secs: 0, ...over,
})

/** What waits on you (`GET /api/waiting`), one of each shape a reader draws: a destructive approval,
 *  a workflow's confirmation gate, a trigger's parked action and a loop's question, asked minutes
 *  before the rows' fixed noon and waiting until answered. */
export const FIXED_WAITING = {
  waiting: [
    fixedWaiting({
      id: 'approval:e2e-ap-1', kind: 'approval', consequence: 'destructive', risk: 'destructive',
      asked_at: '2026-11-02T11:58:00+00:00', route: '',
      brief: { tool: 'bash', tool_input: 'rm -rf build', tool_purpose: 'Clear the build folder', source_label: 'automation “Nightly”', confirms: ['deletes'] },
      answer: { method: 'POST', path: '/api/approvals/e2e-ap-1/{answer}', takes: ['approve', 'reject'], body: {} },
    }),
    fixedWaiting({
      id: 'gate:r-e2e:conf-1:0', kind: 'gate', work: 'workflow:r-e2e', consequence: 'changes',
      asked_at: '2026-11-02T11:50:00+00:00', route: '#/workflows/runs/r-e2e',
      brief: { workflow: 'release', node_id: 'approve', ask: { kind: 'approval', prompt: 'Ship v1.2?', choices: [] } },
      answer: { method: 'POST', path: '/api/workflows/runs/r-e2e/confirm', takes: ['approve', 'reject'], body: { resume_token: 'tok-e2e' } },
    }),
    fixedWaiting({
      id: 'park:schedule:clock:e2e', kind: 'park', work: 'trigger:schedule:clock:e2e', consequence: 'changes',
      asked_at: '2026-11-02T11:55:00+00:00', route: '#/triggers?open=schedule%3Aclock%3Ae2e',
      brief: { trigger_name: 'Bank statements', question: 'Sign in to the bank’s site', card: {} },
      answer: { method: 'POST', path: '/api/triggers/schedule%3Aclock%3Ae2e/answer', takes: [true, false], body: { resume_token: 'tok-park' } },
    }),
    fixedWaiting({
      id: 'loop_question:l-e2e', kind: 'loop_question', work: 'loop:l-e2e', consequence: 'asks',
      asked_at: '2026-11-02T11:40:00+00:00', route: '#/loops/l-e2e',
      brief: { name: 'Sort the inbox', question: 'May I archive last year’s mail?' },
      answer: { method: 'POST', path: '/api/loops/l-e2e/nudge', takes: ['text'], body: {} },
    }),
  ],
  unreadable: [],
}

/** Needs you's three reads, fixed: the waiting list, one skill proposal and one Inbox proposal, so
 *  both its groups draw. The run's home has nothing waiting on you and nothing to review, so the page
 *  drew only its all-clear. What each card does is held by `src/pages/dashboard/needsYouIsOneList.test.tsx`. */
export const NEEDS_YOU_READS: StubbedRead[] = [{
  url: /\/api\/waiting(\?.*)?$/,
  json: FIXED_WAITING,
  why: "the run's home has nothing waiting on you",
}, {
  url: /\/api\/skills\/proposals(\?.*)?$/,
  json: { proposals: [{
    id: 'sp-e2e-1', slug: 'incident-writeup', description: 'Write up an incident from its timeline and its fixes.',
    created_at: '2026-11-02T10:30:00+00:00', kind: 'new',
  }], lastReview: null },
  why: "the run's home has no skill proposal",
}, {
  url: /\/api\/inbox\/open(\?.*)?$/,
  json: [{
    id: 'ib-e2e-1', item_kind: 'proposal', status: 'pending', message: 'Keep a weekly digest of what the agents did',
    context_summary: '', created_at: Date.parse('2026-11-02T10:45:00Z') / 1000, refs: {}, sender_name: 'Learning', channel_name: '',
  }],
  why: "the run's home has nothing open in the Inbox",
}]

// ── A project's page (`projects/p-0a1b2c3d` and its substates, in both specs) ────────────────────

/** Garden, as its page and its Tasks view read it: a folder that is there, in preview, a brief and
 *  instructions. The run's home has no project, so without it the page could only say the project
 *  no longer exists. */
export const GARDEN = {
  id: 'p-0a1b2c3d', name: 'Garden', is_builtin: false, status: 'active', workspace_dir: '/srv/code/garden',
  folder: { resolved: '/srv/code/garden', trust: 'preview' },
  brief: 'Plan and grow the spring beds, and keep the soil sensors working. The beds go in by April.',
  agent_instructions_template: 'Check the soil readings before suggesting a planting date.',
  task_list_count: 1, made_for: null, created_at: '2026-10-01T09:00:00+00:00', updated_at: '2026-11-01T09:00:00+00:00',
}

/** Garden's one task list, its own (no loop's phases). */
export const GARDEN_LISTS = [
  { id: 'tl-e2e-1', name: 'Garden', project_id: 'p-0a1b2c3d', loop_id: '', task_count: 3, created_at: '2026-10-01T09:00:00+00:00', updated_at: '2026-11-01T15:00:00+00:00' },
]

const gardenTask = (id: string, title: string, status: string, priority: string) => ({
  id, title, status, priority, project: 'Garden', task_list_id: 'tl-e2e-1', tags: [],
  created_at: '2026-10-30T09:00:00+00:00', updated_at: '2026-11-01T15:00:00+00:00',
})

/** Three of Garden's tasks, one of them done. */
export const GARDEN_TASKS = [
  gardenTask('t-e2e-1', 'Order the soil', 'open', 'high'),
  gardenTask('t-e2e-2', 'Plant the beans', 'in_progress', 'medium'),
  gardenTask('t-e2e-3', 'Fix the gate latch', 'done', 'low'),
]

/** What a new chat in Garden is told, as `GET /api/legibility/always-on` builds it. */
const GARDEN_TOLD = {
  items: [], project_id: 'p-0a1b2c3d', counts: { total: 0, always_skills: 0, project_instructions: 0 }, always_skill_mechanism: '',
  project_block: {
    text: 'You are working in the project Garden.\n- Project brief (the goal/scope/background of this project — treat as foundational context): Plan and grow the spring beds, and keep the soil sensors working. The beds go in by April.\n- Project instructions: Check the soil readings before suggesting a planting date.',
    parts: { brief: 1, overview: 0, decisions: 0, open_questions: 0, out_of_scope: 0 },
  },
}

/** Garden's own reads, which its Tasks view (`?tab=tasks`) asks too: the project, the list it is
 *  in, its board, its task lists and its tasks. The run's home has none of them. */
export const GARDEN_READS: StubbedRead[] = [{
  url: /\/api\/projects(\?.*)?$/,
  json: { projects: [GARDEN] },
  why: "the run's home has no project",
}, {
  url: /\/api\/projects\/p-0a1b2c3d(\?.*)?$/,
  json: GARDEN,
  why: "the run's home has no project",
}, {
  url: /\/api\/projects\/p-0a1b2c3d\/work(\?.*)?$/,
  json: { board: [], sections: [], completeness: 'complete', attention: 0, loadedAt: 0 },
  why: "the run's home has no project, so no work in one",
}, {
  url: /\/api\/task-lists(\?.*)?$/,
  json: { task_lists: GARDEN_LISTS },
  why: "the run's home has no project, so no task list of one",
}, {
  url: /\/api\/tasks(\?.*)?$/,
  json: { tasks: GARDEN_TASKS, total: GARDEN_TASKS.length, complete: true, owner: '' },
  why: "the run's home has no project, so no task of one",
}]

/** The page's reads at rest, each answered whole: Garden's own, what a new chat there is told,
 *  the instruction changes proposed for it (one, so About › Instructions says "1 change
 *  proposed"), its folder's check, what waits and runs in it (`PROJECTS_LIST`'s two live rows),
 *  and its one chat. What the page does with each is held by
 *  `src/pages/projects/aProjectsPageIsHomeForOneProject.test.tsx` and its siblings. */
export const PROJECT_PAGE: StubbedRead[] = [...GARDEN_READS, {
  url: /\/api\/legibility\/always-on(\?.*)?$/,
  json: GARDEN_TOLD,
  why: "the run's home has no project, so nothing a new chat in one is told",
}, {
  url: /\/api\/learning\/proposals(\?.*)?$/,
  json: {
    rows: [{
      id: 'lp-e2e-1', kind: 'project_instruction', project_id: 'p-0a1b2c3d', title: 'Water before noon in summer',
      summary: '', created_at: '2026-11-02T10:00:00+00:00', status: 'pending', risk_tier: 'review',
    }],
    total: 1, by_kind: { project_instruction: 1 }, by_tier: { review: 1 }, flagged: 0, unrenderable: [], bulk_acceptable: 0,
  },
  why: "the run's home has no project, so no change proposed for one",
}, {
  url: /\/api\/browse-dirs\?path=/,
  json: { path: '/srv/code/garden', parent: '/srv/code', dirs: [{ name: 'beds', path: '/srv/code/garden/beds' }, { name: 'sensors', path: '/srv/code/garden/sensors' }] },
  why: "the run's home binds no folder, and the runner has no /srv/code/garden",
}, PROJECTS_LIST[1], PROJECTS_LIST[2], {
  url: /\/api\/chat\/sessions(\?.*)?$/,
  json: [{
    key: 'chat-e2e-garden', title: 'When to sow the beans', messages: 6, project_id: 'p-0a1b2c3d', origin: 'manual', lifecycle: 'active',
    created: '2026-11-01T16:00:00+00:00', last_activity_ts: '2026-11-02T10:30:00+00:00', run_state: { session: 'chat-e2e-garden', phase: 'idle' },
  }],
  why: "the run's home has no project, so no chat in one, and other specs' chats are whatever ran first",
}]

/** The `?open=folder` tree's listing of Garden's folder. Only that substate asks it. */
export const GARDEN_FOLDER_TREE: StubbedRead = {
  url: /\/api\/file-list\?path=/,
  json: {
    roots: [], path: '/srv/code/garden',
    entries: [
      { name: 'beds', path: '/srv/code/garden/beds', is_dir: true },
      { name: 'sensors', path: '/srv/code/garden/sensors', is_dir: true },
      { name: 'README.md', path: '/srv/code/garden/README.md', is_dir: false, size: 812, mtime: 1730538000 },
    ],
  },
  why: "the run's home binds no folder, and the runner has no /srv/code/garden",
}
