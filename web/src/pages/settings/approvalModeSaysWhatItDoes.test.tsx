import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'

// ── Approval mode says what each value does for an agent no chat started ───────────────────────
//
// The row read "When the agent must ask before running a tool." over Auto · Ask each time · Trust
// reads, with Auto first and the default. Auto is the value that lets a trigger's agent, or a
// subagent started outside a chat, approve every call it makes, file changes and shell commands
// included, and nothing on the row said so. The mode ships asking now; the row lists the values
// strictest first, says what the chosen one does, and marks Auto as relaxing a safety default.

let stored: Record<string, unknown> = {}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      ...(await orig<{ api: Record<string, unknown> }>()).api,
      personalclawConfig: async () => ({ agent: { yolo: false, approval_timeout_minutes: 120, ...stored } }),
      agents: async () => ({ agents: [], default_agent: '' }),
      savedAgents: async () => [],
      agentProviders: async () => [],
      patchConfig: async () => ({ ok: true }),
      skills: async () => [],
      tools: async () => [],
      hooks: async () => [],
    },
  }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

async function mount() {
  const { AgentDefaultsPanel } = await import('./AgentDefaultsPanel')
  render(<AgentDefaultsPanel />)
  return screen.findByRole('button', { name: 'Approval mode: Ask each time' })
}

describe('Approval mode', () => {
  it('🔑 lists its values strictest first, and a read without one shows the default, which asks', async () => {
    stored = {}
    const asks = await mount()
    const labels = screen.getAllByRole('button', { name: /^Approval mode: / }).map((b) => b.getAttribute('aria-label'))
    expect(labels).toEqual(['Approval mode: Ask each time', 'Approval mode: Trust reads', 'Approval mode: Auto'])
    expect(asks.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText(/an agent no chat started \(a trigger’s Invoke Agent agent, a subagent started outside a chat\) in your Inbox/)).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: 'Relaxes a safety default' })).toBeNull()
  })

  it('🔴 a stored Auto says it lets those agents act without asking, and is marked as relaxing a safety default', async () => {
    stored = { approval_mode: 'auto' }
    await mount()
    expect(screen.getByRole('button', { name: 'Approval mode: Auto' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText(/approves every tool call it makes, file changes and shell commands included, without asking you\. Chats still ask\./)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Relaxes a safety default' })).toBeInTheDocument()
  })
})
