import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A failed stats read must not claim a capability is off, least of all with advice ─────────────
//
// `knowledgeStats()` used to end
//
//     .catch(() => ({ items: 0, entities: 0, relations: 0, embeddings: { enabled: false } }))
//
// turning a failed GET into four confident statements. Three are wrong counts. The fourth is worse
// than wrong: `EmbeddingChip` branches on `!e?.enabled` and renders "semantic search off", titled
// "No embedding model active — search is keyword + entity-graph only. Set one in Settings › AI &
// Models." So an unreachable gateway told the user a capability was off AND sent them to reconfigure
// a setting that may already have been correct.
//
// 🔑 A WRONG INDICATOR IS BAD; WRONG ACTIONABLE ADVICE FROM A REQUEST THAT NEVER LANDED IS THE PART
// THIS CLOSES. The user cannot tell the two apart, and the advice costs them a trip to Settings to
// "fix" something that was never broken.
//
// 🪤 THE FIX ADDS NO UI. `const stats = statsData ?? null` plus `{stats && (…)}` already gate the
// whole strip, so an unread response now renders NOTHING — identical to the not-yet-loaded state,
// which is precisely what it is.
//
// 🔴 AND THE FABRICATED ZERO WAS DOING MORE DAMAGE THAN THE CHIP. `empty` is
// `stats && stats.items === 0`, and it gates BOTH the "Knowledge base is empty" card and (via
// `!empty`) the Home shelves. With the swallow in place a stats outage forced `items: 0`, so a
// POPULATED library was told it was empty and offered "Add knowledge". Removing the swallow leaves
// `stats` null, `empty` falsy, and the real item list renders instead.
//
// 🪤 `empty` STAYS ON `stats.items` — an earlier draft of this fix "decoupled" it to
// `items.length === 0` and that was a regression: `items` is filtered by search and shelf, so a
// no-match search would have claimed the library was empty and suppressed Home.
// `libraryHomeReachable.test.tsx` caught it. Whole-library claims need the whole-library count.

const okStats = { items: 3, entities: 9, relations: 4, embeddings: { enabled: true, embedded_items: 3, model: 'm' } }
const boom = () => Promise.reject(new Error('gateway down'))

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      knowledgeStats: () => Promise.resolve(okStats),
      knowledgeItems: () => Promise.resolve({ items: [] }),
      knowledgeCollections: () => Promise.resolve([]),
      knowledgeIntents: () => Promise.resolve([]),
      ...over,
    },
  }))
}

describe('the knowledge stats strip never states a capability it could not read', () => {
  beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

  it('a failed stats read renders no "semantic search off" claim and no advice', async () => {
    mockApi({ knowledgeStats: boom })
    const { KnowledgeListPage } = await import('./KnowledgeListPage')
    render(<KnowledgeListPage query={{}} setQuery={() => {}} onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />)

    // 🪤 WAIT ON A POSITIVE SIGNAL FIRST, NEVER ON THE ABSENCE ITSELF. An earlier draft did
    // `await waitFor(() => expect(queryByText(/semantic search off/i)).toBeNull())`, which SUCCEEDS
    // ON ITS FIRST CHECK — before the read could ever have produced that text. Falsifying the fix
    // exposed it: with the swallow restored, the source assertion below failed while this test
    // still passed, i.e. it was green against the exact defect it exists to catch.
    //
    // The anchor is the page title, which is present regardless of either read. It deliberately is
    // NOT the empty-state card: with stats down `empty` is falsy by design, so that card is
    // legitimately absent here.
    await waitFor(() => expect(screen.getByText('Knowledge')).toBeInTheDocument())

    expect(screen.queryByText(/semantic search off/i), 'a failed read must not claim the capability is off').toBeNull()
    expect(screen.queryByText(/No embedding model active/i), 'and the advice must not appear either').toBeNull()
    expect(screen.queryByText(/Set one in Settings/i)).toBeNull()
  })

  it('a SUCCESSFUL read still renders the strip — or the assertion above proves nothing', async () => {
    // 🪤 Without this, a page that crashed or never mounted the strip would satisfy the first test.
    mockApi({})
    const { KnowledgeListPage } = await import('./KnowledgeListPage')
    render(<KnowledgeListPage query={{}} setQuery={() => {}} onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText('entities')).toBeInTheDocument())
    expect(screen.getByText('relations')).toBeInTheDocument()
  })

  it('a populated library is NOT called empty when the stats read fails', async () => {
    // 🔴 THE REAL DAMAGE THE FABRICATED ZERO DID. `empty` gates the "Knowledge base is empty" card,
    // and `items: 0` from a swallowed failure made it true no matter what the library held — so a
    // stats outage told a user with three items that their knowledge base was empty, and offered to
    // create their first one. With the rejection propagating, `stats` is null, `empty` is falsy, and
    // the real list renders.
    mockApi({ knowledgeStats: boom, knowledgeItems: () => Promise.resolve({ items: [{ id: 'a', title: 'Real note', type: 'note' }] }) })
    const { KnowledgeListPage } = await import('./KnowledgeListPage')
    render(<KnowledgeListPage query={{}} setQuery={() => {}} onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText('Real note')).toBeInTheDocument())
    expect(screen.queryByText('Knowledge base is empty'), 'a stats outage must not empty a populated library').toBeNull()
  })

  it('and a genuinely DISABLED embedding model still says so — the claim is real then', async () => {
    // The other half: suppressing the chip unconditionally would pass both tests above while
    // removing a true and useful signal.
    mockApi({ knowledgeStats: () => Promise.resolve({ ...okStats, embeddings: { enabled: false } }) })
    const { KnowledgeListPage } = await import('./KnowledgeListPage')
    render(<KnowledgeListPage query={{}} setQuery={() => {}} onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} />)
    await waitFor(() => expect(screen.getByText(/semantic search off/i)).toBeInTheDocument())
  })
})

describe('the fetcher no longer swallows', () => {
  it('knowledgeStats passes the rejection through', () => {
    const code = readFileSync(join(process.cwd(), 'src/pages/knowledge/knowledgeStore.ts'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    // The load-bearing half: with the catch in place the DOM tests above could pass against a
    // fabricated `embeddings: { enabled: false }` rather than against a real absence of data.
    expect(code, 'the zeros-and-disabled fallback is the defect').not.toMatch(/knowledgeStats\(\)\.catch/)
    expect(code, 'and the function still exists to be called').toMatch(/export async function knowledgeStats/)
  })
})
