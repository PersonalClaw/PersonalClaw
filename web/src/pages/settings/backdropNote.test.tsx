import { describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'

// ── Settings › Design says where the animated backdrop went ─────────────────────────────────────
//
// The backdrop used to be the wave field for everyone; it is now off by default and comes with
// Claw Arcade (`web/DESIGN.md` §7). An owner who misses it looks for it where the backdrop's own
// controls are, so the "Backdrop & motion" section says so in one line, and says where to turn it
// back on: the Background control just below the line, in any identity, or Claw Arcade, which
// brings it. The line is held to the registry and to the control it names, so it cannot outlive
// the behaviour it describes.
//
// Mounted in the REAL ThemeProvider + AppearanceProvider + PersonalityProvider, as the page is.

vi.mock('../../lib/api', () => ({
  api: new Proxy({}, { get: (_t, key) => (key === 'themes' ? () => Promise.resolve([]) : () => Promise.resolve(null)) }),
}))

import { ThemeProvider } from '../../app/theme'
import { AppearanceProvider } from '../../app/appearance'
import { PersonalityProvider } from '../../app/personality'
import { TOKENS } from '../../design/tokenRegistry'
import { PERSONALITIES } from '../../design/personalities'
import { DesignPanel } from './DesignPanel'

const NOTE = 'The animated backdrop is off by default; Background below turns it on, and Claw Arcade brings it.'

function mount() {
  localStorage.clear()
  render(
    <ThemeProvider>
      <AppearanceProvider>
        <PersonalityProvider>
          <DesignPanel />
        </PersonalityProvider>
      </AppearanceProvider>
    </ThemeProvider>,
  )
}

const section = (title: string) => screen.getByRole('heading', { level: 2, name: title }).closest('section')!

describe('the Backdrop & motion section', () => {
  it('says the animated backdrop is off by default, and where to turn it back on', () => {
    mount()
    expect(within(section('Backdrop & motion')).getByText(NOTE, { exact: false })).toBeTruthy()
  })

  it('says it once, in that section only', () => {
    mount()
    expect(screen.getAllByText(NOTE, { exact: false })).toHaveLength(1)
    for (const other of ['Typography & scale', 'Layout & shape']) {
      expect(within(section(other)).queryByText(NOTE, { exact: false }), `the note leaked into ${other}`).toBeNull()
    }
  })

  it('and the line is true: the backdrop token defaults to none, and Claw Arcade brings the wave field', () => {
    const backdrop = TOKENS.find((t) => t.varName === '--bg-style')!
    expect(backdrop.kind === 'select' && backdrop.value).toBe('none')
    const arcade = PERSONALITIES.find((p) => p.label === 'Claw Arcade')!
    expect(arcade.behavior.dials?.bgStyle).toBe('waves')
  })

  it('names a control that is there: Background, below the line, offers the wave field', () => {
    mount()
    const backdrop = section('Backdrop & motion')
    const note = within(backdrop).getByText(NOTE, { exact: false })
    const waves = within(backdrop).getByRole('button', { name: 'Background: waves' })
    expect(note.compareDocumentPosition(waves) & Node.DOCUMENT_POSITION_FOLLOWING, 'Background is not below the line').toBeTruthy()
  })
})
