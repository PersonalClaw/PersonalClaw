import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import type { WsMessage } from '../../lib/useChatSocket'

// ── A server's card follows what the gateway says, without a reload ──────────────────────────────
//
// 🔴 MEASURED: after Allow, a server's card kept "waiting for your Allow" until the page was
// reloaded. The page read its server cards and its tool list in one `Promise.all`, and the tool
// list waits for each server it lists to answer, so while the server was still starting nothing
// new was drawn (31 s, measured). Then the card said "Not connected — timeout." of a server that
// had exited, and later a raw traceback line.
//
// The cards are now a read of their own, read again on the gateway's `refresh` frame naming `mcp`
// (sent whenever what a server's card says changes), and say what the gateway's one record says: a
// server that exited says so in one line, with its error output behind Details, and a server
// PersonalClaw stopped starting says so, with Retry.

const WAITING_REASON = 'Not allowed to run yet. It starts only after you allow what it runs, with Allow on the Tools page.'
const EXITED = 'notes exited with code 1 before it answered: RuntimeError: the build of its dependency failed.'
const STDERR = 'Traceback (most recent call last):\n  File "/home/user/build.py", line 3, in <module>\nRuntimeError: the build of its dependency failed'
const STOPPED = 'notes failed to start 3 times in a row, so PersonalClaw stopped trying. The last time: '
  + 'notes exited with code 1 before it answered: RuntimeError: the build of its dependency failed. Press Retry to start it again.'

const waiting = {
  name: 'notes', transport: 'stdio', status: 'waiting', enabled: true, allowed: false, allowRevision: 'rev-1',
  tools: [], error: WAITING_REASON,
}
const probing = { ...waiting, status: 'probing', allowed: true, allowRevision: undefined, error: '' }
const exited = { ...probing, status: 'error', error: EXITED, detail: STDERR }
const stopped = { ...probing, status: 'stopped', error: STOPPED, detail: STDERR }

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

describe('a server card after Allow', () => {
  it('follows the gateway while the tool list has not answered, with no reload', async () => {
    servers = [waiting]
    mockApi()
    await mount()
    expect(screen.getByText('waiting for your Allow')).toBeInTheDocument()

    // Allowed: the gateway starts it, and the tool list waits on it from now on.
    toolsHang = true
    servers = [probing]
    fireEvent.click(screen.getByRole('button', { name: 'Allow notes' }))
    await waitFor(() => expect(screen.getByText('checking')).toBeInTheDocument())
    expect(screen.queryByText('waiting for your Allow')).toBeNull()

    // It exits; the gateway says the card changed.
    servers = [exited]
    await frame({ type: 'refresh', data: { kinds: ['mcp'] } })
    await waitFor(() => expect(screen.getByText(`Not connected — ${EXITED}`)).toBeInTheDocument())
    expect(screen.queryByText('checking')).toBeNull()
    // The headline is one line; the traceback is behind Details.
    expect(screen.queryByText(/line 3, in <module>/)).not.toBeVisible()
    fireEvent.click(screen.getByText('Details'))
    expect(screen.getByText(/line 3, in <module>/)).toBeVisible()
  })

  it('a server stopped after failing to start says so, with Retry, and Retry asks the gateway', async () => {
    servers = [exited]
    mockApi()
    await mount()
    servers = [stopped]
    await frame({ type: 'refresh', data: { kinds: ['mcp'] } })
    await waitFor(() => expect(screen.getByText('failed — stopped retrying')).toBeInTheDocument())
    expect(screen.getByText(STOPPED)).toBeInTheDocument()
    expect(screen.getByText('Details')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry notes' }))
    await waitFor(() => expect(reconnect).toHaveBeenCalledWith('notes'))
  })

  it('reads nothing on a frame about something else', async () => {
    servers = [exited]
    mockApi()
    await mount()
    servers = [stopped]
    await frame({ type: 'refresh', data: { kinds: ['crons'] } })
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByText('failed — stopped retrying')).toBeNull()
  })
})
