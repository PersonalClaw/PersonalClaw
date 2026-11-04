/** Home › Overview's "Your agents today", in the built app: a row per agent on the hours around
 *  now, each with its state in words, an ask linked to Attention and answered nowhere else, and a
 *  loop that runs to a cycle and never to a guessed time. The band's reads are answered in the page
 *  itself, so it writes nothing to the run's home and reads nothing another spec counts. The shots
 *  it attaches, at 1440 and 390 in both themes, are the band as a person meets it. */
import { test, expect, type Page } from '@playwright/test'
import { gotoRoute, seedTheme } from './helpers'

const MIN = 60_000

/** One evening, measured from the moment the page is answered. */
function evening(now: number) {
  const iso = (ms: number) => new Date(ms).toISOString()
  const ago = (min: number) => iso(now - min * MIN)
  const approval = {
    id: 'e2e-band-ap', request_id: 'e2e-band-ap', source: 'chat', tool: 'write_file',
    tool_input: '{"path": "~/Documents/trips/lisbon.md"}', tool_purpose: '', session: 'e2e-lisbon',
    ts: (now - 2 * MIN) / 1000, session_title: 'Lisbon trip, 12–16 May', agent: '', risk: 'caution',
    grant_agent: '', source_label: 'chat “Lisbon trip, 12–16 May”', confirms: [], work: 'chat:e2e-lisbon',
    step: '', room: '',
  }
  return {
    day: {
      since: ago(240), until: iso(now), cut: 0,
      agents: [
        { agent: '', blocks: [
          { start: ago(90), end: ago(80), session: 'dashboard:e2e-lisbon', source: 'chat' },
          { start: ago(45), end: ago(35), session: 'dashboard:e2e-sam', source: 'chat' },
          { start: ago(20), end: ago(2), session: 'dashboard:e2e-lisbon', source: 'chat' },
        ], before: null },
        { agent: 'Code reviewer', blocks: [{ start: ago(80), end: ago(12), session: 'loop-e2e-fix', source: 'loop' }], before: ago(260) },
        { agent: 'Researcher', blocks: [{ start: ago(29), end: ago(1), session: 'loop-e2e-desks', source: 'loop' }], before: null },
        { agent: 'Release helper', blocks: [{ start: ago(130), end: ago(128), session: 'sub-e2e-rel', source: 'subagent' }], before: null },
        // Earlier today, before both the four hours a wide screen draws and the three a phone does.
        { agent: 'Inbox triage', blocks: [], before: ago(300) },
      ],
    },
    running: {
      running: [
        { work: 'chat:e2e-lisbon', kind: 'chat', session: 'e2e-lisbon', phase: 'approval', line: 'Needs your OK',
          since: ago(2), name: 'Lisbon trip, 12–16 May', agent: '', started_at: ago(20), route: '#/chat/e2e-lisbon',
          blocked: false, waiting: [approval.id] },
        { work: 'loop:e2e-desks', kind: 'loop', session: '', phase: 'thinking', line: 'Reading a review',
          since: ago(1), name: 'Compare three standing desks', agent: 'Researcher', started_at: ago(29),
          route: '#/loops/e2e-desks', blocked: false, waiting: [] },
        { work: 'loop:e2e-fix', kind: 'loop', session: '', phase: 'input', line: 'Waiting for your answer',
          since: ago(12), name: 'Fix flaky tests', agent: 'Code reviewer', started_at: ago(80),
          route: '#/loops/e2e-fix', blocked: false, waiting: ['loop_question:e2e-fix'] },
      ],
      finished: [],
    },
    approvals: [approval],
    waiting: {
      waiting: [
        { id: `approval:${approval.id}`, kind: 'approval', work: approval.work, session: approval.session, brief: { ...approval },
          consequence: 'changes', risk: 'caution', asked_at: ago(2), age_secs: 120, expires_at: null, current: true,
          interrupt: 'deliver', answer: { method: 'POST', path: `/api/approvals/${approval.id}/{answer}`, takes: ['approve', 'reject'], body: {} },
          route: '#/chat/e2e-lisbon' },
        { id: 'loop_question:e2e-fix', kind: 'loop_question', work: 'loop:e2e-fix', session: '', brief: { merge: true },
          consequence: 'changes', risk: '', asked_at: ago(12), age_secs: 720, expires_at: null, current: true,
          interrupt: 'deliver', answer: { method: 'POST', path: '/api/loops/e2e-fix/merge', takes: [], body: {} },
          route: '#/loops/e2e-fix' },
      ],
      unreadable: [],
    },
    loops: [
      { id: 'e2e-desks', kind: 'research', name: 'Compare three standing desks', task: 'Compare three standing desks',
        status: 'running', agent: 'Researcher', model: '', max_cycles: 8, total_cycles: 3, completed_at: null,
        created_at: (now - 30 * MIN) / 1000, started_at: (now - 29 * MIN) / 1000, attended: false },
      { id: 'e2e-fix', kind: 'code', name: 'Fix flaky tests', task: 'Fix flaky tests', status: 'needs_input',
        agent: 'Code reviewer', model: '', max_cycles: 0, total_cycles: 1, completed_at: null,
        created_at: (now - 90 * MIN) / 1000, started_at: (now - 80 * MIN) / 1000, attended: true },
    ],
    spawn: { agents: [{ id: 'e2e-rel', task: 'cut the release', title: 'Release helper', done: true, parent: 'loop-e2e-fix',
      agent: 'Release helper', started: (now - 130 * MIN) / 1000, ended: (now - 128 * MIN) / 1000,
      result: '', error: 'Couldn’t reach api.github.com: not on your allowed hosts' }] },
    week: { start: iso(now), end: iso(now + 2 * 24 * 60 * MIN), occurrences: [
      { trigger_id: 'schedule:e2e-summary', trigger_name: 'Evening summary', at: (now + 190 * MIN) / 1000, suppressed_by: '', reason: '' },
    ] },
    agents: { agents: [], default_agent: 'PersonalClaw' },
    budget: { spent_dollars: 1.79, spent_tokens: 0, unpriced_calls: 0, max_dollars_per_day: 5 },
  }
}

async function answerTheBandsReads(page: Page): Promise<void> {
  const day = evening(Date.now())
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  await page.route(/\/api\/agents\/day(\?.*)?$/, (r) => r.fulfill(json(day.day)))
  await page.route(/\/api\/running(\?.*)?$/, (r) => r.fulfill(json(day.running)))
  await page.route(/\/api\/approvals(\?.*)?$/, (r) => r.fulfill(json(day.approvals)))
  await page.route(/\/api\/waiting(\?.*)?$/, (r) => r.fulfill(json(day.waiting)))
  await page.route(/\/api\/loops(\?.*)?$/, (r) => r.fulfill(json({ loops: day.loops })))
  await page.route(/\/api\/spawn(\?.*)?$/, (r) => r.fulfill(json(day.spawn)))
  await page.route(/\/api\/triggers\/week(\?.*)?$/, (r) => r.fulfill(json(day.week)))
  await page.route(/\/api\/agents(\?.*)?$/, (r) => r.fulfill(json(day.agents)))
  await page.route(/\/api\/usage\/budget(\?.*)?$/, (r) => r.fulfill(json(day.budget)))
}

const band = (page: Page) => page.locator('section').filter({ has: page.getByRole('heading', { name: 'Your agents today' }) })

test.describe('Your agents today', () => {
  test('reads as a person meets it', async ({ page }) => {
    await answerTheBandsReads(page)
    await gotoRoute(page, 'dashboard?view=overview')
    const list = band(page).getByRole('list', { name: 'Your agents' })
    await expect(list).toBeVisible()

    const rows = list.getByRole('listitem')
    await expect(rows.first()).toHaveAccessibleName(/^PersonalClaw, waiting on you: /)
    await expect(rows.nth(1)).toHaveAccessibleName(/^Code reviewer, waiting on you: /)
    await expect(list.getByRole('listitem', { name: /^Release helper, failed: Failed: Couldn’t reach api\.github\.com/ })).toBeVisible()
    await expect(list.getByRole('listitem', { name: /^Researcher, working: / })).toBeVisible()
    await expect(list.getByRole('listitem', { name: /^Scheduled, agent not named: / })).toBeVisible()

    const answer = list.getByRole('link', { name: 'Answer in Attention' }).first()
    await expect(answer).toHaveAttribute('href', /^#\/dashboard\?view=attention&open=waiting%3Aapproval%3Ae2e-band-ap$/)
    await expect(band(page).getByRole('button', { name: /^(approve|allow|deny)/i })).toHaveCount(0)
    await expect(band(page).getByText('Runs to cycle 8')).toBeVisible()
    await expect(band(page).getByText(/On its own today: \$1\.79 of \$5/)).toBeVisible()

    await answer.click()
    await expect(page).toHaveURL(/view=attention/)
  })

  for (const theme of ['light', 'dark'] as const) {
    // A phone's height leaves the band under the fixed bottom bar, which a shot of it would draw over.
    for (const [w, h, tag] of [[1440, 900, 'desktop'], [390, 1600, 'mobile']] as const) {
      test(`the band at ${w} in ${theme}`, async ({ page }, info) => {
        await page.setViewportSize({ width: w, height: h })
        await seedTheme(page, theme)
        await answerTheBandsReads(page)
        await gotoRoute(page, 'dashboard?view=overview')
        await expect(band(page).getByRole('list', { name: 'Your agents' })).toBeVisible()
        const file = info.outputPath(`agents-band-${tag}-${theme}.png`)
        await band(page).screenshot({ path: file })
        await info.attach(`agents-band-${tag}-${theme}.png`, { path: file, contentType: 'image/png' })
      })
    }
  }
})
