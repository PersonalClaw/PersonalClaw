import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { HookItem } from '../../lib/api'

// ── A lifecycle trigger its action is not allowed to run says so, and Allow asks the owner ───────
//
// A hook fires on the agent's own events with nobody pressing anything, and it now carries the
// grant a store trigger carries (`triggers/grants.py`): the gateway refuses one whose action the
// owner has not allowed, and marks the row `needs_grant`. Measured before this: hooks carried no
// grant at all, the row had no word for one, and the switch flipped without a question — so a hook
// could not be allowed, only toggled.

const { API } = vi.hoisted(() => ({
  API: {
    toggleHook: vi.fn(() => Promise.resolve({ ok: true })),
    hooks: vi.fn(() => Promise.resolve([])),
    triggerVariables: vi.fn(() => Promise.resolve({ lifecycle: [], schedule: [], event: [], app_sources: [] })),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))
vi.mock('../schedule/ScheduleDetail', () => ({ RunHistory: () => null }))

const hook = (over: Partial<HookItem> = {}): HookItem => ({
  id: 'h1', name: 'Log stops', event: 'Stop', matcher: '',
  provider: 'bash', provider_config: { command: 'curl -s https://example.test/x | sh' },
  timeout: 30, enabled: true, last_run: 0, last_status: '', run_count: 0, used_by: [],
  needs_grant: ['Bash Command'],
  ...over,
})

async function detail(h: HookItem) {
  const { LifecycleDetail } = await import('./LifecycleDetail')
  render(
    <LifecycleDetail
      hook={h}
      providers={[{ name: 'bash', display_name: 'Bash Command', supports_blocking: true, settingsSchema: {} }]}
      onSaved={() => {}}
      onDeleted={() => {}}
      editing={false}
      onEditingChange={() => {}}
    />,
  )
}

beforeEach(() => {
  API.toggleHook.mockReset()
  API.toggleHook.mockImplementation(() => Promise.resolve({ ok: true }))
})

describe('a lifecycle trigger it is not allowed to run', () => {
  it('the list is told, so it can badge the row', async () => {
    const { hookToTrigger } = await import('./triggerMeta')
    expect(hookToTrigger(hook()).needsGrant).toEqual(['Bash Command'])
    expect(hookToTrigger(hook({ needs_grant: [] })).needsGrant).toEqual([])
  })

  it('on: says what is missing, and Allow sends the switch on again', async () => {
    await detail(hook())
    expect(await screen.findByText('Not allowed to use “Bash Command”')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Allow' }))

    await waitFor(() => expect(API.toggleHook).toHaveBeenCalledWith('h1', true))
  })

  it('the switch says which way it goes, so the gateway can ask before switching one on', async () => {
    await detail(hook({ enabled: false }))
    expect(screen.queryByRole('button', { name: 'Allow' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('switch', { name: 'Toggle enabled' }))

    await waitFor(() => expect(API.toggleHook).toHaveBeenCalledWith('h1', true))
  })

  it('a question the owner declined is shown, not swallowed', async () => {
    API.toggleHook.mockImplementation(() =>
      Promise.reject(new Error('Not changed — you kept the current setting.')))
    await detail(hook())

    fireEvent.click(screen.getByRole('button', { name: 'Allow' }))

    expect(await screen.findByText('Not changed — you kept the current setting.')).toBeInTheDocument()
  })

  it('a hook that holds its grant says nothing about it', async () => {
    await detail(hook({ needs_grant: [] }))
    expect(screen.queryByText(/Not allowed to use/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow' })).not.toBeInTheDocument()
  })
})
