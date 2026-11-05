import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'

// ── A server whose tool list was not read to its end says so on its card ───────────────────────
//
// A server that pages its tool list is read page by page; one whose list never ends, or that sends
// the same page again, is kept as far as it was read, and the gateway hands its card a note saying
// so (`note`, from `mcp_client.McpServerConn._refresh_tools`). The card shows it, in words, above
// the tools that were read: the rest are missing from agents too, and a tooltip on "ready" was the
// only place it could have been seen.

const NOTE = 'notes listed more than 50 pages of tools, so PersonalClaw stopped reading it: the first 50 are shown.'
const ready = { name: 'notes', transport: 'stdio', status: 'ok', enabled: true, allowed: true, tools: ['a', 'b'] }

let servers: unknown[] = []
/** The tool list as the gateway answered it while a server was still starting: not yet. */
let toolsHang = false
const reconnect = vi.fn()

function mockApi() {
  vi.doMock('../../ui/dialog', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    confirm: () => Promise.resolve(true),
  }))
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: () => {},
  }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../lib/api')>()
    return {
      ...real,
      api: {
        toolsIndex: () => (toolsHang ? new Promise(() => {}) : Promise.resolve({ tools: [], load_failures: [] })),
        mcpServers: () => Promise.resolve(servers),
        importableMcp: () => Promise.resolve({ servers: [], unreadable: [] }),
        mcpPoolStats: () => Promise.resolve({}),
        toolGroups: () => Promise.resolve(null),
        mcpElicitationServers: () => Promise.resolve([]),
        allowMcpServer: () => Promise.resolve({ ok: true, name: 'notes', allowed: true }),
        reconnectMcp: (name: string) => { reconnect(name); return Promise.resolve({}) },
      },
    }
  })
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
  await waitFor(() => expect(screen.getByRole('button', { name: /server notes$/ })).toBeInTheDocument())
  await act(async () => { socket().onopen?.() })
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  toolsHang = false
  reconnect.mockClear()
})
afterEach(() => { vi.unstubAllGlobals() })

describe("a server whose tool list was not read to its end", () => {
  it('says so on its card, in words', async () => {
    servers = [{ ...ready, note: NOTE }]
    mockApi()
    await mount()
    expect(screen.getByText('ready')).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent(NOTE)
  })

  it('and a server read whole says nothing more', async () => {
    servers = [ready]
    mockApi()
    await mount()
    expect(screen.getByText('ready')).toBeInTheDocument()
    expect(screen.queryByRole('note')).toBeNull()
  })
})
