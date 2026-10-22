import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ROUTES, SETTINGS_ROUTES, VIEW_ROUTES, NON_NAV_ROUTES, SUBSTATE_ROUTES, THEMES, type Theme } from './routes'
import { seedTheme, gotoRoute, assertMounted, OPENERS } from './helpers'
import { BASELINED_STEPS, runMatrix, type Baselines, type Measured, type SurfaceContext } from './surfaceChecks'

// ── The UI quality check: every surface, once per theme, every check ────────────────────────────
//
// The one named UI check (#3941, which #3811 and #3943 point at): every route and substate URL in
// `routes.ts`, and every opened surface in `OPENERS`, in light and dark. Each surface is LOADED ONCE
// per theme and `runMatrix` (surfaceChecks.ts) runs the approved matrix on it as named steps: axe,
// the non-axe detectors, the accessibility tree, reduced motion, overflow at 320 to 1440 px with
// 44 px targets at the compact widths, 200% text zoom, keyboard-only operation, focus order and the
// rendered contrast of each focus indicator. The step order and why it is safe are written there.
//
// Every step reports through `expect.soft`, so one failing surface reports every check that failed
// on it, each under its step's name. The title keeps `${label} (#/${route})`, which is what
// `assert-no-route-skips.mjs` counts: one scan per declared route and theme, none skipped, each
// carrying every step. `surfaceMatrix.control.spec.ts` proves each step can fail.
//
// Two committed baselines, platform-free so a macOS and a Linux run read the same files:
//   · `__aria__/<id>.aria.yml`: the surface's normalized accessibility tree, ONE for both themes;
//   · `__targets__/<id>.json`: per theme and compact width, the controls already under 44 px.
//     Shrink-only and self-clearing: a new small control reds, and so does a baselined one that
//     grew, until its entry is deleted.
// `npx playwright test e2e/surfaces.spec.ts --update-snapshots` rewrites both from what it measures,
// which a reviewer then reads as a diff. Outside CI a missing baseline is written on first run.

const HERE = dirname(fileURLToPath(import.meta.url))
const ARIA_DIR = join(HERE, '__aria__')
const BASELINE_DIR = join(HERE, '__ui-baselines__')

/** One surface's baseline file: per theme, its 44 px targets per compact width and its known
 *  findings per baselined step (`BASELINED_STEPS`). */
type BaselineFile = Record<string, { targets: Record<string, string[]>; known: Record<string, string[]> }>

export const SURFACES = [...ROUTES, ...SETTINGS_ROUTES, ...VIEW_ROUTES, ...NON_NAV_ROUTES, ...SUBSTATE_ROUTES]

/** Surfaces loaded under a fixed clock, and the instant. The week grid draws the week of the
 *  browser's today, and which hour cells hold a fire (and so are controls) moves with the clock;
 *  a fixed instant makes it the same week, and the same cells, on every run. */
const FIXED_CLOCK: Record<string, string> = {
  'triggers?view=week': '2026-11-02T09:00:00',
  // The feed groups its notes under Today, Yesterday and Earlier in the browser's zone, and a task
  // row says how long ago it changed. A zoned noon, so the same notes fall on the same days in any
  // zone a runner has.
  'inbox?filter=notifications': '2026-11-02T12:00:00Z',
  'projects/p-0a1b2c3d?tab=tasks': '2026-11-02T12:00:00Z',
}

/** The one project the Tasks view row opens, with three of its tasks; the run's home has none. */
const FIXED_PROJECT = { id: 'p-0a1b2c3d', name: 'Garden', status: 'active', task_list_count: 0, created_at: '2026-10-01T09:00:00+00:00', updated_at: '2026-11-01T09:00:00+00:00' }
const fixedTask = (id: string, title: string, status: string, priority: string) => ({
  id, title, status, priority, project: 'Garden', tags: [],
  created_at: '2026-10-30T09:00:00+00:00', updated_at: '2026-11-01T15:00:00+00:00',
})
const FIXED_TASKS = [
  fixedTask('t-e2e-1', 'Order the soil', 'open', 'high'),
  fixedTask('t-e2e-2', 'Plant the beans', 'in_progress', 'medium'),
  fixedTask('t-e2e-3', 'Fix the gate latch', 'done', 'low'),
]

/** The feed the Notifications filter lists: one unread and one read note today, one read yesterday. */
const fixedNote = (ts: string, title: string, acked: boolean) => ({ kind: 'job_completed', title, body: `${title}.`, ts, acked })
const FIXED_NOTIFICATIONS = {
  notifications: [
    fixedNote('2026-11-02T11:40:00+00:00', 'Weekly backup finished', false),
    fixedNote('2026-11-02T10:15:00+00:00', 'Morning briefing is ready', true),
    fixedNote('2026-11-01T12:00:00+00:00', 'Inbox digest sent', true),
  ],
  unread: 1,
}

/** Settings › updates' fixed CHANGELOG: the file's own shape (its front matter, an unreleased
 *  section, a release with its summary), and long enough that the Changelog box scrolls, so its
 *  scroll region stays a tab stop the axe and keyboard steps reach. */
const FIXED_CHANGELOG = [
  '# Changelog', '', 'All notable changes are recorded here.', '',
  '## [Unreleased]', '', '### Added', '',
  ...Array.from({ length: 8 }, (_, i) => `- **An entry not released yet, ${i + 1}.**`), '',
  '### Fixed', '', '- **A fix not released yet.**', '',
  '## [0.2.3] — 2026-10-19', '', 'A patch release.', '', '### Fixed', '',
  ...Array.from({ length: 16 }, (_, i) => `- **A fix in this release, ${i + 1}.**`), '',
].join('\n')

/** Settings › updates' fixed release notes, long enough that the Release notes box scrolls too. */
const FIXED_RELEASE_NOTES = Array.from({ length: 24 }, (_, i) => `- **A change in this release, ${i + 1}.**`).join('\n')

/** Triggers › week's fixed projection: four triggers in the week its fixed clock draws, with a cell
 *  in each state the legend names. Each fire is a local time on a day of that week (day 0 is the
 *  clock's day, the grid's first column), read in the runner's zone, which is the zone the browser
 *  draws the grid in; the projection names that zone as the gateway's, as a gateway on the same
 *  machine does, so no zone caption appears on one machine and not another. */
function fixedWeek(clock: string) {
  const first = new Date(clock)
  first.setHours(0, 0, 0, 0)
  const on = (day: number, hour: number, minute = 0) => {
    const d = new Date(first)
    d.setDate(d.getDate() + day)
    d.setHours(hour, minute)
    return d
  }
  const date = (d: Date) => [d.getFullYear(), d.getMonth() + 1, d.getDate()].map((n) => String(n).padStart(2, '0')).join('-')
  const fire = (trigger: string, name: string, at: Date, suppressed_by = '', reason = '') =>
    ({ trigger_id: `schedule:e2e-${trigger}`, trigger_name: name, at: at.getTime() / 1000, suppressed_by, reason })
  const quiet = 'quiet hours 22:00–07:00'
  return {
    start: first.toISOString(),
    end: on(7, 0).toISOString(),
    server_tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    occurrences: [
      // Weekdays at 08:00, the fourth a skip date: four cells that run and one struck.
      ...[0, 1, 2, 3, 4].map((day) =>
        day === 3
          ? fire('briefing', 'Morning briefing', on(day, 8), 'skipped', `skip date ${date(on(day, 8))}`)
          : fire('briefing', 'Morning briefing', on(day, 8))),
      fire('review', 'Weekly review', on(4, 17)),
      // Inside its quiet hours, both times: one cell all quiet, and one it shares with the backup.
      fire('digest', 'Evening digest', on(1, 22, 30), 'quiet', quiet),
      fire('digest', 'Evening digest', on(5, 22, 30), 'quiet', quiet),
      // The same hour as the second digest, and not quiet: that cell is partly suppressed.
      fire('backup', 'Weekly backup', on(5, 22)),
    ],
    truncated: [],
  }
}

/** Reads a surface's row answers with a fixed body, and why. Only for a read whose answer depends on
 *  what other specs did first in the shared home or on state outside the run, so the surface would
 *  hold one of several states on any run, or one the run's home can only answer empty. Each state
 *  the stub hides is held by a unit test instead, named here. */
const STUBBED_READS: Record<string, { url: RegExp; json: unknown; why: string }[]> = {
  settings: [{
    url: /\/api\/usage\/totals(\?.*)?$/,
    // One fixed day with turns: the hub's Usage tile shows today's totals, and the shared home's
    // count is however many chat turns other specs drove before this row loaded (0 on a lone run,
    // 20 after the chat specs, and a `#k` once it passes 1,000 tokens). The tile's empty state is
    // held by `src/pages/settings/usageTileEmptyState.test.tsx`.
    json: {
      since: '2026-11-02T08:00:00+00:00', until: '', session: '',
      totals: {
        input_tokens: 840, output_tokens: 180, cache_read_tokens: 0, cache_creation_tokens: 0,
        cost_usd: 0.0123, turns: 20, priced: true, units: {},
      },
    },
    why: "today's usage counts the turns other specs ran first in the shared home",
  }],
  'settings/audit': [{
    url: /\/api\/security\/audit(\?.*)?$/,
    // One fixed page of the log, one row per outcome family and one unclassified, and no older
    // page. The shared home's log is every request other specs made before this row loaded, so its
    // rows, and which of them are under 44 px, differed with the order the specs ran in. What the
    // rows say is held by `src/pages/settings/auditRowSaysWhatRan.test.tsx`, the pills by
    // `auditOutcomeFilters.test.tsx`, and a long outcome word by `auditOutcomeFitsItsRow.test.tsx`.
    // The verify read is not stubbed: it answers for the real chain.
    json: {
      events: [
        { event_id: 'e2e-audit-1', timestamp: '2026-11-02T08:59:00+00:00', event_type: 'tool_invocation', operation: 'shell', resources: 'git status', outcome: 'success', outcome_tone: 'success', integrity_ok: true },
        { event_id: 'e2e-audit-2', timestamp: '2026-11-02T08:58:00+00:00', event_type: 'api_access', operation: 'devices_listed', outcome: 'ok', outcome_tone: 'success', integrity_ok: true },
        { event_id: 'e2e-audit-3', timestamp: '2026-11-02T08:57:00+00:00', event_type: 'tool_invocation', operation: 'shell', outcome: 'denied', outcome_tone: 'danger', integrity_ok: true, metadata: { command: 'rm -rf build', reason: 'denylist' } },
        { event_id: 'e2e-audit-4', timestamp: '2026-11-02T08:56:00+00:00', event_type: 'api_access', operation: 'durability_export', outcome: 'error', outcome_tone: 'danger', integrity_ok: true, error: 'disk full' },
        { event_id: 'e2e-audit-5', timestamp: '2026-11-02T08:55:00+00:00', event_type: 'tool_invocation', operation: 'write_file', resources: 'notes/today.md', outcome: 'needs_confirm', outcome_tone: 'warning', integrity_ok: true },
        { event_id: 'e2e-audit-6', timestamp: '2026-11-02T08:54:00+00:00', event_type: 'api_access', operation: 'tool_policy.no_session_key', outcome: 'fail_open', outcome_tone: 'neutral', integrity_ok: true },
      ],
      count: 6, next_cursor: '', scanned: 6, truncated: false,
      outcome_families: [
        { key: 'denied', label: 'Denied', tone: 'danger', values: ['denied', 'rejected', 'blocked', 'refused', 'declined'] },
        { key: 'failed', label: 'Failed', tone: 'danger', values: ['failure', 'failed', 'error', 'not_found'] },
        { key: 'needs_confirm', label: 'Needs confirmation', tone: 'warning', values: ['needs_confirm', 'needs_input', 'needs_human'] },
        { key: 'ok', label: 'Succeeded', tone: 'success', values: ['success', 'ok', 'executed', 'allowed', 'approved', 'auto_approved', 'completed', 'granted', 'enabled', 'disabled'] },
      ],
    },
    why: 'the audit log lists every request other specs made first in the shared home',
  }],
  'settings/updates': [{
    url: /\/api\/update\/check(\?.*)?$/,
    // A git checkout that is up to date on the stable channel, with automatic checks on and
    // nothing applied on its own. The real check compares this checkout with its remote and
    // the newest release, so the Version row read "A new version is ready to install. (#
    // commit behind)" once a release or a commit landed, and "Up to date" on the next run. An
    // update that waits is held by `src/pages/settings/updatesPanelHeadlines.test.tsx` (the
    // entries it brings, and how many commits behind a checkout is) and by
    // `updatesPanelCheckNow.test.tsx` ("offers the update it finds").
    json: {
      available: false, update_available: false, checked: true, auto: 'off', kind: 'git',
      channel: 'stable', pin: '', current: '0.2.3', latest: '0.2.3', version: '0.2.3',
      commits_behind: 0, pin_miss: false, pin_older: false, apply_method: 'pipeline',
      unattended_apply: true, instructions: [], image_tag: '', release_name: 'v0.2.3',
      release_notes: FIXED_RELEASE_NOTES, changes: '', check_enabled: true,
      check_interval_hours: 12, last_version: '',
    },
    why: "the update check compares this checkout with its remote and the newest release",
  }, {
    url: /\/api\/changelog(\?.*)?$/,
    // The checkout's CHANGELOG.md, served whole: it gains a line with almost every change that
    // lands, so a baseline of it held for one merge. How the panel shows a changelog is held by
    // `src/pages/settings/changelogBody.test.ts` and its scroll box by
    // `updatesPanelScrollRegions.test.tsx`.
    json: { content: FIXED_CHANGELOG },
    why: "the changelog is the checkout's CHANGELOG.md, which almost every change adds a line to",
  }],
  'triggers?view=week': [{
    url: /\/api\/triggers\/week(\?.*)?$/,
    // A fixed week under the row's fixed clock (`fixedWeek`). The run's home has every trigger
    // switched off (`e2e/fixtures/home/`), so the real projection has no fire and the view drew only
    // its empty state: the grid, its cells as controls and the shaded cells whose contrast axe
    // reads, went unchecked. `src/pages/triggers/weekGridInTheUiCheck.test.ts` holds this row's tree
    // to the grid. The empty week is held by `src/pages/loadErrorSurfaces.test.tsx` (an empty
    // answer is "No fires this week") and its copy by `weekGridEmptyStateCopy.test.ts`.
    json: fixedWeek(FIXED_CLOCK['triggers?view=week']),
    why: "the run's home has every trigger switched off, so the real week has no fire to draw",
  }],
  'inbox?filter=notifications': [{
    url: /\/api\/notifications(\?.*)?$/,
    // A fixed feed under the row's fixed clock. The shared home's feed is whatever other specs raised
    // first (a finished turn, a trigger's fire), so the filter listed a different set on each run.
    // What the filter does with the feed (its Show menu, unread first, the acknowledge) is held by
    // `src/app/notificationsAreAFilterOfTheInbox.test.tsx`.
    json: FIXED_NOTIFICATIONS,
    why: 'the shared home\'s feed is whatever other specs raised before this row loaded',
  }],
  'projects/p-0a1b2c3d?tab=tasks': [{
    url: /\/api\/projects(\?.*)?$/,
    // One project and its tasks. The run's home has no project, so the Tasks view could only draw
    // "not found". The view's own behavior (held to the project, its board, the scope All tasks
    // remembers) is held by `src/app/tasksAreAViewOfEachProject.test.tsx`.
    json: { projects: [FIXED_PROJECT] },
    why: "the run's home has no project",
  }, {
    url: /\/api\/projects\/p-0a1b2c3d(\?.*)?$/,
    json: FIXED_PROJECT,
    why: "the run's home has no project",
  }, {
    url: /\/api\/projects\/p-0a1b2c3d\/work(\?.*)?$/,
    json: { board: [], sections: [], completeness: 'complete', attention: 0, loadedAt: 0 },
    why: "the run's home has no project",
  }, {
    url: /\/api\/tasks(\?.*)?$/,
    json: { tasks: FIXED_TASKS, total: FIXED_TASKS.length, complete: true, owner: '' },
    why: "the run's home has no project, so no task of one",
  }],
}

const fileId = (s: string) => s.replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-|-$/g, '')

const updating = (info: TestInfo) => info.config.updateSnapshots === 'all' || info.config.updateSnapshots === 'changed'

/** The committed baselines for one surface and theme. A missing one is ADOPTED (written from this
 *  run, and not a finding) under `--update-snapshots` or outside CI; in CI it is a red. */
function readFile(id: string): BaselineFile | null {
  const path = join(BASELINE_DIR, `${id}.json`)
  return existsSync(path) ? (JSON.parse(readFileSync(path, 'utf8')) as BaselineFile) : null
}

function readBaselines(id: string, theme: Theme, info: TestInfo): Baselines {
  const aria = join(ARIA_DIR, `${id}.aria.yml`)
  const mine = readFile(id)?.[theme]
  // A theme entry lists only the steps that found something (`writeBaselines`), so once the theme
  // has an entry, a step it does not name had nothing to baseline: it is held to zero findings,
  // not reported as a baseline nobody committed.
  const known = mine ? Object.fromEntries(BASELINED_STEPS.map((s) => [s, mine.known?.[s] ?? []])) : {}
  return {
    tree: existsSync(aria) ? readFileSync(aria, 'utf8') : null,
    targets: mine?.targets ?? {},
    known,
    adopt: updating(info) || !process.env.CI,
  }
}

/** Write what this run measured, when asked to (`--update-snapshots`) or, outside CI, when the
 *  surface has no baseline yet. The tree is written by the light theme only: it is one file. */
function writeBaselines(id: string, theme: Theme, measured: Measured, info: TestInfo): string[] {
  const update = updating(info)
  const aria = join(ARIA_DIR, `${id}.aria.yml`)
  const wrote: string[] = []
  if ((update || (!process.env.CI && !existsSync(aria))) && theme === 'light') {
    mkdirSync(ARIA_DIR, { recursive: true })
    writeFileSync(aria, measured.tree.trimEnd() + '\n')
    wrote.push(aria)
  }
  const file = readFile(id)
  if (update || (!process.env.CI && !(file && theme in file))) {
    mkdirSync(BASELINE_DIR, { recursive: true })
    const all: BaselineFile = file ?? {}
    // Only steps that found something are written, so a clean step leaves no entry behind.
    const known = Object.fromEntries(Object.entries(measured.known).filter(([, v]) => v.length).sort())
    all[theme] = { targets: measured.targets, known }
    const sorted = Object.fromEntries(Object.keys(all).sort().map((k) => [k, all[k]]))
    const path = join(BASELINE_DIR, `${id}.json`)
    writeFileSync(path, JSON.stringify(sorted, null, 2) + '\n')
    wrote.push(path)
  }
  return wrote
}

async function checkSurface(page: Page, info: TestInfo, ctx: SurfaceContext, id: string): Promise<void> {
  const baselines = readBaselines(id, ctx.theme, info)
  const report: string[] = []
  const measured = await runMatrix(page, ctx, baselines, async (name, findings) => {
    await test.step(name, async () => {
      const found = await findings()
      report.push(`## ${name}: ${found.length}`, ...found.map((f) => `  ${f}`))
      expect.soft(found, `${name} on ${ctx.where}`).toEqual([])
    })
  })
  writeBaselines(id, ctx.theme, measured, info)
  await info.attach(`ui-quality-${id}-${ctx.theme}.txt`, { body: report.join('\n'), contentType: 'text/plain' })
}

for (const theme of THEMES) {
  test.describe(`UI quality: ${theme} theme`, { tag: '@ui-quality' }, () => {
    // One test runs the whole matrix: six widths, a zoom and a keyboard walk on top of the load.
    // Measured on the slowest surface; see the commit for the figure.
    test.describe.configure({ timeout: 120_000 })

    for (const { route, id, label } of SURFACES) {
      test(`${label} (#/${route})`, async ({ page }, info) => {
        await seedTheme(page, theme)
        if (FIXED_CLOCK[route]) await page.clock.setFixedTime(new Date(FIXED_CLOCK[route]))
        for (const stub of STUBBED_READS[route] ?? []) {
          info.annotations.push({ type: 'stubbed-read', description: `${stub.url.source}: ${stub.why}` })
          await page.route(stub.url, (r) => r.fulfill({ json: stub.json }))
        }
        await gotoRoute(page, route)
        await checkSurface(page, info, { where: `#/${route} (${theme})`, theme, kind: 'route', route }, fileId(id ?? route))
      })
    }

    // The dashboard's schedule card in its "every recent fire was held by a gate" state. The run's
    // home is seeded with every system trigger switched off (`e2e/fixtures/home/`), so the plain
    // `#/dashboard` row above always shows the card empty; this row answers the card's one read
    // with a single held fire, so the held state is checked too, in a state that does not move.
    // The answer is a stub on this page alone: writing the fire into the home would change what
    // every other spec sharing the gateway reads. The title carries no `(#/`, so the route-scan
    // count in `assert-no-route-skips.mjs` stays one scan per declared route.
    test('Home, every recent fire held [held fire]', async ({ page }, info) => {
      await seedTheme(page, theme)
      await page.route(/\/api\/triggers\/history(\?.*)?$/, (route) => {
        const at = new Date(Date.now() - 5 * 60_000).toISOString()
        const held = {
          id: 'e2e-held-fire', trigger_id: 'schedule:e2e-held', trigger_name: 'Morning check',
          outcome: 'skipped_gate', reason: 'held: inside quiet hours', weight: 'ledger',
          scheduled_for: at, started_at: at, finished_at: at, duration_secs: 0, run_id: '',
          mutated: false, counters: {}, incomplete: false, acted_on: false, dismissed: false,
        }
        return route.fulfill({
          json: {
            runs: [held], total: 1, kinds: ['schedule'], summaries: 0,
            did_ids: [], suppressed_ids: [held.id], suppressed: 1, schedule_total: 1,
            outcomes: { skipped_gate: 1 },
          },
        })
      })
      await gotoRoute(page, 'dashboard')
      await expect(page.getByText('every recent fire was held by a gate'), 'the stub reached the card').toBeVisible()
      await checkSurface(
        page,
        info,
        { where: `#/dashboard, one fire held (${theme})`, theme, kind: 'route', route: 'dashboard' },
        'dashboard-held-fire',
      )
    })

    for (const opener of OPENERS) {
      test(`${opener.label} [opened]`, async ({ page }, info) => {
        await seedTheme(page, theme)
        await gotoRoute(page, opener.route)
        const before = await page.evaluate(() => document.querySelectorAll('*').length)
        const opened = await opener.open(page)
        // A recipe whose target is absent must not report a clean surface: it skips with its reason.
        test.skip(opened !== true, opened === true ? '' : `${opener.label}: ${opened.skip}`)
        await page.waitForTimeout(700)
        await assertMounted(page, before, opener.label)
        await checkSurface(
          page,
          info,
          { where: `${opener.label} [opened] (${theme})`, theme, kind: 'opener', route: opener.route, opener: opener.label },
          `opened-${fileId(opener.label)}`,
        )
      })
    }
  })
}
