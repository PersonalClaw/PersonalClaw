import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'

// ── A server no agent can call says so, and says why ────────────────────────────────────────────
//
// Every external MCP server's tools reach an agent through the built-in MCP Tool Servers
// provider. When it is switched off or failed to load, the gateway answers `status: 'unserved'`
// for a server it connected to. The page drew any status it did not know as its raw word in a
// caption, with the reason in a `title` — `unserved`, and a reason no touch screen can show. So
// the page names the state in words and says the reason in the page. It sends nobody to the
// Store: the provider ships with PersonalClaw, so there is nothing there to install.

const REASON = 'Connected, but no agent can call its tools.'
let servers: unknown[] = []
const tools = [
  { name: 'mcp/notes/list_directory', provider: 'notes', description: 'list a folder', parameters: {}, requires_approval: true, risk_level: 'safe' },
]

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools, load_failures: [] }),
      mcpServers: () => Promise.resolve(servers),
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
  await waitFor(() => expect(screen.getByText('mcp/notes/list_directory')).toBeInTheDocument())
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
})

describe('a connected server whose tools no agent can call', () => {
  it('names the state in words and says, in the page, why no agent can call it', async () => {
    servers = [{ name: 'notes', status: 'unserved', enabled: true, tools: ['list_directory'], error: REASON }]
    mockApi()
    await mount()
    expect(screen.getByText("agents can't call it")).toBeInTheDocument()
    expect(screen.queryByText('unserved')).toBeNull()
    expect(screen.getByText(/No agent can call these tools right now/)).toBeInTheDocument()
    expect(screen.getByText(/switched off or failed to load/)).toBeInTheDocument()
    // A provider that ships built in is never something to fetch from the Store.
    expect(screen.queryByRole('link', { name: /Store/ })).toBeNull()
  })

  it('says nothing of the kind once an agent can call it', async () => {
    servers = [{ name: 'notes', status: 'ok', enabled: true, tools: ['list_directory'], error: '' }]
    mockApi()
    await mount()
    expect(screen.getByText('ready')).toBeInTheDocument()
    expect(screen.queryByText(/No agent can call these tools right now/)).toBeNull()
  })

  it('says nothing of the kind for a server the user turned off', async () => {
    servers = [{ name: 'notes', status: 'unserved', enabled: false, tools: ['list_directory'], error: REASON }]
    mockApi()
    await mount()
    expect(screen.queryByText(/No agent can call these tools right now/)).toBeNull()
  })
})
