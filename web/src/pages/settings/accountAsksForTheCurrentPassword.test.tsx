// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── Settings → Account asks for the current password before changing it ───────────────────────────
//
// The gateway changes a password that is already set only with the current one, and the
// authenticator code when one is set up (`handlers/auth.api_auth_set_password`). So the form asks for
// them where the change is made, says which is missing on the button, and sends them with the change;
// the first password needs neither.

const setLoginPassword = vi.fn()
const authSession = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<typeof import('../../lib/api')>()),
  api: {
    dashboardConfig: () => Promise.resolve({ user_name: 'Ada Lovelace', username: 'lovelace' }),
    saveDashboardConfig: () => Promise.resolve({}),
    personalclawConfig: () => Promise.resolve({ agent: { bot_name: 'Astra' } }),
    patchConfig: () => Promise.resolve({}),
    authSession: () => authSession(),
    setLoginPassword: (...a: unknown[]) => setLoginPassword(...a),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../app/identity', async (orig) => ({
  ...(await orig<typeof import('../../app/identity')>()),
  useIdentity: () => ({ status: 'ready', name: 'Ada Lovelace', username: 'lovelace', onboarded: true, setName: vi.fn() }),
}))

import { AccountPanel } from './AccountPanel'

const SESSION = {
  login_enabled: true, credential_configured: true, username: 'ada', totp_enabled: false,
  totp_required: false, lockout_threshold: 5, lockout_window: '15 minutes',
}
const NEW = 'a-new-and-longer-passphrase'
const save = () => screen.getByRole('button', { name: /Save sign-in/ })
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

beforeEach(() => {
  vi.clearAllMocks()
  setLoginPassword.mockResolvedValue({ ok: true, username: 'ada' })
})
afterEach(cleanup)

describe('changing a password that is set', () => {
  it('asks for the current one, and sends it with the change', async () => {
    authSession.mockResolvedValue(SESSION)
    render(<AccountPanel />)
    await waitFor(() => expect(screen.getByLabelText('Current password')).toBeTruthy())
    type('New password', NEW)
    type('Confirm password', NEW)
    expect(save().getAttribute('aria-disabled') ?? String((save() as HTMLButtonElement).disabled)).toMatch(/true/)
    expect(save().getAttribute('title') ?? save().getAttribute('aria-description') ?? '').toMatch(/current password/)
    type('Current password', 'correct-horse-battery-staple')
    fireEvent.click(save())
    await waitFor(() => expect(setLoginPassword).toHaveBeenCalledOnce())
    expect(setLoginPassword).toHaveBeenCalledWith('ada', NEW, { password: 'correct-horse-battery-staple', code: '' })
  })

  it('asks for the authenticator code too when one is set up', async () => {
    authSession.mockResolvedValue({ ...SESSION, totp_enabled: true })
    render(<AccountPanel />)
    await waitFor(() => expect(screen.getByLabelText('Authenticator code')).toBeTruthy())
    type('Current password', 'correct-horse-battery-staple')
    type('New password', NEW)
    type('Confirm password', NEW)
    type('Authenticator code', ' 123456 ')
    fireEvent.click(save())
    await waitFor(() => expect(setLoginPassword).toHaveBeenCalledOnce())
    expect(setLoginPassword).toHaveBeenCalledWith('ada', NEW, { password: 'correct-horse-battery-staple', code: '123456' })
  })
})

describe('setting the first password', () => {
  it('asks for nothing more', async () => {
    authSession.mockResolvedValue({ ...SESSION, credential_configured: false, login_enabled: false, username: '' })
    render(<AccountPanel />)
    await waitFor(() => expect(screen.getByLabelText('New password')).toBeTruthy())
    expect(screen.queryByLabelText('Current password')).toBeNull()
    type('Sign-in username', 'ada')
    type('New password', NEW)
    type('Confirm password', NEW)
    fireEvent.click(save())
    await waitFor(() => expect(setLoginPassword).toHaveBeenCalledOnce())
    expect(setLoginPassword).toHaveBeenCalledWith('ada', NEW, undefined)
  })
})
