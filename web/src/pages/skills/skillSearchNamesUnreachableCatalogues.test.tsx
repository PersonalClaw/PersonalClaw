import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// ── Skills › Browse names a catalogue it could not search ────────────────────────────────────────
//
// The search fans out to every catalogue and skips one that fails, so one dead catalogue cannot
// empty the store. But the skip was silent: a search the only remote catalogue never answered
// rendered "No results — Try a different search term or marketplace", blaming the query for an
// outage. The gateway now names each catalogue it could not reach (`unreachable`); the page says
// which, beside whatever the others found, and offers the search again.

const MISSED = [{ source: 'skills.sh', reason: "The request failed unexpectedly. Check the provider's logs and try again." }]

let searchSkillsCounted = vi.fn()

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      skillMarketplaces: () => Promise.resolve([{ name: 'skills.sh' }]),
      searchSkillsCounted: (...a: unknown[]) => searchSkillsCounted(...a),
    },
  }))
}

async function mount() {
  const { SkillsPage } = await import('./SkillsPage')
  render(<SkillsPage query={{ mode: 'browse', q: 'postgres' }} setQuery={() => {}} />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the skill search says which catalogues it could not reach', () => {
  it('names the catalogue instead of saying there are no results, and searches again', async () => {
    searchSkillsCounted = vi.fn(() => Promise.resolve({ results: [], counts: {}, installableSources: 1, unreachable: MISSED }))
    mockApi()
    await mount()
    const said = await screen.findByText(/Couldn't search skills\.sh, so its skills are missing here/)
    expect(said.closest('[role="status"]')).not.toBeNull()
    expect(screen.getByText(MISSED[0].reason, { exact: false })).toBeInTheDocument()
    expect(screen.queryByText('No results'), 'the query is not what failed').toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(searchSkillsCounted).toHaveBeenCalledTimes(2))
  })

  it('still says "No results" when every catalogue answered and none matched', async () => {
    searchSkillsCounted = vi.fn(() => Promise.resolve({ results: [], counts: {}, installableSources: 1, unreachable: [] }))
    mockApi()
    await mount()
    expect(await screen.findByText('No results')).toBeInTheDocument()
    expect(screen.queryByText(/Couldn't search/)).toBeNull()
  })
})
