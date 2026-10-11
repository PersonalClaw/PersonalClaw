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
const saveListEdits = vi.fn()
const setPersistentSessions = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    externalAccess: (...a: unknown[]) => externalAccess(...a),
    patchConfig: (...a: unknown[]) => patchConfig(...a),
    saveListEdits: (...a: unknown[]) => saveListEdits(...a),
    externalAccessCreateClient: vi.fn(),
    externalAccessRevokeClient: vi.fn(),
    externalAccessSetClientDisabled: vi.fn(),
    externalAccessSetClientPersistentSessions: (...a: unknown[]) => setPersistentSessions(...a),
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
    saveListEdits.mockResolvedValue([])
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

  it('adding a host sends that one host to external_access.capture.upstream_allowlist — never the list', async () => {
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Add to capture upstream allow-list')).toBeTruthy())
    await userEvent.type(
      screen.getByLabelText('Add to capture upstream allow-list'),
      'api.anthropic.com{Enter}',
    )
    // The nested spelling is the assertion. `external_access.capture_upstream_allowlist` — the flat
    // form the neighbouring retention knob uses — is NOT in the PATCH allowlist, so a control that
    // wrote it would move on screen and 400 on the wire.
    // The edit from the painted list to the next one; `saveListEdits` sends only its difference
    // (one `add`), so a host another tab added since is not dropped by this save.
    await waitFor(() =>
      expect(saveListEdits).toHaveBeenCalledWith('external_access.capture.upstream_allowlist',
        ['api.openai.com'], ['api.openai.com', 'api.anthropic.com']),
    )
    expect(patchConfig.mock.calls.filter((c) => c[0] === 'external_access.capture.upstream_allowlist')).toEqual([])
    // VACUITY / cross-wiring floor: editing the allow-list must not write a neighbouring cap.
    expect(
      patchConfig.mock.calls.filter((c) => c[0] === 'external_access.capture.retention_days'),
    ).toEqual([])
  })

  it('capture retention PATCHes the NESTED key, and the saved value round-trips back (#2950)', async () => {
    // The flat `external_access.capture_retention_days` used to be both the rendered value's
    // source AND the PATCH target — which looked coherent but wasn't. `load()` always prefers
    // the nested `capture.retention_days` once it exists in config.json (a fresh install ships
    // it), so a PATCH to the flat name wrote the file and changed nothing `load()` ever read
    // back: 200 OK, value unchanged on the next GET. This is the same trap
    // `capture.upstream_allowlist` already avoids — the assertion is on the CALL SITE, and on
    // the value the panel repaints from after its post-save refetch, not just that a call
    // happened.
    externalAccess.mockResolvedValueOnce(STATE).mockResolvedValue({
      ...STATE,
      caps: { ...STATE.caps, capture_retention_days: 45 },
    })
    render(<ExternalAccessPanel />)
    await waitFor(() =>
      expect(screen.getByLabelText('Keep captured sessions for (days)')).toBeTruthy(),
    )
    const retention = screen.getByLabelText(
      'Keep captured sessions for (days)',
    ) as HTMLInputElement
    await userEvent.clear(retention)
    await userEvent.type(retention, '45{Enter}')
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith('external_access.capture.retention_days', 45),
    )
    // The legacy flat spelling must never be the PATCH target again.
    expect(
      patchConfig.mock.calls.filter((c) => c[0] === 'external_access.capture_retention_days'),
    ).toEqual([])
    // Round-trip: the panel refetches after a save, so 45 is what it repaints from — a
    // control that only mutated local state, or PATCHed a key `load()` ignores, would still
    // show the OLD value here.
    await waitFor(() => expect(retention.value).toBe('45'))
  })

  it('removing a host sends that one removal, and the value round-trips back into the pane', async () => {
    externalAccess.mockResolvedValueOnce(STATE).mockResolvedValue({
      ...STATE,
      caps: { ...STATE.caps, capture_upstream_allowlist: [] },
    })
    render(<ExternalAccessPanel />)
    await waitFor(() => expect(screen.getByLabelText('Remove api.openai.com')).toBeTruthy())
    await userEvent.click(screen.getByLabelText('Remove api.openai.com'))
    await waitFor(() =>
      expect(saveListEdits).toHaveBeenCalledWith('external_access.capture.upstream_allowlist', ['api.openai.com'], []),
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
      'external_access.capture.retention_days',
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

  // ── The "deliberately a `config.json` edit" note ─────────────────────────────────────────────
  //
  // JSX text is not Markdown: a backticked span in plain text renders the backticks literally
  // (a `` `config.json` `` reads as a literal backtick-config.json-backtick), not as code. The
  // sibling note in the "Remote access" section below already gets this right
  // (`<code>config.json</code>`); the note under "Limits" used to read literally. Render the
  // panel, find the note that names `config.json`, and assert two things at once:
  //   1. the note contains a `<code>` element with text `config.json` (the Markdown intent), and
  //   2. the note's rendered text has NO backtick character at all (the Markdown-impostor test).
  it('renders `config.json` as a <code> element in the Limits note, never a literal backtick', async () => {
    render(<ExternalAccessPanel />)
    // The Limits note's first sentence names `config.json` and is the only prose span that does —
    // `getAllByText` because the Remote access section below ALSO names it (correctly), so
    // finding the ONE that fails would not be the assertion.
    const matches = await screen.findAllByText(/not editable here/i)
    expect(matches.length).toBeGreaterThan(0)
    // Walk each matching container's `<code>` descendants: at least one must contain the
    // literal `config.json` text, and the union of all text in those containers must be
    // backtick-free. jsdom keeps whitespace between elements, so we read `textContent` and
    // assert no `` ` `` appears anywhere — a backtick in any sibling would fail the second
    // half of the assertion even if the first half passed.
    let foundCode = false
    for (const root of matches) {
      const container = root.closest('div') ?? root
      const codes = container.querySelectorAll('code')
      for (const c of codes) {
        if ((c.textContent ?? '').trim() === 'config.json') foundCode = true
      }
      expect(container.textContent ?? '').not.toContain('`')
    }
    expect(foundCode, 'a <code>config.json</code> element must render where the Limits note is').toBe(true)
  })
})

// ── A token's lifetime is stated where the surface and the client are ────────────────────────────
//
// A surface's own token can stop working while the surface stays on for registered clients, and a
// client's token ends 90 days after it was issued at the latest. Neither may read as "fine".

describe('the page says when an integration token stopped working', () => {
  const NOW = Math.floor(Date.now() / 1000)

  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    patchConfig.mockResolvedValue({})
    saveListEdits.mockResolvedValue([])
  })

  it('names an expired surface token beside a surface that is still on for its clients', async () => {
    externalAccess.mockResolvedValue({
      ...STATE,
      surfaces: [{ ...STATE.surfaces[0], token_state: 'expired', token_expires_at: NOW - 60 }],
    })
    render(<ExternalAccessPanel />)
    const pill = await screen.findByText('token expired')
    expect(pill.getAttribute('title')).toBe(
      "This surface's own token no longer works (token expired); registered clients keep their own. Create a new one with personalclaw inbound token create mcp --rotate.",
    )
    expect(screen.queryByText('not serving')).toBeNull()
  })

  it('says nothing about a surface token that works', async () => {
    externalAccess.mockResolvedValue({
      ...STATE,
      surfaces: [{ ...STATE.surfaces[0], token_state: 'live', token_expires_at: NOW + 86400 }],
    })
    render(<ExternalAccessPanel />)
    await screen.findByText('Requests per second')
    expect(screen.queryByText(/^token (expired|revoked|replaced|refused)$/)).toBeNull()
  })

  it('states when a client’s token stops working, and marks one that already has', async () => {
    const client = {
      client_id: 'abc', label: 'ide', surfaces: ['mcp'], agent: '', tools: [], scope: {},
      rate_overrides: {}, disabled: false, created_at: '', last_seen_at: '',
      requests_seen: 0, refusals_seen: 0,
    }
    externalAccess.mockResolvedValue({
      ...STATE,
      clients: [
        { ...client, expires_at: NOW + 7 * 86400 },
        { ...client, client_id: 'old', label: 'old-ide', expires_at: NOW - 86400 },
      ],
    })
    render(<ExternalAccessPanel />)
    expect(await screen.findByText(/^token works until /)).toBeTruthy()
    expect(screen.getByText(/^token stopped working /)).toBeTruthy()
    expect(screen.getAllByText('expired')).toHaveLength(1)
  })

  it('a control bridge that is on, with a token, and not listening yet says it starts at the next start', async () => {
    // Every surface on the dashboard's port serves from its next request. The bridge listens on a
    // port of its own, started with the gateway, so one turned on while PersonalClaw runs is the
    // one surface that can be on and silent — and the row says so instead of reading as serving.
    const bridge = {
      surface: 'bridge', enabled: true, allow_remote: false, token_configured: true,
      token_problem: '', loopback_only: true,
    }
    externalAccess.mockResolvedValue({ ...STATE, surfaces: [{ ...bridge, listening: false }] })
    const view = render(<ExternalAccessPanel />)
    const pill = await screen.findByText('not serving')
    expect(pill.getAttribute('title')).toBe(
      'On, but not serving: it starts listening the next time PersonalClaw starts.',
    )
    view.unmount()
    externalAccess.mockResolvedValue({ ...STATE, surfaces: [{ ...bridge, listening: true }] })
    render(<ExternalAccessPanel />)
    await screen.findByText('Requests per second')
    expect(screen.queryByText('not serving')).toBeNull()
  })
})

describe('a rate cap whose raise is declined or refused keeps the stored cap', () => {
  // Raising a cap is asked about (`external_access.rate_*` are security controls). This panel paints
  // only what the endpoint reports, so a declined question or a refused save moved nothing — and the
  // box kept the number that was typed while the cap in effect was the old one. The save now tells
  // the field it was not stored, and the field shows the cap again.
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    externalAccess.mockResolvedValue(STATE)
    saveListEdits.mockResolvedValue([])
  })

  async function raise(to: string) {
    render(<ExternalAccessPanel />)
    const box = (await screen.findByLabelText('Requests per second')) as HTMLInputElement
    await userEvent.clear(box)
    await userEvent.type(box, to)
    await userEvent.tab()
    return box
  }

  it('a declined consent puts the stored cap back, with a note and no alarm', async () => {
    const { ConsentDeclined } = await import('../../lib/securityConsent')
    patchConfig.mockRejectedValue(new ConsentDeclined('external_access.rate_rps'))
    const box = await raise('100')
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('external_access.rate_rps', 100))
    await waitFor(() => expect(box.value).toBe('1'))
    expect(screen.queryByRole('alert'), 'declining is a choice, not a failure').toBeNull()
  })

  it('a refused save puts it back too, and the refusal is named', async () => {
    patchConfig.mockRejectedValue(new Error('must be between 0.01 and 1000.0'))
    const box = await raise('5000')
    await waitFor(() => expect(box.value).toBe('1'))
    expect((await screen.findByRole('alert')).textContent).toContain('must be between 0.01 and 1000.0')
  })
})

// ── Whether a client keeps its conversation is chosen on its row ─────────────────────────────────
//
// The record always held the choice and the OpenAI-compatible endpoint always read it, but nothing
// showed or set it, so every client kept no conversation while the reference described one that
// keeps it. The row now shows which way each client is set, in words that say what that means, and
// changes it through the client's own route.

describe('a client of the OpenAI-compatible API keeps its conversation as you choose', () => {
  const NOTES = {
    client_id: 'notes', label: 'notes app', surfaces: ['openai'], agent: '', tools: [], scope: {},
    rate_overrides: {}, persistent_sessions: false, disabled: false, created_at: '',
    last_seen_at: '', expires_at: 0, requests_seen: 0, refusals_seen: 0,
  }
  const KEPT = 'notes app conversation: One per user'
  const ALONE = 'notes app conversation: Each request alone'

  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    patchConfig.mockResolvedValue({})
    saveListEdits.mockResolvedValue([])
    setPersistentSessions.mockResolvedValue({ ok: true })
  })

  it('shows the choice the client has, and says what it means', async () => {
    externalAccess.mockResolvedValue({ ...STATE, clients: [NOTES] })
    const view = render(<ExternalAccessPanel />)
    const alone = await screen.findByRole('button', { name: ALONE })
    expect(alone.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: KEPT }).getAttribute('aria-pressed')).toBe('false')
    expect(
      screen.getByText(/each request is answered as if it were its first/i).textContent,
    ).toContain('nothing an earlier one said reaches it')
    view.unmount()

    externalAccess.mockResolvedValue({ ...STATE, clients: [{ ...NOTES, persistent_sessions: true }] })
    render(<ExternalAccessPanel />)
    const kept = await screen.findByRole('button', { name: KEPT })
    expect(kept.getAttribute('aria-pressed')).toBe('true')
    const said = screen.getByText(/continue one conversation/i).textContent ?? ''
    expect(said).toContain('user field')
    expect(said).toContain('X-PersonalClaw-Session header')
  })

  it('choosing the other one sends it for that client, and the row shows what the gateway stored', async () => {
    externalAccess
      .mockResolvedValueOnce({ ...STATE, clients: [NOTES] })
      .mockResolvedValue({ ...STATE, clients: [{ ...NOTES, persistent_sessions: true }] })
    render(<ExternalAccessPanel />)
    await userEvent.click(await screen.findByRole('button', { name: KEPT }))
    await waitFor(() => expect(setPersistentSessions).toHaveBeenCalledWith('notes', true))
    expect(setPersistentSessions).toHaveBeenCalledTimes(1)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: KEPT }).getAttribute('aria-pressed')).toBe('true'),
    )
    // What a change does is said where it is made.
    expect(screen.getByText(/changing this starts its conversations over/i)).toBeTruthy()
    // VACUITY / cross-wiring floor: the choice is not a config key, and choosing again what is
    // already chosen sends nothing.
    expect(patchConfig).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: KEPT }))
    expect(setPersistentSessions).toHaveBeenCalledTimes(1)
  })

  it('a refused change is named, and the row keeps showing the stored choice', async () => {
    externalAccess.mockResolvedValue({ ...STATE, clients: [NOTES] })
    setPersistentSessions.mockRejectedValue(
      new Error('Only a client bound to the OpenAI-compatible surface has a conversation to keep; this one is not.'),
    )
    render(<ExternalAccessPanel />)
    await userEvent.click(await screen.findByRole('button', { name: KEPT }))
    expect((await screen.findByRole('alert')).textContent).toContain('has a conversation to keep')
    expect(screen.getByRole('button', { name: ALONE }).getAttribute('aria-pressed')).toBe('true')
  })

  it('a client the OpenAI-compatible API does not admit has no conversation to choose', async () => {
    externalAccess.mockResolvedValue({
      ...STATE,
      clients: [NOTES, { ...NOTES, client_id: 'ide', label: 'ide', surfaces: ['mcp'] }],
    })
    render(<ExternalAccessPanel />)
    // The client the API admits has the choice, so its absence beside it is the row's own.
    expect(await screen.findByRole('button', { name: ALONE })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^ide conversation:/ })).toBeNull()
    expect(screen.getAllByRole('button', { name: /conversation:/ })).toHaveLength(2)
  })
})
