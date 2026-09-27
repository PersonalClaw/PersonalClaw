import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react'

// ── Settings → Security: the sign-in lockout, and replacing the sign-in key ─────────────────────
//
// `auth.lockout_threshold` and `auth.lockout_window` were editable, bounded and read by every
// sign-in door, and no control anywhere wrote them. And the one answer to "the key, or a sign-in,
// may have been copied" — replacing the key every sign-in is signed with — existed in the gateway
// with no caller at all. Both are driven here through the real panel and the real dialog.

async function mount(opts: {
  auth?: Record<string, unknown>
  patch?: (...a: unknown[]) => Promise<unknown>
  rotate?: () => Promise<unknown>
} = {}) {
  vi.resetModules()
  sessionStorage.clear()
  const notify = vi.fn()
  vi.doMock('../../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify }))
  const patchConfig = vi.fn(opts.patch ?? (() => Promise.resolve({})))
  const rotateSigningKey = vi.fn(opts.rotate ?? (() => Promise.resolve({
    ok: true,
    signed_out: 3,
    notice: {
      code: 'session_signed_out',
      reason: 'key_replaced',
      message: 'Every device was signed out just now, when the key PersonalClaw signs sign-ins with was replaced. Sign in again with your password.',
    },
  })))
  vi.doMock('../../lib/api', () => ({
    api: {
      devices: () => Promise.resolve([]),
      securityStats: () => Promise.resolve({
        denied_commands: 1, suspicious_patterns: 1, tool_schemas: 1, redaction_paths: 1,
        child_ceilings: { contained: true, note: '' },
      }),
      deniedCommands: () => Promise.resolve({
        builtin: [], user: [], user_additions: 0,
        baseline: { version: 1, sha256: 'x', count: 0, verified: true, detail: '' },
      }),
      securityEgress: () => Promise.resolve({ value: { allow_hosts: [], deny_hosts: [], allow_private: false }, revision: 'r1' }),
      outsideHome: () => Promise.resolve({ places: [], allowed: [] }),
      desktopState: () => Promise.resolve({
        connected: false, shell: null, capabilities: {}, registered_at: '', last_seen: '',
      }),
      credentialStore: () => Promise.resolve({
        migration: 'credentials_to_keychain', backend: 'dotenv', requested: 'dotenv',
        blocked: true, pending_keys: [], pending: 0, keychain_keys: 0,
        rollback_available: false, snapshot_name: '.env.pre-keychain', verified: true,
        verification: { checked: 0, missing: [], still_in_dotenv: [] },
      }),
      personalclawConfig: () => Promise.resolve({
        auth: opts.auth ?? { session_ttl: '30d', lockout_threshold: 5, lockout_window: '15m' },
        sandbox: { nofile: 4096, max_pids: 0, max_rss_mb: 0, cgroup_scopes: false, env_passthrough: [] },
      }),
      patchConfig,
      rotateSigningKey,
    },
  }))
  const consent = await import('../../lib/securityConsent')
  const signedOut = await import('../../lib/signedOut')
  signedOut.resetSignedOutForTests()
  const panel = await import('./SecurityPanel')
  const { DialogHost } = await import('../../ui/dialog/DialogHost')
  await act(async () => {
    render(<><panel.SecurityPanel /><DialogHost /></>)
    await new Promise((res) => setTimeout(res, 0))
  })
  return { patchConfig, rotateSigningKey, notify, signedOut, ConsentDeclined: consent.ConsentDeclined }
}

const attempts = () => screen.getByRole('combobox', { name: 'Stop accepting sign-ins after' }) as HTMLSelectElement
const lockout = () => screen.getByRole('combobox', { name: 'Lockout lasts' }) as HTMLSelectElement

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the sign-in lockout controls', () => {
  it('show the lockout the gateway has', async () => {
    await mount({ auth: { lockout_threshold: 10, lockout_window: '1h' } })
    await waitFor(() => expect(attempts().value).toBe('10'))
    expect(lockout().value).toBe('1h')
  })

  it('write each field through the config PATCH, the attempt count as a number', async () => {
    const { patchConfig } = await mount()
    await waitFor(() => expect(attempts().disabled).toBe(false))
    fireEvent.change(attempts(), { target: { value: '3' } })
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('auth.lockout_threshold', 3))
    fireEvent.change(lockout(), { target: { value: '4h' } })
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('auth.lockout_window', '4h'))
  })

  it('show a value set outside Settings as itself', async () => {
    await mount({ auth: { lockout_threshold: 7, lockout_window: '45m' } })
    await waitFor(() => expect(attempts().value).toBe('7'))
    expect(within(attempts()).getByRole('option', { name: '7 wrong attempts — set outside Settings' })).toBeTruthy()
    expect(within(lockout()).getByRole('option', { name: '45 minutes — set outside Settings' })).toBeTruthy()
  })

  it('say a lockout that is not a length of time lasts the default', async () => {
    await mount({ auth: { lockout_threshold: 5, lockout_window: 'soon' } })
    await waitFor(() => expect(lockout().value).toBe('soon'))
    const note = screen.getByText(
      'auth.lockout_window is “soon”, which is not a length of time, so a lockout lasts the 15-minute default. Choose a length.',
    )
    expect(note.closest('[role="status"]')).not.toBeNull()
    expect((within(lockout()).getByRole('option', { name: 'soon — not a length of time' }) as HTMLOptionElement).disabled).toBe(true)
  })

  it('put the value back and say why when the gateway refuses it', async () => {
    const { notify } = await mount({ patch: () => Promise.reject(new Error('auth.lockout_threshold must be at most 100')) })
    await waitFor(() => expect(attempts().disabled).toBe(false))
    fireEvent.change(attempts(), { target: { value: '20' } })
    await waitFor(() => expect(attempts().value).toBe('5'))
    expect(notify).toHaveBeenCalledWith("Couldn't change the sign-in lockout: auth.lockout_threshold must be at most 100", 'error')
  })

  it('put the value back quietly when the owner keeps the current one', async () => {
    let declined: Error | null = null
    const mounted = await mount({ patch: () => Promise.reject(declined) })
    declined = new mounted.ConsentDeclined('auth.lockout_threshold')
    await waitFor(() => expect(attempts().disabled).toBe(false))
    fireEvent.change(attempts(), { target: { value: '20' } })
    await waitFor(() => expect(attempts().value).toBe('5'))
    expect(mounted.notify).toHaveBeenCalledWith('Not changed — you kept the current setting.')
  })
})

describe('replacing the sign-in key', () => {
  const replace = () => screen.getByRole('button', { name: /replace the key/i })

  it('asks first, saying it signs this browser out too — and a dismissal replaces nothing', async () => {
    const { rotateSigningKey } = await mount()
    fireEvent.click(await waitFor(replace))
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent ?? '').toMatch(/this browser too/)
    expect(dialog.textContent ?? '').toMatch(/Integration tokens are separate/)
    fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(rotateSigningKey).not.toHaveBeenCalled()
  })

  it('once confirmed, replaces it and shows this browser the sentence it is signed out with', async () => {
    const { rotateSigningKey, signedOut } = await mount()
    fireEvent.click(await waitFor(replace))
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Replace and sign everyone out' }))
    await waitFor(() => expect(rotateSigningKey).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(signedOut.signedOutState()).not.toBeNull())
    expect(signedOut.signedOutState()).toEqual({
      code: 'session_signed_out',
      reason: 'key_replaced',
      message: 'Every device was signed out just now, when the key PersonalClaw signs sign-ins with was replaced. Sign in again with your password.',
    })
  })

  it('says so when the key could not be replaced', async () => {
    await mount({ rotate: () => Promise.reject(new Error('The sign-in key could not be replaced, so nobody was signed out: read-only file system')) })
    fireEvent.click(await waitFor(replace))
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Replace and sign everyone out' }))
    const error = await screen.findByText(/Couldn't replace the sign-in key: The sign-in key could not be replaced/)
    expect(error.closest('[role="alert"]')).not.toBeNull()
  })
})
