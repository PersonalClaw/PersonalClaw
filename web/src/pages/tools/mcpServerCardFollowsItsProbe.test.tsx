import { afterEach, describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import type { WsMessage } from '../../lib/useChatSocket'

// ── A server's card says what the server is now, after every change ──────────────────────────────
//
// After an add, an edit, an Allow or a switch-on, the gateway probes the server again and says
// `probing` until the probe lands, which takes as long as the server takes to start. The page read the
// list once, 400 ms after the change, and never again, so a server that was fine read "unknown" beside
// the tools it listed until the owner pressed Re-probe. The card now says it is checking the server,
// and the page reads the list again when the gateway says what a server's card says has changed (the
// `refresh` frame naming `mcp`), and not otherwise: there is no timer.

let servers: Array<Record<string, unknown>> = []
let tools: Array<Record<string, unknown>> = []
const mcpServers = vi.fn(() => Promise.resolve(servers))
const toolsIndex = vi.fn(() => Promise.resolve({ tools, load_failures: [] }))

function mockModules() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex,
      mcpServers,
      importableMcp: () => Promise.resolve({ servers: [], unreadable: [] }),
      mcpPoolStats: () => Promise.resolve({}),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: () => Promise.resolve([]),
      mcpReadOnlyServers: () => Promise.resolve([]),
    },
  }))
}

class FakeSocket {
  static all: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeSocket.all.push(this) }
  close(): void { this.closed = true }
}
const socket = () => FakeSocket.all.filter((s) => !s.closed).at(-1)!

async function frame(m: WsMessage) {
  await act(async () => { socket().onmessage?.({ data: JSON.stringify(m) }) })
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
  await act(async () => { socket().onopen?.() })
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  mcpServers.mockClear()
  toolsIndex.mockClear()
  tools = []
})
afterEach(() => { vi.unstubAllGlobals() })

describe('a server the gateway is checking', () => {
  it('says so, and the page reads the list again when the gateway says the probe landed', async () => {
    servers = [{ name: 'notes', transport: 'stdio', status: 'probing', enabled: true, allowed: true, tools: [], error: '' }]
    mockModules()
    await mount()
    expect(screen.getByText('checking')).toBeInTheDocument()
    expect(screen.getByText('Checking the server…')).toBeInTheDocument()
    expect(screen.queryByText('unknown')).toBeNull()

    // Nothing is said, so nothing is read: there is no timer.
    const before = mcpServers.mock.calls.length
    await new Promise((r) => setTimeout(r, 1800))
    expect(mcpServers.mock.calls.length).toBe(before)

    // The probe lands, and the gateway says so: the next read finds it connected, and the tool list
    // is read again for the tool it now lists.
    servers = [{ name: 'notes', transport: 'stdio', status: 'ok', enabled: true, allowed: true, tools: [{ name: 'hello' }], error: '' }]
    tools = [{ name: 'mcp/notes/hello', provider: 'notes', serverTool: 'hello', description: 'say hello', parameters: {}, requires_approval: true, risk_level: 'caution' }]
    const listed = toolsIndex.mock.calls.length
    await frame({ type: 'refresh', data: { kinds: ['mcp'] } })
    await waitFor(() => expect(screen.getByText('ready')).toBeInTheDocument())
    expect(screen.queryByText('checking')).toBeNull()
    await waitFor(() => expect(screen.getByText('mcp/notes/hello')).toBeInTheDocument())
    expect(toolsIndex.mock.calls.length).toBeGreaterThan(listed)
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
