import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'

// ── The default-agent picker never claims "no default" for one it could not read ──────────────
//
// The default agent's read is tolerated so the rest of Agent defaults still renders, and it fell
// back to `''` — which the picker shows as "Select an agent…", i.e. NO default. A user who took that
// at its word picked one, against a default that existed and had simply not been read. The row now
// says it could not read it, with a retry, and offers no pick until one succeeds.
//
// Same family as the onboarding defect (`app/identityReadFailure.test.tsx`): a failed read became
// the empty state, and the empty state invited a write.

const agents = vi.fn()

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  agents.mockReset()
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      ...(await orig<{ api: Record<string, unknown> }>()).api,
      personalclawConfig: async () => ({ agent: { approval_mode: 'auto', yolo: false } }),
      agents: () => agents(),
      savedAgents: async () => [],
      agentProviders: async () => [],
      setDefaultAgent: async () => ({ ok: true }),
      patchConfig: async () => ({ ok: true }),
      skills: async () => [],
      tools: async () => [],
      hooks: async () => [],
    },
  }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

async function mountPanel() {
  const { AgentDefaultsPanel } = await import('./AgentDefaultsPanel')
  render(<AgentDefaultsPanel />)
}

describe('Default agent', () => {
  it('a failed read says so and offers a retry — not an empty picker', async () => {
    agents.mockRejectedValue(new Error('agents unreadable'))
    await mountPanel()
    expect(await screen.findByText("Couldn't read the default agent, so it can't be changed until a retry succeeds.")).toBeInTheDocument()
    expect(screen.queryByText('Select an agent…'), 'an unread default read as none').toBeNull()
    // The rest of the panel still renders: the read is tolerated, not fatal.
    expect(screen.getByText('Defaults')).toBeInTheDocument()
  })

  it('a retry that reads it shows the stored default in the picker', async () => {
    agents.mockRejectedValueOnce(new Error('agents unreadable')).mockResolvedValue({ agents: [], default_agent: 'scout' })
    await mountPanel()
    await screen.findByText("Couldn't read the default agent, so it can't be changed until a retry succeeds.")
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByText('scout')).toBeInTheDocument())
    expect(screen.queryByText(/Couldn't read the default agent/)).toBeNull()
  })
})
