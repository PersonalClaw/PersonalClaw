import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { HookItem, ScheduleJob } from '../../lib/api'

// ── An automation is saved only over the copy its edit form read ──────────────────────────────────
//
// Both trigger editors in the Triggers panel save the WHOLE trigger: every field they show, the
// untouched ones as they read them. When the same trigger changed in between — the agent's
// `automation_update`, a skip date added in another tab — the save put it back without a word. The
// gateway now refuses a save whose base is stale (`409 stale_write`, `handlers/triggers.py`); these pin
// what the panel does with the refusal: the notice, the draft kept, and the revision each save names.

const { api } = vi.hoisted(() => ({
  api: {
    schedules: vi.fn(),
    updateSchedule: vi.fn(),
    hooks: vi.fn(),
    updateHook: vi.fn(),
    triggerHistory: vi.fn(),
    // the edit forms' pickers
    savedAgents: () => Promise.resolve([]),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    triggerVariables: () => Promise.resolve({ schedule: [], lifecycle: [], app_sources: [] }),
  },
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return { ...mod, api: { ...mod.api, ...api } }
})

import { ScheduleDetail } from '../schedule/ScheduleDetail'
import { LifecycleDetail } from './LifecycleDetail'

function staleWrite() {
  return Object.assign(new Error('This write replaces the automation, which changed after the copy it was built from was read.'), { status: 409, code: 'stale_write' })
}

// ── the schedule editor ─────────────────────────────────────────────────────────────────────────

const PAINTED_JOB: ScheduleJob = {
  id: 'nightly', name: 'Nightly digest', message: 'summarise my inbox', enabled: true,
  schedule: 'every 1h', every_secs: 3600, cron_expr: null,
  action: { provider: 'invoke-agent', config: { task_template: 'summarise my inbox' } },
  skip_dates: ['2026-12-25'],
  last_run_ts: null, last_run_status: '', last_status: 'ok', run_count: 0,
  next_run_ts: null, is_running: false, warnings: [], broken: [],
  revision: 's1',
}
// What is stored by the time the form saves: the agent's `automation_update` added a skip date.
const STORED_JOB: ScheduleJob = { ...PAINTED_JOB, skip_dates: ['2026-12-25', '2027-01-01'], revision: 's2' }

function mountSchedule() {
  const onSaved = vi.fn()
  const onEditingChange = vi.fn()
  render(
    <ScheduleDetail job={PAINTED_JOB} onSaved={onSaved} onDeleted={vi.fn()} onChanged={vi.fn()}
      editing onEditingChange={onEditingChange} />,
  )
  return { onSaved, onEditingChange }
}

const nameBox = () => screen.getByDisplayValue(/Nightly/) as HTMLInputElement

beforeEach(() => {
  sessionStorage.clear()
  for (const f of Object.values(api)) if (vi.isMockFunction(f)) f.mockReset()
  api.triggerHistory.mockResolvedValue({ runs: [], total: 0 })
  api.schedules.mockResolvedValue({ jobs: [STORED_JOB], server_tz: 'UTC' })
  api.updateSchedule.mockImplementation((_id: string, _body: unknown, base: string) =>
    base === 's2' ? Promise.resolve({ ok: true, trigger: {} }) : Promise.reject(staleWrite()))
})

describe('the schedule editor', () => {
  it('a save from a stale copy is refused with the notice, and the draft is kept', async () => {
    const { onSaved, onEditingChange } = mountSchedule()
    fireEvent.change(await screen.findByDisplayValue('Nightly digest'), { target: { value: 'Nightly inbox digest' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save/ })) })

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/This trigger changed elsewhere/)
    expect(nameBox().value, 'the draft survives').toBe('Nightly inbox digest')
    expect(onSaved).not.toHaveBeenCalled()
    expect(onEditingChange).not.toHaveBeenCalledWith(false)
    // Sent over the revision the panel painted.
    expect(api.updateSchedule).toHaveBeenCalledTimes(1)
    const [id, body, base] = api.updateSchedule.mock.calls[0]
    expect([id, base]).toEqual(['nightly', 's1'])
    expect(body).toMatchObject({ name: 'Nightly inbox digest', skip_dates: ['2026-12-25'] })
  })

  it('Reload and reapply keeps the skip date added elsewhere and puts the rename on top', async () => {
    const { onSaved } = mountSchedule()
    fireEvent.change(nameBox(), { target: { value: 'Nightly inbox digest' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save/ })) })
    const alert = await screen.findByRole('alert')
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(api.updateSchedule).toHaveBeenCalledTimes(2))
    const [, body, base] = api.updateSchedule.mock.calls[1]
    expect(base).toBe('s2')
    expect(body).toMatchObject({ name: 'Nightly inbox digest', skip_dates: ['2026-12-25', '2027-01-01'] })
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })

  it('a save from a current copy lands and names the painted revision', async () => {
    api.updateSchedule.mockResolvedValue({ ok: true, trigger: {} })
    const { onSaved, onEditingChange } = mountSchedule()
    fireEvent.change(nameBox(), { target: { value: 'Nightly inbox digest' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save/ })) })

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(onEditingChange).toHaveBeenCalledWith(false)
    expect(api.updateSchedule.mock.calls[0][2]).toBe('s1')
    expect(screen.queryByText(/changed elsewhere/)).toBeNull()
  })
})

// ── the lifecycle-trigger editor ────────────────────────────────────────────────────────────────

const PAINTED_HOOK: HookItem = {
  id: 'h1', name: 'guard-writes', event: 'PreToolUse', matcher: 'write_file',
  provider: 'bash', provider_config: { command: 'exit 2' },
  timeout: 30, enabled: true, last_run: 0, last_status: '', run_count: 0, used_by: [],
  revision: 'h1r1',
}
// Stored by the time the form saves: another tab re-pointed the command.
const STORED_HOOK: HookItem = { ...PAINTED_HOOK, provider_config: { command: 'exit 3' }, revision: 'h1r2' }
const BASH = [{
  name: 'bash', display_name: 'Bash', supports_blocking: true,
  settingsSchema: { type: 'object', properties: { command: { type: 'string' } } },
}]

function mountHook() {
  const onSaved = vi.fn()
  const onEditingChange = vi.fn()
  render(<LifecycleDetail hook={PAINTED_HOOK} providers={BASH} onSaved={onSaved} onDeleted={vi.fn()} editing onEditingChange={onEditingChange} />)
  return { onSaved, onEditingChange }
}

const matcherBox = () => screen.getByPlaceholderText('write_file') as HTMLInputElement

describe('the lifecycle-trigger editor', () => {
  beforeEach(() => {
    api.hooks.mockResolvedValue([STORED_HOOK])
    api.updateHook.mockImplementation((_id: string, _body: unknown, base: string) =>
      base === 'h1r2' ? Promise.resolve({ ok: true, hook: STORED_HOOK }) : Promise.reject(staleWrite()))
  })

  it('a save from a stale copy is refused with the notice, and the draft is kept', async () => {
    const { onSaved, onEditingChange } = mountHook()
    fireEvent.change(matcherBox(), { target: { value: 'edit_file' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save/ })) })

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/This trigger changed elsewhere/)
    expect(matcherBox().value, 'the draft survives').toBe('edit_file')
    expect(onSaved).not.toHaveBeenCalled()
    expect(onEditingChange).not.toHaveBeenCalledWith(false)
    const [id, body, base] = api.updateHook.mock.calls[0]
    expect([id, base]).toEqual(['h1', 'h1r1'])
    expect(body).toMatchObject({ matcher: 'edit_file', provider_config: { command: 'exit 2' } })
  })

  it('Reload and reapply keeps the command changed elsewhere and puts the matcher on top', async () => {
    const { onSaved } = mountHook()
    fireEvent.change(matcherBox(), { target: { value: 'edit_file' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save/ })) })
    const alert = await screen.findByRole('alert')
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(api.updateHook).toHaveBeenCalledTimes(2))
    const [, body, base] = api.updateHook.mock.calls[1]
    expect(base).toBe('h1r2')
    expect(body).toMatchObject({ matcher: 'edit_file', provider_config: { command: 'exit 3' } })
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })

  it('a save from a current copy lands and names the painted revision', async () => {
    api.updateHook.mockResolvedValue({ ok: true, hook: PAINTED_HOOK })
    const { onSaved } = mountHook()
    fireEvent.change(matcherBox(), { target: { value: 'edit_file' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save/ })) })
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(api.updateHook.mock.calls[0][2]).toBe('h1r1')
  })
})
