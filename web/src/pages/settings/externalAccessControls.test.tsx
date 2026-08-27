import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ExternalAccess } from '../../lib/api'
import { ExternalAccessPanel } from './ExternalAccessPanel'

// ── The switches on this page have to actually reach the backend ───────────────────────────────────
//
// `#/settings/external-access` is the only surface in PersonalClaw that turns network exposure ON.
// The failure that matters here is not a wrong pixel: it is a control that renders, moves, and
// PATCHes nothing — the operator believes a surface is off while it is serving. So every assertion
// below is on the CALL SITE (`api.patchConfig` and the exact dotted path), never on the rendered
// switch alone.
//
// `caps` is the second thing measured. The read endpoint has always computed and shipped five cap
// numbers; the panel rendered none of them, which is the repo's recurring "backend truth, frontend
// silence" shape — a payload field with no reader. Each one is now a control, and each is asserted
// to PATCH its own config key rather than a neighbour's.

const externalAccess = vi.fn()
const patchConfig = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    externalAccess: (...a: unknown[]) => externalAccess(...a),
    patchConfig: (...a: unknown[]) => patchConfig(...a),
    externalAccessCreateClient: vi.fn(),
    externalAccessRevokeClient: vi.fn(),
    externalAccessSetClientDisabled: vi.fn(),
  },
}))

const STATE: ExternalAccess = {
  enabled: true,
  incident_active: false,
  public_url: '',
  caps: {
    rate_rps: 1,
    rate_burst: 20,
    rate_concurrent: 4,
    auto_disable_after_breaches: 10,
    capture_retention_days: 30,
    capture_upstream_allowlist: ['api.openai.com'],
  },
  surfaces: [
    {
      surface: 'mcp',
      enabled: true,
      allow_remote: false,
      token_configured: true,
      token_problem: '',
      loopback_only: false,
    },
    {
      surface: 'a2a',
      enabled: false,
      allow_remote: false,
      token_configured: false,
      token_problem: 'no token configured (run: personalclaw inbound token create a2a)',
      loopback_only: false,
    },
  ],
  clients: [],
}

describe('the external-access controls reach the backend', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    externalAccess.mockResolvedValue(STATE)
    patchConfig.mockResolvedValue({})
  })

  it('renders every cap the endpoint reports — none of them is a silent payload field', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Requests per second')).toBeTruthy())
    expect((screen.getByLabelText('Requests per second') as HTMLInputElement).value).toBe('1')
    expect((screen.getByLabelText('Burst') as HTMLInputElement).value).toBe('20')
    expect((screen.getByLabelText('Concurrent requests') as HTMLInputElement).value).toBe('4')
    expect((screen.getByLabelText('Switch a client off after') as HTMLInputElement).value).toBe('10')
    expect(
      (screen.getByLabelText('Keep captured sessions for (days)') as HTMLInputElement).value,
    ).toBe('30')
  })

  // ── The upstream allow-list: the cap whose ABSENCE was user-facing ──────────────────────────
  //
  // Backend round-trip was 4-of-5 (dataclass + _meta, load(), to_dict, PATCH allowlist) with no
  // control, and the default is EMPTY while the list is exclusive — so enabling capture produced a
  // blanket 502 with nothing in the UI to fix. These three tests are the fifth point: the value
  // arrives, an edit PATCHes the NESTED key `_EDITABLE_CONFIG` actually accepts, and it round-trips.

  it('renders the upstream allow-list the endpoint reports', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Add to capture upstream allow-list')).toBeTruthy())
    // The VALUE, not just the control: a chipped list bound to nothing renders an input and
    // no chips, which is exactly what a dead control looks like.
    expect(screen.getByText('api.openai.com')).toBeTruthy()
    expect(screen.getByLabelText('Remove api.openai.com')).toBeTruthy()
  })

  it('adding a host PATCHes external_access.capture.upstream_allowlist with the WHOLE list', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Add to capture upstream allow-list')).toBeTruthy())
    await userEvent.type(
      screen.getByLabelText('Add to capture upstream allow-list'),
      'api.anthropic.com{Enter}',
    )
    // The nested spelling is the assertion. `external_access.capture_upstream_allowlist` — the flat
    // form the neighbouring retention knob uses — is NOT in the PATCH allowlist, so a control that
    // wrote it would move on screen and 400 on the wire.
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('external_access.capture.upstream_allowlist', [
        'api.openai.com',
        'api.anthropic.com',
      ]),
    )
    // VACUITY / cross-wiring floor: editing the allow-list must not write a neighbouring cap.
    expect(
      patchConfig.mock.calls.filter((c) => c[0] === 'external_access.capture_retention_days'),
    ).toEqual([])
  })

  it('removing a host PATCHes the remaining list, and the value round-trips back into the pane', async () => {
    externalAccess.mockResolvedValueOnce(STATE).mockResolvedValue({
      ...STATE,
      caps: { ...STATE.caps, capture_upstream_allowlist: [] },
    })
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Remove api.openai.com')).toBeTruthy())
    await userEvent.click(screen.getByLabelText('Remove api.openai.com'))
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('external_access.capture.upstream_allowlist', []),
    )
    // Round-trip: the panel refetches after a save, so the emptied list must be what it repaints
    // from. A control that only mutated local state would still show the chip here.
    await waitFor(() => expect(screen.queryByText('api.openai.com')).toBeNull())
  })

  it('the MASTER switch PATCHes external_access.enabled', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Allow inbound access')).toBeTruthy())
    await userEvent.click(screen.getByLabelText('Allow inbound access'))
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('external_access.enabled', false),
    )
  })

  it('a surface switch PATCHes that surface, not the master', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText(/MCP tool surface/i)).toBeTruthy())
    await userEvent.click(screen.getByLabelText(/MCP tool surface/i))
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('external_access.mcp.enabled', false),
    )
    // The distinction is the point: a control wired to the master switch would still
    // "work" on screen while turning off four surfaces the operator did not touch.
    expect(patchConfig).not.toHaveBeenCalledWith('external_access.enabled', expect.anything())
  })

  it('each cap PATCHes its OWN key', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Burst')).toBeTruthy())
    const burst = screen.getByLabelText('Burst') as HTMLInputElement
    await userEvent.clear(burst)
    // `NumberField` commits on blur or Enter, never per-keystroke — so a test that only
    // types measures the local input state and never the PATCH. Enter is what a user
    // presses, so that is what this drives.
    await userEvent.type(burst, '25{Enter}')
    await waitFor(() =>
      expect(patchConfig.mock.calls.some((c) => c[0] === 'external_access.rate_burst')).toBe(true),
    )
    // VACUITY / cross-wiring floor: touching Burst must not write any other cap key.
    const otherCaps = [
      'external_access.rate_rps',
      'external_access.rate_concurrent',
      'external_access.auto_disable_after_breaches',
      'external_access.capture_retention_days',
    ]
    expect(patchConfig.mock.calls.filter((c) => otherCaps.includes(c[0] as string))).toEqual([])
  })

  it('says the public URL is not editable here rather than just omitting the control', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() =>
      expect(screen.getByText(/not set — every surface is loopback-only/i)).toBeTruthy(),
    )
    // An absent control with no explanation is indistinguishable from a missing feature,
    // and this one is absent on purpose — the endpoint refuses a write to it.
    // `getAllBy` because the sentence sits in a nested div and matches both it and its
    // parent — the claim is "the explanation is present", not "it appears exactly once".
    expect(screen.getAllByText(/not editable here/i).length).toBeGreaterThan(0)
  })
})
