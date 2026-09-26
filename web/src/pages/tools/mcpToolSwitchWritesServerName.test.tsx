import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── An MCP server's tool is switched under the name its server gives it ─────────────────────────
//
// The switch writes that server's `disabledTools` in mcp.json, the list an ACP agent reads and the
// gateway's own check (`tool_prefs.is_disabled`) reads. The row's `name` is how an agent sees the
// tool, `mcp/<server>/<tool>`, and the switch used to send that: the list then held
// `mcp/fixture/hello`, which matches nothing, so the tool stayed on for every agent while the page
// had written "off". The row now carries `serverTool`, the name in the list.

const toggleMcpTool = vi.fn()
const toggleTool = vi.fn()
const servers = [{ name: 'fixture', status: 'connected', enabled: true, tools: ['hello'] }]
const tools = [
  {
    name: 'mcp/fixture/hello', serverTool: 'hello', provider: 'fixture', description: 'greet',
    parameters: {}, requires_approval: true, risk_level: 'safe', disabled: false,
  },
]

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools, load_failures: [] }),
      mcpServers: () => Promise.resolve(servers),
      importableMcp: () => Promise.resolve([]),
      mcpPoolStats: () => Promise.resolve({ available: false }),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: () => Promise.resolve([]),
      toggleMcpTool: (...a: unknown[]) => { toggleMcpTool(...a); return Promise.resolve({ ok: true }) },
      toggleTool: (...a: unknown[]) => { toggleTool(...a); return Promise.resolve({ ok: true }) },
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); toggleMcpTool.mockClear(); toggleTool.mockClear() })

describe("an MCP server's tool switch", () => {
  it('writes the name its server gives the tool, not the name an agent sees', async () => {
    mockApi()
    const { ToolsPage } = await import('./ToolsPage')
    render(<ToolsPage query={{}} setQuery={() => {}} />)
    const off = await screen.findByRole('button', { name: 'Disable mcp/fixture/hello' })

    fireEvent.click(off)

    await waitFor(() => expect(toggleMcpTool).toHaveBeenCalledWith('fixture', 'hello', false))
    expect(toggleTool, 'a native tool switch would be a second switch for this tool').not.toHaveBeenCalled()
  })
})
