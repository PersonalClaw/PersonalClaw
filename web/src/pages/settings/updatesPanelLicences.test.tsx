import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import type { UpdateCheck } from '../../lib/api'

// ── Settings → Updates shows the licences beside the version ─────────────────────────────────
//
// The dashboard ships other projects' code and fonts, and their notices are plain-text files the
// gateway serves at the dist root: THIRD_PARTY_NOTICES.txt (the fonts, tracked in web/public) and
// THIRD_PARTY_NOTICES_NPM.txt (every bundled npm package, written by the web build). Before this,
// neither was linked from anywhere, and the gateway answered both URLs with the dashboard itself.
// tests/test_pwa_file_symlink.py holds the gateway's routes to these same two paths.

vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../ui/dialog', () => ({ confirm: vi.fn() }))
const useQuery = vi.fn()
vi.mock('../../lib/data', () => ({
  useQuery: (...a: unknown[]) => useQuery(...a),
  invalidateKeys: vi.fn(),
}))

const { UpdatesPanel } = await import('./UpdatesPanel')

const INFO = { available: false, checked: true, kind: 'pip', current: '0.1.3', channel: 'stable', check_enabled: true } as UpdateCheck

describe('Settings → Updates → Licences', () => {
  it('names the licence PersonalClaw is under and links both notices, each opening in a new tab', () => {
    useQuery.mockReturnValue({ data: { info: INFO, changelog: '' }, loading: false, error: null, refresh: vi.fn() })
    render(<UpdatesPanel />)

    const section = screen.getByRole('heading', { name: 'Licences' }).closest('section') ?? document.body
    expect(within(section).getByText(/PersonalClaw is MIT-licensed/)).toBeTruthy()
    const packages = within(section).getByRole('link', { name: "Open the open-source packages' licence notices" })
    expect(packages.getAttribute('href')).toBe('/THIRD_PARTY_NOTICES_NPM.txt')
    expect(packages.getAttribute('target')).toBe('_blank')
    const fonts = within(section).getByRole('link', { name: "Open the fonts' licence notices" })
    expect(fonts.getAttribute('href')).toBe('/THIRD_PARTY_NOTICES.txt')
    expect(fonts.getAttribute('target')).toBe('_blank')
  })

  it('sits directly under the Version section, where the version it describes is shown', () => {
    useQuery.mockReturnValue({ data: { info: INFO, changelog: '' }, loading: false, error: null, refresh: vi.fn() })
    render(<UpdatesPanel />)
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings.slice(0, 2)).toEqual(['Version', 'Licences'])
  })
})
