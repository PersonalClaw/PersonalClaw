import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── Settings → Design: the color editor's fields and the saved-theme deletes ──────────────────────
//
// Three defects the settings validator measured on `#/settings/design`, one render each:
//
//   • **The hex fields ignored typing.** Each was controlled by the APPLIED color and wrote only when
//     the whole value was already `#rrggbb`, so any keystroke in between was refused and React put
//     the old value back — you could paste into the field and nothing else.
//   • **Twenty-eight hex fields had no accessible name.** The swatch beside each one says
//     "<token> color"; the field itself announced as a bare "edit text".
//   • **Every "Delete saved theme" button had the same name**, one per saved theme, with nothing
//     saying which theme it deletes.
//
// Mounted in the REAL ThemeProvider + AppearanceProvider + PersonalityProvider, so "applied" is read
// off the `<html>` element the provider writes, not off a mock of it.

const THEMES = [
  { slug: 'ocean', name: 'Ocean', emoji: '🌊', created_at: '' },
  { slug: 'dusk', name: 'Dusk', emoji: '🌇', created_at: '' },
]

vi.mock('../../lib/api', () => ({
  api: new Proxy({}, {
    get: (_target, key) => {
      if (key === 'themes') return () => Promise.resolve(THEMES)
      if (key === 'theme') {
        return (slug: string) => Promise.resolve({
          ...THEMES.find((t) => t.slug === slug),
          dark: { '--color-primary': '#123456' },
          light: { '--color-primary': '#654321' },
        })
      }
      return () => Promise.resolve(null)
    },
  }),
}))

import { ThemeProvider } from '../../app/theme'
import { AppearanceProvider } from '../../app/appearance'
import { PersonalityProvider } from '../../app/personality'
import { TOKENS } from '../../design/tokenRegistry'
import { COLOR_GROUPS } from '../../design/schemes'
import { DesignPanel } from './DesignPanel'

const COLOR_TOKENS = TOKENS.filter((t) => t.kind === 'color' && COLOR_GROUPS.includes(t.group))
const PRIMARY = COLOR_TOKENS.find((t) => t.varName === '--color-primary')!

async function mountEditor() {
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
  await act(async () => { fireEvent.click(screen.getByText('Edit colors & save a custom theme')) })
}

/** The hex field in a color row, found by the row rather than by a name — so the typing test reads
 *  the typing defect on its own, whether or not the field has a name yet. */
function hexFieldIn(varLabel: string): HTMLInputElement {
  const swatch = screen.getByLabelText(`${varLabel} color`)
  const row = swatch.closest('.py-2') as HTMLElement
  return row.querySelector('input:not([type="color"])') as HTMLInputElement
}

const applied = (varName: string) => document.documentElement.style.getPropertyValue(varName)

describe('a hex color field takes what you type', () => {
  it('🔴 keeps every keystroke, and a complete value applies live', async () => {
    await mountEditor()
    const field = hexFieldIn(PRIMARY.label)
    await userEvent.clear(field)
    expect(field.value, 'clearing the field is a keystroke too').toBe('')
    await userEvent.type(field, '#12ab34')
    expect(field.value).toBe('#12ab34')
    expect(applied(PRIMARY.varName), 'and the color in effect is the one typed').toBe('#12ab34')
  })

  it('a half-typed value is marked, applies nothing, and gives way to the color in effect on blur', async () => {
    await mountEditor()
    const field = hexFieldIn(PRIMARY.label)
    const before = applied(PRIMARY.varName)
    await userEvent.clear(field)
    await userEvent.type(field, '#12')
    expect(field.value).toBe('#12')
    expect(field.getAttribute('aria-invalid')).toBe('true')
    expect(applied(PRIMARY.varName), 'an incomplete hex changes nothing').toBe(before)
    await userEvent.tab()
    expect(field.value, 'leaving the field shows the color actually applied').toBe(before)
    expect(field.getAttribute('aria-invalid')).toBe('false')
  })
})

describe('every hex field has a name of its own', () => {
  it('🔴 all twenty-eight are named after their token', async () => {
    await mountEditor()
    // Vacuity: the editor really rendered the whole color vocabulary.
    expect(COLOR_TOKENS).toHaveLength(28)
    const names = COLOR_TOKENS.map((t) => `${t.label} hex value`)
    for (const name of names) expect(screen.getByRole('textbox', { name })).toBeTruthy()
    expect(new Set(names).size, 'and no two share one').toBe(names.length)
  })
})

describe('each saved theme has its own delete', () => {
  it('🔴 the delete names the theme it removes', async () => {
    await mountEditor()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Delete saved theme Ocean' })).toBeTruthy())
    expect(screen.getByRole('button', { name: 'Delete saved theme Dusk' })).toBeTruthy()
    expect(screen.queryAllByRole('button', { name: 'Delete saved theme' }), 'no bare shared name').toHaveLength(0)
  })
})
