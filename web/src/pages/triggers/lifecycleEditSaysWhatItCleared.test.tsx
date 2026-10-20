import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { HookItem } from '../../lib/api'

// ── A lifecycle trigger's edit says what it cleared ─────────────────────────────────────────────────
//
// The gateway puts the settings an edit sends over the action it stores, and keeps every setting the
// edit leaves out (`triggers/action_edit.py`), so a form that cleared a field by leaving it out of the
// save would keep its old value. The lifecycle editor sends each field it shows: a value as it is, a
// field left empty as cleared (`null`), and nothing for a stored setting its form does not draw.

const { api } = vi.hoisted(() => ({
  api: {
    hooks: vi.fn(),
    updateHook: vi.fn(),
    triggerVariables: () => Promise.resolve({ schedule: [], lifecycle: [], app_sources: [] }),
  },
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return { ...mod, api: { ...mod.api, ...api } }
})

import { LifecycleDetail } from './LifecycleDetail'
import { editedActionConfig } from './ActionConfig'

const BASH = [{
  name: 'bash', display_name: 'Bash', supports_blocking: true,
  settingsSchema: {
    type: 'object', required: ['command'],
    properties: { command: { type: 'string' }, timeout: { type: 'integer' } },
  },
}]

const HOOK: HookItem = {
  id: 'h1', name: 'Log stops', event: 'Stop', matcher: '',
  // `note` is a setting no field of the form draws: an older version wrote it.
  provider: 'bash', provider_config: { command: 'echo stopped', timeout: 60, note: 'kept' },
  timeout: 30, enabled: true, last_run: 0, last_status: '', run_count: 0, used_by: [],
  revision: 'r1',
}

beforeEach(() => {
  api.hooks.mockReset()
  api.updateHook.mockReset()
  api.updateHook.mockResolvedValue({ ok: true, hook: HOOK })
})

describe('the lifecycle editor', () => {
  it('sends a field it shows and the user emptied as cleared', async () => {
    render(<LifecycleDetail hook={HOOK} providers={BASH} onSaved={vi.fn()} onDeleted={vi.fn()} editing onEditingChange={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('timeout'), { target: { value: '' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save/ })) })

    await waitFor(() => expect(api.updateHook).toHaveBeenCalledTimes(1))
    const [, body] = api.updateHook.mock.calls[0]
    expect(body.provider_config).toEqual({ command: 'echo stopped', timeout: null })
  })

  it('sends a field it shows as it is, and nothing for a setting it does not draw', () => {
    expect(editedActionConfig(BASH, 'bash', HOOK.provider_config)).toEqual({
      config: { command: 'echo stopped', timeout: 60 },
    })
  })
})
