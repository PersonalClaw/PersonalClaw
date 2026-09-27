import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'

// ── An unread grant list is never written back (MBR-1) ────────────────────────────────────────
//
// The per-server "may ask you questions" grant is stored as ONE allowlist. The grant control used to
// write the whole list, splicing the clicked server in or out of what it read, and that read used to
// swallow its failure into `[]`: granting one server from that `[]` sent `[that one]`, so every other
// server's grant was revoked — behind a confirmation reading "No other server is affected." The
// control now writes ONE server's grant, and still stays disabled while the grants are unread, since
// which way to flip it is unknown.
//
// Same family as the onboarding defect (`app/identityReadFailure.test.tsx`): a failed read became
// an empty state, and the empty state licensed a write.

const grantMcpElicitation = vi.fn()
const revokeMcpElicitation = vi.fn()
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
      mcpPoolStats: () => Promise.resolve({}),
      toolGroups: () => Promise.resolve(null),
      mcpElicitationServers: grants,
      mcpReadOnlyServers: () => Promise.resolve([]),
      grantMcpElicitation: (...a: unknown[]) => { grantMcpElicitation(...a); return Promise.resolve({ ok: true }) },
      revokeMcpElicitation: (...a: unknown[]) => { revokeMcpElicitation(...a); return Promise.resolve({ ok: true }) },
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

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); grantMcpElicitation.mockClear(); revokeMcpElicitation.mockClear() })

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
    expect(grantMcpElicitation, 'a grant was written from an unread list').not.toHaveBeenCalled()
    expect(revokeMcpElicitation).not.toHaveBeenCalled()
  })

  it('granting one server writes that one grant — never the list, so no other grant can be lost', async () => {
    mockApi(() => Promise.resolve(['beta']))
    await mount()
    fireEvent.click(screen.getByRole('button', { name: 'Let alpha ask you questions' }))
    await waitFor(() => expect(grantMcpElicitation).toHaveBeenCalledWith('alpha', true))
    expect(revokeMcpElicitation).not.toHaveBeenCalled()
  })

  it('revoking one server writes that one revoke', async () => {
    mockApi(() => Promise.resolve(['alpha', 'beta']))
    await mount()
    fireEvent.click(alphaGrant())
    await waitFor(() => expect(revokeMcpElicitation).toHaveBeenCalledWith('alpha'))
    expect(grantMcpElicitation).not.toHaveBeenCalled()
  })
})
