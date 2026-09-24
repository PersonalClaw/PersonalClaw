import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { SearchCapabilitiesInfo, SearchProviderInfo } from '../../lib/api'
import { SearchPanel, pickableProviders } from './SearchPanel'

// ── A binding that outlived its provider ─────────────────────────────────────────────────────────
//
// Measured on a fresh install: install DuckDuckGo → bind it to General search → uninstall the app.
// `active_search_providers.json` still held `{"search-general": ["duckduckgo"]}` (deliberately — a
// reinstall restores the choice), so Settings → Search rendered, in one panel:
//
//   subtitle  "duckduckgo"                                     ← asserts an active binding
//   body      "No search providers configured. Add one…"        ← asserts there are none
//   controls  the four card headers, and nothing else           ← no way to clear it
//
// Every other layer already knew better. `api_search_active_set` REFUSES to create this state
// ("silently stranding the use-case on a dead provider name"), and `resolve_search_provider_for_use
// _case` already ignores it ("a bound name whose provider isn't registered (disabled/removed) → fall
// through to the implicit fallback"). Only the picker believed the stored name, because it built its
// chip list from the REGISTERED providers and its subtitle from the STORED one — two reads that
// cannot disagree while an app is installed, and must not be shown as one once it is removed.
//
// 🔑 EVERY "not installed" ASSERTION HERE IS PAIRED WITH ITS VACUITY CONTROL. A test that only
// checked the stale case would pass if the chip list stopped rendering at all, so each one is
// matched by the same panel with the provider REGISTERED, asserting `ready` and no stale wording.

const searchProviders = vi.fn()
const searchActive = vi.fn()
const setActiveSearchProvider = vi.fn()
const tools = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    searchProviders: (...a: unknown[]) => searchProviders(...a),
    searchActive: (...a: unknown[]) => searchActive(...a),
    setActiveSearchProvider: (...a: unknown[]) => setActiveSearchProvider(...a),
    tools: (...a: unknown[]) => tools(...a),
  },
}))

const CAPS: SearchCapabilitiesInfo = {
  returns_content: true, returns_answer: false, returns_highlights: false,
  supports_recency: true, supports_domains: false, supports_fetch: false, depths: [],
}
const FETCH_CAPS: SearchCapabilitiesInfo = { ...CAPS, supports_fetch: true }
const DDG: SearchProviderInfo = {
  name: 'duckduckgo', display_name: 'DuckDuckGo', capabilities: CAPS, available: true,
}

/** Open a use-case card — `DisclosureCard` owns its own collapsed state, so the chip list is not
 *  in the tree until the header is pressed. */
async function openCard(label: RegExp) {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: label }))
  return user
}

/** The chip list, scoped. Unscoped `getByRole('button', {name: /duckduckgo/i})` matches the card
 *  HEADER too — its subtitle names the binding — so an unscoped query is ambiguous by construction
 *  once the subtitle is honest. Scoping to the named group is also the assertion that the group is
 *  still named (`exclusiveChoiceNamed` owns that rule). */
function chips(groupLabel: RegExp) {
  return within(screen.getByRole('group', { name: groupLabel }))
}

describe('pickableProviders offers every binding, not only the bindable ones', () => {
  it('a bound provider that is no longer registered comes back as stale', () => {
    expect(pickableProviders('search-general', [], ['duckduckgo']))
      .toEqual([{ kind: 'stale', name: 'duckduckgo' }])
  })

  it('and the same name comes back REGISTERED once the provider is there (the control)', () => {
    expect(pickableProviders('search-general', [DDG], ['duckduckgo']))
      .toEqual([{ kind: 'registered', name: 'duckduckgo', provider: DDG }])
  })

  it('does not duplicate a binding that is already in the eligible list', () => {
    const rows = pickableProviders('search-general', [DDG], ['duckduckgo'])
    expect(rows).toHaveLength(1)
  })

  it('a fetch-article binding to a provider without fetch support is registered, not stale', () => {
    // It still RESOLVES — registry.py step 1 checks registration, not capability — so calling it
    // "not installed" would be a second lie in the other direction. It is simply not on offer for
    // this use-case, and must still be visible and clearable.
    expect(pickableProviders('fetch-article', [DDG], ['duckduckgo']))
      .toEqual([{ kind: 'registered', name: 'duckduckgo', provider: DDG }])
  })

  it('still filters the OFFER for fetch-article by supports_fetch', () => {
    const fetcher: SearchProviderInfo = {
      name: 'tavily', display_name: 'Tavily', capabilities: FETCH_CAPS, available: true,
    }
    expect(pickableProviders('fetch-article', [DDG, fetcher], []))
      .toEqual([{ kind: 'registered', name: 'tavily', provider: fetcher }])
  })
})

describe('Settings → Search tells the truth about a binding whose app was uninstalled', () => {
  beforeEach(() => {
    localStorage.clear()   // `useQuery(persist: true)` would otherwise carry a prior test's data
    vi.clearAllMocks()
    tools.mockResolvedValue([{ name: 'web_search', description: '', provider: 'web-tools' }])
    searchActive.mockResolvedValue({ 'search-general': ['duckduckgo'] })
  })

  it('names the stale binding "not installed" instead of showing it as the active provider', async () => {
    searchProviders.mockResolvedValue([])
    render(<SearchPanel />)
    // The header itself must not read as a working binding: the bare name claimed one.
    const header = await screen.findByRole('button', { name: /General search/ })
    expect(header.textContent).toMatch(/not installed/i)
    expect(header.textContent).toMatch(/falls back to any available provider/i)
  })

  it('the same panel says "ready" when the provider IS registered (the control)', async () => {
    searchProviders.mockResolvedValue([DDG])
    render(<SearchPanel />)
    const header = await screen.findByRole('button', { name: /General search/ })
    expect(header.textContent).not.toMatch(/not installed/i)
    await openCard(/General search/)
    const chip = await screen.findByRole('button', { name: /DuckDuckGo/ })
    expect(chip.textContent).toMatch(/ready/i)
    expect(chip.textContent).not.toMatch(/not installed/i)
  })

  it('puts the stale binding in the picker so it can be cleared, and clearing it sends []', async () => {
    searchProviders.mockResolvedValue([])
    setActiveSearchProvider.mockResolvedValue({ ok: true })
    render(<SearchPanel />)
    const user = await openCard(/General search/)
    // 🔑 The whole defect in one assertion: before the fix the body was the "No search providers
    // configured" empty state and this control did not exist, so the binding was unremovable.
    const stale = chips(/General search provider/).getByRole('button', { name: /duckduckgo/i })
    expect(stale.getAttribute('aria-pressed')).toBe('true')
    expect(stale.textContent).toMatch(/not installed/i)
    await user.click(stale)
    await waitFor(() => expect(setActiveSearchProvider).toHaveBeenCalledWith('search-general', []))
  })

  it('does not render the "no providers configured" empty state beside a stale binding', async () => {
    searchProviders.mockResolvedValue([])
    render(<SearchPanel />)
    await openCard(/General search/)
    // The panel-level banner stays — there genuinely are none, and the Store is the remedy. What
    // must not survive is the ROW saying it has nothing while its own header names a binding.
    await waitFor(() => expect(chips(/General search provider/).getByRole('button', { name: /duckduckgo/i })).toBeTruthy())
    expect(screen.queryByText(/Add one in Providers first/i)).toBeNull()
  })

  it('an unbound use-case with no providers still shows the empty state (the control)', async () => {
    searchProviders.mockResolvedValue([])
    searchActive.mockResolvedValue({})
    render(<SearchPanel />)
    await openCard(/General search/)
    expect(await screen.findByText(/Add one in Providers first/i)).toBeTruthy()
  })
})
