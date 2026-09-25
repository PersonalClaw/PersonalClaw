import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ActionProvider, ScheduleJob, TriggerRunResult } from '../../lib/api'

// ── A dry run shows its result and never enters "Running…" (failure mode 3) ──────────────────────
//
// Measured on a live gateway (c1b-069 / c1b-072): `POST …/run {dry_run: true}` answered 200 in under
// 100ms, executed nothing and recorded nothing — and the Run button turned into "Running…". The panel
// had set its local "I just triggered a run" flag, whose watcher waits for `last_run_ts` to advance.
// A dry run never moves it, so on a DISABLED trigger the button still read "Running…" 110s later, and
// on an enabled one it cleared only when the next REAL scheduled fire happened to land. Meanwhile the
// note said "See history for the result" about a history row no code writes.
//
// The response IS the result. These assertions pin both halves: the buttons are idle the moment the
// request returns (no watcher, no poll), and what a real run WOULD do is on screen.

const { API } = vi.hoisted(() => ({
  API: {
    runSchedule: vi.fn(),
    triggerHistory: vi.fn(),
    triggerRunDetail: vi.fn(),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))

const { ScheduleDetail } = await import('./ScheduleDetail')

const NOTIFY: ActionProvider = {
  name: 'notify', display_name: 'Dashboard Notification', supports_blocking: false, invokes_model: false,
  settingsSchema: {
    type: 'object', required: ['title_template'],
    properties: {
      title_template: { type: 'string', 'x-meta': { label: 'Title' } },
      body_template: { type: 'string', 'x-meta': { label: 'Body' } },
      kind: { type: 'string', default: 'info', 'x-meta': { label: 'Kind' } },
    },
  },
}

const CONFIG = { title_template: 'Standup nudge: review Q4 tasks', body_template: 'Fired by the Standup nudge trigger.', kind: 'info' }

function job(over: Partial<ScheduleJob> = {}): ScheduleJob {
  return {
    id: 'clock:standup-nudge', name: 'Standup nudge', message: '', enabled: true,
    schedule: 'every 60s', every_secs: 60, cron_expr: null,
    action: { provider: 'notify', config: CONFIG },
    last_run_ts: 1790357800, last_run_status: 'success', last_status: 'ok', run_count: 3,
    next_run_ts: null, is_running: false, warnings: [], broken: [],
    ...over,
  }
}

function dryAnswer(enabled: boolean): TriggerRunResult {
  return {
    ok: true,
    name: 'Standup nudge',
    text: 'Dry run of clock:standup-nudge (Standup nudge).\n  nothing was executed.',
    would_run: { provider: 'notify', config: CONFIG },
    result: {
      plan: { enforced: ['incident', 'screen', 'budget', 'claim', 'capability'], bypassed: ['quiet', 'duty'], dry_run: true, executes: false },
      trigger: { id: 'clock:standup-nudge', enabled },
    },
  }
}

function mount(j: ScheduleJob, onChanged = vi.fn()) {
  render(
    <ScheduleDetail job={j} providers={[NOTIFY]} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={onChanged}
      editing={false} onEditingChange={vi.fn()} />,
  )
  return onChanged
}

beforeEach(() => {
  API.runSchedule.mockReset()
  API.triggerHistory.mockReset().mockResolvedValue({ runs: [], total: 0, supported: true })
  API.triggerRunDetail.mockReset()
})

describe('Dry run renders its response and waits for nothing', () => {
  it('the button is idle the moment the answer arrives — never "Running…"', async () => {
    API.runSchedule.mockResolvedValue(dryAnswer(true))
    const onChanged = mount(job())
    fireEvent.click(screen.getByRole('button', { name: /^dry run$/i }))

    await screen.findByText('Dry run — nothing was executed')
    expect(API.runSchedule).toHaveBeenCalledWith('clock:standup-nudge', true)
    expect(screen.queryByText(/Running/)).toBeNull()
    expect(screen.getByRole('button', { name: /^run now$/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /^dry run$/i })).toBeEnabled()
    // No completion watcher: the old path called `onChanged()` and then polled it every 2.5s.
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('says what a real run WOULD do, in the labels the create form uses', async () => {
    API.runSchedule.mockResolvedValue(dryAnswer(true))
    mount(job())
    fireEvent.click(screen.getByRole('button', { name: /^dry run$/i }))
    const result = await screen.findByRole('status')

    expect(result).toHaveTextContent('Dashboard Notification')
    expect(result).toHaveTextContent('Title')
    expect(result).toHaveTextContent('Standup nudge: review Q4 tasks')
    expect(result).toHaveTextContent('Fired by the Standup nudge trigger.')
    // `kind: 'info'` is the schema default — not something anyone chose, so not read back.
    expect(result).not.toHaveTextContent('Kind')
    expect(result).toHaveTextContent('Run now skips quiet hours and duty limits')
  })

  it('promises no history row that nothing writes', async () => {
    API.runSchedule.mockResolvedValue(dryAnswer(true))
    mount(job())
    fireEvent.click(screen.getByRole('button', { name: /^dry run$/i }))
    await screen.findByText('Dry run — nothing was executed')
    expect(screen.queryByText(/see history/i)).toBeNull()
  })

  it('a DISABLED trigger (the 110s case) is idle too, and the result says it stays off', async () => {
    API.runSchedule.mockResolvedValue(dryAnswer(false))
    mount(job({ enabled: false }))
    fireEvent.click(screen.getByRole('button', { name: /^dry run$/i }))
    const result = await screen.findByRole('status')
    expect(result).toHaveTextContent('switched off')
    expect(screen.queryByText(/Running/)).toBeNull()
  })

  it('a dry run that cannot even plan shows its reason instead of a preview', async () => {
    API.runSchedule.mockResolvedValue({ ok: false, text: 'Error: clock:standup-nudge has a parse error and cannot run.' })
    mount(job())
    fireEvent.click(screen.getByRole('button', { name: /^dry run$/i }))
    await screen.findByText(/has a parse error and cannot run/)
    expect(screen.queryByText('Dry run — nothing was executed')).toBeNull()
  })

  it('the result can be dismissed', async () => {
    API.runSchedule.mockResolvedValue(dryAnswer(true))
    mount(job())
    fireEvent.click(screen.getByRole('button', { name: /^dry run$/i }))
    await screen.findByText('Dry run — nothing was executed')
    fireEvent.click(screen.getByRole('button', { name: /dismiss the dry run result/i }))
    await waitFor(() => expect(screen.queryByText('Dry run — nothing was executed')).toBeNull())
  })
})

describe('a notify trigger shows what it sends, not an empty Command box (c1b-068)', () => {
  it('renders the action\'s own fields and no Command section', async () => {
    mount(job())
    expect(screen.queryByText('Command')).toBeNull()
    expect(screen.getByText('Settings')).toBeInTheDocument()
    expect(screen.getByText('Standup nudge: review Q4 tasks')).toBeInTheDocument()
    await screen.findByText('No runs recorded yet.')
  })

  it('an action with no config (the system digests) shows neither', async () => {
    mount(job({ action: { provider: 'notification-digest', config: {} } }))
    expect(screen.queryByText('Command')).toBeNull()
    expect(screen.queryByText('Settings')).toBeNull()
    await screen.findByText('No runs recorded yet.')
  })

  it('a real Command still gets its box (the vacuity leg)', async () => {
    mount(job({ command: 'rsync -a ~/data /backup', action: { provider: 'bash', config: { command: 'rsync -a ~/data /backup' } } }))
    expect(screen.getByText('Command')).toBeInTheDocument()
    expect(screen.getByText('rsync -a ~/data /backup')).toBeInTheDocument()
    await screen.findByText('No runs recorded yet.')
  })
})

describe('an advisory is readable in the panel, not only on hover', () => {
  it('prints the row\'s warnings in words', async () => {
    const warning = '60s is below the 900s floor for an LLM-invoking trigger; it will still run, but confirm this is intended'
    mount(job({ action: { provider: 'invoke-agent', config: { task_template: 'go' } }, warnings: [warning] }))
    expect(screen.getByRole('note')).toHaveTextContent(warning)
    await screen.findByText('No runs recorded yet.')
  })
})
