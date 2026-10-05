import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react'

// ── Settings → Security says how long a sign-in lasts, and changes it ───────────────────────────
//
// `auth.session_ttl` had no control anywhere: `personalclaw config set` and a hand-edited
// `config.json` were the only ways to shorten how long a stolen browser cookie keeps working. The
// control writes through the one config PATCH (the gateway validates it and asks for consent when
// it lengthens the sign-in), and the 90-day limit is IN the control: nothing longer is offered.

const DAY = 86400

async function mount(
  auth: Record<string, unknown> | Error,
  patch?: (...a: unknown[]) => Promise<unknown>,
  devices: Record<string, unknown>[] = [],
) {
  vi.resetModules()
  sessionStorage.clear()
  const notify = vi.fn()
  vi.doMock('../../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify }))
  const patchConfig = vi.fn(patch ?? (() => Promise.resolve({})))
  vi.doMock('../../lib/api', () => ({
    api: {
      devices: () => Promise.resolve(devices),
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
      personalclawConfig: () => auth instanceof Error
        ? Promise.reject(auth)
        : Promise.resolve({ auth, sandbox: { nofile: 4096, max_pids: 0, max_rss_mb: 0, cgroup_scopes: false, env_passthrough: [] } }),
      patchConfig,
    },
  }))
  const consent = await import('../../lib/securityConsent')
  const panel = await import('./SecurityPanel')
  await act(async () => {
    render(<panel.SecurityPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return { patchConfig, notify, panel, ConsentDeclined: consent.ConsentDeclined }
}

function lifetimeSelect(): HTMLSelectElement {
  return screen.getByRole('combobox', { name: 'Sign-ins last' }) as HTMLSelectElement
}

function optionValues(select: HTMLSelectElement): string[] {
  return Array.from(select.options).filter((o) => !o.disabled).map((o) => o.value)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('what a sign-in from a personalclaw token link lasts', () => {
  // The link `personalclaw token` prints keeps its own lifetime (20 hours unless made with --ttl),
  // and in a container it is the only way in: a browser signed in with one was signed out after 20
  // hours, mid-turn, beside a Settings page that said sign-ins last 30 days.
  const browser = {
    id: 'd1', name: 'Firefox on Linux', kind: 'browser', minted_at: 0, last_seen: 0, ip: '',
    pool: 'browser', current: true,
  }
  const ends = Math.floor(new Date(2026, 9, 1, 21, 5).getTime() / 1000)

  it('says so beside the choice, for every browser', async () => {
    await mount({ session_ttl: '30d' })
    await waitFor(lifetimeSelect)
    expect(lifetimeSelect().getAttribute('aria-describedby')).toBeTruthy()
    const hint = document.getElementById(lifetimeSelect().getAttribute('aria-describedby')!)
    expect(hint?.textContent).toContain(
      'A link from personalclaw token keeps its own lifetime instead: 20 hours, unless it was made with --ttl (at most 90 days).',
    )
  })

  it('tells a browser signed in with one when its sign-in ends', async () => {
    await mount({ session_ttl: '30d' }, undefined, [{ ...browser, issuer: 'token', expires_at: ends }])
    const { absTime } = await import('../schedule/scheduleMeta')
    expect(await screen.findByText(
      `This browser signed in with a link from personalclaw token, so it stays signed in until ${absTime(ends)}: the link's own lifetime, not the one chosen here. To stay signed in longer, open a link from personalclaw token --ttl 30d.`,
    )).toBeTruthy()
  })

  it('says nothing of the kind to a browser that signed in another way', async () => {
    await mount({ session_ttl: '30d' }, undefined, [{ ...browser, issuer: 'login', expires_at: ends }])
    await waitFor(lifetimeSelect)
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(screen.queryByText(/This browser signed in with a link from personalclaw token/)).toBeNull()
  })
})

describe('the sign-in lifetime control', () => {
  it('offers nothing longer than the 90-day limit', async () => {
    const { panel } = await mount({ session_ttl: '30d' })
    const select = await waitFor(lifetimeSelect)
    const offered = optionValues(select)
    expect(offered).toEqual(['12h', '1d', '7d', '14d', '30d', '60d', '90d'])
    for (const value of offered) {
      expect(panel.lifetimeSecs(value)).not.toBeNull()
      expect(panel.lifetimeSecs(value)!).toBeLessThanOrEqual(90 * DAY)
    }
    expect(within(select).getByRole('option', { name: '90 days — the limit' })).toBeTruthy()
  })

  it('shows the lifetime the gateway has', async () => {
    await mount({ session_ttl: '7d' })
    await waitFor(() => expect(lifetimeSelect().value).toBe('7d'))
  })

  it('shows the 30-day default when the gateway has none', async () => {
    await mount({})
    await waitFor(() => expect(lifetimeSelect().value).toBe('30d'))
  })

  it('writes the lifetime chosen through the config PATCH', async () => {
    const { patchConfig } = await mount({ session_ttl: '30d' })
    await waitFor(() => expect(lifetimeSelect().disabled).toBe(false))
    fireEvent.change(lifetimeSelect(), { target: { value: '14d' } })
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('auth.session_ttl', '14d'))
    expect(lifetimeSelect().value).toBe('14d')
  })

  it('shows a lifetime set outside Settings as itself, not as a preset', async () => {
    await mount({ session_ttl: '45d' })
    await waitFor(() => expect(lifetimeSelect().value).toBe('45d'))
    expect(within(lifetimeSelect()).getByRole('option', { name: '45 days — set outside Settings' })).toBeTruthy()
    expect(screen.queryByText(/longer than the 90-day limit/)).toBeNull()
  })

  it('says a lifetime longer than the limit is applied as 90 days, and offers no way to keep it', async () => {
    await mount({ session_ttl: '365d' })
    await waitFor(() => expect(lifetimeSelect().value).toBe('365d'))
    const kept = within(lifetimeSelect()).getByRole('option', { name: '365d — longer than the limit' }) as HTMLOptionElement
    expect(kept.disabled).toBe(true)
    const note = screen.getByText(
      'auth.session_ttl is 365d, longer than the 90-day limit for a sign-in, so every sign-in lasts 90 days. Choose 90 days or less.',
    )
    expect(note.closest('[role="status"]')).not.toBeNull()
  })

  it('says a value that is not a length of time falls back to the default', async () => {
    await mount({ session_ttl: 'soon' })
    await waitFor(() => expect(lifetimeSelect().value).toBe('soon'))
    const note = screen.getByText(
      'auth.session_ttl is “soon”, which is not a length of time, so every sign-in lasts the 30-day default. Choose a lifetime.',
    )
    expect(note.closest('[role="status"]')).not.toBeNull()
  })

  it('names a failed read rather than showing a lifetime it does not know', async () => {
    await mount(new Error('config unreadable'))
    expect(await screen.findByText("Couldn't load your sign-in lifetime")).toBeTruthy()
    expect(screen.queryByRole('combobox', { name: 'Sign-ins last' })).toBeNull()
  })

  it('puts the lifetime back and says why when the gateway refuses it', async () => {
    const { notify } = await mount({ session_ttl: '30d' }, () => Promise.reject(new Error('auth.session_ttl can be at most 90 days')))
    await waitFor(() => expect(lifetimeSelect().disabled).toBe(false))
    fireEvent.change(lifetimeSelect(), { target: { value: '60d' } })
    await waitFor(() => expect(lifetimeSelect().value).toBe('30d'))
    expect(notify).toHaveBeenCalledWith(
      "Couldn't change how long a sign-in lasts: auth.session_ttl can be at most 90 days", 'error',
    )
  })

  it('puts the lifetime back quietly when the owner keeps the current one', async () => {
    let declined: Error | null = null
    const mounted = await mount({ session_ttl: '7d' }, () => Promise.reject(declined))
    declined = new mounted.ConsentDeclined('auth.session_ttl')
    await waitFor(() => expect(lifetimeSelect().disabled).toBe(false))
    fireEvent.change(lifetimeSelect(), { target: { value: '90d' } })
    await waitFor(() => expect(lifetimeSelect().value).toBe('7d'))
    expect(mounted.notify).toHaveBeenCalledWith('Not changed — you kept the current setting.')
  })
})
