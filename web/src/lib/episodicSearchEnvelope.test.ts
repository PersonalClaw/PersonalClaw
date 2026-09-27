/**
 * `api.searchEpisodic` reads the envelope the search actually answers (F-52).
 *
 * `GET /api/memory/episodic/search` answers `{results, ranking}`
 * (`dashboard/handlers/memory.py`), while the episodic LIST answers `{entries}`. The wrapper read
 * `entries` off the search, so every search resolved to `undefined`; it had no caller until ⌘K's
 * memory search, which is where a live drive threw it. Driven through the real request helper
 * with a stubbed `fetch` answering the handler's own shape.
 */
import { describe, it, expect, afterEach, vi } from 'vitest'
import { api } from './api'

afterEach(() => { vi.unstubAllGlobals() })

describe('api.searchEpisodic', () => {
  it("resolves to the search's results", async () => {
    const body = { results: [{ id: 'e1', text: 'Talked the budget through with Sam' }], ranking: { mode: 'keyword' } }
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchSpy)
    const found = await api.searchEpisodic('budget')
    expect(found).toEqual(body.results)
    const [url] = fetchSpy.mock.calls[0] as unknown as [string]
    expect(url).toBe('/api/memory/episodic/search?q=budget')
  })
})
