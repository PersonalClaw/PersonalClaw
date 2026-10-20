import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── Settings → Workflows → Default quiet hours says which form it takes ──────────────────────────
//
// `10pm-7am` saved with a 200 and then read as no window at all: the write checked only "a string of
// at most 64 characters" while the scheduler parsed it with `parse_default_window`. The write now
// refuses with that same parser (`tests/test_default_quiet_window_is_validated_on_save.py`). This
// pins the other half — the field TELLS you the form before you type, and a refusal reaches you with
// the server's reason rather than leaving the refused text looking saved.

const REFUSAL = "'10pm' is not a time — write the window as HH:MM-HH:MM in 24-hour time, e.g. 22:00-07:00"
const patchConfig = vi.fn((_path: string, _value: unknown) => Promise.reject(new Error(REFUSAL)))
const notified: string[] = []

vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: () => Promise.resolve({ workflows: { default_quiet_windows: '' } }),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
  },
}))
vi.mock('../../app/appSdk', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  notify: (msg: string) => { notified.push(msg) },
}))

describe('the default quiet hours field', () => {
  it('🔴 names the 24-hour form, with an example and the 12-hour form it refuses', async () => {
    const { WorkflowsPanel } = await import('./WorkflowsPanel')
    render(<WorkflowsPanel />)
    const quiet = await screen.findByRole('textbox', { name: /default quiet hours/i })
    // Read as the field's DESCRIPTION — what a screen reader announces with it — not as nearby text.
    const hint = document.getElementById(quiet.getAttribute('aria-describedby') ?? '')?.textContent ?? ''
    expect(hint).toContain('HH:MM-HH:MM in 24-hour time — 22:00-07:00, not 10pm-7am')
  })

  it('a refused window says why and does not stay in the box', async () => {
    const { WorkflowsPanel } = await import('./WorkflowsPanel')
    render(<WorkflowsPanel />)
    const quiet = (await screen.findByRole('textbox', { name: /default quiet hours/i })) as HTMLInputElement
    await userEvent.type(quiet, '10pm-7am{Enter}')
    expect(patchConfig).toHaveBeenCalledWith('workflows.default_quiet_windows', '10pm-7am')
    await waitFor(() => expect(notified.some((m) => m.includes(REFUSAL))).toBe(true))
    await waitFor(() => expect(quiet.value).toBe(''))
  })
})
