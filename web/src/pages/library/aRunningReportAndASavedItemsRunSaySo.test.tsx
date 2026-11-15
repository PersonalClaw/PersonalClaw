/** A report being written and an intent checking what you saved each say so (red-first
 *  14): the report's bar reads "Writing a new report. The last one stays until it's done." with Run
 *  now off and its reason; the intent's set-up row and its findings bar read "Checking saved items ·
 *  1,240 of 4,741" with Stop. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { answers, resetGateway, row } from '../../test/libraryHarness'

vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { LibraryPreview } = await import('../../ui/LibraryPreview')
const { SetupPanel } = await import('./setup/SetupPanel')
const { resetDataStore } = await import('../../lib/data/store')

const actions = { navigate: vi.fn(), writeUps: new Map<string, string>(), projects: [], chatTitle: () => '', pickChat: vi.fn(), pickProject: vi.fn() }
const REPORT = {
  id: 'rpt-1a2b3c4d', name: 'Weekly perf digest', enabled: true, last_status: 'ok', last_run_ts: null, running: true,
  schedule: { kind: 'cron', cron_expr: '0 8 * * 1' }, schedule_shown: { words: 'Every Monday at 8:00', timezone: '', next_run_at: '' },
  source: { tags: [], window_secs: 0 }, missing_tags: [], finding_item_id: '',
}
const INTENT = { id: 'homelab', goal: 'Anything that could improve my homelab', enabled: true, outcome_count: 14, revision: 'r1' }

beforeEach(() => {
  resetDataStore(); resetGateway()
  answers.researchReports = { reports: [REPORT] }
  answers.knowledgeIntents = { intents: [INTENT] }
  answers.knowledgeSources = { sources: [], kinds: [] }
  answers.intentRun = { status: 'running', done: 1240, total: 4741, kept: 3 }
  answers.knowledgeIntentOutcomesPage = { intent: INTENT, outcomes: [], before: '' }
})
afterEach(cleanup)

describe('a running report and a saved-items run say so', () => {
  it('🔑 the report\'s bar says it is writing, with Run now off and why', async () => {
    render(<LibraryPreview item={row('report:rpt-1a2b3c4d', 'Weekly perf digest', { running: true })} actions={actions} />)
    expect(await screen.findByText("Writing a new report. The last one stays until it's done.")).toBeTruthy()
    const run = screen.getByRole('button', { name: /Run now/ })
    expect(run.getAttribute('aria-disabled') === 'true' || (run as HTMLButtonElement).disabled).toBe(true)
    // The reason rides the button's title, its tooltip and its description (`Button`'s `disabledReason`).
    expect(run.getAttribute('title') ?? '').toContain("It's already writing one")
  })

  it('🔑 the intent\'s set-up row counts the check, with Stop', async () => {
    render(<SetupPanel nav={{ form: vi.fn(), item: vi.fn() }} onClose={vi.fn()} />)
    expect(await screen.findByText('Checking saved items · 1,240 of 4,741')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeTruthy()
    expect(screen.getByText('You can keep working. New findings show up here as they come.')).toBeTruthy()
  })

  it('🔑 the findings log\'s bar counts it too, with Stop', async () => {
    render(<LibraryPreview item={row('findings:homelab', INTENT.goal)} actions={actions} />)
    expect(await screen.findByText('Checking saved items · 1,240 of 4,741')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeTruthy()
  })

  it('🔴 control: a finished check says the intent checks each new item', async () => {
    answers.intentRun = { status: 'done', done: 4741, total: 4741, kept: 3 }
    render(<LibraryPreview item={row('findings:homelab', INTENT.goal)} actions={actions} />)
    expect(await screen.findByText('Your intent checks each new item you add.')).toBeTruthy()
    expect(screen.queryByText(/Checking saved items/)).toBeNull()
  })
})
