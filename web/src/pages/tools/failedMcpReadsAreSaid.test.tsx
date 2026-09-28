import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── A failed MCP read is said where its answer would have been ───────────────────────────────────
//
// `/api/mcp/importable` answered a failed look through the other tools' settings as 200
// `{"servers": []}`, and this page turned any rejection of it — or of the server list — into `[]`.
// So a failure read as "nothing to import", and a failed server list as "no MCP servers". The
// gateway now answers the failure as a failure; these pin the page's half. It says what failed, in
// the gateway's words, and what to do; it offers the read again; and it keeps the rest of the page,
// because the reads stay tolerated (`toolsIndexLoadError.test.tsx` holds that half).

const idx = {
  tools: [
    { name: 'read_file', description: 'Read a file', provider: 'native', risk_level: 'safe' },
    { name: 'mcp/notes/search', description: 'Search the notes', provider: 'notes', serverTool: 'search' },
  ],
  load_failures: [],
}
const IMPORT_FAILED = "Couldn't look through your other tools' MCP settings for servers to import: Permission denied: '/home/you/.claude.json'"

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve(idx),
      mcpServers: () => Promise.resolve([]),
      importableMcp: () => Promise.resolve([]),
      mcpPoolStats: () => Promise.resolve({}),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: () => Promise.resolve([] as string[]),
      mcpReadOnlyServers: () => Promise.resolve([] as string[]),
      ...over,
    },
  }))
}

async function mount() {
  const { ToolsPage } = await import('./ToolsPage')
  render(<ToolsPage query={{}} setQuery={() => {}} />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/tools says a failed MCP read instead of drawing an empty one', () => {
  it('a failed import read says what failed and what to do, and offers it again', async () => {
    const importableMcp = vi.fn(() => Promise.reject(new Error(IMPORT_FAILED)))
    mockApi({ importableMcp })
    await mount()
    const said = await screen.findByText(/Couldn't look through your other tools' MCP settings/)
    expect(said.textContent).toContain("Permission denied: '/home/you/.claude.json'.")
    expect(said.textContent).toContain('Nothing was imported or changed. Fix what it names, then try again.')
    expect(said.closest('[role="status"]'), 'a part of the page, not a page failure').not.toBeNull()
    expect(screen.queryByText(/Discovered in other tools/), 'no import list is drawn from a failed read').toBeNull()
    expect(screen.getByText('read_file'), 'the tools still render').toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(importableMcp).toHaveBeenCalledTimes(2))
  })

  it('an unanswered import read still says it failed', async () => {
    mockApi({ importableMcp: () => Promise.reject(new TypeError('Failed to fetch')) })
    await mount()
    expect(await screen.findByText(
      "Couldn't check your other tools for MCP servers to import: the gateway did not answer. Nothing was imported or changed.",
    )).toBeInTheDocument()
  })

  it('a failed server list says so, and draws no MCP server as a native provider', async () => {
    mockApi({ mcpServers: () => Promise.reject(new Error('gateway down')) })
    await mount()
    expect(await screen.findByText(/^Couldn't read your MCP servers: gateway down\./)).toBeInTheDocument()
    expect(screen.getByText('read_file')).toBeInTheDocument()
    // With the list unread, `notes` would otherwise be drawn as a native provider group — offering a
    // native provider's switch for an MCP server.
    expect(screen.queryByText('notes'), 'an MCP server drawn as a native provider').toBeNull()
  })

  it('reads that answered say nothing of the kind', async () => {
    mockApi({})
    await mount()
    await screen.findByText('read_file')
    expect(screen.queryByText(/^Couldn't/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })
})
