import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { SearchCapabilitiesInfo } from '../../lib/api'
import { SearchPanel } from './SearchPanel'

// ── Search bindings are shown as stored, or said to be unreadable ─────────────────────────────
//
// The bindings read fell back to `{}`, so a failed `GET /api/search/active` made every use case read
// "none — falls back to General" and offered its providers to pick — as if nothing were bound, which
// is a claim about the server, and an invitation to "fix" a binding that was never missing. The
// budget comment in `ui/loadErrorState.test.tsx` recorded it as exactly that claim.
//
// Same family as the onboarding defect (`app/identityReadFailure.test.tsx`): a failed read became
// the empty state, and the empty state licensed a write.

const searchProviders = vi.fn()
const searchActive = vi.fn()
const setActiveSearchProvider = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    searchProviders: (...a: unknown[]) => searchProviders(...a),
    searchActive: (...a: unknown[]) => searchActive(...a),
    setActiveSearchProvider: (...a: unknown[]) => setActiveSearchProvider(...a),
    tools: () => Promise.resolve([{ name: 'web_search', description: '', provider: 'web-tools' }]),
  },
}))

const CAPS: SearchCapabilitiesInfo = {
  returns_content: true, returns_answer: true, returns_highlights: false,
  supports_recency: true, supports_domains: false, supports_fetch: false, depths: [],
}
const PROVIDERS = [{ name: 'searxng', display_name: 'SearXNG', capabilities: CAPS, available: true }]

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
  searchProviders.mockResolvedValue(PROVIDERS)
})

describe('the search bindings', () => {
  it('a failed read says so, instead of reading every use case as unbound', async () => {
    searchActive.mockRejectedValue(new Error('config unreadable'))
    render(<SearchPanel />)
    expect(await screen.findByText(/Couldn't read which provider each use case is bound to/)).toBeInTheDocument()
    expect(screen.queryByText(/none — falls back to General/), 'an unread binding read as none').toBeNull()
    expect(screen.queryByRole('group', { name: /provider$/ }), 'providers were offered to pick against an unread binding').toBeNull()
    expect(setActiveSearchProvider).not.toHaveBeenCalled()
  })

  it('a retry that reads them shows each use case as stored', async () => {
    searchActive.mockRejectedValueOnce(new Error('config unreadable')).mockResolvedValue({ 'search-general': ['searxng'] })
    render(<SearchPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByText(/Couldn't read which provider/)).toBeNull())
    // General is bound to searxng; the other use cases really are unbound, and say so.
    expect(screen.getAllByText(/none — falls back to General/).length).toBeGreaterThan(0)
    expect(screen.getByText('searxng')).toBeInTheDocument()
  })
})
