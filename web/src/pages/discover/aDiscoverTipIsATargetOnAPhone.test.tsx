// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { DiscoverResponse } from '../../lib/api'

// ── A Discover tip's link and dismiss are 44 px targets on a phone ────────────────────────────────
//
// The tip (b4093fb6, #3818) shows in an empty Library, Knowledge, Automations or Capabilities page,
// and the UI quality matrix measured both of its controls under 44 px at 320, 390, 412 and 768 px
// on every one of them (#3905): the dismiss is drawn at 24 px and the link is a small text link.
// Each now carries the compact 44 px floor the buddy card's controls do, and keeps its own size
// above the compact widths.
//
// jsdom has no layout, so the classes are the honest thing to pin: they are what set the box. The
// matrix (`e2e/surfaces.spec.ts`) measures the box itself.

const discover = vi.fn<() => Promise<DiscoverResponse>>()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<typeof import('../../lib/api')>()),
  api: { discover: () => discover(), dismissDiscoverTip: () => Promise.resolve({ ok: true, dismissed: [] }) },
}))

const { DiscoverTip, TIP_TARGET } = await import('./DiscoverTip')
const { resetDataStore } = await import('../../lib/data/store')

const feed = (): DiscoverResponse => ({
  enabled: true,
  areas: [{ area: 'Area', tips: [{ id: 'chat', area: 'Area', title: 'Start a chat', lesson: 'What a chat does.', try_it: { route: 'chat/new', label: 'Open Chat' } }] }],
  visible_count: 1, total: 1, dismissed_count: 0,
} as DiscoverResponse)

beforeEach(() => {
  resetDataStore()
  discover.mockReset().mockResolvedValue(feed())
})
afterEach(() => cleanup())

/** Whether `el`'s classes give it a 44 px box below the large breakpoint, in both dimensions. */
const compactFloor = (el: Element) => {
  const cls = el.className.toString().split(/\s+/)
  return cls.includes('max-lg:min-h-11') && cls.includes('max-lg:min-w-11')
}

describe('a Discover tip on a phone', () => {
  it('the floor is 44 px both ways, below the large breakpoint only', () => {
    // min-h-11 and min-w-11 are 2.75rem, 44 px; `max-lg:` is under 1024 px, so 320 to 768.
    expect(TIP_TARGET.split(' ')).toEqual(expect.arrayContaining(['max-lg:min-h-11', 'max-lg:min-w-11']))
    expect(TIP_TARGET).not.toMatch(/(^|\s)min-[hw]-/)
  })

  it('its link and its dismiss each carry it', async () => {
    render(<DiscoverTip here="library" />)
    const dismiss = await screen.findByRole('button', { name: 'Dismiss this tip' })
    const link = screen.getByRole('link', { name: 'Open Chat' })
    expect(compactFloor(dismiss), dismiss.className.toString()).toBe(true)
    expect(compactFloor(link), link.className.toString()).toBe(true)
  })

  it('so does the link first run hands its own way out', async () => {
    render(<DiscoverTip here="library" onGo={() => {}} />)
    const go = await screen.findByRole('button', { name: 'Open Chat' })
    expect(compactFloor(go), go.className.toString()).toBe(true)
  })
})
