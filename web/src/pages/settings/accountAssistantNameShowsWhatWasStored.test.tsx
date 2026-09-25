// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'

// ── "✓ Saved" beside a name the server did not store ───────────────────────────────────────────
//
// Measured in a real browser: Settings → Account → Assistant name, type `Chloé's Aide`, Save → the
// panel showed "✓ Saved" beside the typed value. Reload → `Chlos Aide`. The PATCH had answered 200
// with `"bot_name": "Chlos Aide"` ALREADY in its body, and `saveBot` discarded it: it set the field
// from the draft it had sent. The server no longer strips a name (it refuses a character a name
// cannot carry, by name), but a panel that trusts its own draft over the server's answer is the
// defect whichever way the server normalises — so this pins the field to the ANSWER, the way the
// Username field beside it already re-reads what the server stored.

const personalclawConfig = vi.fn()
const patchConfig = vi.fn()
const notify = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    dashboardConfig: () => Promise.resolve({ user_name: 'Ada Lovelace', username: 'lovelace' }),
    saveDashboardConfig: vi.fn(),
    personalclawConfig: () => personalclawConfig(),
    patchConfig: (...a: unknown[]) => patchConfig(...a),
    // Kept PENDING: the sign-in section renders nothing until its session read lands.
    authSession: () => new Promise(() => {}),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))

import { AccountPanel } from './AccountPanel'
import { IdentityProvider } from '../../app/identity'

const botField = () => screen.getByLabelText('Assistant name') as HTMLInputElement
const saveBot = () => screen.getByRole('button', { name: 'Save: Assistant name' })

async function renderPanel(stored: string) {
  personalclawConfig.mockResolvedValue({ agent: { bot_name: stored } })
  render(<IdentityProvider><AccountPanel /></IdentityProvider>)
  await waitFor(() => expect(botField().value).toBe(stored))
}

beforeEach(() => { vi.clearAllMocks() })
afterEach(cleanup)

describe('the assistant name shows what the server stored', () => {
  it('after a save, the field holds the name in the response — not the draft that was sent', async () => {
    await renderPanel('Astra')
    patchConfig.mockResolvedValue({ agent: { bot_name: 'Chlos Aide' } })
    fireEvent.change(botField(), { target: { value: "Chloé's Aide" } })
    fireEvent.click(saveBot())
    await waitFor(() => expect(botField().value).toBe('Chlos Aide'))
    expect(patchConfig).toHaveBeenCalledWith('agent.bot_name', "Chloé's Aide")
    // "Saved" now sits beside the stored name, and there is nothing left to save.
    expect(saveBot().textContent).toContain('Saved')
    expect(saveBot().getAttribute('aria-disabled')).toBe('true')
  })

  it('a name stored as typed stays as typed (the control for the case above)', async () => {
    await renderPanel('')
    patchConfig.mockResolvedValue({ agent: { bot_name: 'Zoë' } })
    fireEvent.change(botField(), { target: { value: 'Zoë' } })
    fireEvent.click(saveBot())
    await waitFor(() => expect(saveBot().textContent).toContain('Saved'))
    expect(botField().value).toBe('Zoë')
  })

  it('a refused name stays in the field, unsaved, with the reason the server gave', async () => {
    await renderPanel('Astra')
    patchConfig.mockRejectedValue(new Error("“*” can't be part of a name"))
    fireEvent.change(botField(), { target: { value: '**Astra**' } })
    fireEvent.click(saveBot())
    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect(notify.mock.calls[0][0]).toContain("“*” can't be part of a name")
    expect(botField().value).toBe('**Astra**')
    expect(saveBot().textContent).not.toContain('Saved')
    expect(saveBot().getAttribute('aria-disabled')).toBeNull()
  })
})
