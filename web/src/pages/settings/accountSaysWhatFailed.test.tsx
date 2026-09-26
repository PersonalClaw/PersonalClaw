// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── Settings → Account says what failed, and only what failed ─────────────────────────────────────
//
// Two defects on one panel:
//
//   · Username: the field shows the slug the server STORED, and getting it took a second GET chained
//     onto the PUT under one catch. A GET that failed after a PUT that landed said "Couldn't save your
//     username" — about a save that had worked. The PUT now answers what it stored, so there is no
//     second request and the only failure left is the save's own.
//   · "Sign in from outside your network": its read swallowed a failure (`.catch(() => {})`) and the
//     section returned null, so a failed read left no section at all — no message, no Retry, nothing
//     to say the control exists.

const dashboardConfig = vi.fn()
const saveDashboardConfig = vi.fn()
const authSession = vi.fn()
const notify = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<typeof import('../../lib/api')>()),
  api: {
    dashboardConfig: () => dashboardConfig(),
    saveDashboardConfig: (...a: unknown[]) => saveDashboardConfig(...a),
    personalclawConfig: () => Promise.resolve({ agent: { bot_name: 'Astra' } }),
    patchConfig: () => Promise.resolve({}),
    authSession: () => authSession(),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))
vi.mock('../../app/identity', async (orig) => ({
  ...(await orig<typeof import('../../app/identity')>()),
  useIdentity: () => ({ status: 'ready', name: 'Ada Lovelace', username: 'lovelace', onboarded: true, setName: vi.fn() }),
}))

import { AccountPanel } from './AccountPanel'

const SESSION = {
  login_enabled: false, credential_configured: false, username: '', totp_enabled: false,
  totp_required: false, lockout_threshold: 5, lockout_window: '15 minutes',
}
const handleField = () => screen.getByLabelText('Username') as HTMLInputElement

beforeEach(() => {
  vi.clearAllMocks()
  dashboardConfig.mockResolvedValue({ user_name: 'Ada Lovelace', username: 'lovelace' })
  authSession.mockResolvedValue(SESSION)
})
afterEach(cleanup)

describe('Username', () => {
  it('shows the slug the save answered with, and a read that would fail is never made', async () => {
    // The server slugifies what it is sent, and says what it kept.
    saveDashboardConfig.mockImplementation(async () => ({ ok: true, user_name: 'Ada Lovelace', username: 'jo-smith' }))
    render(<AccountPanel />)
    await waitFor(() => expect(handleField().value).toBe('lovelace'))
    // The re-read the old save chained on, failing. It must not be asked for, and so cannot fail it.
    dashboardConfig.mockRejectedValue(new TypeError('Failed to fetch'))
    fireEvent.change(handleField(), { target: { value: 'Jo Smith' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save: Username' }))
    await waitFor(() => expect(handleField().value).toBe('jo-smith'))
    expect(notify, 'a save that landed was reported as failing').not.toHaveBeenCalled()
    expect(dashboardConfig, 'one read, when the panel opened — none after the save').toHaveBeenCalledTimes(1)
  })

  it('a save that fails still says so', async () => {
    saveDashboardConfig.mockRejectedValue(new Error('config.json is read-only'))
    render(<AccountPanel />)
    await waitFor(() => expect(handleField().value).toBe('lovelace'))
    fireEvent.change(handleField(), { target: { value: 'ada' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save: Username' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith("Couldn't save your username: config.json is read-only", 'error'))
  })
})

describe('Sign in from outside your network', () => {
  it('a failed read keeps the section and says so, with a Retry that brings the controls back', async () => {
    authSession.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    render(<AccountPanel />)
    expect(await screen.findByText("Couldn't load sign-in settings.")).toBeTruthy()
    expect(screen.getByText('Sign in from outside your network'), 'the section must not vanish').toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('switch', { name: 'Offer password sign-in' })).toBeTruthy()
    expect(screen.queryByText("Couldn't load sign-in settings.")).toBeNull()
  })
})
