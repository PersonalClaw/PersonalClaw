import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { McpPoolTile } from './ToolsPage'
import type { McpPoolStats } from '../../lib/api'

// ── The pool facts the tile dropped ────────────────────────────────────────────
//
// `pool_stats()` returns 9 metrics + `available`. The tile rendered 6. Of the three it dropped:
//
//   configured_servers   REAL and load-bearing — it is the DENOMINATOR the others are read
//                        against. "0 live" means something different out of 1 configured than out
//                        of 6. Worse, it was missing from the tile's GATE
//                        (`live_connections || spawns`), so a pool with servers configured and none
//                        spawned yet rendered nothing at all — precisely the state where "knows
//                        about N, opened none" is the useful answer. Same shape as #1003's
//                        tokens-only gate.
//   evicted              REAL, and the only counter here tied to SESSION lifecycle rather than
//                        pooling: `evict_session` drops a session's isolated connections on expiry
//                        while leaving shared ones alone. Shown only when non-zero.
//   served               a DISTINCTION, deliberately left unrendered. `reused = max(0, served -
//                        spawns)`, so the tile already shows the derived pooling payoff; the raw
//                        total adds a number without adding a fact. Pinned below so a later
//                        "surface every unread field" pass does not add it back.
//
// Both real fields were verified writer-first before any UI: `configured_servers` is
// `len(self._specs)` (live pool state) and `evicted` is incremented in `evict_session`, which the
// session-expire callback calls on a real path.

const base: McpPoolStats = {
  available: true,
  live_connections: 2,
  shared_conns: 1,
  session_conns: 1,
  configured_servers: 4,
  spawns: 3,
  reaps: 1,
  served: 9,
  evicted: 0,
  reused: 6,
}

const labels = (c: HTMLElement) =>
  [...c.querySelectorAll('.rounded-lg')].map((d) => d.textContent?.replace(/^\d+/, '').trim())

describe('configured_servers is shown and gates the tile', () => {
  it('renders the configured count', () => {
    const { container } = render(<McpPoolTile stats={base} />)
    expect(container.textContent).toContain('Configured')
    expect(labels(container)[0]).toBe('Configured')  // the denominator leads
  })

  it('renders for a pool with servers configured but nothing spawned yet', () => {
    // The old gate (`live_connections || spawns`) hid this entirely. "4 configured, 0 live" is a
    // real answer to "is my MCP set up?" — not an empty state.
    const { container } = render(
      <McpPoolTile stats={{ ...base, live_connections: 0, shared_conns: 0, session_conns: 0, spawns: 0, reaps: 0, served: 0, reused: 0 }} />)
    expect(container.textContent).toContain('MCP connection pool')
    expect(container.textContent).toContain('Configured')
  })

  it('still renders nothing for a genuinely empty pool', () => {
    // Nothing configured and nothing ever spawned — the one state worth hiding.
    const { container } = render(
      <McpPoolTile stats={{ ...base, live_connections: 0, shared_conns: 0, session_conns: 0, configured_servers: 0, spawns: 0, reaps: 0, served: 0, reused: 0 }} />)
    expect(container.textContent).toBe('')
  })

  it('renders nothing when the pool is unavailable, whatever the counters say', () => {
    const { container } = render(<McpPoolTile stats={{ ...base, available: false }} />)
    expect(container.textContent).toBe('')
  })

  it('renders nothing for a null stats object', () => {
    expect(render(<McpPoolTile stats={null} />).container.textContent).toBe('')
  })
})

describe('evicted appears only when a session eviction has happened', () => {
  it('shows the count when non-zero', () => {
    const { container } = render(<McpPoolTile stats={{ ...base, evicted: 3 }} />)
    expect(container.textContent).toContain('Evicted')
    expect(labels(container)).toContain('Evicted')
  })

  it('is absent at zero', () => {
    // On a single-session install this is permanently 0, and a zero cell beside six live ones
    // reads as a broken metric rather than one that has not happened yet.
    expect(render(<McpPoolTile stats={base} />).container.textContent).not.toContain('Evicted')
  })
})

describe('served stays unrendered — it is derivable, not dropped', () => {
  it('the tile shows Reused rather than the raw served total', () => {
    // reused = max(0, served - spawns). Showing both is a number without a fact.
    const { container } = render(<McpPoolTile stats={base} />)
    expect(container.textContent).toContain('Reused')
    expect(labels(container)).not.toContain('Served')
  })
})
