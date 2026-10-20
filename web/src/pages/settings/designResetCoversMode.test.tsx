import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'

// ── "Reset everything to defaults" covers MODE (#675) ────────────────────────────────────────────
//
// Mode (Dark/Light/Auto) lives in its own store (localStorage 'mode', app/theme.tsx) while the
// reset was scoped to the appearance store (localStorage 'appearance') plus the personality —
// so after switching to Light, "Reset everything to defaults" left the UI light. Mode is exactly
// the control an unusable-contrast recovery reaches for, and the button gives no cue its scope
// stops short of the picker six sections above it. This mounts the REAL ThemeProvider +
// AppearanceProvider + PersonalityProvider around the real panel and pins the round trip.

vi.mock('../../lib/api', () => ({
  api: new Proxy({}, { get: () => () => Promise.resolve(null) }),
}))

import { ThemeProvider, DEFAULT_PREFERENCE } from '../../app/theme'
import { AppearanceProvider } from '../../app/appearance'
import { PersonalityProvider } from '../../app/personality'
import { DesignPanel } from './DesignPanel'

function mount() {
  return render(
    <ThemeProvider>
      <AppearanceProvider>
        <PersonalityProvider>
          <DesignPanel />
        </PersonalityProvider>
      </AppearanceProvider>
    </ThemeProvider>,
  )
}

describe('Design reset covers the mode preference (#675)', () => {
  it('after switching to Light, Reset everything returns mode to the default', async () => {
    localStorage.clear()
    mount()
    // Switch to Light via the panel's own control.
    await act(async () => { fireEvent.click(screen.getByLabelText('Mode: Light')) })
    expect(localStorage.getItem('mode')).toBe('light')
    expect(document.documentElement.classList.contains('light')).toBe(true)

    await act(async () => {
      fireEvent.click(screen.getByText('Reset everything to defaults'))
    })
    // The mode store and the applied root class BOTH return to default — the
    // appearance-only reset left them light.
    expect(localStorage.getItem('mode')).toBe(DEFAULT_PREFERENCE)
    expect(document.documentElement.classList.contains('light')).toBe(false)
  })

  it('the first-load fallback and the reset share one named default', async () => {
    // Guards the two-literals drift the fix collapsed: if someone changes the
    // out-of-the-box mode, the reset follows automatically.
    localStorage.clear()
    mount()
    expect(localStorage.getItem('mode')).toBe(DEFAULT_PREFERENCE)
  })
})
