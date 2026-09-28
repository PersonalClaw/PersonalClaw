import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { ProviderCard } from './ProviderCard'
import type { AgentRuntime, SettingsProvider } from '../../lib/api'

// ── An agent CLI starts only when you press its Test ────────────────────────────────────────────
//
// PersonalClaw never starts another agent's CLI unless the user asks for that specific action. A
// runtime's card used to read "Checking…" the first time it was seen, while the gateway started the
// CLI in the background to find out — and "Check availability" re-probed every runtime whose card
// had no runtime row. The card now answers from what the gateway knows without running anything, says
// plainly when the CLI has not been tried, and offers ONE control that starts it: Test.

const ext: SettingsProvider = {
  name: 'demo-cli-agent', displayName: 'Demo CLI', enabled: true, managed: true,
  provider: { type: 'agent', capabilities: ['acp'] }, availability: { state: 'available', reason: '', checkedAt: 1 },
}

function runtime(over: Partial<AgentRuntime> = {}): AgentRuntime {
  return {
    name: 'acp:demo-cli', provider_id: 'acp:demo-cli', type: 'acp_agent', extension: 'demo-cli-agent',
    ready: false, state: 'untested',
    detail: 'Not tried yet. PersonalClaw starts it only when you press Test (Settings → Providers).',
    login_command: null, tested_at: null, ...over,
  }
}

afterEach(() => vi.restoreAllMocks())

function mount(rt: AgentRuntime | undefined, onTest = vi.fn(), onSignIn = vi.fn()) {
  render(<ProviderCard ext={ext} runtime={rt} open={false} onOpenChange={() => {}} onChanged={() => {}}
    onSignIn={onSignIn} onTest={onTest} />)
  return { onTest, onSignIn }
}

describe("an agent runtime's card", () => {
  it('says an installed CLI nobody has tried is not tried yet — never "Ready", never checking', () => {
    mount(runtime())
    expect(screen.getByText('Not tried yet')).toBeTruthy()
    expect(screen.getByText(/PersonalClaw starts it only when you press Test/)).toBeTruthy()
    for (const text of ['Ready', 'Checking…', /Last tested/]) expect(screen.queryByText(text)).toBeNull()
  })

  it('offers one Test, which says what it starts, and starts nothing until it is pressed', async () => {
    const { onTest } = mount(runtime())
    const test = screen.getByRole('button', { name: 'Test: Demo CLI' })
    expect(test.getAttribute('title')).toBe('Starts Demo CLI once to check it runs and is signed in')
    expect(onTest).not.toHaveBeenCalled()
    await act(async () => { fireEvent.click(test) })
    expect(onTest).toHaveBeenCalledTimes(1)
    expect(onTest.mock.calls[0][0].provider_id).toBe('acp:demo-cli')
  })

  it("a tested runtime shows its Test's answer and when it ran", () => {
    mount(runtime({ ready: true, state: 'ready', detail: 'initialize OK (caps: none)', tested_at: '2026-09-28T10:00:00+00:00' }))
    expect(screen.getByText('Ready')).toBeTruthy()
    expect(screen.getByText(/Last tested/)).toBeTruthy()
    expect(screen.queryByText('Not tried yet')).toBeNull()
  })

  it('Sign in opens the terminal and starts no Test by itself; the card says to press Test after', async () => {
    const { onTest, onSignIn } = mount(runtime({
      state: 'needs_login', detail: 'agent requires sign-in: auth required', login_command: ['demo-cli', 'login'],
      tested_at: '2026-09-28T10:00:00+00:00',
    }))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Sign in: Demo CLI' })) })
    expect(onSignIn).toHaveBeenCalledTimes(1)
    expect(onTest).not.toHaveBeenCalled()
    expect(screen.getByRole('status').textContent).toMatch(/press Test to check it/)
  })

  it('the in-process native runtime has nothing to start, so it offers no Test', () => {
    // The floor: a Test offered on every card would pass the tests above.
    mount(runtime({ name: 'native', provider_id: 'native', type: 'native', ready: true, state: 'ready', detail: '' }))
    expect(screen.queryByRole('button', { name: /^Test:/ })).toBeNull()
  })
})
