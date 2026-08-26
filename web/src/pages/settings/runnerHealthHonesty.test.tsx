import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { AgentDefaultsPanel } from './AgentDefaultsPanel'

// ── Settings → Agents runner rows (EI-5, SC4) ────────────────────────────────
//
// Two things no python test can see, asserted where a user meets them:
//
//  · the VERBATIM probe error reaches the screen. A row that reads "unavailable" while the
//    API carried "'gemini' not found on PATH …" has thrown away the only part of the message
//    that tells you which binary to install. So the assertion is on the exact API string.
//  · an UNMEASURED value renders as unknown, never as a number. `latency_ms: null` must not
//    become "0 ms" — a zero reads as "the handshake was instant", which is a fabricated
//    measurement, and the same goes for an unparseable version and a never-recorded
//    capability matrix.

const agentRunners = vi.fn()
const personalclawConfig = vi.fn()

const VERBATIM = "'gemini' not found on PATH (looked for: gemini); set GEMINI_CLI_EXECUTABLE to override"

vi.mock('../../lib/api', () => ({
  api: {
    agentRunners: (probe?: boolean) => agentRunners(probe),
    personalclawConfig: () => personalclawConfig(),
    patchConfig: () => Promise.resolve({}),
    agents: () => Promise.resolve({ default_agent: '' }),
    setDefaultAgent: () => Promise.resolve({}),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../lib/agents', () => ({
  useAgentCatalog: () => ({ options: [], loading: false, discovered: [] }),
  ensureBindableAgentName: (v: string) => Promise.resolve(v),
}))
vi.mock('../../lib/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    const [error, setError] = useState<unknown>(null)
    useEffect(() => { fn().then(setData).catch(setError) }, [])
    return { data, error, refresh: () => {} }
  },
  invalidateKeys: () => {},
}))

const unhealthyRow = {
  id: 'gemini-cli', display_name: 'Gemini CLI', runtime_id: 'acp:gemini-cli', source: 'builtin',
  dialect: '', bin_names: ['gemini'],
  health: {
    ok: false, probe: 'path', checked_at: '2026-08-17T10:00:00+00:00',
    version: null, latency_ms: null, error: VERBATIM, resolved_command: [],
  },
  health_stale: false,
  capabilities: null,
  adapter: { npm_pkg: '', pinned: false, state: 'no_adapter', verified: true, detail: 'launches its own binary' },
  lease: null,
}

const healthyRow = {
  id: 'claude-code', display_name: 'Claude Code', runtime_id: 'acp:claude-code', source: 'builtin',
  dialect: 'claude-code', bin_names: ['claude'],
  health: {
    ok: true, probe: 'version', checked_at: '2026-08-17T10:00:00+00:00',
    version: '2.1.233', latency_ms: 58, error: null, resolved_command: ['/usr/bin/claude'],
  },
  health_stale: false,
  capabilities: {
    source: 'initialize', recorded_at: '2026-08-17T10:00:00+00:00',
    models: ['m1', 'm2'], permission_modes: ['default', 'acceptEdits'], efforts: ['low', 'high'],
  },
  adapter: { npm_pkg: '@x/adapter', pinned: false, state: 'unverified', verified: false, detail: 'no recorded provenance' },
  lease: null,
}

describe('the runner rows in Settings → Agent defaults', () => {
  beforeEach(() => {
    personalclawConfig.mockResolvedValue({ agent: { unattended_requires_verified_adapter: false } })
    agentRunners.mockResolvedValue([healthyRow, unhealthyRow])
  })

  it('prints the probe error verbatim, not a house paraphrase', async () => {
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText(VERBATIM)).toBeTruthy())
  })

  it('renders an unmeasured latency and version as unknown, never as a number', async () => {
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('latency unknown')).toBeTruthy())
    expect(screen.getByText('version unknown')).toBeTruthy()
    // The fabrication this guards: `latency_ms: null` rendered through a `?? 0`.
    expect(screen.queryByText('0 ms')).toBeNull()
  })

  it('shows measured evidence and capability chips for a healthy runner', async () => {
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('v2.1.233')).toBeTruthy())
    expect(screen.getByText('58 ms')).toBeTruthy()
    expect(screen.getByText('acceptEdits')).toBeTruthy()
    expect(screen.getByText('2 models')).toBeTruthy()
  })

  it('marks an overdue check without contradicting the reading itself', async () => {
    // The row still says "healthy" — that was the measurement — but it also says the
    // measurement is older than agent.runner_health_check_secs, so the user is not
    // reading a stale value as the present state.
    agentRunners.mockResolvedValue([{ ...healthyRow, health_stale: true }])
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('check overdue')).toBeTruthy())
    expect(screen.getByText('healthy')).toBeTruthy()
  })

  it('does not mark a fresh or unknown-age check overdue', async () => {
    // VACUITY FLOOR for the chip: `health_stale: false` is a positive statement of
    // freshness and `null` is "we do not know the age" (never probed, unparseable
    // timestamp). Neither may render the overdue chip, or every row wears it forever.
    agentRunners.mockResolvedValue([healthyRow, { ...unhealthyRow, health_stale: null }])
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('healthy')).toBeTruthy())
    expect(screen.queryByText('check overdue')).toBeNull()
  })

  it('says capabilities are unknown when no handshake was ever recorded', async () => {
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText(/Capabilities unknown/)).toBeTruthy())
  })

  it('surfaces each row adapter verdict so the gate is legible before it fires', async () => {
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('adapter unverified')).toBeTruthy())
    expect(screen.getByText('no recorded provenance')).toBeTruthy()
  })

  it('renders the failure instead of an empty runner list when the read fails', async () => {
    agentRunners.mockRejectedValue(new Error('boom'))
    render(<AgentDefaultsPanel />)
    // A swallowed rejection would paint "No runners in the catalog", which reads as
    // "you have none" rather than "we could not ask".
    await waitFor(() => expect(screen.getByText(/Couldn't load your runners/i)).toBeTruthy())
    expect(screen.queryByText(/No runners in the catalog/)).toBeNull()
  })

  // ── EI-6 SC5: "its lease holder is visible in Settings" ────────────────────
  //
  // The clause is a SURFACE claim, so it is asserted on rendered text. A python test can
  // only prove the endpoint carries `lease` — deleting the chip that reads it would leave
  // every backend test green and the user with no way to see who holds a runner.

  const held = {
    holder: 'chat:web:alice', taken_at: 0, expires_at: 0, renewals: 2,
    age_secs: 42, expires_in_secs: 1758,
  }

  it('names the session holding a runner, and says when idle-release takes it back', async () => {
    agentRunners.mockResolvedValue([{ ...healthyRow, lease: held }, unhealthyRow])
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('held by chat:web:alice')).toBeTruthy())
    // Not just WHO but for how long and how long until release — "held by X" alone does not
    // tell a user whether to wait or to go look at that session.
    expect(screen.getByText('held for 42s')).toBeTruthy()
    expect(screen.getByText('released in 1758s if idle')).toBeTruthy()
    // The health verdict is untouched: a held runner is still a healthy one.
    expect(screen.getByText('healthy')).toBeTruthy()
  })

  it('shows no holder for a free runner', async () => {
    // VACUITY FLOOR. Without this, "the holder is visible" could be satisfied by a chip
    // that renders unconditionally — every runner would look permanently taken, which is
    // the exact misreading idle-release exists to prevent.
    agentRunners.mockResolvedValue([healthyRow, unhealthyRow])
    render(<AgentDefaultsPanel />)
    await waitFor(() => expect(screen.getByText('healthy')).toBeTruthy())
    expect(screen.queryByText(/held by/)).toBeNull()
    expect(screen.queryByText(/released in/)).toBeNull()
  })
})
