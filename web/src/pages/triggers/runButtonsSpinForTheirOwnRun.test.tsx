import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Trigger as WireTrigger } from '../../lib/api'

// ── The automation panel's Run now spins for its own run, and only for it ─────────────────────────
//
// Run now took the panel's shared `loading={busy}`, and `busy` is set by every action here — the
// switch, Allow, Dry run and Delete. So a Delete in flight drew Run now's spinner and published
// `aria-busy` on it: a screen reader heard a run working that nobody started. Each run button now
// spins for its own request and is merely held (disabled, with the reason) while a sibling works.
// What a finished run recorded is a status line, as the schedule panel's is.

const { API } = vi.hoisted(() => ({
  API: {
    runStoreTrigger: vi.fn(),
    deleteStoreTrigger: vi.fn(),
    toggleStoreTrigger: vi.fn(),
    triggerHistory: vi.fn(() => Promise.resolve({ runs: [], total: 0, supported: true })),
    triggerRunDetail: vi.fn(),
  },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: API,
}))

vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<object>()),
  confirmDelete: () => Promise.resolve(true),
}))

const { StoreTriggerDetail } = await import('./StoreTriggerDetail')

function row(): WireTrigger {
  return {
    kind: 'store', id: 'store:file:notes', raw_id: 'file:notes', name: 'Summarize my notes',
    enabled: true, action: { provider: 'run-prompt', config: {} }, store_kind: 'file',
    spec: { paths: ['/tmp/notes.example'] }, broken: [], warnings: [], run_count: 0,
  } as unknown as WireTrigger
}

/** A request the test lets land when it chooses. */
function pending<T>(fn: ReturnType<typeof vi.fn>, value: T): () => Promise<void> {
  let land: (v: T) => void = () => {}
  fn.mockImplementation(() => new Promise<T>((r) => { land = r }))
  return async () => { await act(async () => land(value)) }
}

const runNow = () => screen.getByRole('button', { name: /Run now/ })
const dryRun = () => screen.getByRole('button', { name: /Dry run/ })
const held = (el: HTMLElement) => el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true'

beforeEach(() => {
  for (const fn of Object.values(API)) fn.mockReset()
  API.triggerHistory.mockImplementation(() => Promise.resolve({ runs: [], total: 0, supported: true }))
})

describe('the automation panel’s run buttons', () => {
  it('🔴 a Delete in flight holds Run now without making it announce a run', async () => {
    const land = pending(API.deleteStoreTrigger, undefined)
    render(<StoreTriggerDetail trigger={row()} onChanged={vi.fn()} onDeleted={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }))
    await waitFor(() => expect(API.deleteStoreTrigger).toHaveBeenCalled())

    expect(held(runNow()), 'held while the Delete is out').toBe(true)
    expect(runNow(), 'but it is not the one working').not.toHaveAttribute('aria-busy')
    expect(screen.queryByText('Running…')).toBeNull()
    await land()
  })

  it('a switch change in flight holds both run buttons, and neither spins', async () => {
    const land = pending(API.toggleStoreTrigger, undefined)
    render(<StoreTriggerDetail trigger={row()} onChanged={vi.fn()} onDeleted={vi.fn()} />)
    fireEvent.click(screen.getByRole('switch', { name: /Enabled/ }))
    await waitFor(() => expect(API.toggleStoreTrigger).toHaveBeenCalled())

    for (const button of [runNow(), dryRun()]) {
      expect(held(button)).toBe(true)
      expect(button).not.toHaveAttribute('aria-busy')
    }
    await land()
  })

  it('Run now spins for its own run and says so; Dry run is only held', async () => {
    const land = pending(API.runStoreTrigger, { ok: true, result: 'ran', status: 'success' })
    render(<StoreTriggerDetail trigger={row()} onChanged={vi.fn()} onDeleted={vi.fn()} />)
    fireEvent.click(runNow())

    expect(runNow()).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('Running…')).toBeTruthy()
    expect(held(dryRun())).toBe(true)
    expect(dryRun()).not.toHaveAttribute('aria-busy')
    await land()
    expect(runNow()).not.toHaveAttribute('aria-busy')
  })

  it('a dry run spins Dry run, not Run now', async () => {
    const land = pending(API.runStoreTrigger, { ok: true, dry_run: true, text: 'Would run the prompt.' })
    render(<StoreTriggerDetail trigger={row()} onChanged={vi.fn()} onDeleted={vi.fn()} />)
    fireEvent.click(dryRun())

    expect(API.runStoreTrigger).toHaveBeenCalledWith('file:notes', true)
    expect(dryRun()).toHaveAttribute('aria-busy', 'true')
    expect(held(runNow())).toBe(true)
    expect(runNow(), 'a preview is not a run').not.toHaveAttribute('aria-busy')
    await land()
  })

  it('what the run recorded is announced as a status', async () => {
    API.runStoreTrigger.mockResolvedValue({ ok: true, result: 'ran', status: 'waiting' })
    render(<StoreTriggerDetail trigger={row()} onChanged={vi.fn()} onDeleted={vi.fn()} />)
    fireEvent.click(runNow())

    // Found by its words, then asked its role: the run history below has its own loading status.
    const line = await screen.findByText('Waiting for you')
    expect(line).toHaveAttribute('role', 'status')
  })

  it('🔴 a run already in flight is said in words', async () => {
    // A Run now holds the trigger's claim now, so a second one is refused 409 for every kind of
    // automation, not only a schedule — and the panel printed the bare "already running".
    const { ApiError } = await import('../../lib/api')
    API.runStoreTrigger.mockRejectedValue(new ApiError('already running', 409))
    render(<StoreTriggerDetail trigger={row()} onChanged={vi.fn()} onDeleted={vi.fn()} />)
    fireEvent.click(runNow())

    expect(await screen.findByText('This automation is already running.')).toBeTruthy()
  })
})
