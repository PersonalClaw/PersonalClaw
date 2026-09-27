import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ThemeRecord, ThemeWrite } from '../../lib/api'

// ── "Update theme" is saved only over the copy of the theme its colors came from ──────────────────
//
// The update rewrites the whole saved theme: the name and emoji the Design panel loaded, and the
// colors this browser holds (seeded when the theme was applied, and persisted since). When the theme
// changed in between — renamed in another tab, recolored on another device and synced here — the
// update replaced it with this page's copy, and nothing said so. The gateway now refuses an update
// whose base is stale (`409 stale_write`); this mounts the real providers around the real panel.

const PAINTED: ThemeRecord = {
  slug: 'harbor', name: 'Harbor', emoji: '🌊', created_at: '2026-09-01T00:00:00Z',
  dark: { '--color-primary': '#112233' }, light: { '--color-primary': '#445566' },
}
// What is stored by the time the update is sent: another device renamed it, and sync pulled that in.
const STORED: ThemeRecord = { ...PAINTED, name: 'Harbor at dusk' }

const { themeReads, updateTheme } = vi.hoisted(() => ({ themeReads: { n: 0 }, updateTheme: vi.fn() }))

function staleWrite() {
  return Object.assign(new Error("This write replaces the theme 'harbor', which changed after the copy it was built from was read."), { status: 409, code: 'stale_write' })
}

vi.mock('../../lib/api', () => ({
  api: new Proxy({
    themes: () => Promise.resolve([{ slug: 'harbor', name: 'Harbor', emoji: '🌊', created_at: '' }]),
    theme: () => {
      themeReads.n += 1
      return Promise.resolve(themeReads.n === 1 ? { ...PAINTED, revision: 'r1' } : { ...STORED, revision: 'r2' })
    },
    updateTheme: (...a: unknown[]) => updateTheme(...a),
  } as Record<string, unknown>, { get: (t, k: string) => t[k] ?? (() => Promise.resolve(null)) }),
}))

import { ThemeProvider } from '../../app/theme'
import { AppearanceProvider } from '../../app/appearance'
import { PersonalityProvider } from '../../app/personality'
import { DesignPanel } from './DesignPanel'

async function updateOverAStaleCopy() {
  render(
    <ThemeProvider>
      <AppearanceProvider>
        <PersonalityProvider>
          <DesignPanel />
        </PersonalityProvider>
      </AppearanceProvider>
    </ThemeProvider>,
  )
  // Apply the saved theme: its colors — and the copy they came from — are what this page now holds.
  // The tile itself — not its trash button, which is named after the theme too.
  const tile = await screen.findByRole('button', { name: /^(?!Delete saved theme).*Harbor/ })
  await act(async () => { fireEvent.click(tile) })
  fireEvent.click(screen.getByRole('button', { name: /Edit colors & save a custom theme/ }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Update theme/ })) })
}

beforeEach(() => {
  localStorage.clear()
  themeReads.n = 0
  updateTheme.mockReset().mockImplementation((_slug: string, body: ThemeWrite, base: string) =>
    base === 'r2'
      ? Promise.resolve({ ok: true, theme: { ...STORED, ...body }, revision: 'r3' })
      : Promise.reject(staleWrite()))
})

describe('"Update theme" from a stale copy', () => {
  it('is refused with the notice, over the revision the colors came from', async () => {
    await updateOverAStaleCopy()
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/The theme “Harbor” changed elsewhere/)
    expect(updateTheme).toHaveBeenCalledTimes(1)
    const [slug, body, base] = updateTheme.mock.calls[0]
    expect([slug, base]).toEqual(['harbor', 'r1'])
    expect(body).toMatchObject({ name: 'Harbor', emoji: '🌊' })
    expect((body as ThemeWrite).dark['--color-primary']).toBe('#112233')
  })

  it('Reload and reapply keeps the rename made elsewhere and saves the colors over it', async () => {
    await updateOverAStaleCopy()
    const alert = await screen.findByRole('alert')
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })
    await waitFor(() => expect(updateTheme).toHaveBeenCalledTimes(2))
    const [, body, base] = updateTheme.mock.calls[1]
    expect(base).toBe('r2')
    expect(body).toMatchObject({ name: 'Harbor at dusk' })
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })
})
