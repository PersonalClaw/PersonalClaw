import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DegradedChip } from './DegradedChip'
import { api } from '../lib/api'

// ── A bound chat provider that is DOWN must not read as "everything is fine" ─────────────────────
//
// Every degraded surface asks one question — "does a model resolve?" — and that probe makes no
// network call by design. So with chat bound to an instance whose key the vendor rejected, or whose
// server is off, every surface read `available: true` and this chip rendered NOTHING: the one shell
// indicator whose job is "a provider went away" stayed silent while chat failed on every turn.
//
// The report now carries `chat_provider`: the bound instance's last MEASURED connection, read from
// the gateway's cache (never probed by the report). A failed one is a known fault, so the chip
// shows it, says what failed in the connection test's own words, and links to where it is fixed.

const FAILED = {
  provider: 'my-anthropic',
  state: 'failed' as const,
  detail: 'https://api.anthropic.com/v1/models rejected the credential (HTTP 401) — re-enter this provider\'s API key.',
  rejected_credential: true,
  checked_at: 1,
}

beforeEach(() => {
  vi.spyOn(api, 'onboarding').mockResolvedValue({ has_model_provider: true } as never)
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  }))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('the chat provider half of the degraded chip', () => {
  it('says the chat provider is not answering when every surface still resolves', async () => {
    vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces: [], degraded: [], chat_provider: FAILED } as never)
    render(<DegradedChip />)

    const chip = await screen.findByRole('button', { name: /chat provider not answering/i })
    fireEvent.click(chip)
    const panel = await screen.findByRole('dialog')
    expect(panel.textContent).toContain("Your chat model's provider, my-anthropic, isn't answering.")
    expect(panel.textContent).toContain('rejected the credential (HTTP 401)')
    const link = screen.getByRole('link', { name: /update its key in settings → providers/i })
    expect(link.getAttribute('href')).toBe('#/settings/providers')
  })

  it('stays silent for a connected, a still-checking, or an unmeasured chat provider', async () => {
    // The vacuity floor: a chip that showed for any `chat_provider` would pass the test above.
    for (const chat_provider of [
      { ...FAILED, state: 'connected' as const, detail: 'Connected — 10 model(s) available', rejected_credential: false },
      { ...FAILED, state: 'checking' as const, detail: '', rejected_credential: false },
      null,
    ]) {
      vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces: [], degraded: [], chat_provider } as never)
      const { container, unmount } = render(<DegradedChip />)
      await waitFor(() => expect(api.degraded).toHaveBeenCalled())
      await new Promise((r) => setTimeout(r, 0))
      expect(container.textContent, JSON.stringify(chat_provider)).toBe('')
      unmount()
      vi.mocked(api.degraded).mockReset()
    }
  })

  it('outranks a surface on its floor in the face, and keeps the surface in the popover', async () => {
    // Driven live: with Transcription on its floor as well, the face read "Transcription degraded"
    // while chat — the thing the user types into next — was failing every turn.
    vi.spyOn(api, 'degraded').mockResolvedValue({
      surfaces: [{ surface: 'transcription', available: false, floor: 'Speech-to-text is off.', backlog: 0, use_cases: ['stt'] }],
      degraded: ['transcription'], chat_provider: FAILED,
    } as never)
    render(<DegradedChip />)
    const chip = await screen.findByRole('button', { name: /chat provider not answering/i })
    expect(chip.getAttribute('title')).toContain('and 1 surface is running without a model')
    fireEvent.click(chip)
    const panel = await screen.findByRole('dialog')
    expect(panel.textContent).toContain("Your chat model's provider, my-anthropic, isn't answering.")
    expect(panel.textContent).toContain('No model for Speech-to-text')
  })

  it('an unreachable provider points at checking it, not at its key', async () => {
    vi.spyOn(api, 'degraded').mockResolvedValue({
      surfaces: [], degraded: [],
      chat_provider: { ...FAILED, rejected_credential: false, detail: 'Could not reach http://localhost:11434 — the connection was refused.' },
    } as never)
    render(<DegradedChip />)
    fireEvent.click(await screen.findByRole('button', { name: /chat provider not answering/i }))
    expect(await screen.findByRole('link', { name: /check it in settings → providers/i })).toBeTruthy()
  })
})
