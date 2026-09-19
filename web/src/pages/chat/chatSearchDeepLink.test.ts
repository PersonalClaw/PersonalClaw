import { describe, it, expect, vi, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { chatFindPath, searchSourceLabel } from './searchDeepLink'
import { api } from '../../lib/api'

// ── SM-2: open a search result SCROLLED TO ITS MATCH + surface which path answered ──────
//
// Two clauses of SM-2's done-when were shipped (ranked results, marked snippet); these
// cover the two this change builds:
//   1. clicking a result opens the chat with ?find=<term> → the find bar seeds + scrolls
//   2. the endpoint's `source` (index|scan) is no longer dropped by the client, and shows
//
// The pure path/label logic is unit-tested directly. The two wirings that live inside
// ChatPage's shell — too heavy to mount for this (the sessionLoadHonesty / chatListNoMatch
// precedent) — are pinned from source, which is where a regression would actually land.

describe('chatFindPath (the ?find deep-link the "Open" action navigates to)', () => {
  it('carries the search term as a URL-encoded ?find on the chat route', () => {
    expect(chatFindPath('abc', 'kiln target')).toBe('chat/abc?find=kiln%20target')
  })

  it('URL-encodes reserved characters — the term is threaded, never interpolated raw', () => {
    // A query with a slash / ampersand must not corrupt the hash grammar (`?a&b`, `/seg`).
    expect(chatFindPath('k', 'a & b/c')).toBe('chat/k?find=a%20%26%20b%2Fc')
  })

  it('with no active search term it is a plain chat deep-link, unchanged', () => {
    expect(chatFindPath('abc', '')).toBe('chat/abc')
    expect(chatFindPath('abc', '   ')).toBe('chat/abc')
  })
})

describe('searchSourceLabel (the honest index/scan indicator)', () => {
  it('names each path the endpoint can report', () => {
    expect(searchSourceLabel('index')).toBe('matched via index')
    expect(searchSourceLabel('scan')).toBe('scanned transcripts')
  })

  it('renders nothing when no search ran or the source is unknown', () => {
    expect(searchSourceLabel(undefined)).toBe('')
    expect(searchSourceLabel(null)).toBe('')
    expect(searchSourceLabel('')).toBe('')
    expect(searchSourceLabel('mystery')).toBe('')
  })
})

describe('api.sessionsSearch keeps the source the endpoint reports', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('resolves to {sessions, source} rather than dropping source down to the array', async () => {
    const payload = {
      sessions: [{ key: 'dashboard_abc', title: 'A chat', snippet: 'said <<foo>> once' }],
      source: 'index',
    }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => payload } as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)

    const res = await api.sessionsSearch('foo')
    // The whole envelope survives — this is the clause that was regressing ((d) => d.sessions).
    expect(res.source).toBe('index')
    expect(res.sessions).toHaveLength(1)
    expect(res.sessions[0].key).toBe('dashboard_abc')
    // And the query rode the URL encoded.
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/search?q=foo', expect.anything())
  })

  it('tolerates the source-less envelope the endpoint returns for an empty/short query', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ sessions: [] }) } as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)
    const res = await api.sessionsSearch('x')
    expect(res.sessions).toEqual([])
    expect(res.source).toBeUndefined()
  })
})

// ── Source-structural pins for the two ChatPage wirings (shell too heavy to mount) ──────
const src = readFileSync(join(process.cwd(), 'src', 'pages', 'ChatPage.tsx'), 'utf8')

describe('ChatSession auto-opens the find bar from a ?find deep-link', () => {
  it('reads the find param through the URL-state idiom', () => {
    expect(src).toMatch(/useQueryParam\(query, setQuery, 'find', '', \{ replace: true \}\)/)
  })

  it('seeds + opens the bar, then clears the param so a re-render/Back does not re-fire', () => {
    // The three-line body of the deep-link effect, in order.
    expect(src).toMatch(/setFindSeed\(findParam\)/)
    expect(src).toMatch(/setFindOpen\(true\)/)
    expect(src).toMatch(/setFindParam\(''\)/)
  })

  it('passes the captured seed to the find bar as initialQuery', () => {
    expect(src).toMatch(/initialQuery=\{findSeed\}/)
  })

  it('a manual ⌘F clears the seed first, so it never inherits the deep-link term', () => {
    expect(src).toMatch(/setFindSeed\(''\)\s*\n\s*setFindOpen\(\(o\) => !o\)/)
  })
})

describe('the search result Open/Expand threads the query into the chat', () => {
  it('both peek entry points navigate through chatFindPath with the search term', () => {
    const hits = src.match(/navigate\(chatFindPath\(peekKey, q\)\)/g) ?? []
    // The SidePanel onExpand AND SessionPeekBody onOpen — the two ways a result opens.
    expect(hits.length).toBe(2)
  })
})

describe('the client-kept source surfaces in the search UI', () => {
  it('captures the source from the response into state', () => {
    expect(src).toMatch(/setContentSource\(source \?\? null\)/)
    // destructures the envelope rather than the old (rows) => that dropped source
    expect(src).toMatch(/api\.sessionsSearch\(query\)\.then\(\(\{ sessions: rows, source \}\)/)
  })

  it('renders the index/scan label as a sanctioned caption (parsed text, not raw HTML)', () => {
    expect(src).toMatch(/searchSourceLabel\(contentSource\)/)
    expect(src).toMatch(/data-type="caption"[^>]*>\s*\n\s*\{searchSourceLabel\(contentSource\)\}/)
  })
})
