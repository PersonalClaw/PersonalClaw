import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'

// ── An MCP server's edit is saved only over the definition the form was seeded from ───────────────
//
// The edit form sends the whole definition as it was when the form opened. When the server changed
// in between — Settings → Providers edited its arguments, another tab saved it, an import landed —
// the save replaced that change without a word. The gateway now refuses a save whose base is stale
// (`409 stale_write`); this pins what the form does with the refusal.

const saveMcpServer = vi.fn()
const servers = [{ name: 'gh', status: 'connected', enabled: true, tools: ['gh_search'] }]
const tools = [
  { name: 'gh_search', provider: 'gh', description: 'search', parameters: {}, requires_approval: false, risk_level: 'safe' },
]
const PAINTED = { name: 'gh', editable: true, transport: 'stdio', command: 'npx', args: ['-y', 'gh-mcp'], env: [] }
// What is stored by the time the form saves: the provider card changed the arguments.
const STORED = { ...PAINTED, args: ['-y', 'gh-mcp', '--read-only'] }

function staleWrite() {
  return Object.assign(new Error("This write replaces the MCP server 'gh', which changed after the copy it was built from was read."), { status: 409, code: 'stale_write' })
}

async function mount() {
  vi.resetModules()
  sessionStorage.clear()
  let reads = 0
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools, load_failures: [] }),
      mcpServers: () => Promise.resolve(servers),
      importableMcp: () => Promise.resolve([]),
      mcpPoolStats: () => Promise.resolve({ available: false }),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: () => Promise.resolve([]),
      mcpServerDefinition: () => {
        reads += 1
        return Promise.resolve(reads === 1 ? { ...PAINTED, revision: 'r1' } : { ...STORED, revision: 'r2' })
      },
      saveMcpServer: (...a: unknown[]) => {
        saveMcpServer(...a)
        return a[2] === 'r2' ? Promise.resolve({ ok: true, name: 'gh', revision: 'r3' }) : Promise.reject(staleWrite())
      },
    },
  }))
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
  await waitFor(() => expect(screen.getByText('gh_search')).toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: 'Edit gh' }))
  const command = await screen.findByRole('textbox', { name: 'Command' })
  fireEvent.change(command, { target: { value: 'uvx' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
}

beforeEach(() => { saveMcpServer.mockClear() })

describe('an MCP server edit from a stale copy', () => {
  it('is refused with the notice, keeps what was typed, and was sent over the painted revision', async () => {
    await mount()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/The server “gh” changed elsewhere/)
    expect((screen.getByRole('textbox', { name: 'Command' }) as HTMLInputElement).value).toBe('uvx')
    expect(saveMcpServer).toHaveBeenCalledTimes(1)
    expect(saveMcpServer.mock.calls[0]).toEqual(['gh', { transport: 'stdio', command: 'uvx', args: ['-y', 'gh-mcp'] }, 'r1'])
  })

  it('Reload and reapply keeps the provider card’s arguments and applies the new command', async () => {
    await mount()
    const alert = await screen.findByRole('alert')
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    await waitFor(() => expect(saveMcpServer).toHaveBeenCalledTimes(2))
    expect(saveMcpServer.mock.calls[1]).toEqual(
      ['gh', { transport: 'stdio', command: 'uvx', args: ['-y', 'gh-mcp', '--read-only'] }, 'r2'])
  })
})
