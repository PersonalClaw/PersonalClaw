import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { api, type ScheduleJob } from '../../lib/api'
import { draftToPayload, toDraft } from './ScheduleForm'
import { ScheduleDetail } from './ScheduleDetail'

// ── An edit of an agent schedule keeps what its agent may do ────────────────────────────────────────
//
// The schedule editor built its Invoke Agent action from the five fields it drew — the prompt, the
// agent, the model, the approval and the working folder — and the gateway replaced the stored action
// with it. So moving an automation's time dropped the files its agent may change, its capability and
// its turn cap, and it ran read-only from then on. The editor now draws those three as well and sends
// every field it shows: a value as it is, a field the user emptied as cleared (`null`). The gateway
// puts what an edit sends over the stored action, so a setting the editor does not show stays as it
// was (`triggers/action_edit.py`).

const STORED = {
  task_template: 'Tidy the kitchen note from the new receipts.',
  agent: 'researcher',
  model: 'fixture-model',
  approval_mode: 'auto',
  cwd: '~/Documents',
  writes: ['~/Notes/kitchen.md', '~/Notes/pantry.md'],
  capability: 'mutating',
  max_turns: 7,
}

function job(config: Record<string, unknown> = STORED, provider = 'invoke-agent'): ScheduleJob {
  return {
    id: 'kitchen', name: 'Kitchen note', message: String(config.task_template ?? ''), enabled: true,
    schedule: 'at 09:00 on weekdays', cron_expr: '0 9 * * 1-5', every_secs: null,
    action: { provider, config },
    agent: (config.agent as string) ?? null, model: (config.model as string) ?? null,
    approval_mode: (config.approval_mode as string) ?? null, cwd: (config.cwd as string) ?? null,
    command: (config.command as string) ?? null,
    last_run_ts: null, last_run_status: '', last_status: 'ok', run_count: 0,
    next_run_ts: null, is_running: false, revision: 's1',
  }
}

/** The bodies the gateway is sent, as the browser sends them. */
function wire(): Record<string, unknown>[] {
  const sent: Record<string, unknown>[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
    sent.push(JSON.parse(String(init.body)) as Record<string, unknown>)
    return new Response(JSON.stringify({ ok: true, trigger: {} }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
  return sent
}

afterEach(() => vi.unstubAllGlobals())

describe('the schedule editor and what its agent may do', () => {
  it('sends every stored setting it shows, unchanged, when only the time moves', async () => {
    const sent = wire()
    await api.updateSchedule('kitchen', draftToPayload({ ...toDraft(job()), cron: '30 7 * * 1-5' }), 's1')

    expect(sent).toHaveLength(1)
    expect(sent[0].cron).toBe('30 7 * * 1-5')
    expect(sent[0].action).toEqual({ provider: 'invoke-agent', config: STORED })
  })

  it('sends a field the user emptied as cleared, and nothing for a setting it does not show', async () => {
    // A setting no field of this form shows: the gateway keeps it because the edit never names it.
    const stored = job({ ...STORED, note: 'from an older version' })
    const sent = wire()
    const draft = { ...toDraft(stored), writes: [], capability: '', max_turns: '', model: '' }
    await api.updateSchedule('kitchen', draftToPayload(draft), 's1')

    const { config } = sent[0].action as { config: Record<string, unknown> }
    expect(config).toEqual({
      task_template: STORED.task_template, agent: 'researcher', model: null, approval_mode: 'auto',
      cwd: '~/Documents', writes: null, capability: null, max_turns: null,
    })
    expect('note' in config).toBe(false)
  })

  it('shows them in the editor', () => {
    render(<ScheduleDetail job={job()} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()} editing onEditingChange={vi.fn()} />)

    expect(screen.getByText('~/Notes/kitchen.md')).toBeTruthy()
    expect(screen.getByText('~/Notes/pantry.md')).toBeTruthy()
    const capability = screen.getByRole('combobox', { name: 'Capability' }) as HTMLSelectElement
    expect(capability.value).toBe('mutating')
    expect(capability.selectedOptions[0].textContent).toBe('Write access')
    // Under the select, what the chosen capability lets its agent do.
    expect(screen.getByText(/Its agent may also change files and run commands/)).toBeTruthy()
    expect((screen.getByDisplayValue('7') as HTMLInputElement).name).toBe('max-turns')
  })

  it('keeps a stored capability none of its choices names, shown as it is', async () => {
    render(<ScheduleDetail job={job({ ...STORED, capability: 'text' })} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()} editing onEditingChange={vi.fn()} />)
    expect((screen.getByRole('combobox', { name: 'Capability' }) as HTMLSelectElement).value).toBe('text')

    const sent = wire()
    await api.updateSchedule('kitchen', draftToPayload(toDraft(job({ ...STORED, capability: 'text' }))), 's1')
    expect((sent[0].action as { config: Record<string, unknown> }).config.capability).toBe('text')
  })

  it("shows them on the schedule's panel", () => {
    render(<ScheduleDetail job={job()} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()} editing={false} onEditingChange={vi.fn()} />)

    expect(screen.getByTitle('What its agent may do').textContent).toContain('Write access')
    expect(screen.getByTitle('The most turns its agent takes on a run').textContent).toContain('at most 7 turns')
    expect(screen.getAllByTitle('A file it may change').map((c) => c.textContent?.trim())).toEqual(['~/Notes/kitchen.md', '~/Notes/pantry.md'])
  })

  it('a command schedule sends its command, and not the timeout it does not show', async () => {
    const sent = wire()
    const stored = job({ command: 'rsync -a ~/data /backup', timeout: 600 }, 'bash')
    await api.updateSchedule('kitchen', draftToPayload({ ...toDraft(stored), cron: '30 7 * * 1-5' }), 's1')

    expect(sent[0].action).toEqual({ provider: 'bash', config: { command: 'rsync -a ~/data /backup' } })
  })

  it('a new automation still sends the action it was given, as it is', async () => {
    // The positive control: the create page builds its own action, and nothing is added to it.
    const sent = wire()
    const action = { provider: 'invoke-agent', config: { task_template: 'Tidy the kitchen note.', writes: ['~/Notes/kitchen.md'], max_turns: 4 } }
    await api.createSchedule({ name: 'Kitchen note', cron: '0 9 * * 1-5', action })

    expect(sent[0].action).toEqual(action)
  })
})
