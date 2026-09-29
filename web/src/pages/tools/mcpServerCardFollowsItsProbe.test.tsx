import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'

// ── A server's card says what the server is now, after every change ──────────────────────────────
//
// After an add, an edit, an Allow or a switch-on, the gateway probes the server again and says
// `probing` until the probe lands, which takes as long as the server takes to start. The page read the
// list once, 400 ms after the change, and never again, so a server that was fine read "unknown" beside
// the tools it listed until the owner pressed Re-probe. The card now says it is checking the server,
// and the page reads the list again until no server is being checked.

let servers: Array<Record<string, unknown>> = []
let tools: Array<Record<string, unknown>> = []
const mcpServers = vi.fn(() => Promise.resolve(servers))

function mockModules() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools, load_failures: [] }),
      mcpServers,
      importableMcp: () => Promise.resolve({ servers: [], unreadable: [] }),
      mcpPoolStats: () => Promise.resolve({}),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: () => Promise.resolve([]),
      mcpReadOnlyServers: () => Promise.resolve([]),
    },
  }))
}

async function mount() {
  const { ToolsPage } = await import('./ToolsPage')
  function Harness() {
    const [query, setQueryState] = useState<Record<string, string>>({})
    const setQuery = (patch: Record<string, string | null | undefined>) => setQueryState((q) => {
      const next = { ...q }
      for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
      return next
    })
    return <ToolsPage query={query} setQuery={setQuery} />
  }
  render(<Harness />)
  await waitFor(() => expect(screen.getAllByText('notes').length).toBeGreaterThan(0))
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  mcpServers.mockClear()
  tools = []
})

describe('a server the gateway is checking', () => {
  it('says so, and the page reads the list again until the probe lands', async () => {
    servers = [{ name: 'notes', transport: 'stdio', status: 'probing', enabled: true, allowed: true, tools: [], error: '' }]
    mockModules()
    await mount()
    expect(screen.getByText('checking')).toBeInTheDocument()
    expect(screen.getByText('Checking the server…')).toBeInTheDocument()
    expect(screen.queryByText('unknown')).toBeNull()

    // The probe lands: the next read finds it connected, with its tool.
    servers = [{ name: 'notes', transport: 'stdio', status: 'ok', enabled: true, allowed: true, tools: [{ name: 'hello' }], error: '' }]
    tools = [{ name: 'mcp/notes/hello', provider: 'notes', serverTool: 'hello', description: 'say hello', parameters: {}, requires_approval: true, risk_level: 'caution' }]
    await waitFor(() => expect(screen.getByText('ready')).toBeInTheDocument(), { timeout: 5000 })
    expect(screen.queryByText('checking')).toBeNull()
    const reads = mcpServers.mock.calls.length
    expect(reads).toBeGreaterThan(1)

    // Nothing is being checked any more, so the page stops reading.
    await new Promise((r) => setTimeout(r, 1800))
    expect(mcpServers.mock.calls.length).toBe(reads)
  })
})

describe('the line a card shows in place of tools', () => {
  it('names the gateway’s reason as it wrote it, without claiming the server did not respond', async () => {
    const { noToolsLine } = await import('./ToolsPage')
    const refused = { name: 'gh', status: 'error', enabled: true, tools: [], error: 'gh refused the connection but does not publish how to sign in. If it takes an API key, add it to the server as a header instead.' }
    expect(noToolsLine(refused, 'error', undefined)).toBe(
      'Not connected — gh refused the connection but does not publish how to sign in. If it takes an API key, add it to the server as a header instead.')
    expect(noToolsLine({ ...refused, error: '' }, 'error', undefined)).toBe('Not connected — no tools available.')
    expect(noToolsLine({ ...refused, status: 'probing', error: '' }, 'checking', undefined)).toBe('Checking the server…')
    expect(noToolsLine({ ...refused, enabled: false }, 'disabled', undefined)).toBe('Server disabled.')
  })
})
