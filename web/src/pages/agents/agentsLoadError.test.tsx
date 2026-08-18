import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The swallow was in the ADAPTER, and then one level deeper still ──────────────────────────────
//
// `#/agents` answered a failed read with "No native agents · Create an agent to define its model,
// system prompt, skills, tools, triggers, and workflows" — the newcomer empty state, over a network
// failure. Two layers had to be fixed, and finding the second one is the point:
//
//   1. `useAgentsData` called `useCachedData(...)` and DROPPED its `error`, returning `data ?? []`.
//      The page could not have told a failure from an empty catalog even if it wanted to.
//   2. 🔑 Fixing only that would have shipped an INERT control: `fetchAgentGroups` awaits
//      `Promise.allSettled([api.agents(), api.agentProviders()])`, which NEVER rejects — a failed
//      native read became `agents: []` and the fetcher resolved happily. So the hook's `error` could
//      never have been anything but `undefined`.
//
// The native slice's rejection now propagates (it is the read the empty state makes a claim about),
// while the ACP provider slices stay tolerant — an unready runtime is still rendered as its own
// group, which is that surface's whole job. Asserted from BOTH sides below.

const nativeOk = { agents: [{ name: 'scout', model: 'x' }], default_agent: 'scout' }
const boom = () => Promise.reject(new Error('gateway down'))

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      agents: () => Promise.resolve(nativeOk),
      agentProviders: () => Promise.resolve([]),
      syncAgents: () => Promise.resolve({ ok: true }),
      ...over,
    },
  }))
}

async function mount() {
  const { AgentsListPage } = await import('./AgentsListPage')
  render(<AgentsListPage query={{}} setQuery={() => {}} onCreate={() => {}} />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/agents distinguishes a failed read from an empty catalog', () => {
  it('shows a retryable LoadError when the native read rejects', async () => {
    mockApi({ agents: boom })
    await mount()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/agents/i)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No native agents' }), 'not the newcomer state').toBeNull()
  })

  it('still shows "No native agents" when the catalog really is empty', async () => {
    mockApi({ agents: () => Promise.resolve({ agents: [], default_agent: '' }) })
    await mount()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No native agents' })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty catalog is not an error').toBeNull()
  })

  it('a failed PROVIDER read still renders the native list — partial tolerance is the design', async () => {
    // The distinction this cycle deliberately kept: an unreachable ACP runtime must not take the
    // page down, because showing it as an unready group IS the feature.
    mockApi({ agentProviders: boom })
    await mount()
    await waitFor(() => expect(screen.getByText('scout')).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a provider outage is not a page failure').toBeNull()
  })
})

describe('the adapter no longer swallows, and neither does its fetcher', () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/agents/agentsData.ts'), 'utf8')
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('re-exposes the read error, matching the one adapter that always did', () => {
    expect(code, 'the hook must return the error it reads').toMatch(/return \{[\s\S]*?\berror\b/)
    expect(code, "and a `loaded` flag — `groups: []` cannot say 'not read yet'").toMatch(/loaded: data !== undefined/)
  })

  it('the native slice rejects rather than degrading to an empty group', () => {
    // 🪤 The assertion that stops the inert-control regression: without this throw the exposed
    // `error` is permanently undefined, and every DOM test above would go green on a dead branch.
    expect(code).toMatch(/if \(nat\.status === 'rejected'\) throw nat\.reason/)
    expect(code, 'the native agents no longer come from a fulfilled-check fallback')
      .not.toMatch(/nat\.status === 'fulfilled' \? nat\.value\.agents : \[\]/)
  })

  it('the provider slice is still tolerant', () => {
    expect(code, 'provs keeps its fulfilled-check — a dead runtime is shown, not fatal')
      .toMatch(/provs\.status === 'fulfilled'/)
  })
})
