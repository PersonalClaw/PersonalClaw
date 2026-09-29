import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { api, type ScheduleJob } from '../../lib/api'
import { draftToPayload, toDraft } from './ScheduleForm'
import { ScheduleDetail } from './ScheduleDetail'

// ── An agent schedule keeps the folder its agent works in, through every edit ──────────────────────
//
// The Invoke Agent action takes a working folder (`cwd`): the folder its agent works in, so its file
// tools reach the files there. The create page draws the field from the action's schema, but the
// schedule's own editor rebuilds the agent action from the fields it has — the prompt, the agent, the
// model — so a folder set at creation was dropped by the first save of any other field, and the
// action the owner allowed changed under her. The editor now reads it back, shows it, and sends it.

const JOB: ScheduleJob = {
  id: 'morning-brief', name: 'Morning brief', message: 'Summarise Calendar/family.ics.', enabled: true,
  schedule: 'at 07:15 on weekdays', cron_expr: '15 7 * * 1-5', every_secs: null,
  action: { provider: 'invoke-agent', config: { task_template: 'Summarise Calendar/family.ics.', cwd: '~/Documents' } },
  cwd: '~/Documents',
  last_run_ts: null, last_run_status: '', last_status: 'ok', run_count: 0,
  next_run_ts: null, is_running: false, revision: 's1',
}

afterEach(() => vi.unstubAllGlobals())

describe('the schedule editor and the working folder', () => {
  it('reads the folder back and sends it with the agent fields', () => {
    const draft = toDraft(JOB)
    expect(draft.mode).toBe('agent')
    expect(draft.cwd).toBe('~/Documents')
    expect(draftToPayload({ ...draft, name: 'Weekday brief' }).cwd).toBe('~/Documents')
    expect(draftToPayload({ ...draft, cwd: '  ~/Documents/Calendar ' }).cwd).toBe('~/Documents/Calendar')
  })

  it('puts it in the agent action it saves', async () => {
    // What reaches the gateway: the edit's body, as `api.updateSchedule` sends it.
    const sent: Record<string, unknown>[] = []
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      sent.push(JSON.parse(String(init.body)) as Record<string, unknown>)
      return new Response(JSON.stringify({ ok: true, trigger: {} }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    await api.updateSchedule('morning-brief', draftToPayload({ ...toDraft(JOB), name: 'Weekday brief' }), 's1')
    expect(sent).toHaveLength(1)
    expect(sent[0].action).toEqual({
      provider: 'invoke-agent',
      config: { task_template: 'Summarise Calendar/family.ics.', agent: '', model: '', approval_mode: '', cwd: '~/Documents' },
    })
    // Folded into the action, never sent beside it: the gateway reads the action.
    expect('cwd' in sent[0]).toBe(false)
  })

  it('offers the field while editing an agent schedule', () => {
    render(<ScheduleDetail job={JOB} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()} editing onEditingChange={vi.fn()} />)
    expect((screen.getByDisplayValue('~/Documents') as HTMLInputElement).name).toBe('working-folder')
  })

  it("shows it on the schedule's panel", () => {
    render(<ScheduleDetail job={JOB} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()} editing={false} onEditingChange={vi.fn()} />)
    expect(screen.getByTitle('The folder its agent works in').textContent).toContain('~/Documents')
  })
})
