/** Home's "Your agents today" with work on it: one fixed evening the band's reads are answered with,
 *  in the page itself, so it writes nothing to the run's home and reads nothing another spec counts.
 *  The band's own spec reads it as a person meets it; the visual spec pins its look in goldens. */
import type { Page } from '@playwright/test'

const MIN = 60_000
/** The evening the band is read at: 18:20 on the run's clock (its zone is UTC). Pinned, so a failure
 *  two hours back is still today whatever time the run starts, and nothing drifts between runs. */
export const NOW = Date.parse('2026-11-04T18:20:00Z')

/** One evening, measured from `now`. */
export function evening(now: number) {
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
    // Two runs inside both a wide screen's four hours and a phone's three, the later one late enough in
    // the window that its name is drawn before its mark, facing the earlier one's.
    week: { start: iso(now), end: iso(now + 2 * 24 * 60 * MIN), occurrences: [
      { trigger_id: 'schedule:e2e-plants', trigger_name: 'Water the plants', at: (now + 85 * MIN) / 1000, suppressed_by: '', reason: '' },
      { trigger_id: 'schedule:e2e-summary', trigger_name: 'Evening summary', at: (now + 170 * MIN) / 1000, suppressed_by: '', reason: '' },
    ] },
    agents: { agents: [], default_agent: 'PersonalClaw' },
    budget: { spent_dollars: 1.79, spent_tokens: 0, unpriced_calls: 0, max_dollars_per_day: 5 },
  }
}

/** Pins the page's clock to the evening, and answers each of the band's reads from it. */
export async function answerTheBandsReads(page: Page): Promise<void> {
  await page.clock.setFixedTime(new Date(NOW))
  const day = evening(NOW)
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
  // Needs You counts an open note and a skill proposal too: answered empty, so the run's home can't
  // add to what the band counts.
  await page.route(/\/api\/inbox\/open(\?.*)?$/, (r) => r.fulfill(json([])))
  await page.route(/\/api\/skills\/proposals(\?.*)?$/, (r) => r.fulfill(json({ proposals: [], lastReview: null })))
}

/** The band, by its heading. */
export const band = (page: Page) => page.locator('section').filter({ has: page.getByRole('heading', { name: 'Your agents today' }) })
