import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react'

// ── Agent defaults sets the reply style every agent uses unless it or the chat chooses its own ──
//
// `agent.reply_style` is the platform scope of `reply_style.REPLY_STYLE_PRECEDENCE`. The control
// shows what is stored and PATCHes the one allowlisted path; a refused PATCH rolls back and says so.

const patchConfig = vi.fn(async (..._a: unknown[]) => ({ ok: true }))
const cfgMock = vi.fn(async (): Promise<unknown> => ({ agent: { approval_mode: 'interactive', reply_style: 'terse' } }))
const notify = vi.fn()

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  patchConfig.mockClear()
  notify.mockClear()
  cfgMock.mockResolvedValue({ agent: { approval_mode: 'interactive', reply_style: 'terse' } })
  vi.doMock('../../app/appSdk', () => ({ notify }))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      ...(await orig<{ api: Record<string, unknown> }>()).api,
      personalclawConfig: cfgMock,
      agents: async () => ({ agents: [], default_agent: 'scout' }),
      agentProviders: async () => [],
      agentRunners: async () => [],
      patchConfig,
      skills: async () => [],
      tools: async () => [],
      hooks: async () => [],
    },
  }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

async function mountPanel() {
  const { AgentDefaultsPanel } = await import('./AgentDefaultsPanel')
  return render(<AgentDefaultsPanel />)
}

describe('Agent defaults › Reply style', () => {
  it('shows the stored style as the pressed option', async () => {
    await mountPanel()
    const terse = await screen.findByRole('button', { name: 'Reply style: Terse' })
    expect(terse.getAttribute('aria-pressed')).toBe('true')
    for (const name of ['No style', 'Plain', 'Conversational'])
      expect(screen.getByRole('button', { name: `Reply style: ${name}` }).getAttribute('aria-pressed')).toBe('false')
  })

  it('a pick PATCHes agent.reply_style with the style id', async () => {
    await mountPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Reply style: Conversational' }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('agent.reply_style', 'conversational'))
  })

  it('"No style" stores default, which is the floor', async () => {
    await mountPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Reply style: No style' }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('agent.reply_style', 'default'))
  })

  it('a config with no stored style reads as No style, never as a guessed style', async () => {
    cfgMock.mockResolvedValue({ agent: { approval_mode: 'interactive' } })
    await mountPanel()
    const none = await screen.findByRole('button', { name: 'Reply style: No style' })
    expect(none.getAttribute('aria-pressed')).toBe('true')
  })

  it('a refused PATCH rolls back and says so', async () => {
    patchConfig.mockRejectedValueOnce(new Error('not on the allowlist'))
    await mountPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Reply style: Plain' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith("Couldn't save reply style: not on the allowlist", 'error'))
    expect(screen.getByRole('button', { name: 'Reply style: Terse' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('says the order and that a style changes style only', async () => {
    await mountPanel()
    await screen.findByRole('button', { name: 'Reply style: Terse' })
    expect(screen.getByText(/chat or the agent chooses its own/)).toBeTruthy()
    expect(screen.getByText(/never a fact, a caveat or a refusal/)).toBeTruthy()
  })
})
