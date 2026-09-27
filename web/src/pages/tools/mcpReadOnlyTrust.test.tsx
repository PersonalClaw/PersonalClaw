import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'

// ── An MCP server's "read-only" labels are believed only once you trust that server ─────────
//
// A server labels its own tools (`readOnlyHint`), and it can label anything read-only. Until the
// owner trusts a server's labels, every one of its tools is treated as a change: it asks, and Ask
// and Plan mode refuse it. The switch is per server, asks before it trusts, and — like the
// elicitation grant beside it — writes one server's entry and stays disabled while the list is
// unread, since which way to flip it is unknown.

const trustMcpReadOnly = vi.fn()
const distrustMcpReadOnly = vi.fn()
const confirmSpy = vi.fn()
const servers = [
  { name: 'alpha', status: 'connected', enabled: true, tools: ['alpha_search'] },
  { name: 'beta', status: 'connected', enabled: true, tools: ['beta_fetch'] },
]
const tools = [
  { name: 'alpha_search', provider: 'alpha', description: 'alpha', parameters: {}, requires_approval: true, risk_level: 'caution' },
  { name: 'beta_fetch', provider: 'beta', description: 'beta', parameters: {}, requires_approval: true, risk_level: 'safe' },
]

function mockApi(trusted: () => Promise<string[]>, answer = true) {
  vi.doMock('../../ui/dialog', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    confirm: (opts: unknown) => { confirmSpy(opts); return Promise.resolve(answer) },
  }))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools, load_failures: [] }),
      mcpServers: () => Promise.resolve(servers),
      importableMcp: () => Promise.resolve([]),
      mcpPoolStats: () => Promise.resolve({}),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: () => Promise.resolve([]),
      mcpReadOnlyServers: trusted,
      trustMcpReadOnly: (...a: unknown[]) => { trustMcpReadOnly(...a); return Promise.resolve({ ok: true }) },
      distrustMcpReadOnly: (...a: unknown[]) => { distrustMcpReadOnly(...a); return Promise.resolve({ ok: true }) },
    },
  }))
}

async function mount() {
  const { ToolsPage } = await import('./ToolsPage')
  render(<ToolsPage query={{}} setQuery={() => {}} />)
  await waitFor(() => expect(screen.getByText('alpha_search')).toBeInTheDocument())
}

/** Alpha's trust switch, whatever it is named in the state under test. */
const alphaTrust = () => screen.getByRole('button', { name: /alpha.*read-only labels|read-only labels.*alpha/i })
const settle = () => act(() => new Promise((r) => setTimeout(r, 30)))

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  trustMcpReadOnly.mockClear()
  distrustMcpReadOnly.mockClear()
  confirmSpy.mockClear()
})

describe("an MCP server's read-only labels", () => {
  it('are not trusted until you say so, and trusting asks first and writes that one server', async () => {
    mockApi(() => Promise.resolve([]))
    await mount()
    const control = screen.getByRole('button', { name: "Trust alpha's read-only labels" })
    expect(control.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(control)
    await waitFor(() => expect(trustMcpReadOnly).toHaveBeenCalledWith('alpha', true))
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    const asked = confirmSpy.mock.calls[0][0] as { title: string; body: string }
    expect(asked.title).toMatch(/alpha/)
    expect(asked.body, 'the question says what trusting a label lets happen').toMatch(/without anyone being asked/)
    expect(distrustMcpReadOnly).not.toHaveBeenCalled()
  })

  it('a declined question writes nothing', async () => {
    mockApi(() => Promise.resolve([]), false)
    await mount()
    fireEvent.click(screen.getByRole('button', { name: "Trust alpha's read-only labels" }))
    await settle()
    expect(trustMcpReadOnly).not.toHaveBeenCalled()
  })

  it('stopping trusting one server writes that one removal, with no question', async () => {
    mockApi(() => Promise.resolve(['alpha', 'beta']))
    await mount()
    const control = screen.getByRole('button', { name: "Stop trusting alpha's read-only labels" })
    expect(control.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(control)
    await waitFor(() => expect(distrustMcpReadOnly).toHaveBeenCalledWith('alpha'))
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(trustMcpReadOnly).not.toHaveBeenCalled()
  })

  it('an unread list disables the switch, and a click writes nothing', async () => {
    mockApi(() => Promise.reject(new Error('config unreadable')))
    await mount()
    const control = alphaTrust()
    expect(control).toHaveAttribute('aria-disabled', 'true')
    expect(control.getAttribute('title')).toMatch(/couldn't read which servers' read-only labels you trust/)
    fireEvent.click(control)
    await settle()
    expect(trustMcpReadOnly).not.toHaveBeenCalled()
    expect(distrustMcpReadOnly).not.toHaveBeenCalled()
  })
})
