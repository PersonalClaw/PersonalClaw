import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'

// ── A server that waits for the owner's Allow says so, and starts only on their yes ──────────────
//
// A server the owner has not allowed as it is defined now is never started by the gateway: one
// imported, brought over, set up by a pack or an app, or written into mcp.json by hand reads
// `status: 'waiting'`. The row says so in the page, with Allow, and its switch reads off. Allow is
// answered by the gateway's question, which says exactly what the server runs, and only a yes to
// that question is sent with `confirm: true`. The consent flow here is the real one
// (`lib/securityConsent`); the gateway is a stand-in that asks until the body says yes.

const WAITING_REASON = 'Not allowed to run yet. It starts only after you allow what it runs, with Allow on the Tools page.'
const CONSENT = 'Allowing “notes” lets PersonalClaw run /opt/notes/server --stdio as you. Like any program you '
  + 'start, it can read and change your files and reach the network. A change to what it runs asks you again.'
const TITLE = 'Allow this MCP server to run?'

let servers: unknown[] = []
let answer = true
const asked = vi.fn()
const allowSent = vi.fn()
const toggleSent = vi.fn()
const notify = vi.fn()

const waiting = {
  name: 'notes', status: 'waiting', enabled: true, allowed: false, allowRevision: 'rev-1',
  tools: [], error: WAITING_REASON,
}

function mockApi() {
  vi.doMock('../../ui/dialog', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    confirm: (opts: unknown) => { asked(opts); return Promise.resolve(answer) },
  }))
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (...a: unknown[]) => notify(...a),
  }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../lib/api')>()
    const { withSecurityConsent } = await import('../../lib/securityConsent')
    return {
      ...real,
      api: {
        toolsIndex: () => Promise.resolve({ tools: [], load_failures: [] }),
        mcpServers: () => Promise.resolve(servers),
        importableMcp: () => Promise.resolve([]),
        mcpPoolStats: () => Promise.resolve({}),
        toolGroups: () => Promise.resolve(null),
        mcpElicitationServers: () => Promise.resolve([]),
        mcpReadOnlyServers: () => Promise.resolve([]),
        toggleMcpServer: (...a: unknown[]) => { toggleSent(...a); return Promise.resolve({ ok: true }) },
        allowMcpServer: (name: string, revision: string) => withSecurityConsent((confirm) => {
          allowSent({ name, revision, confirm })
          if (!confirm) {
            return Promise.reject(new real.ApiError(`send {"confirm": true} to confirm — ${CONSENT}`, 400,
              'confirmation_required', { field: `mcp.servers.${name}`, consent: CONSENT, title: TITLE }))
          }
          return Promise.resolve({ ok: true, name, allowed: true })
        }),
      },
    }
  })
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
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  answer = true
  for (const f of [asked, allowSent, toggleSent, notify]) f.mockClear()
})

describe('a server that waits for your Allow', () => {
  it('says so in its row, with Allow, and its switch reads off', async () => {
    servers = [waiting]
    mockApi()
    await mount()
    expect(screen.getByText('waiting for your Allow')).toBeInTheDocument()
    expect(screen.getByText(/has not run\. PersonalClaw starts it only once you allow what it runs\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Allow notes' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enable server notes' })).toBeInTheDocument()
    // One sentence says why it has no tools: the line above, not a second box beneath it.
    expect(screen.queryByText('No tools exposed yet.')).toBeNull()
  })

  it('asks with what the server runs, and sends the yes only after it', async () => {
    servers = [waiting]
    mockApi()
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Allow notes' }))
    await waitFor(() => expect(allowSent).toHaveBeenCalledTimes(2))
    expect(asked).toHaveBeenCalledWith(expect.objectContaining({ title: TITLE, body: CONSENT, confirmLabel: 'Allow' }))
    expect(allowSent.mock.calls.map(([c]) => c)).toEqual([
      { name: 'notes', revision: 'rev-1', confirm: false },
      { name: 'notes', revision: 'rev-1', confirm: true },
    ])
    await waitFor(() => expect(notify).toHaveBeenCalledWith('Allowed “notes”. It starts now.', 'success'))
  })

  it('sends nothing more when the owner says no, and reports no failure', async () => {
    servers = [waiting]
    answer = false
    mockApi()
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Allow notes' }))
    await waitFor(() => expect(asked).toHaveBeenCalledTimes(1))
    await new Promise((r) => setTimeout(r, 30))
    expect(allowSent).toHaveBeenCalledTimes(1)
    expect(allowSent.mock.calls[0][0]).toMatchObject({ confirm: false })
    expect(notify).not.toHaveBeenCalled()
  })

  it('is allowed, not merely switched on, by its switch', async () => {
    servers = [waiting]
    mockApi()
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Enable server notes' }))
    await waitFor(() => expect(allowSent).toHaveBeenCalledTimes(2))
    expect(toggleSent).not.toHaveBeenCalled()
  })

  it('leaves an allowed server and its switch as they were', async () => {
    servers = [{ name: 'notes', status: 'ok', enabled: true, allowed: true, tools: [], error: '' }]
    mockApi()
    await mount()
    expect(screen.queryByRole('button', { name: 'Allow notes' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Disable server notes' }))
    await waitFor(() => expect(toggleSent).toHaveBeenCalledWith('notes', false))
    expect(allowSent).not.toHaveBeenCalled()
  })
})
