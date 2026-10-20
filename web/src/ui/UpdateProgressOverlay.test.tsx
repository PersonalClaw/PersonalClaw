/**
 * The update overlay's Cancel stops the update and shows what that left, and is offered only where
 * it stops something.
 *
 * 🔴 Measured before: pressing Cancel while the installer ran closed the sheet on the spot, the
 * gateway broadcast "Update cancelled by user", and the update went on: the installer kept running
 * and the gateway restarted into the new release. Cancel was offered at every step, a plain restart
 * included, and Dismiss on a failed update sent the same cancel.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ApiError } from '../lib/api'

type Frame = { type: string; data?: Record<string, unknown> }
const sockets: Array<(m: Frame) => void> = []
// Typed with a rest parameter so the forwarding spread below typechecks.
const cancelUpdate = vi.fn(async (..._a: unknown[]): Promise<unknown> => ({}))
const dismissUpdate = vi.fn(async (..._a: unknown[]): Promise<unknown> => ({ ok: true }))
const notified: string[] = []

vi.mock('../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      status: vi.fn(async () => ({})),
      cancelUpdate: (...a: unknown[]) => cancelUpdate(...a),
      dismissUpdate: (...a: unknown[]) => dismissUpdate(...a),
    },
  }
})
vi.mock('../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: Frame) => void) => { sockets.push(onMessage) },
}))
vi.mock('../app/appSdk', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  notify: (m: string) => { notified.push(m) },
}))

const { UpdateProgressOverlay } = await import('./UpdateProgressOverlay')

/** One `update_progress` frame, as the gateway broadcasts it. */
function step(name: string, detail = '') {
  act(() => { sockets.at(-1)!({ type: 'update_progress', data: { step: name, detail } }) })
}

function installing() {
  render(<UpdateProgressOverlay />)
  step('pulling', 'Fast-forwarding main…')
  step('installing', 'Installing package…')
}

const CANCELLED = 'The update was cancelled. Nothing was changed: the checkout is back on main at d831774.'

beforeEach(() => {
  cancelUpdate.mockReset()
  cancelUpdate.mockImplementation(async () => ({}))
  dismissUpdate.mockClear()
  notified.length = 0
})
afterEach(() => cleanup())

describe('Cancel stops the update, and the sheet says what that left', () => {
  it('🔴 keeps the sheet open while the gateway stops the update, then shows what the stop left, announced', async () => {
    let answer!: (v: unknown) => void
    cancelUpdate.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    installing()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(cancelUpdate).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('alertdialog'), 'the sheet closed over an update still running').toBeTruthy()
    expect(screen.getByText('Cancelling the update')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toBe('Stopping the update…')
    expect(screen.getByRole('button', { name: 'Cancel' }).getAttribute('aria-busy')).toBe('true')

    await act(async () => {
      answer({ ok: true, status: 'stopped', update_progress: { step: 'cancelled', detail: CANCELLED } })
    })

    expect(screen.getByText('Update cancelled')).toBeTruthy()
    expect(screen.getByRole('status').textContent, 'the outcome is not in the polite live region').toBe(CANCELLED)
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
    expect(document.activeElement, 'focus left the dialog with the Cancel button').toBe(
      screen.getByRole('button', { name: 'Dismiss' }),
    )
  })

  it('🔴 a cancel that could not put everything back is shown like a failure, assertively', async () => {
    const left =
      'The update was cancelled. The checkout could not be put back on v0.0.1 (another git is ' +
      'running), so it is on v0.0.2. To put it back on v0.0.1 before PersonalClaw next starts, run: ' +
      'git -C /home/user/checkout checkout --detach 1a2b3c4'
    cancelUpdate.mockImplementationOnce(async () => ({ ok: true, status: 'stopped', update_progress: { step: 'error', detail: left } }))
    installing()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe(left))
    expect(screen.getByText('Update failed')).toBeTruthy()
  })

  it('🔴 a Cancel the gateway refuses shows its sentence, assertively, and claims no cancel', async () => {
    const refused =
      'The new release is installed, so the update can no longer be cancelled: it builds the ' +
      'dashboard, then restarts PersonalClaw into it.'
    cancelUpdate.mockImplementationOnce(async () => { throw new ApiError(refused, 409, 'update_not_cancellable') })
    installing()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe(refused))
    expect(screen.getByRole('alertdialog')).toBeTruthy()
    expect(screen.queryByText('Update cancelled')).toBeNull()
  })

  it('a Cancel with no update running says so, and closes the sheet', async () => {
    const none = 'No update is running, so there was nothing to cancel.'
    cancelUpdate.mockImplementationOnce(async () => ({ ok: true, status: 'not_running', detail: none }))
    installing()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(notified).toEqual([none]))
    expect(dismissUpdate).toHaveBeenCalledTimes(1)
  })
})

describe('Cancel is offered only where it stops something', () => {
  it('🔴 once the new release is installed there is no Cancel: it says the update will finish, and Hide only closes the sheet', () => {
    installing()
    step('building', 'Building frontend…')

    expect(screen.queryByRole('button', { name: 'Cancel' }), 'Cancel offered where nothing stops').toBeNull()
    expect(screen.getByText('The new release is installed, so this can no longer be cancelled.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Hide' }))

    expect(cancelUpdate).not.toHaveBeenCalled()
    expect(dismissUpdate).not.toHaveBeenCalled()
  })

  it('🔴 a plain restart offers no Cancel: a restart cannot be cancelled', () => {
    render(<UpdateProgressOverlay />)
    step('restarting', 'Restarting gateway…')

    expect(screen.getByText('Restarting gateway')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Hide' })).toBeTruthy()
  })

  it('🔴 Dismiss closes what an update ended with, and never sends a cancel', () => {
    render(<UpdateProgressOverlay />)
    step('error', 'uv sync failed: No solution found. Nothing was changed: the checkout is back on v0.0.1.')

    expect(screen.getByRole('alert').textContent).toContain('Nothing was changed')
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    expect(dismissUpdate).toHaveBeenCalledTimes(1)
    expect(cancelUpdate).not.toHaveBeenCalled()
  })
})
