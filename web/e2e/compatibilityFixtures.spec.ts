import { test, expect, type BrowserContext, type Page } from '@playwright/test'
import { idleRunState } from '../src/lib/workPhase'
import { getScheme } from '../src/design/schemes'
import { SHIPPED_ROUTES } from '../src/app/shippedRoutes'

// ── What a person had open still opens, however they come back to it ────────────────────────────
//
// #3819's approved update: "Reload, back, forward, copied URLs, and compact navigation must resolve
// to the same state", over explicit fixtures: the previous shell's local storage, a persisted palette
// and theme, and a restored project, thread, pane, mode, artifact and review file. The unit half is
// `everyShippedRouteResolves.test.tsx` and `routeRedirects.test.tsx` (every shipped address lands
// where its row says) and `thePaletteOpensEveryDestination.test.tsx`. This is what only a browser
// can say: a real reload, real history, a second tab of the same profile, and a 390 px window.
//
// Each fixture is an address, the local storage a profile holds before the app loads, the reads it
// needs (answered with route stubs, so the gateway's own state is never the variable), and what a
// person sees there, read from the page. One case per fixture opens the copied address in a fresh
// tab and requires that view, then requires it again after a reload, after Back from a page pushed
// on top, after Forward and Back again, and at 390 px in a fresh tab, where the sidebar is the
// drawer, and after a trip through the drawer and Back. Every step compares with the fixture's own
// expectation, not with the step before, so a page that shows nothing passes none of them.
//
// A harness that compares can go blind, so each fixture also names one value it holds and a planted
// script that drops it on the reload. Its control case requires the reload step to fail, and to fail
// on that value's part of the view alone.
//
// The old addresses close the file: each opens its new home when followed from another page, as a
// clicked link would, and Back returns to the page before it, never to the old address.

test.describe.configure({ timeout: 120_000 })

/** How long one step may take to show the fixture's view. */
const STEP_MS = 15_000
const PHONE = { width: 390, height: 844 }
const RAIL = 'nav[data-tour="rail"]'
/** The sidebar's destination rows, by their names; a thread row is none of these. */
const DESTINATIONS = ['Home', 'Chat', 'Projects', 'Library', 'Tasks', 'Knowledge', 'Files', 'Artifacts', 'Memory',
  'Terminal', 'Inbox', 'Automations', 'Triggers', 'Capabilities', 'Workflows', 'Companion', 'Settings']

// ── What a person sees ──────────────────────────────────────────────────────────────────────────

/** The page as a person reads it, in one pass. Hidden things (an `aria-hidden` probe, a closed
 *  menu) are left out. */
interface Seen {
  hash: string
  /** `<html>`'s theme attributes: the mode, the personality, the palette and the density. */
  mode: string | null
  personality: string | null
  palette: string | null
  density: string | null
  /** The primary colour as the browser computes it. */
  primary: string
  /** The sidebar's own toggle: 'Expand sidebar' while it is collapsed. */
  sidebar: string | null
  /** The destination rows the sidebar shows, in order, and the one lit. */
  rows: string[]
  lit: string | null
  headings: string[]
  /** Each named choice and its chosen option: a radio group, or the pill a folded one shows. */
  chosen: Record<string, string>
  /** Pressed buttons, by name. */
  pressed: string[]
  /** Named regions. */
  regions: string[]
  /** The chat's split, when one is open: `beside` or `stacked`. */
  split: string | null
  turns: number
  text: string
}

function look(page: Page): Promise<Seen> {
  return page.evaluate((destinations) => {
    const html = document.documentElement
    const shown = (el: Element) => !el.closest('[aria-hidden="true"], [inert]') && (el as HTMLElement).getClientRects().length > 0
    const nameOf = (el: Element) => {
      const by = el.getAttribute('aria-labelledby')
      const named = by ? by.split(' ').map((id) => document.getElementById(id)?.textContent ?? '').join(' ') : ''
      return (named || el.getAttribute('aria-label') || el.textContent || '').trim()
    }
    const chosen: Record<string, string> = {}
    for (const group of document.querySelectorAll('[role="radiogroup"]')) {
      if (!shown(group)) continue
      const on = group.querySelector('[role="radio"][aria-checked="true"]')
      const name = nameOf(group)
      if (on && name) chosen[name] = (on.getAttribute('title') || on.textContent || '').trim()
    }
    // A strip with no room folds into one pill naming its option (`Segmented`'s collapse='menu').
    for (const pill of document.querySelectorAll('button[aria-haspopup="listbox"]')) {
      const name = pill.getAttribute('aria-label') ?? ''
      if (!shown(pill) || !name || name in chosen) continue
      const title = pill.getAttribute('title') ?? ''
      chosen[name] = title.startsWith(`${name}: `) ? title.slice(name.length + 2) : (pill.textContent ?? '').trim()
    }
    const rail = document.querySelector('[role="dialog"][aria-label="Navigation"] nav') ?? document.querySelector('nav[data-tour="rail"]')
    const rows = rail ? [...rail.querySelectorAll('button[aria-label]')].filter(shown).map((b) => b.getAttribute('aria-label') ?? '') : []
    const main = document.querySelector('main') as HTMLElement | null
    return {
      hash: location.hash,
      mode: html.dataset.mode ?? null,
      personality: html.dataset.personality ?? null,
      palette: html.dataset.theme ?? null,
      density: html.dataset.ui ?? null,
      primary: getComputedStyle(html).getPropertyValue('--color-primary').trim().toLowerCase(),
      sidebar: document.querySelector('button[aria-label="Expand sidebar"], button[aria-label="Collapse sidebar"]')?.getAttribute('aria-label') ?? null,
      // A row's badge rides its name ("Projects, 1 active loop").
      rows: rows.map((r) => r.split(', ')[0]).filter((r) => destinations.includes(r)),
      lit: rail?.querySelector('[aria-current="page"]')?.getAttribute('aria-label') ?? null,
      headings: [...document.querySelectorAll('h1')].filter(shown).map((h) => (h.textContent ?? '').trim()),
      chosen,
      pressed: [...document.querySelectorAll('button[aria-pressed="true"]')].filter(shown).map(nameOf),
      // A named <section> is a region too.
      regions: [...document.querySelectorAll('[role="region"], section[aria-labelledby], section[aria-label]')].filter(shown).map(nameOf),
      split: document.querySelector('[data-pane-host="chat"]')?.getAttribute('data-pane-layout') ?? null,
      turns: document.querySelectorAll('[data-transcript-turn]').length,
      text: (main ?? document.body).innerText,
    }
  }, DESTINATIONS)
}

/** Which of `phrases` the page shows. */
const showing = (seen: Seen, phrases: readonly string[]) => phrases.filter((p) => seen.text.includes(p))

// ── The reads a fixture needs ───────────────────────────────────────────────────────────────────

type Body = string | object | ((url: URL) => unknown)

/** Answer `GET <path>` (a pathname, or a pattern over it) for every tab of the profile. A string
 *  body is sent as text, anything else as JSON. */
async function answer(context: BrowserContext, path: string | RegExp, body: Body): Promise<void> {
  const matches = (url: URL) => (typeof path === 'string' ? url.pathname === path : path.test(url.pathname))
  await context.route(matches, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const value = typeof body === 'function' ? (body as (url: URL) => unknown)(new URL(route.request().url())) : body
    return typeof value === 'string'
      ? route.fulfill({ status: 200, contentType: 'text/plain', body: value })
      : route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) })
  })
}

/** The profile's local storage, written once, before the app's first load in it, as the last
 *  release left it. Every later load reads what the app itself kept. */
async function seed(context: BrowserContext, values: Record<string, string>): Promise<void> {
  await context.addInitScript((v) => {
    try {
      if (localStorage.getItem('e2e-profile-seeded')) return
      for (const [key, value] of Object.entries(v)) localStorage.setItem(key, value)
      localStorage.setItem('e2e-profile-seeded', '1')
    } catch { /* a sandboxed frame has no storage */ }
  }, values)
}

// ── The fixtures' records ───────────────────────────────────────────────────────────────────────

const PROJECTS = [
  { id: 'p-garden', name: 'Garden', status: 'active' },
  { id: 'p-house', name: 'House move', status: 'active' },
]
const task = (id: string, title: string, project: string) => ({ id, title, status: 'open', project, created_at: '2026-10-20T09:00:00+00:00' })
const TASKS = [task('t1', 'Order the soil', 'Garden'), task('t2', 'Book the movers', 'House move'), task('t3', 'Plant the beans', 'Garden')]
const TASK_TITLES = TASKS.map((t) => t.title)

async function answerProjectsAndTasks(context: BrowserContext): Promise<void> {
  await answer(context, '/api/projects', { projects: PROJECTS })
  for (const p of PROJECTS) {
    await answer(context, `/api/projects/${p.id}`, p)
    await answer(context, `/api/projects/${p.id}/work`, { board: [], sections: [], completeness: 'complete', attention: 0, loadedAt: 0 })
    await answer(context, `/api/projects/${p.id}/linked`, { loops: [], code: [], artifacts: [], chats: [], knowledge: [] })
  }
  await answer(context, '/api/task-lists', { task_lists: [] })
  await answer(context, '/api/tasks/ready', { tasks: [] })
  await answer(context, '/api/tasks', (url) => {
    const project = url.searchParams.get('project')
    const rows = project ? TASKS.filter((t) => t.project === project) : TASKS
    const offset = Number(url.searchParams.get('offset') ?? '0')
    return { tasks: offset > 0 ? [] : rows, total: rows.length, complete: true, limit: 200, offset, owner: '' }
  })
  await answer(context, '/api/loops', { loops: [] })
}

const CHAT = 'compat-thread-1790000000'
const REPLY = [
  'Here is the planting script.',
  '',
  '```python',
  'def beds(rows):',
  '    return [f"bed {n}: beans, soil ordered, the line long enough to need the room a split gives" for n in range(rows)]',
  '```',
].join('\n')
const CHAT_DETAIL = {
  key: CHAT,
  title: 'The planting week',
  messages: [
    { role: 'user', content: 'Write me the planting script.', ts: '2026-10-20T09:00:00+00:00' },
    { role: 'assistant', content: REPLY, ts: '2026-10-20T09:00:20+00:00' },
  ],
  run_state: idleRunState(CHAT),
  queue: [],
  task_mode: 'agent',
  approval: 'normal',
  memory_mode: 'persistent',
}
const answerTheChat = (context: BrowserContext) => answer(context, `/api/chat/sessions/${CHAT}`, CHAT_DETAIL)

const ARTIFACT = 'garden-plan'
const ARTIFACT_ROW = {
  slug: ARTIFACT, name: 'Garden plan', kind: 'markdown', source: 'chat', description: '', tags: [], version: 3,
  created_at: '2026-10-18T09:00:00+00:00', updated_at: '2026-10-20T09:00:00+00:00', events: [], source_path: '',
  readonly: false, collection: '',
}
const ARTIFACT_BODY: Record<number, string> = {
  2: '# Garden plan\n\nSecond draft: beans along the south fence.',
  3: '# Garden plan\n\nThird draft: beans and squash along the south fence.',
}
async function answerTheArtifact(context: BrowserContext): Promise<void> {
  await answer(context, '/api/artifacts', { artifacts: [ARTIFACT_ROW] })
  await answer(context, `/api/artifacts/${ARTIFACT}`, { ...ARTIFACT_ROW, content: ARTIFACT_BODY[3], content_revision: 'r3', live_dirty: false })
  await answer(context, `/api/artifacts/${ARTIFACT}/versions`, { slug: ARTIFACT, versions: [1, 2, 3] })
  await answer(context, `/api/artifacts/${ARTIFACT}/versions/2`, { ...ARTIFACT_ROW, version: 2, content: ARTIFACT_BODY[2] })
  await answer(context, `/api/artifacts/${ARTIFACT}/events`, { slug: ARTIFACT, events: [] })
}

const CODE = 'c0dec0de'
const WORKSPACE = '/work/garden-site'
const CHANGED = 'src/beds.py'
const CODE_LOOP = {
  id: CODE, kind: 'code', name: 'Raised bed planner', task: 'Plan the raised beds', status: 'running',
  workspace_dir: WORKSPACE, files_dir: `${WORKSPACE}/.loop`, plan: [{ stage: 'build', title: 'Build' }],
  phase_status: { build: 'active' }, task_list_ids: { build: 'l-build' }, kind_config: { project_kind: 'brownfield' },
  execution: 'solo', agent: 'default', model: '', attended: false, max_cycles: 10, idle_secs: 0,
  created_at: '2026-10-20T09:00:00+00:00', updated_at: '2026-10-20T09:00:00+00:00',
}
/** The loop's row in the sidebar's threads. */
const LOOP_ROW = 'Raised bed planner, running'
async function answerTheCockpit(context: BrowserContext): Promise<void> {
  await answer(context, `/api/loops/${CODE}`, CODE_LOOP)
  await answer(context, '/api/loops', { loops: [CODE_LOOP] })
  await answer(context, '/api/tasks', { tasks: [], total: 0, complete: true, limit: 200, offset: 0, owner: '' })
  await answer(context, '/api/file-list', (url) => ({ roots: [], entries: [], path: url.searchParams.get('path') ?? WORKSPACE }))
  await answer(context, '/api/file-git-status', { repoRoot: WORKSPACE, branch: 'main', statuses: { [`${WORKSPACE}/${CHANGED}`]: ' M' } })
  await answer(context, '/api/file-git-log', { repoRoot: WORKSPACE, commits: [] })
  await answer(context, '/api/file-read', 'def beds(rows):\n    return rows * 2\n')
  await answer(context, '/api/file-git-original', { content: 'def beds(rows):\n    return rows\n', exists: true })
}

const KNOWLEDGE = 'k-garden-notes'
async function answerTheKnowledgeItem(context: BrowserContext): Promise<void> {
  const body = ['# Notes on the raised beds', '', ...Array.from({ length: 12 }, (_, i) => `Bed ${i + 1} takes beans in spring and squash after the frost.`)].join('\n\n')
  await answer(context, `/api/knowledge/items/${KNOWLEDGE}`, {
    id: KNOWLEDGE, title: 'Notes on the raised beds', content: body, item_type: 'note', type: 'note', tags: [],
    processing_status: 'done', read_state: 'unread', created_at: '2026-10-20T09:00:00+00:00', updated_at: '2026-10-20T09:00:00+00:00',
  })
  await answer(context, `/api/knowledge/items/${KNOWLEDGE}/extracted`, { contents: [] })
  await answer(context, `/api/knowledge/items/${KNOWLEDGE}/related`, [])
  await answer(context, `/api/knowledge/items/${KNOWLEDGE}/annotations`, { annotations: [] })
  await answer(context, `/api/knowledge/items/${KNOWLEDGE}/duplicates`, { duplicates: [], not_compared: 0, not_compared_note: '' })
}

const NOTES = [
  { kind: 'info', title: 'The soil is on its way', body: 'Delivery on Thursday.', ts: '2026-10-21T08:00:00+00:00', acked: false },
  { kind: 'info', title: 'The movers are booked', body: 'Saturday at nine.', ts: '2026-10-20T08:00:00+00:00', acked: true },
]
const answerTheNotes = (context: BrowserContext) => answer(context, '/api/notifications', { notifications: NOTES, unread: 1 })

// ── The fixtures ────────────────────────────────────────────────────────────────────────────────

type Form = 'wide' | 'compact'
type View = Record<string, unknown>

interface Fixture {
  name: string
  /** The copied address, after `#/`. A fixture whose address is made by a click makes it here. */
  address: (context: BrowserContext) => Promise<string>
  /** The profile's local storage before the app first loads in it. */
  seed?: Record<string, string>
  stub: (context: BrowserContext) => Promise<void>
  /** The part of what a person sees that this fixture holds. */
  view: (seen: Seen, form: Form) => View
  expected: (form: Form) => View
  /** The phone drawer's rows or its lit row, where the fixture holds them. */
  drawer?: Partial<Pick<Seen, 'rows' | 'lit'>>
  /** The planted defect: a script run before the app on the reload, which drops one value the
   *  fixture holds, and the parts of the view that must then differ. */
  dropped: { what: string; script: string; parts: string[] }
}

/** A script that takes `key` out of the address's query. */
const dropQueryKey = (key: string) => `{
  const [path, query = ''] = location.hash.split('?')
  const q = new URLSearchParams(query); q.delete(${JSON.stringify(key)})
  history.replaceState(null, '', path + (q.toString() ? '?' + q : ''))
}`

const PHOSPHOR_LIGHT = getScheme('phosphor')!.colors['--color-primary'].light.toLowerCase()
const OCEAN_LIGHT = getScheme('ocean')!.colors['--color-primary'].light.toLowerCase()

const FIXTURES: Fixture[] = [
  {
    name: "the previous shell's local storage",
    address: async () => 'tasks',
    // What the last release kept for someone who picked Retro Terminal in light mode, collapsed the
    // sidebar, kept the starter rail with Tasks and the terminal pinned (and Knowledge, pinned when it
    // was a row of its own), and left All tasks on the board, scoped to one project.
    seed: {
      mode: 'light',
      personality: 'retro-terminal',
      appearance: JSON.stringify({
        colors: {}, scalars: { '--expressiveness': 0.25, '--bounciness': 0 },
        selects: { '--ui-density': 'cli', '--dot-shape': 'square', '--dot-pattern': 'grid', '--bg-style': 'still' },
        scheme: 'phosphor', widthPreset: 'full',
      }),
      'nav-collapsed': '1',
      'nav-disclosure': JSON.stringify({ mode: 'starter', pinned: ['tasks', 'knowledge', 'terminal'] }),
      'tasks-view': 'board',
      'tasks-scope': 'House move',
    },
    stub: answerProjectsAndTasks,
    view: (s, form) => ({
      hash: s.hash, mode: s.mode, personality: s.personality, palette: s.palette, primary: s.primary, density: s.density,
      ...(form === 'wide' ? { sidebar: s.sidebar, rows: s.rows } : {}),
      heading: s.headings[0] ?? null, view: s.chosen.View ?? null, tasks: showing(s, TASK_TITLES),
    }),
    expected: (form) => ({
      hash: '#/tasks', mode: 'light', personality: 'retro-terminal', palette: 'phosphor', primary: PHOSPHOR_LIGHT, density: 'cli',
      // The record the starter rail kept is no longer read: every destination has its row, and the
      // terminal none.
      ...(form === 'wide' ? { sidebar: 'Expand sidebar', rows: ['Home', 'Chat', 'Projects', 'Library', 'Inbox', 'Automations', 'Capabilities', 'Settings'] } : {}),
      heading: 'All tasks', view: 'Kanban board', tasks: ['Book the movers'],
    }),
    drawer: { rows: ['Home', 'Chat', 'Projects', 'Library', 'Inbox', 'Automations', 'Capabilities', 'Companion', 'Settings'] },
    dropped: { what: 'the remembered board view', script: "localStorage.removeItem('tasks-view')", parts: ['view'] },
  },
  {
    name: 'a persisted palette and theme',
    address: async () => 'settings/design',
    seed: {
      mode: 'light',
      appearance: JSON.stringify({ colors: {}, scalars: {}, selects: {}, scheme: 'ocean', widthPreset: 'full' }),
    },
    stub: async () => {},
    view: (s) => ({
      hash: s.hash, mode: s.mode, palette: s.palette, primary: s.primary,
      pressed: s.pressed.filter((p) => p.startsWith('Mode: ') || p.startsWith('Ocean') || p.startsWith('Coral')),
    }),
    expected: () => ({ hash: '#/settings/design', mode: 'light', palette: 'ocean', primary: OCEAN_LIGHT, pressed: ['Mode: Light', 'Ocean'] }),
    dropped: {
      what: 'the stored palette',
      script: "{ const a = JSON.parse(localStorage.getItem('appearance') || '{}'); delete a.scheme; localStorage.setItem('appearance', JSON.stringify(a)) }",
      parts: ['palette', 'primary', 'pressed'],
    },
  },
  {
    name: "a project's board",
    address: async () => 'projects/p-garden?tab=tasks&view=board',
    stub: answerProjectsAndTasks,
    view: (s, form) => ({
      hash: s.hash, heading: s.headings[0] ?? null, ...(form === 'wide' ? { lit: s.lit } : {}),
      project: s.chosen['Project view'] ?? null, view: s.chosen.View ?? null, tasks: showing(s, TASK_TITLES),
    }),
    expected: (form) => ({
      hash: '#/projects/p-garden?tab=tasks&view=board', heading: 'Garden', ...(form === 'wide' ? { lit: 'Projects' } : {}),
      project: 'Tasks', view: 'Kanban board', tasks: ['Order the soil', 'Plant the beans'],
    }),
    drawer: { lit: 'Projects' },
    dropped: { what: 'the Tasks view', script: dropQueryKey('tab'), parts: ['hash', 'project', 'view', 'tasks'] },
  },
  {
    name: 'a thread with its Activity pane',
    address: async () => `chat/${CHAT}?activity=1`,
    stub: answerTheChat,
    view: (s) => ({ hash: s.hash, turns: s.turns, activity: s.regions.includes('Activity') }),
    expected: () => ({ hash: `#/chat/${CHAT}?activity=1`, turns: 2, activity: true }),
    dropped: { what: 'the open pane', script: dropQueryKey('activity'), parts: ['hash', 'activity'] },
  },
  {
    name: "a reply's block open beside the thread",
    // The address the page writes when the block is opened, copied as a person copies it.
    address: async (context) => {
      const page = await context.newPage()
      await page.goto(`/#/chat/${CHAT}`)
      await page.getByRole('button', { name: 'Open python code beside' }).click()
      await expect(page.getByRole('region', { name: 'python code' })).toBeVisible()
      const hash = await page.evaluate(() => location.hash)
      await page.close()
      expect(hash, 'the address names the block').toMatch(/[?&]beside=code-/)
      return hash.replace(/^#\//, '')
    },
    stub: answerTheChat,
    view: (s) => ({ beside: /[?&]beside=code-/.test(s.hash), turns: s.turns, block: s.regions.includes('python code'), split: s.split }),
    expected: (form) => ({ beside: true, turns: 2, block: true, split: form === 'wide' ? 'beside' : 'stacked' }),
    dropped: { what: 'the block beside the thread', script: dropQueryKey('beside'), parts: ['beside', 'block', 'split'] },
  },
  {
    name: 'an artifact at an earlier version',
    address: async () => `artifacts/${ARTIFACT}?v=2`,
    stub: answerTheArtifact,
    view: (s, form) => ({
      hash: s.hash, ...(form === 'wide' ? { lit: s.lit } : {}), tab: s.chosen.Library ?? null,
      showing: showing(s, ['Garden plan', 'Viewing historical v2', 'Second draft', 'Third draft']),
    }),
    expected: (form) => ({
      hash: `#/artifacts/${ARTIFACT}?v=2`, ...(form === 'wide' ? { lit: 'Library' } : {}), tab: 'Artifacts',
      showing: ['Garden plan', 'Viewing historical v2', 'Second draft'],
    }),
    drawer: { lit: 'Library' },
    dropped: { what: 'the version', script: dropQueryKey('v'), parts: ['hash', 'showing'] },
  },
  {
    name: "a code review's open diff",
    address: async () => `code/${CODE}?diff=${encodeURIComponent(CHANGED)}&pane=changes`,
    stub: answerTheCockpit,
    view: (s, form) => ({
      hash: s.hash, ...(form === 'wide' ? { lit: s.lit } : {}), diff: s.regions.filter((r) => r.startsWith('Diff')),
      named: showing(s, ['Raised bed planner']),
    }),
    // The sidebar lights the loop's own thread row, which names its state, and not Projects beside it.
    expected: (form) => ({
      hash: `#/code/${CODE}?diff=${encodeURIComponent(CHANGED)}&pane=changes`, ...(form === 'wide' ? { lit: LOOP_ROW } : {}),
      diff: ['Diff — beds.py'], named: ['Raised bed planner'],
    }),
    drawer: { lit: LOOP_ROW },
    dropped: { what: 'the open diff', script: dropQueryKey('diff'), parts: ['hash', 'diff'] },
  },
]

// ── The harness ─────────────────────────────────────────────────────────────────────────────────

async function profileFor(context: BrowserContext, f: Fixture): Promise<string> {
  if (f.seed) await seed(context, f.seed)
  await f.stub(context)
  return f.address(context)
}

/** The page shows the fixture's view, within a step's time. */
async function sees(page: Page, f: Fixture, form: Form, step: string): Promise<void> {
  await expect
    .poll(async () => f.view(await look(page), form), { message: `${f.name}: ${step}`, timeout: STEP_MS })
    .toEqual(f.expected(form))
}

/** Home, pushed on top of the page as a click in the sidebar pushes it. */
async function goHomeFromTheSidebar(page: Page): Promise<void> {
  await page.locator(RAIL).getByRole('button', { name: 'Home', exact: true }).click()
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/dashboard')
}

async function openTheDrawer(page: Page) {
  await page.getByRole('button', { name: 'Expand sidebar' }).click()
  const drawer = page.getByRole('dialog', { name: 'Navigation' })
  await expect(drawer).toBeVisible()
  return drawer
}

/** One fixture's two cases: it holds, and its control. */
function holdsAsItWasLeft(f: Fixture): void {
  test.describe(`${f.name} opens as it was left`, { tag: '@gate-c' }, () => {
    test('from a copied address, after a reload, Back and Forward, and at 390 px', async ({ context }) => {
      const address = await profileFor(context, f)

      const page = await context.newPage()
      await page.goto(`/#/${address}`)
      await sees(page, f, 'wide', 'the copied address, in a fresh tab')

      await page.reload()
      await sees(page, f, 'wide', 'after a reload')

      await goHomeFromTheSidebar(page)
      await page.goBack()
      await sees(page, f, 'wide', 'after Back from Home')
      await page.goForward()
      await expect.poll(() => page.evaluate(() => location.hash), { message: `${f.name}: Forward returns to Home` }).toBe('#/dashboard')
      await page.goBack()
      await sees(page, f, 'wide', 'after Forward and Back again')

      const phone = await context.newPage()
      await phone.setViewportSize(PHONE)
      await phone.goto(`/#/${address}`)
      await sees(phone, f, 'compact', 'the copied address at 390 px')
      const drawer = await openTheDrawer(phone)
      if (f.drawer) {
        const { rows, lit } = await look(phone)
        const held = Object.keys(f.drawer) as (keyof NonNullable<Fixture['drawer']>)[]
        expect(Object.fromEntries(held.map((k) => [k, { rows, lit }[k]])), `${f.name}: the drawer at 390 px`).toEqual(f.drawer)
      }
      await drawer.getByRole('button', { name: 'Home', exact: true }).click()
      await expect.poll(() => phone.evaluate(() => location.hash), { message: `${f.name}: the drawer's Home` }).toBe('#/dashboard')
      await phone.goBack()
      await sees(phone, f, 'compact', 'after Back from the drawer at 390 px')
    })

    test(`control: a reload that drops ${f.dropped.what} fails the reload step, on that value alone`, async ({ context }) => {
      const address = await profileFor(context, f)
      const page = await context.newPage()
      // Counts this tab's loads, and drops the value on the second, before the app reads it.
      await page.addInitScript({
        content: `{
          const loads = Number(sessionStorage.getItem('e2e-loads') || '0') + 1
          sessionStorage.setItem('e2e-loads', String(loads))
          if (loads === 2) ${f.dropped.script}
        }`,
      })
      await page.goto(`/#/${address}`)
      await sees(page, f, 'wide', 'the copied address, in a fresh tab')
      await page.reload()
      await expect(sees(page, f, 'wide', 'after a reload')).rejects.toThrow(`${f.name}: after a reload`)
      const view = f.view(await look(page), 'wide')
      const expected = f.expected('wide')
      const differ = Object.keys(expected).filter((k) => JSON.stringify(view[k]) !== JSON.stringify(expected[k]))
      expect(differ, `what the reload changed: ${JSON.stringify(view)}`).toEqual(f.dropped.parts)
    })
  })
}

FIXTURES.forEach(holdsAsItWasLeft)

// ── The old addresses ───────────────────────────────────────────────────────────────────────────

/** The page an old address is followed from. Not Home, where `#/onboarding` lands, so that Back
 *  to it says something for every address. */
const BEFORE = 'settings'

/** Follow `address` from the page before, as a clicked link does: a pushed hash change. Answers how
 *  long the history was before it. */
async function followFromThePageBefore(page: Page, address: string): Promise<number> {
  await page.goto(`/#/${BEFORE}`)
  await expect(page.locator(RAIL).getByRole('button', { name: 'Settings', exact: true })).toHaveAttribute('aria-current', 'page')
  const before = await page.evaluate(() => history.length)
  await page.evaluate((to) => { location.hash = to }, `#/${address}`)
  return before
}

/** The old address left no entry of its own: Back from `lands` returns to the page before, and
 *  Forward to `lands`. */
async function backReturnsToThePageBefore(page: Page, before: number, lands: string): Promise<void> {
  const landed = await page.evaluate(() => history.length)
  await page.goBack()
  await expect.poll(() => page.evaluate(() => location.hash), { message: 'Back returns to the page before' }).toBe(`#/${BEFORE}`)
  await expect(page.locator(RAIL).getByRole('button', { name: 'Settings', exact: true })).toHaveAttribute('aria-current', 'page')
  expect(landed, 'the old address left no entry of its own').toBe(before + 1)
  await page.goForward()
  await expect.poll(() => page.evaluate(() => location.hash), { message: 'Forward returns to the new home' }).toBe(`#/${lands}`)
}

test.describe('an old address opens its new home, and Back returns to the page before it', { tag: '@gate-c' }, () => {
  test('#/notifications?filter=unread opens the Inbox on its notifications, the unread ones', async ({ context, page }) => {
    await answerTheNotes(context)
    const lands = 'inbox?filter=notifications&show=unread'
    const before = await followFromThePageBefore(page, 'notifications?filter=unread')
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(`#/${lands}`)
    await expect(page.locator(RAIL).getByRole('button', { name: /^Inbox/ })).toHaveAttribute('aria-current', 'page')
    await expect(page.getByText('The soil is on its way')).toBeVisible()
    await expect(page.getByText('The movers are booked'), 'a read note is not unread').toHaveCount(0)
    await backReturnsToThePageBefore(page, before, lands)
  })

  test('#/knowledge/item/<id>?read=1 opens the reader, in Library', async ({ context, page }) => {
    await answerTheKnowledgeItem(context)
    const address = `knowledge/item/${KNOWLEDGE}?read=1`
    const before = await followFromThePageBefore(page, address)
    await expect(page.getByRole('group', { name: 'Article body' })).toBeVisible()
    await expect(page.getByRole('group', { name: 'Article body' })).toContainText('Bed 1 takes beans in spring')
    expect(await page.evaluate(() => location.hash)).toBe(`#/${address}`)
    await expect(page.locator(RAIL).getByRole('button', { name: 'Library', exact: true })).toHaveAttribute('aria-current', 'page')
    await backReturnsToThePageBefore(page, before, address)
  })

  test("#/loops/<id> of a code loop opens its cockpit at #/code/<id>", async ({ context, page }) => {
    await answerTheCockpit(context)
    // A cockpit opened at its bare address opens its review on the first uncommitted change and
    // names it in the address, a replace (2c704fcc6, #3821): so the new home's entry ends as the
    // cockpit with that change named, on the way in and after Forward alike (#3905).
    const lands = `code/${CODE}?diff=${encodeURIComponent(CHANGED)}`
    const before = await followFromThePageBefore(page, `loops/${CODE}`)
    await expect.poll(() => page.evaluate(() => location.hash)).toBe(`#/${lands}`)
    await expect(page.getByRole('button', { name: 'Raised bed planner', exact: true })).toBeVisible()
    await expect(page.locator(RAIL).getByRole('button', { name: LOOP_ROW, exact: true })).toHaveAttribute('aria-current', 'page')
    await backReturnsToThePageBefore(page, before, lands)
  })

  // Every other address the dashboard shipped that now lands somewhere else (`shippedRoutes.ts`).
  for (const r of SHIPPED_ROUTES.filter((e) => e.lands && e.home !== 'new' && e.route !== 'notifications?filter=unread')) {
    test(`#/${r.route} lands at #/${r.lands}`, async ({ page }) => {
      const before = await followFromThePageBefore(page, r.route)
      await expect.poll(() => page.evaluate(() => location.hash), { message: `#/${r.route} lands` }).toBe(`#/${r.lands}`)
      await backReturnsToThePageBefore(page, before, r.lands!)
    })
  }
})
