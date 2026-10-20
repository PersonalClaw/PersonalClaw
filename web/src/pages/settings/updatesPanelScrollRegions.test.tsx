import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { UpdateCheck } from '../../lib/api'

// ── The Updates panel's two scroll boxes are tab stops that say what they are ───────────────────────
//
// Release notes and Changelog are each capped at `max-h-96`, and both overflow with real data. The
// release notes are the newest release's GitHub notes: 0.2.0's carried links, so the box held
// something a keyboard could reach, but 0.2.1's are 34 lines with no link in them. From the moment that
// release was published, `#/settings/updates` failed axe's `scrollable-region-focusable` (serious) in
// both themes, on every pull request the browser gate ran for. The fix is the repo's canonical trio
// (`design/scrollRegionNamed.test.tsx`): a tab stop, `role="group"`, and the section's own title as the
// name, so focus does not announce the whole text as the region's name either.

const useQuery = vi.fn()
vi.mock('../../lib/data', () => ({
  useQuery: (...a: unknown[]) => useQuery(...a),
  invalidateKeys: vi.fn(),
}))

const { UpdatesPanel } = await import('./UpdatesPanel')

const NOTES = Array.from({ length: 34 }, (_, i) => `- **A change with no link in it, ${i + 1}.**`).join('\n')

const INFO: UpdateCheck = {
  available: false, checked: true, auto: 'off', kind: 'pip', current: '0.2.1', latest: '0.2.1',
  update_available: false, channel: 'stable', check_enabled: true, changes: '', release_notes: NOTES,
}

function mount(info: UpdateCheck = INFO, changelog = '## [0.2.1] — 2026-10-15\n\n### Fixed\n\n- **A fix.**\n') {
  useQuery.mockReturnValue({ data: { info, changelog }, loading: false, error: null, refresh: vi.fn() })
  return render(<UpdatesPanel />)
}

describe('the Updates panel names and focuses its scroll boxes', () => {
  it.each(['Release notes', 'Changelog'])('%s is a focusable group named by its section', async (name) => {
    mount()
    const box = await screen.findByRole('group', { name })
    expect(box.tabIndex, 'a keyboard reaches it even with no link inside').toBe(0)
    expect(box.className).toMatch(/max-h-96 overflow-auto/)
    expect(box.querySelectorAll('a').length, 'the case that failed: nothing inside to focus').toBe(0)
  })

  it('puts the notes inside the named box', async () => {
    mount()
    const box = await screen.findByRole('group', { name: 'Release notes' })
    expect(box.querySelectorAll('li')).toHaveLength(34)
  })

  it('draws neither box when there is nothing to scroll', async () => {
    mount({ ...INFO, release_notes: '' }, '')
    expect(await screen.findByText('No changelog available.')).toBeTruthy()
    expect(screen.queryByRole('group', { name: 'Release notes' })).toBeNull()
    expect(screen.queryByRole('group', { name: 'Changelog' })).toBeNull()
  })
})
