import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A mute the product can create, the product must be able to undo (issue 414) ──────────────────
//
// Three dismissals of a routing suggestion mute an agent PERMANENTLY. There is no expiry, and
// `is_suppressed` returns on the mute BEFORE it reads `cooldown_hours`, so no setting walks it back.
// `api.routingUnmute` + `api.routingStatus` were defined in `lib/api.ts` and called by NOTHING,
// while this panel's "Dismiss cooldown" hint told the user that three dismissals "mute it until you
// re-enable". Copy asserting a capability the product did not have.
//
// Measured on a live gateway with three real dismissals recorded per row, BEFORE this change:
//
//     surface                                    rendered                              undo
//     ──────────────────────────────────────────────────────────────────────────────────────
//     agent detail · zz414-probe-agent            "Muted — the auto-router stopped…"     1 button
//     agent detail · PersonalClaw (mixed case)    "Active — eligible for auto-routing"   0  ← LIED
//     agent detail · zz414-phantom-agent          no such page                           0
//     agent detail · personalclaw-coder           no Advanced disclosure at all          0
//     Settings › Chat › Agent routing             "…until you re-enable"                 0
//
// So AR2-8's per-agent control closed one of five rows, and three of the other four cannot be
// reached from a per-agent surface at all: `record_dismiss` writes a key without checking that an
// agent exists, an agent can be DELETED while muted, and reserved built-ins render no Advanced
// section. The fix is a list of the store's OWN keys, which is why the assertions below insist on
// a phantom name surviving the render.
//
// 🪤 THE EMPTY CASE IS ASSERTED TOO, deliberately. "Render only when non-empty" was the tempting
// shape, and it leaves the promise sentence pointing at a row that is not there on the day a user
// reads it — the mute happens silently in a chat, so the affordance has to be findable BEFORE it is
// needed. `tests/test_routing_mute_reachable.py` is this rail's backend half (the route → client →
// caller chain, derived from `server.py`, so a new mute endpoint cannot ship inert).

const notified: string[] = []
const unmuted: string[] = []
let statusCalls = 0

const MUTED = ['personalclaw', 'zz414-phantom-agent', 'zz414-probe-agent']

function mockApi(opts: { muted?: string[]; unmute?: (a: string) => Promise<unknown> } = {}) {
  const muted = opts.muted ?? MUTED
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as Record<string, unknown>),
        dashboardConfig: () => Promise.resolve({}),
        personalclawConfig: () => Promise.resolve({ agents_routing: { enabled: true, cooldown_hours: 24 }, session: {}, resilience: {}, checkpoints: {} }),
        // These are the `.then(d => d.x)`-unwrapped clients: they resolve to the ARRAY, and
        // handing back the envelope crashes the panel's other sections during render.
        sessionTemplates: () => Promise.resolve([]),
        agentProviders: () => Promise.resolve([]),
        savedAgents: () => Promise.resolve([]),
        agents: () => Promise.resolve({ agents: [], default_agent: '' }),
        routingStatus: () => {
          statusCalls += 1
          return Promise.resolve({
            enabled: true,
            muted,
            dismissals: Object.fromEntries(muted.map((m) => [m, { count: 3, last_dismissed_at: 1 }])),
          })
        },
        routingUnmute: opts.unmute ?? ((agent: string) => { unmuted.push(agent); return Promise.resolve({ ok: true, agent }) }),
      },
    }
  })
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string) => { notified.push(msg) },
  }))
}

beforeEach(() => {
  vi.resetModules()
  notified.length = 0
  unmuted.length = 0
  statusCalls = 0
  sessionStorage.clear()
})

describe('Settings › Chat › Agent routing can undo a mute', () => {
  it('lists every key the store holds — including one with no agent to visit', async () => {
    mockApi()
    const { ChatPanel } = await import('./ChatPanel')
    render(<ChatPanel />)
    await screen.findByText('Muted agents')
    // 🔑 The phantom is the load-bearing one: it has no agent detail page, so before this row it
    // was durable state with no surface anywhere in the app that could clear it.
    for (const name of MUTED) expect(await screen.findByText(name), `${name} must be listed`).toBeTruthy()
    // Queried by the name a screen reader HEARS, not by the visible word: this is a column of N
    // identical "Unmute" buttons, so the accessible name has to carry the row's own key or the
    // actions list announces one indistinguishable label N times. `design/rowActionNames` bounds
    // exactly that population, and asserting per-key here is what keeps the subject from being
    // dropped later — a bare `/^Unmute$/` would pass on a button that named nothing.
    for (const name of MUTED) {
      expect(await screen.findByRole('button', { name: `Unmute ${name}` }), `${name}'s undo must name its row`).toBeTruthy()
    }
    const buttons = await screen.findAllByRole('button', { name: /^Unmute / })
    expect(buttons.length, 'one Unmute per muted key').toBe(MUTED.length)
    expect(statusCalls, 'the status endpoint is actually called — it had no caller at all').toBeGreaterThan(0)
  })

  it('explains WHY each agent is muted, not just that it is', async () => {
    mockApi({ muted: ['zz414-probe-agent'] })
    const { ChatPanel } = await import('./ChatPanel')
    render(<ChatPanel />)
    expect(await screen.findByText(/3 dismissals/), 'the dismissal count is the explanation').toBeTruthy()
  })

  it('sends back the STORE key, not a prettified name', async () => {
    mockApi({ muted: ['personalclaw'] })
    const { ChatPanel } = await import('./ChatPanel')
    render(<ChatPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Unmute personalclaw' }))
    await waitFor(() => expect(unmuted).toEqual(['personalclaw']))
  })

  it('a refused unmute says so and keeps the row — the click must not read as having worked', async () => {
    mockApi({ muted: ['zz414-probe-agent'], unmute: () => Promise.reject(new Error('store is read-only')) })
    const { ChatPanel } = await import('./ChatPanel')
    render(<ChatPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Unmute zz414-probe-agent' }))
    await waitFor(() => expect(notified.some((m) => /store is read-only/.test(m))).toBe(true))
    expect(notified[0], "carries the server's own sentence").toMatch(/Couldn't unmute zz414-probe-agent/)
    expect(screen.getByText('zz414-probe-agent'), 'the row stays, because the mute did').toBeTruthy()
  })

  it('says so honestly when nothing is muted, rather than hiding the affordance', async () => {
    mockApi({ muted: [] })
    const { ChatPanel } = await import('./ChatPanel')
    render(<ChatPanel />)
    expect(await screen.findByText(/None — no agent is muted/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Unmute/ }), 'nothing to undo').toBeNull()
  })
})

describe('the copy no longer promises a control that does not exist', () => {
  const src = () => readFileSync(join(process.cwd(), 'src', 'pages/settings/ChatPanel.tsx'), 'utf8')
  const strip = (s: string) =>
    s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('reads the real file', () => {
    expect(strip(src()), 'the Agent routing section must still be here').toContain('Dismiss cooldown')
  })

  it('the dismiss-cooldown hint no longer implies the cooldown field re-enables an agent', () => {
    // The exact sentence, measured live at ChatPanel.tsx:185 before the fix:
    //   "…suppress it for this long (three dismissals mute it until you re-enable)."
    // Setting the field to 0 does NOT clear a mute — `is_suppressed` returns on the mute first —
    // so the parenthetical pointed the user at the one control that provably cannot do it.
    expect(strip(src()), 'the unqualified promise must be gone').not.toContain('until you re-enable')
  })

  it('the hint names where the undo actually lives', () => {
    const hint = /hint="After you dismiss a suggestion for an agent[^"]*"/.exec(strip(src()))?.[0] ?? ''
    expect(hint, 'the cooldown hint must still exist').toBeTruthy()
    expect(hint, 'and point at the row that owns the undo').toMatch(/Muted agents/)
  })

  it('the muted list is rendered UNCONDITIONALLY — no guard of any kind', () => {
    // Measured: with a mute recorded, PATCHing agents_routing.enabled false→true left `muted`
    // unchanged, and so did dragging cooldown_hours to 0. Hiding the only working undo behind
    // either control would recreate the trap. `enabled &&` was the specific temptation; the
    // assertion is deliberately wider than that, because `{false && …}` is what a mutation drive
    // reached for and the narrow form stayed green on it.
    const code = strip(src())
    const at = code.indexOf('<MutedAgentsField')
    expect(at, 'the field must be rendered').toBeGreaterThan(-1)
    const line = code.slice(code.lastIndexOf('\n', at) + 1, at)
    expect(line.trim(), 'no conditional may gate the row').toBe('')
  })
})
