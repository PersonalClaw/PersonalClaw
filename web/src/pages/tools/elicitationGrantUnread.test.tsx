import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'

// ── An unread grant list is never written back (MBR-1) ────────────────────────────────────────
//
// The per-server "may ask you questions" grant is stored as ONE allowlist, and the grant control
// writes the whole list: it splices the clicked server in or out of what it read. That read used to
// swallow its failure into `[]`, defended as the fail-closed answer because every grant then RENDERS
// off. The write is where it broke: granting one server from that `[]` sent `[that one]`, so every
// other server's grant was revoked — behind a confirmation reading "No other server is affected."
//
// Same family as the onboarding defect (`app/identityReadFailure.test.tsx`): a failed read became
// an empty state, and the empty state licensed a write.

const setMcpElicitationServers = vi.fn()
const servers = [
  { name: 'alpha', status: 'connected', enabled: true, tools: ['alpha_search'] },
  { name: 'beta', status: 'connected', enabled: true, tools: ['beta_fetch'] },
]
const tools = [
  { name: 'alpha_search', provider: 'alpha', description: 'alpha', parameters: {}, requires_approval: false, risk_level: 'safe' },
  { name: 'beta_fetch', provider: 'beta', description: 'beta', parameters: {}, requires_approval: false, risk_level: 'safe' },
]

function mockApi(grants: () => Promise<string[]>) {
  // The grant asks first; this suite is about what the click WRITES, so the dialog says yes.
  vi.doMock('../../ui/dialog', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    confirm: () => Promise.resolve(true),
  }))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      toolsIndex: () => Promise.resolve({ tools, load_failures: [] }),
      mcpServers: () => Promise.resolve(servers),
      importableMcp: () => Promise.resolve([]),
      mcpPoolStats: () => Promise.resolve({ available: false }),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: grants,
      setMcpElicitationServers: (...a: unknown[]) => { setMcpElicitationServers(...a); return Promise.resolve({ ok: true }) },
    },
  }))
}

async function mount() {
  const { ToolsPage } = await import('./ToolsPage')
  render(<ToolsPage query={{}} setQuery={() => {}} />)
  await waitFor(() => expect(screen.getByText('alpha_search')).toBeInTheDocument())
}

/** Alpha's grant control, whatever it is named in the state under test. */
const alphaGrant = () => screen.getByRole('button', { name: /alpha.*questions|questions.*alpha/i })
/** Let a click's async chain (confirm → write) run to the end before asserting it wrote nothing. */
const settle = () => act(() => new Promise((r) => setTimeout(r, 30)))

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); setMcpElicitationServers.mockClear() })

describe('the elicitation grant', () => {
  it('a failed read of the grants disables them, and a click writes nothing', async () => {
    mockApi(() => Promise.reject(new Error('config unreadable')))
    await mount()
    // Still tolerated: the tools render, only the grant is unknown.
    expect(screen.getByText('beta_fetch')).toBeInTheDocument()
    const grant = alphaGrant()
    expect(grant).toHaveAttribute('aria-disabled', 'true')
    expect(grant.getAttribute('aria-pressed'), 'an unread grant must not claim to be off').toBe('false')
    expect(grant.getAttribute('title')).toMatch(/couldn't read which servers may ask you questions/)
    fireEvent.click(grant)
    await settle()
    expect(setMcpElicitationServers, 'a whole-list write was built from an unread list').not.toHaveBeenCalled()
  })

  it('a grant read successfully is extended, never replaced', async () => {
    // The control: with the list read, granting alpha keeps beta's grant.
    mockApi(() => Promise.resolve(['beta']))
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Let alpha ask you questions' }))
    await waitFor(() => expect(setMcpElicitationServers).toHaveBeenCalledWith(['beta', 'alpha'], true))
  })
})
