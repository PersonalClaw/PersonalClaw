import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { SearchCapabilitiesInfo } from '../../lib/api'
import { SearchPanel } from './SearchPanel'

// ── A one-of-N bind whose only answer to "which one?" was a coral circle ──────────────────────────
//
// `pages/settings/exclusiveChoiceNamed.test.tsx` proves the ATTRIBUTE is at the call site, across the
// whole family. This file proves the thing that actually matters for the user: the accessibility TREE
// says which provider is bound, and says it for the right use case. A source grep cannot tell the
// difference between `aria-pressed={on}` on the right row and on the wrong one.
//
// 🔑 WHY THIS SURFACE COULD NOT BE MEASURED IN THE BROWSER. Search providers register through the
// extension system (`SearchTypeHandler`), so the list is empty until an app that declares one is
// installed — and this workspace has no apps clone, so `#/settings/search` renders its
// "No search providers configured" path and nothing else. jsdom is the only place the bind list
// exists at all here, which is exactly why the contract needs pinning rather than eyeballing.

const searchProviders = vi.fn()
const searchActive = vi.fn()
const setActiveSearchProvider = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    searchProviders: (...a: unknown[]) => searchProviders(...a),
    searchActive: (...a: unknown[]) => searchActive(...a),
    setActiveSearchProvider: (...a: unknown[]) => setActiveSearchProvider(...a),
  },
}))

const CAPS: SearchCapabilitiesInfo = {
  returns_content: true, returns_answer: true, returns_highlights: false,
  supports_recency: true, supports_domains: false, supports_fetch: false, depths: [],
}

/** Open a use-case row by its heading text — the row button is the disclosure. */
async function openRow(name: RegExp) {
  fireEvent.click(await screen.findByRole('button', { name }))
}

describe('the search bind list announces which provider is bound, and to what', () => {
  beforeEach(() => {
    localStorage.clear()   // `useQuery(persist: true)` would otherwise carry a prior test's data
    vi.clearAllMocks()
    searchProviders.mockResolvedValue([
      { name: 'searxng', display_name: 'SearXNG', capabilities: CAPS, available: true },
      { name: 'tavily', display_name: 'Tavily', capabilities: { ...CAPS, supports_fetch: true }, available: false },
    ])
    searchActive.mockResolvedValue({ 'search-general': ['searxng'] })
  })

  it('the group is named with its use case, so four sibling lists are distinguishable', async () => {
    render(<SearchPanel />)
    await openRow(/General search/)
    // Not "provider" four times over: the dimension is the use case, and it is the only thing that
    // tells the user WHICH of this panel's four binds they are inside.
    expect(await screen.findByRole('group', { name: 'General search provider' })).toBeTruthy()
    await openRow(/News search/)
    expect(screen.getByRole('group', { name: 'News search provider' })).toBeTruthy()
  })

  it('exactly one option is pressed, and it is the bound one', async () => {
    render(<SearchPanel />)
    await openRow(/General search/)
    const group = await screen.findByRole('group', { name: 'General search provider' })
    const opts = [...group.querySelectorAll('button')]
    expect(opts.map((b) => b.textContent?.includes('SearXNG'))).toContain(true)
    const pressed = opts.filter((b) => b.getAttribute('aria-pressed') === 'true')
    expect(pressed.length, 'single-select: one pressed, not zero and not two').toBe(1)
    expect(pressed[0].textContent, 'and it is the provider the backend reports as bound').toContain('SearXNG')
    // The unbound one must say so rather than say nothing — a missing attribute reads as "not a toggle".
    const unpressed = opts.filter((b) => b.getAttribute('aria-pressed') === 'false')
    expect(unpressed.length).toBe(1)
    expect(unpressed[0].textContent).toContain('Tavily')
  })

  it('an unbound use case presses nothing at all', async () => {
    render(<SearchPanel />)
    await openRow(/Financial search/)
    const group = await screen.findByRole('group', { name: 'Financial search provider' })
    const opts = [...group.querySelectorAll('button')]
    expect(opts.length, 'both providers are eligible for a plain search use case').toBe(2)
    expect(opts.filter((b) => b.getAttribute('aria-pressed') === 'true')).toEqual([])
  })

  it('the state follows the write, not the click', async () => {
    // The row re-reads from the backend after a PUT, so the pressed state must come from the
    // refreshed binding. Asserting the click alone would pass on an optimistic UI that lies.
    render(<SearchPanel />)
    await openRow(/General search/)
    const group = await screen.findByRole('group', { name: 'General search provider' })
    searchActive.mockResolvedValue({ 'search-general': ['tavily'] })
    setActiveSearchProvider.mockResolvedValue({ ok: true })
    fireEvent.click([...group.querySelectorAll('button')].find((b) => b.textContent?.includes('Tavily'))!)
    expect(setActiveSearchProvider).toHaveBeenCalledWith('search-general', ['tavily'])
    await waitFor(() => {
      const now = [...screen.getByRole('group', { name: 'General search provider' }).querySelectorAll('button')]
      expect(now.find((b) => b.getAttribute('aria-pressed') === 'true')?.textContent).toContain('Tavily')
    })
  })

  it('fetch-article only offers a provider that can extract content', async () => {
    // Pre-existing behaviour, pinned here because the group name now makes it observable: if the
    // eligibility filter regressed, this group would hold two options instead of one.
    render(<SearchPanel />)
    await openRow(/Article fetch/)
    const group = await screen.findByRole('group', { name: 'Article fetch provider' })
    const opts = [...group.querySelectorAll('button')]
    expect(opts.length).toBe(1)
    expect(opts[0].textContent).toContain('Tavily')
  })
})
