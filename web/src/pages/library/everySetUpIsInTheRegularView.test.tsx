/** Every set-up is in the regular view: with no power-user view, by keyboard,
 *  an intent is created, paused, run on saved items (and stopped; off with its reason while paused),
 *  given a report and deleted; a feed is paused, checked now and removed; a report is run and deleted;
 *  and two items that disagree are decided from either one. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answers, calls, resetGateway } from '../../test/libraryHarness'

vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<typeof import('../../ui/dialog')>()),
  confirm: () => Promise.resolve(true),
  confirmDestructive: () => Promise.resolve(true),
}))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { SetupPanel } = await import('./setup/SetupPanel')
const { IntentForm, typesOf } = await import('./setup/IntentForm')
const { ApiError } = await import('../../lib/api')
const { ComparePane } = await import('./ComparePane')
const { resetDataStore } = await import('../../lib/data/store')

const INTENT = { id: 'homelab', goal: 'Anything that could improve my homelab', enabled: true, enabled_for: [], propose_skill: true, outcome_count: 14, revision: 'r1' }
const REPORT = {
  id: 'rpt-1a2b3c4d', name: 'Weekly perf digest', enabled: true, last_status: 'ok', last_run_ts: null, running: false,
  schedule: { kind: 'cron', cron_expr: '0 8 * * 1' }, schedule_shown: { words: 'Every Monday at 8:00', timezone: '', next_run_at: '' },
  source: { tags: ['perf'], window_secs: 0 }, missing_tags: [],
}
const FEED = {
  id: 's1', name: "Simon's blog", provider: 'rss', kind: 'feed', spec: { url: 'https://simon.example/feed' }, budget: {}, revision: 'v1',
  enrichment: 'full', poll_interval_secs: 3600, item_type: 'bookmark', enabled: true, poll_every_secs: 3600, enrolled: true,
  last_poll_at: null, remediation: { kind: '', guidance: '', detail: '', action: '' },
}

beforeEach(() => {
  resetDataStore(); resetGateway()
  answers.knowledgeIntents = { intents: [INTENT] }
  answers.researchReports = { reports: [REPORT] }
  answers.knowledgeSources = { sources: [FEED], kinds: [] }
  answers.intentRun = new Error('no run')
})
afterEach(cleanup)

const panel = () => render(<SetupPanel nav={{ form: vi.fn(), item: vi.fn() }} onClose={vi.fn()} />)
const press = async (name: string | RegExp) => { await userEvent.click(await screen.findByRole('button', { name })) }

describe('an intent, set up in the regular view', () => {
  it('🔑 is created from New intent\'s form, by keyboard, through the intent\'s one writer', async () => {
    answers.createKnowledgeIntent = { id: 'garden', intents: [] }
    answers.intentCandidates = { total: 4741 }
    const saved = vi.fn()
    render(<IntentForm onClose={vi.fn()} onSaved={saved} />)
    await userEvent.keyboard('Ideas for the spring beds')
    await press('Start looking')
    await waitFor(() => expect(saved).toHaveBeenCalledWith('garden'))
    expect(calls.createKnowledgeIntent?.[0]?.[0]).toEqual({ goal: 'Ideas for the spring beds', enabled: true, enabled_for: [], propose_skill: false })
    expect(calls.startIntentRun, 'checking saved items is off by default').toBeUndefined()
  })

  it('🔑 a new intent can check what is already saved, and write a report from what it finds', async () => {
    answers.createKnowledgeIntent = { id: 'garden', intents: [] }
    answers.intentCandidates = { total: 4741 }
    answers.startIntentRun = { total: 4741 }
    answers.createResearchReport = { id: 'rpt-9' }
    render(<IntentForm onClose={vi.fn()} onSaved={vi.fn()} />)
    await userEvent.keyboard('Ideas for the spring beds')
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Also check the 4,741 items already in your library' }))
    await userEvent.click(screen.getByRole('switch', { name: 'Write a report from what it finds' }))
    await press('Start looking')
    await waitFor(() => expect(calls.startIntentRun).toEqual([['garden']]))
    expect(calls.createResearchReport?.[0]?.[0]).toEqual(expect.objectContaining({
      name: 'Report: Ideas for the spring beds', source: { tags: [], window_secs: 0, intent: 'garden' },
      schedule: { kind: 'cron', cron_expr: '0 8 * * 1' },
    }))
  })

  it('🔑 an edit to its words keeps the item types it was given', async () => {
    answers.saveKnowledgeIntent = { id: 'homelab', intents: [] }
    answers.intentCandidates = { total: 12 }
    const notesOnly = { ...INTENT, enabled_for: ['note'] }
    render(<IntentForm intent={notesOnly} onClose={vi.fn()} onSaved={vi.fn()} />)
    const goal = await screen.findByRole('textbox', { name: /Look out for/ })
    await userEvent.clear(goal)
    await userEvent.type(goal, 'Homelab power use')
    await press('Save')
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(calls.saveKnowledgeIntent[0], 'plain notes, not every kind of note').toEqual([{ id: 'homelab', goal: 'Homelab power use', enabled: true, enabled_for: ['note'], propose_skill: true }, 'r1'])
  })

  it('🔑 a kinds change on an intent given types saves the kinds as ticked', async () => {
    answers.saveKnowledgeIntent = { id: 'homelab', intents: [] }
    answers.intentCandidates = { total: 12 }
    render(<IntentForm intent={{ ...INTENT, enabled_for: ['note'] }} onClose={vi.fn()} onSaved={vi.fn()} />)
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Links' }))
    await press('Save')
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(calls.saveKnowledgeIntent[0], 'Notes and Links, as ticked').toEqual(
      [{ id: 'homelab', goal: INTENT.goal, enabled: true, enabled_for: typesOf(['notes', 'links']), propose_skill: true }, 'r1'])
  })

  it('🔑 after a refused edit is discarded, an edit to its words saves the types stored now', async () => {
    // Another tab added Links since the form read the intent: the first save is refused over r1.
    answers.knowledgeIntents = { intents: [{ ...INTENT, enabled_for: ['note', 'bookmark'], revision: 'r2' }] }
    answers.saveKnowledgeIntent = (_record: unknown, base: string) => (base === 'r1'
      ? Promise.reject(new ApiError('This write replaces it, which changed after the copy it was built from was read.', 409, 'stale_write'))
      : { id: 'homelab', intents: [] })
    answers.intentCandidates = { total: 12 }
    render(<IntentForm intent={{ ...INTENT, enabled_for: ['note'] }} onClose={vi.fn()} onSaved={vi.fn()} />)
    const goal = await screen.findByRole<HTMLInputElement>('textbox', { name: /Look out for/ })
    await userEvent.clear(goal)
    await userEvent.type(goal, 'Homelab power use')
    await press('Save')
    await press('Discard my change')
    await waitFor(() => expect(goal.value, 'Discard puts back what is stored').toBe(INTENT.goal))
    await userEvent.clear(goal)
    await userEvent.type(goal, 'Homelab power use')
    await press('Save')
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(2))
    expect(calls.saveKnowledgeIntent[1], "the other tab's Links stay").toEqual(
      [{ id: 'homelab', goal: 'Homelab power use', enabled: true, enabled_for: ['note', 'bookmark'], propose_skill: true }, 'r2'])
  })

  it('🔑 is paused from its row\'s switch, named by its words and its row, and saves over its revision', async () => {
    answers.saveKnowledgeIntent = { id: 'homelab', intents: [] }
    panel()
    await userEvent.click(await screen.findByRole('switch', { name: `Checking new items: ${INTENT.goal}` }))
    await waitFor(() => expect(calls.saveKnowledgeIntent).toHaveLength(1))
    expect(calls.saveKnowledgeIntent[0]).toEqual([{ id: 'homelab', goal: INTENT.goal, enabled: false, enabled_for: [], propose_skill: true }, 'r1'])
  })

  it('🔑 runs on saved items, and says it is checking them, with Stop', async () => {
    answers.startIntentRun = { total: 4741 }
    let run: unknown = new Error('no run')
    answers.intentRun = () => (run instanceof Error ? Promise.reject(run) : run)
    answers.stopIntentRun = {}
    panel()
    await userEvent.click(await screen.findByRole('button', { name: `More for ${INTENT.goal}` }))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Run on saved items/ }).catch(() => screen.getByText('Run on saved items')))
    await waitFor(() => expect(calls.startIntentRun).toEqual([['homelab']]))
    run = { status: 'running', done: 1240, total: 4741, kept: 3 }
    cleanup(); resetDataStore(); panel()
    expect(await screen.findByText('Checking saved items · 1,240 of 4,741')).toBeTruthy()
    await press('Stop')
    await waitFor(() => expect(calls.stopIntentRun).toEqual([['homelab']]))
  })

  it('🔑 while paused, Run on saved items is off, saying "Resume it to check saved items."', async () => {
    answers.knowledgeIntents = { intents: [{ ...INTENT, enabled: false }] }
    panel()
    await userEvent.click(await screen.findByRole('button', { name: `More for ${INTENT.goal}` }))
    const row = (await screen.findByText('Run on saved items')).closest('button')!
    expect(row.disabled || row.getAttribute('aria-disabled') === 'true').toBe(true)
    expect(screen.getByText('Resume it to check saved items.')).toBeTruthy()
  })

  it('is deleted from its ⋯, saying what goes', async () => {
    answers.deleteKnowledgeIntent = {}
    panel()
    await userEvent.click(await screen.findByRole('button', { name: `More for ${INTENT.goal}` }))
    await userEvent.click(await screen.findByText('Delete intent…'))
    await waitFor(() => expect(calls.deleteKnowledgeIntent).toEqual([['homelab']]))
  })
})

describe('a feed and a report, set up in the regular view', () => {
  it('🔑 a feed is paused, checked now and removed', async () => {
    answers.updateKnowledgeSource = { source: FEED }
    answers.checkKnowledgeSource = {}
    answers.deleteKnowledgeSource = {}
    panel()
    await userEvent.click(await screen.findByRole('switch', { name: `Watching: ${FEED.name}` }))
    await waitFor(() => expect(calls.updateKnowledgeSource).toEqual([['s1', { enabled: false }]]))
    await userEvent.click(screen.getByRole('button', { name: `More for ${FEED.name}` }))
    await userEvent.click(await screen.findByText('Check now'))
    await waitFor(() => expect(calls.checkKnowledgeSource).toEqual([['s1']]))
    await userEvent.click(screen.getByRole('button', { name: `More for ${FEED.name}` }))
    await userEvent.click(await screen.findByText('Remove…'))
    await waitFor(() => expect(calls.deleteKnowledgeSource).toEqual([['s1']]))
  })

  it('🔑 a report is run and deleted', async () => {
    answers.runResearchReport = { ok: true, outcome: 'wrote', result: '' }
    answers.deleteResearchReport = {}
    panel()
    await userEvent.click(await screen.findByRole('button', { name: `More for ${REPORT.name}` }))
    await userEvent.click(await screen.findByText('Run now'))
    await waitFor(() => expect(calls.runResearchReport).toEqual([['rpt-1a2b3c4d']]))
    await userEvent.click(screen.getByRole('button', { name: `More for ${REPORT.name}` }))
    await userEvent.click(await screen.findByText('Delete report…'))
    await waitFor(() => expect(calls.deleteResearchReport).toEqual([['rpt-1a2b3c4d']]))
  })
})

describe('two items that disagree, decided in the regular view', () => {
  const CONFLICT = {
    id: 'c0ffee', choice: '', item_id: 'k-vendor', item_title: 'Vendor pricing 2026', left_item: 'k-vendor', right_item: 'k-q3',
    left_claim: 'The Pro plan costs $40 a month.', right_claim: 'The Pro plan costs $30 a month.', kind: 'number', prefer: '', detail: '', confidence: 1,
  }
  beforeEach(() => {
    answers.knowledgeConflicts = { conflicts: [CONFLICT], count: 1 }
    answers.knowledgeItem = (id: string) => ({ id, title: id === 'k-q3' ? 'Q3 review notes' : 'Vendor pricing 2026', created_at: '2026-10-30T00:00:00Z' })
    answers.decideKnowledgeConflict = { id: 'c0ffee', choice: 'k-q3' }
  })

  it.each([['Vendor pricing 2026'], ['Q3 review notes']])('🔑 from %s, the call names an item, never a side', async (from) => {
    render(<ComparePane cid="c0ffee" fromTitle={from} onBack={vi.fn()} onOpen={vi.fn()} />)
    expect(await screen.findByText('“The Pro plan costs $30 a month.”')).toBeTruthy()
    await press('Go by Q3 review notes')
    await waitFor(() => expect(calls.decideKnowledgeConflict).toEqual([['c0ffee', 'k-q3']]))
  })

  it('both can be right', async () => {
    render(<ComparePane cid="c0ffee" fromTitle="Vendor pricing 2026" onBack={vi.fn()} onOpen={vi.fn()} />)
    await press('Both can be right')
    await waitFor(() => expect(calls.decideKnowledgeConflict).toEqual([['c0ffee', 'both']]))
  })

  it('a decided pair says what was chosen, with Change', async () => {
    answers.knowledgeConflicts = { conflicts: [{ ...CONFLICT, choice: 'k-vendor' }], count: 1 }
    render(<ComparePane cid="c0ffee" fromTitle="Q3 review notes" onBack={vi.fn()} onOpen={vi.fn()} />)
    expect(await screen.findByText(/You chose to go by “Vendor pricing 2026”\./)).toBeTruthy()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Change' })) })
    expect(screen.getByRole('button', { name: 'Both can be right' })).toBeTruthy()
  })
})
