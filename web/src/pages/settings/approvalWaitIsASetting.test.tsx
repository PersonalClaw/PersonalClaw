import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'

// ── How long an approval waits is a setting in Agent defaults ────────────────────────────────────
//
// It was a fixed two hours in the gateway, so an approval asked at night was denied before anyone
// woke, and nothing in the product could change that. `agent.approval_timeout_minutes` carries it
// now, and this is its control: it shows the stored value and writes the allowlisted path.

const patchConfig = vi.fn()

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  patchConfig.mockReset().mockResolvedValue({ ok: true })
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      ...(await orig<{ api: Record<string, unknown> }>()).api,
      personalclawConfig: async () => ({ agent: { approval_mode: 'auto', yolo: false, approval_timeout_minutes: 120 } }),
      agents: async () => ({ agents: [], default_agent: '' }),
      savedAgents: async () => [],
      agentProviders: async () => [],
      patchConfig: (...a: unknown[]) => patchConfig(...a),
      skills: async () => [],
      tools: async () => [],
      hooks: async () => [],
    },
  }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Approval wait', () => {
  it('🔑 shows the stored wait and saves a new one to agent.approval_timeout_minutes', async () => {
    const { AgentDefaultsPanel } = await import('./AgentDefaultsPanel')
    render(<AgentDefaultsPanel />)
    const field = await screen.findByRole('spinbutton', { name: 'Approval wait' })
    expect((field as HTMLInputElement).value).toBe('120')
    expect(screen.getByText(/An automation or a loop allowed to run on its own never waits/)).toBeInTheDocument()
    // A workflow's gates wait the same window, and the one exception says why it is short.
    expect(screen.getByText(/approval gates wait this long too, except in a run started unattended, where a gate gives up after 45 seconds/)).toBeInTheDocument()

    fireEvent.change(field, { target: { value: '720' } })
    fireEvent.blur(field)
    await waitFor(() => expect(patchConfig).toHaveBeenCalled())
    expect(patchConfig.mock.calls.at(-1)?.slice(0, 2)).toEqual(['agent.approval_timeout_minutes', 720])
  })
})
