import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'

// ── The controls that cut off access or stop the agent never wait for setup ─────────────────────
//
// A fresh home pulls every route into first-run setup until setup is finished or skipped. That is
// right for a page that runs on setup's answers (a name to greet and attribute by, a model to chat
// with) and wrong for the pages whose job is to revoke access to this gateway or to stop the agent.
// Measured on a fresh home: `#/settings/devices` showed "Setup comes first", so a sign-in link that
// leaked during setup could only be cut off from the terminal, and the incident kill switch could not
// be reached from the dashboard at all.
//
// Driven through the REAL provider stack and the REAL shell, with the dialogs these controls ask
// through. A control that renders but whose confirmation never appears does nothing, so every write
// below is asserted to have reached the gateway, not just to have been offered.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
// The first-run flow's 3D dot-wave is a canvas; jsdom has no 2D context.
vi.mock('../ui/DotGlow', () => ({ DotGlow: () => null }))

/** Every gateway call this test's run made, by method name, with its arguments. */
const calls: { method: string; args: unknown[] }[] = []
const called = (method: string) => calls.filter((c) => c.method === method).map((c) => c.args)

const NOW = Math.floor(Date.now() / 1000)
/** This browser, and a phone paired a few minutes ago. */
const DEVICES = [
  {
    id: 'dev-this', name: 'Browser on this computer', kind: 'browser', minted_at: NOW - 600, last_seen: NOW,
    ip: '127.0.0.1', issuer: 'startup', pool: 'browser', expires_at: NOW + 72000, current: true,
  },
  {
    id: 'dev-phone', name: 'Phone', kind: 'mobile', minted_at: NOW - 300, last_seen: NOW - 60,
    ip: '192.0.2.7', issuer: 'pair', pool: 'device', expires_at: NOW + 86400, current: false,
  },
]

/** What the incident read answers. Mutable so one case can open on an active incident. */
let incident = { active: false, reason: '', started_at: '' }
/** The stored identity. A FRESH home has no name, so setup is not done; one case opens on a home
 *  that finished setup and runs it again. */
const FRESH_HOME = { user_name: '', username: '' }
let identity: Record<string, string> = FRESH_HOME

/** Envelope-shaped reads the pages destructure; anything else resolves `[]`. */
const ENVELOPES: Record<string, unknown> = {
  onboarding: { needs_model: true, has_model_provider: false, has_chat_binding: false, step: 'name' },
  saveOnboardingState: { ok: true, state: {} },
  devices: DEVICES,
  deviceIntegrations: { integrations: [], problem: '' },
  devicesRevokeOthers: { ok: true, revoked: 1 },
  deviceRevoke: { ok: true, revoked: 1 },
  rotateSigningKey: { ok: true, signed_out: 2, notice: null },
  incidentOn: { active: true, reason: 'Activated from Settings', started_at: '2026-10-02T08:00:00Z' },
  securityStats: {
    denied_commands: 1, suspicious_patterns: 1, tool_schemas: 1, redaction_paths: 1,
    child_ceilings: { contained: true, note: '' },
  },
  deniedCommands: {
    builtin: [], user: [], user_additions: 0,
    baseline: { version: 1, sha256: 'x', count: 0, verified: true, detail: '' },
  },
  securityEgress: { value: { allow_hosts: [], deny_hosts: [], allow_private: false }, revision: 'r1' },
  outsideHome: { places: [], allowed: [] },
  desktopState: { connected: false, shell: null, capabilities: {}, registered_at: '', last_seen: '' },
  credentialStore: {
    migration: 'credentials_to_keychain', backend: 'dotenv', requested: 'dotenv',
    blocked: true, pending_keys: [], pending: 0, keychain_keys: 0,
    rollback_available: false, snapshot_name: '.env.pre-keychain', verified: true,
    verification: { checked: 0, missing: [], still_in_dotenv: [] },
  },
  personalclawConfig: {
    auth: { session_ttl: '30d', lockout_threshold: 5, lockout_window: '15m' },
    sandbox: { nofile: 4096, max_pids: 0, max_rss_mb: 0, cgroup_scopes: false, env_passthrough: [] },
    guardrails: {},
  },
  autonomyLadder: { rungs: [], rung_meta: [], incident_active: false, types: [], reversals: [] },
  modelsHealth: { providers: [], callers: [], generated_from: 0 },
  externalAccess: {
    enabled: false, incident_active: false, public_url: '',
    caps: {
      rate_rps: 1, rate_burst: 20, rate_concurrent: 4, auto_disable_after_breaches: 10,
      capture_retention_days: 30, capture_upstream_allowlist: [],
    },
    surfaces: [], clients: [],
  },
  channelTrust: {
    providers: [], dm_policies: [], group_policies: [], default_dm_policy: 'pairing',
    default_group_policy: 'disabled', pairing_code_ttl_secs: 600,
  },
  // What the full shell reads once setup is done (the re-run case).
  agents: { agents: [] },
  discover: { enabled: false, visible_count: 0, areas: [] },
  doctor: { ok: true, capabilities: {} },
  skillProposals: { proposals: [], lastReview: null },
  modelsLoaded: {
    loaded: [], providers: [],
    pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 90, warn: false, source: 'unavailable' },
  },
}

vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      calls.push({ method: prop, args })
      if (prop === 'incident') return Promise.resolve(incident)
      if (prop === 'dashboardConfig') return Promise.resolve(identity)
      return Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : [])
    },
  })
  return { ...real, api: stub }
})

// Imported AFTER the mocks so the shell picks them up.
const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
const { IdentityProvider } = await import('./identity')
const { clearOnboardingExit } = await import('./onboarding/exitTo')
const { requestSetupRerun } = await import('./onboarding/rerun')
// Settings is a lazy route; resolving its chunk once keeps the assertions about the app rather than
// about a module transform.
await import('../pages/settings/SettingsPage')

/** `main.tsx`'s provider stack around the real shell, opened at *hash*. */
function openAt(hash: string) {
  location.hash = hash
  return render(
    <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
      <App />
    </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>,
  )
}

const setupHeading = () => screen.findByRole('heading', { name: /^Welcome to / })
const pageHeading = (name: string | RegExp) => screen.findByRole('heading', { level: 1, name })

beforeEach(() => {
  calls.length = 0
  incident = { active: false, reason: '', started_at: '' }
  identity = FRESH_HOME
  localStorage.clear()
  sessionStorage.clear()
  clearOnboardingExit()
})
afterEach(cleanup)

describe('a home whose setup is not done reaches the controls that cut off access', () => {
  it('Settings › Devices opens, not setup', async () => {
    openAt('#/settings/devices')
    expect(await pageHeading('Devices')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: /^Welcome to / }), 'setup stood in front of Devices').toBeNull()
    expect(screen.queryByText(/Setup comes first/)).toBeNull()
    expect(location.hash).toBe('#/settings/devices')
  })

  it('"Sign out all other devices" asks, then signs them out', async () => {
    openAt('#/settings/devices')
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out all other devices' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Sign out all other devices?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign out all others' }))
    await waitFor(() => expect(called('devicesRevokeOthers')).toHaveLength(1))
  })

  it('one device signs out from its own row', async () => {
    openAt('#/settings/devices')
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out Phone' }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Sign out Phone?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign out' }))
    await waitFor(() => expect(called('deviceRevoke')).toEqual([['dev-phone']]))
  })

  it('Settings › Security replaces the sign-in key, which signs every device out', async () => {
    openAt('#/settings/security')
    fireEvent.click(await screen.findByRole('button', { name: /Replace the key/ }))
    const dialog = await screen.findByRole('alertdialog', { name: 'Replace the sign-in key?' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Replace and sign everyone out' }))
    await waitFor(() => expect(called('rotateSigningKey')).toHaveLength(1))
  })

  it('External access and Sender trust open too', async () => {
    openAt('#/settings/external-access')
    // The panel titles itself "External Access"; Settings lists it as "External access".
    expect(await pageHeading(/^External access$/i)).toBeTruthy()
    cleanup()
    openAt('#/settings/sender-trust')
    expect(await pageHeading('Sender trust')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: /^Welcome to / })).toBeNull()
  })
})

describe('…and the switch that stops the agent', () => {
  it('the incident kill switch on Settings › Guardrails turns on', async () => {
    openAt('#/settings/guardrails')
    const toggle = await screen.findByRole('switch', { name: 'Incident mode' })
    // Enabled only once the switch's state has been read; a guessed state is never offered.
    await waitFor(() => expect(toggle).not.toBeDisabled())
    fireEvent.click(toggle)
    await waitFor(() => expect(called('incidentOn')).toHaveLength(1))
  })

  it('an active incident is said on every one of these pages, with its Resume', async () => {
    incident = { active: true, reason: 'Activated from Settings', started_at: '2026-10-02T08:00:00Z' }
    openAt('#/settings/devices')
    // Found among the page's live regions by what it says: the toast host's region is one too.
    const banner = await waitFor(() => {
      const found = screen.getAllByRole('alert').find((el) => /Incident mode is active/.test(el.textContent ?? ''))
      expect(found, 'the incident banner').toBeTruthy()
      return found as HTMLElement
    })
    expect(within(banner).getByRole('button', { name: 'Resume' })).toBeTruthy()
  })
})

describe('the pages that run on setup’s answers still wait for it', () => {
  it.each(['settings/providers', 'settings', 'chat'])('#/%s shows setup, and says it is a deferral', async (route) => {
    openAt(`#/${route}`)
    expect(await setupHeading()).toBeTruthy()
    expect(await screen.findByText(/Setup comes first/)).toBeTruthy()
    expect(screen.queryByRole('heading', { level: 1, name: 'Devices' })).toBeNull()
  })

  it('a destination setup deferred stays promised across a visit to the controls', async () => {
    // Visiting Devices during setup is not the end of setup: the page asked for before is still
    // where finishing lands.
    openAt('#/settings/providers')
    expect(await screen.findByText(/Setup comes first/)).toBeTruthy()
    location.hash = '#/settings/devices'
    expect(await pageHeading('Devices')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Back to setup' }))
    expect(await setupHeading()).toBeTruthy()
    expect(await screen.findByText(/Setup comes first/)).toBeTruthy()
  })
})

describe('setup and the controls lead to each other', () => {
  it('setup has a door to them', async () => {
    openAt('#/onboarding')
    await setupHeading()
    const door = await screen.findByRole('link', { name: 'Security controls' })
    expect(door.getAttribute('href')).toBe('#/settings/devices')
  })

  it('from a control page the way back is setup, not the Settings home', async () => {
    openAt('#/settings/devices')
    await pageHeading('Devices')
    expect(screen.queryByRole('button', { name: 'Back to Settings' }), 'the Settings home waits for setup').toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Back to setup' }))
    expect(await setupHeading()).toBeTruthy()
    expect(location.hash).toMatch(/^#\/onboarding/)
  })

  it('each control page names the others, and nothing else', async () => {
    openAt('#/settings/devices')
    const nav = await screen.findByRole('navigation', { name: 'Security controls' })
    // In Settings' own order, this page named rather than linked.
    expect(within(nav).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual([
      '#/settings/security', '#/settings/sender-trust', '#/settings/guardrails', '#/settings/external-access',
    ])
    expect(within(nav).getByText('Devices').getAttribute('aria-current')).toBe('page')
    // The rail is not drawn: every destination on it but these would only lead back to setup.
    expect(screen.getAllByRole('navigation'), 'the only navigation on the page is these pages').toEqual([nav])
  })
})

describe('a deliberate re-run holds the pages the same way, so it opens them the same way', () => {
  it('from "Setup, again", the door opens Devices with the way back to setup', async () => {
    // A re-run holds every other page as a first run does, so the full shell around Devices would
    // offer a rail whose every destination leads back into setup.
    identity = { user_name: 'Ada Example', username: 'ada' }
    openAt('#/settings/account')
    requestSetupRerun()
    expect(await screen.findByRole('heading', { name: 'Setup, again' })).toBeTruthy()
    const door = await screen.findByRole('link', { name: 'Security controls' })
    expect(door.getAttribute('href')).toBe('#/settings/devices')
    location.hash = '#/settings/devices'
    expect(await pageHeading('Devices')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Back to Settings' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Back to setup' }))
    expect(await screen.findByRole('heading', { name: 'Setup, again' })).toBeTruthy()
  })
})
