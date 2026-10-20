import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import type { SearchCapabilitiesInfo, SearchProviderInfo, SettingsProvider } from '../../lib/api'

// ── A search provider's state is what was measured, and it has a Test ─────────────────────────────
//
// Settings → Search read "ready" beside a search app whose key was merely present: Brave Search with
// a key it refused read exactly like one that worked, and nothing tried the key anywhere (model
// providers have "Test connection", channels a Test). Each provider now carries the outcome of its
// last search — its Test, or a real search — and one nothing has measured says so. Its Test runs one
// search and the row then says what it found, in the provider's own words when it failed.

const searchProviders = vi.fn()
const searchActive = vi.fn()
const tools = vi.fn()
const testSearchProvider = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    searchProviders: (...a: unknown[]) => searchProviders(...a),
    searchActive: (...a: unknown[]) => searchActive(...a),
    tools: (...a: unknown[]) => tools(...a),
    testSearchProvider: (...a: unknown[]) => testSearchProvider(...a),
    setActiveSearchProvider: vi.fn(),
  },
}))

const CAPS: SearchCapabilitiesInfo = {
  returns_content: false, returns_answer: false, returns_highlights: false,
  supports_recency: true, supports_domains: false, supports_fetch: false, depths: [],
}
const REFUSED = 'Brave Search refused the API key (HTTP 401).'

function brave(over: Partial<SearchProviderInfo> = {}): SearchProviderInfo {
  return { name: 'brave', display_name: 'Brave Search', app: 'brave-search', capabilities: CAPS, available: true, check: null, ...over }
}
const ddg: SearchProviderInfo = {
  name: 'duckduckgo', display_name: 'DuckDuckGo', app: 'duckduckgo-search',
  capabilities: { ...CAPS, keyless: true }, available: true, check: null,
}

async function openGeneral() {
  const { SearchPanel } = await import('./SearchPanel')
  render(<SearchPanel />)
  fireEvent.click(await screen.findByRole('button', { name: /General search/ }))
  return screen.findByRole('group', { name: 'General search provider' })
}

const rowOf = (group: HTMLElement, name: string) =>
  within(group).getByRole('button', { name: new RegExp(`^${name}`) })

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
  searchActive.mockResolvedValue({ 'search-general': [] })
  tools.mockResolvedValue([{ name: 'web_search', description: '', provider: 'web-tools' }])
})

describe('the state a search provider is shown in', () => {
  it('is never "ready" for a key nothing has tried', async () => {
    searchProviders.mockResolvedValue([brave(), ddg])
    const group = await openGeneral()
    expect(group.textContent).not.toMatch(/\bready\b/)
    expect(rowOf(group, 'Brave Search').textContent).toContain('not checked')
    expect(rowOf(group, 'DuckDuckGo').textContent).toContain('no key needed')
  })

  it('is "failed" with the words its search failed with', async () => {
    searchProviders.mockResolvedValue([brave({ check: { state: 'failed', detail: REFUSED, checked_at: 1_790_000_000 } })])
    const group = await openGeneral()
    expect(rowOf(group, 'Brave Search').textContent).toContain('failed')
    expect(group.textContent).toContain(REFUSED)
  })

  it('is "working" once a search went through', async () => {
    searchProviders.mockResolvedValue([brave({ check: { state: 'ok', detail: '', checked_at: 1_790_000_000 } })])
    const group = await openGeneral()
    expect(rowOf(group, 'Brave Search').textContent).toContain('working')
  })

  it('is "not configured" without a key, as before', async () => {
    searchProviders.mockResolvedValue([brave({ available: false })])
    const group = await openGeneral()
    expect(rowOf(group, 'Brave Search').textContent).toContain('not configured')
  })
})

describe("a provider's Test", () => {
  it('runs one search through it and says what it found', async () => {
    searchProviders.mockResolvedValue([brave()])
    testSearchProvider.mockResolvedValue(brave({ check: { state: 'failed', detail: REFUSED, checked_at: 1_790_000_000 } }))
    const group = await openGeneral()

    fireEvent.click(within(group).getByRole('button', { name: 'Test: Brave Search' }))

    await waitFor(() => expect(testSearchProvider).toHaveBeenCalledWith('brave'))
    await waitFor(() => expect(screen.getByText(REFUSED)).toBeTruthy())
  })

  it('is not a way to bind it: testing leaves the binding alone', async () => {
    searchProviders.mockResolvedValue([brave()])
    testSearchProvider.mockResolvedValue(brave({ check: { state: 'ok', detail: '', checked_at: 1 } }))
    const group = await openGeneral()
    fireEvent.click(within(group).getByRole('button', { name: 'Test: Brave Search' }))
    await waitFor(() => expect(testSearchProvider).toHaveBeenCalledTimes(1))
    expect(rowOf(group, 'Brave Search').getAttribute('aria-pressed')).toBe('false')
  })
})

describe('Settings › Providers shows the same state and Test on the app', () => {
  it("puts the provider's state and its Test on the search app's card", async () => {
    const { SearchProviderCheck } = await import('./searchProviderState')
    testSearchProvider.mockResolvedValue(brave({ check: { state: 'failed', detail: REFUSED, checked_at: 1 } }))
    render(<SearchProviderCheck provider={brave()} />)
    expect(screen.getByText('not checked')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Test: Brave Search' }))
    await waitFor(() => expect(screen.getByText(REFUSED)).toBeTruthy())
  })

  it('says so when the search state could not be read, and never shows a state it did not read', async () => {
    const { ProviderCard } = await import('./ProviderCard')
    const retry = vi.fn()
    const ext: SettingsProvider = {
      name: 'brave-search', displayName: 'Brave Search', enabled: true, managed: true,
      provider: { type: 'search', capabilities: [] },
    }
    render(<ProviderCard ext={ext} search={brave({ check: { state: 'ok', detail: '', checked_at: 1 } })}
      searchUnread onSearchRetry={retry} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    expect(screen.getByText("Couldn't read what this provider's last search found.")).toBeTruthy()
    expect(screen.queryByText('working'), 'a cached state was shown as if it had been read').toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })
})
