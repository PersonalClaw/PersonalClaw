import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, act, cleanup, waitFor } from '@testing-library/react'
import { Compass } from 'lucide-react'
import { FRAMES_MAY_HAVE_MOVED } from './framesMayHaveMoved'

// ── An overlay gone from over the page says its widget frames may have moved ──────────────────
//
// A pointer resting where a widget frame sits once an overlay over it goes did not move onto the
// frame, so the bridge must not read the arrival that follows as the owner's. ui/Modal, the
// shell's dialogs and ui/Popover say so (their pins are in the chat's rendered cases and in
// Popover.test.tsx); so do the command palette, the snip overlay and the tour, each once it has
// gone.

vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  return { ...real, api: { ...real.api, sessionsSearch: vi.fn(async () => ({ sessions: [], source: 'index' })) } }
})

import { CommandPalette } from '../app/CommandPalette'
import { SnipOverlay } from './SnipOverlay'
import { SpotlightTour } from './SpotlightTour'

/** Count the page's FRAMES_MAY_HAVE_MOVED events from here on. */
function counting() {
  const seen = { n: 0 }
  const count = () => { seen.n++ }
  window.addEventListener(FRAMES_MAY_HAVE_MOVED, count)
  return { seen, stop: () => window.removeEventListener(FRAMES_MAY_HAVE_MOVED, count) }
}

afterEach(() => cleanup())

describe('an overlay gone from over the page', () => {
  it('🔴 the command palette says so once its exit is done', async () => {
    Element.prototype.scrollIntoView ??= () => {}
    const { seen, stop } = counting()
    try {
      render(<CommandPalette commands={[{ id: 'go:discover', label: 'Discover', hint: 'Go to', icon: Compass, keywords: '', run: vi.fn() }]} navigate={vi.fn()} />)
      act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true })) })
      await screen.findByRole('searchbox')
      expect(seen.n, 'open: nothing yet').toBe(0)
      act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })) })
      await waitFor(() => expect(screen.queryByRole('searchbox')).toBeNull())
      await waitFor(() => expect(seen.n).toBe(1))
    } finally { stop() }
  })

  it('🔴 the snip overlay says so once it goes', () => {
    const { seen, stop } = counting()
    try {
      const view = render(<SnipOverlay frame="data:image/png;base64,AAAA" width={1600} height={900} onCancel={vi.fn()} onConfirm={vi.fn()} />)
      expect(seen.n, 'shown: nothing yet').toBe(0)
      view.unmount()
      expect(seen.n).toBe(1)
    } finally { stop() }
  })

  it('🔴 the tour says so once it goes', () => {
    const { seen, stop } = counting()
    try {
      const view = render(<SpotlightTour steps={[{ id: 'one', anchor: 'one', icon: Compass, title: 'The first thing', body: 'What it is for.' }]} index={0} label="Test tour" onIndex={vi.fn()} onExit={vi.fn()} />)
      expect(seen.n, 'shown: nothing yet').toBe(0)
      view.unmount()
      expect(seen.n).toBe(1)
    } finally { stop() }
  })
})
