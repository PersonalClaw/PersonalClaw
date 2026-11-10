import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react'

// ── Agent defaults sets how replies explain things, unless a chat chooses its own ──────────────
//
// `agent.explain` is the platform scope of `explanations.EXPLAIN_PRECEDENCE`. The row comes right
// after Reply style, offers explanations.json's four choices with their labels, shows what is
// stored and PATCHes the one allowlisted path; a refused PATCH rolls back and says so.

const patchConfig = vi.fn(async (..._a: unknown[]) => ({ ok: true }))
const cfgMock = vi.fn(async (): Promise<unknown> => ({ agent: { approval_mode: 'interactive', reply_style: 'terse', explain: 'step_by_step' } }))
const notify = vi.fn()

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  patchConfig.mockClear()
  notify.mockClear()
  cfgMock.mockResolvedValue({ agent: { approval_mode: 'interactive', reply_style: 'terse', explain: 'step_by_step' } })
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

describe('Agent defaults › Explanations', () => {
  it('sits right after Reply style and shows the stored choice as the pressed option', async () => {
    await mountPanel()
    const step = await screen.findByRole('button', { name: 'Explanations: Step by step' })
    expect(step.getAttribute('aria-pressed')).toBe('true')
    for (const name of ['No preference', 'Briefly', 'With the background'])
      expect(screen.getByRole('button', { name: `Explanations: ${name}` }).getAttribute('aria-pressed')).toBe('false')
    const style = screen.getByRole('button', { name: 'Reply style: Terse' })
    expect(style.compareDocumentPosition(step) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a pick PATCHes agent.explain with the choice id', async () => {
    await mountPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Explanations: With the background' }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('agent.explain', 'with_background'))
  })

  it('a config with no stored choice, or one that names none, reads as No preference', async () => {
    for (const explain of [undefined, 'sideways']) {
      cleanup()
      cfgMock.mockResolvedValue({ agent: { approval_mode: 'interactive', explain } })
      await mountPanel()
      const none = await screen.findByRole('button', { name: 'Explanations: No preference' })
      expect(none.getAttribute('aria-pressed')).toBe('true')
    }
  })

  it('a refused PATCH rolls back and says so', async () => {
    patchConfig.mockRejectedValueOnce(new Error('not on the allowlist'))
    await mountPanel()
    fireEvent.click(await screen.findByRole('button', { name: 'Explanations: Briefly' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith("Couldn't save explanations: not on the allowlist", 'error'))
    expect(screen.getByRole('button', { name: 'Explanations: Step by step' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('says what it changes and that it never changes a fact', async () => {
    await mountPanel()
    await screen.findByRole('button', { name: 'Explanations: Step by step' })
    expect(screen.getByText('How replies explain things to you. Changes how things are explained, never a fact, a caveat or a refusal. A chat can choose its own in Chat preferences.')).toBeTruthy()
  })

  it('is found by Settings search', async () => {
    const { SETTINGS_TERMS } = await import('./settingsTerms')
    expect(SETTINGS_TERMS.agent).toContain('Explanations')
  })
})
