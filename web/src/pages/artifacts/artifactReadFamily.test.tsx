import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── One endpoint, four readers, three different swallows ─────────────────────────────────────────
//
// `api.artifacts()` is read from four places, and each had invented its own way to lose the failure.
// The canonical reader already existed, which is what makes this convergence rather than design:
//
//   pages/ChatPage.tsx                    useQuery + `error: artifactsError`   ✅ canonical
//   pages/artifacts/ArtifactsSection.tsx  try/catch { setArtifacts([]) }            🔴 the library
//   pages/dashboard/…/PinnedArtifacts.tsx .catch(() => [])                          🔴 every pin
//   pages/files/FilesSection.tsx          try/catch { setArtifacts([]) }            🔴 the markers
//
// Each failure mode is DIFFERENT, which is why one bulk edit would have been wrong:
//
//   • the library grid renders "No artifacts · Ask the agent to save one…" — the newcomer empty state
//     shown to someone whose library is intact.
//   • the dashboard widget builds a resolution index and then DROPS any pin missing from it, so an
//     empty index hid EVERY pin behind "No pinned artifacts. Pin one from its page to keep it here."
//     The index can now answer "I don't know" (`null`), and a pin is only dropped against a TRUSTED
//     index. Rows survive, labelled by slug, with "details unavailable".
//   • the files tree's marker set is not a list — an empty set ASSERTS that no file is an artifact.
//     It keeps the last known set instead: a stale badge is a much smaller lie than a confident
//     absence, on a surface whose real job (browsing) is unaffected.
//   • and the content search answered a rejected request with "No matches." — a claim about the
//     user's files that the request never looked at.

const arts = [{ slug: 'a1', name: 'Weekly digest', kind: 'doc', version: 1, source_path: '/w/x.md' }]
const boom = () => Promise.reject(new Error('store unreachable'))

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      artifacts: () => Promise.resolve(arts),
      pinnedArtifacts: () => Promise.resolve({ pins: [{ slug: 'a1', pinned_at: 1 }] }),
      artifactCollections: () => Promise.resolve([]),
      ...over,
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the dashboard pin widget survives a failed artifact read', () => {
  async function mountPins() {
    const { PinnedArtifacts } = await import('../dashboard/widgets/PinnedArtifacts')
    render(<PinnedArtifacts navigate={vi.fn()} sub="" navEpoch={0} query={{}} setQuery={() => {}} />)
  }

  it('keeps the pins when the artifact list cannot be read', async () => {
    mockApi({ artifacts: boom })
    await mountPins()
    // The pin is real — the user chose it. Hiding it because a LIST read failed is the defect.
    await waitFor(() => expect(screen.getByText('a1')).toBeInTheDocument())
    expect(screen.getByText(/details unavailable/), 'and it says why it is thin').toBeInTheDocument()
    expect(screen.queryByText(/No pinned artifacts/), 'not the "you have none" claim').toBeNull()
  })

  it('still drops a pin whose artifact is genuinely gone', async () => {
    // The distinction deliberately KEPT: against a trustworthy index, an unresolvable pin is dropped
    // rather than rendered as a broken row. Only the failed-read case changed.
    mockApi({ artifacts: () => Promise.resolve([]) })
    await mountPins()
    await waitFor(() => expect(screen.getByText(/No pinned artifacts/)).toBeInTheDocument())
  })

  it('says the PINS read failed rather than "no pinned artifacts"', async () => {
    // The fifth instance, found by this file's own shape scan after the inner catch was fixed.
    mockApi({ pinnedArtifacts: boom })
    await mountPins()
    await waitFor(() => expect(screen.getByText(/Could ?n.t load your pins/i)).toBeInTheDocument())
    expect(screen.queryByText(/Pin one from its page/), 'not an invitation to start pinning').toBeNull()
  })

  it('renders the resolved name when everything works', async () => {
    mockApi({})
    await mountPins()
    await waitFor(() => expect(screen.getByText('Weekly digest')).toBeInTheDocument())
    expect(screen.queryByText(/details unavailable/)).toBeNull()
  })
})

describe('every reader of api.artifacts() can tell failure from empty', () => {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })

  /** 🪤 COMMENTS STRIPPED FIRST. The first draft flagged all four readers, including the canonical
   *  one — because each file's new comment QUOTES the old `catch { setArtifacts([]) }` shape to explain
   *  what changed, and the scan counted the explanation as code. Fourth time in this session; strip
   *  before matching, always. (The `~200 char` window it also used was the other half of the bug: a
   *  character count is not a statement. It reads the whole file's code now and matches the shape.) */
  const codeOf = (abs: string) => readFileSync(abs, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  const readers = () => walk(SRC)
    .filter((abs) => /api\.artifacts\(\)/.test(codeOf(abs)))
    .map((abs) => ({ rel: abs.slice(SRC.length + 1), code: codeOf(abs) }))

  it('finds all four readers — the scan is not vacuous', () => {
    const rels = readers().map((r) => r.rel).sort()
    expect(rels).toEqual([
      'pages/ChatPage.tsx',
      'pages/artifacts/ArtifactsSection.tsx',
      'pages/dashboard/widgets/PinnedArtifacts.tsx',
      'pages/files/FilesSection.tsx',
    ])
  })

  it('none of them discards the rejection into an empty list', () => {
    // The exact shapes that were there: `catch { setArtifacts([]) }` and `.catch(() => [] as …)`.
    // A reader may still CATCH — FilesSection does, to keep its last known markers — but it must not
    // substitute an empty collection, because that substitution is what the UI reports as fact.
    const bad = readers()
      .filter((r) => /catch\s*\{\s*set\w+\(\[\]\)|\.catch\(\(\)\s*=>\s*\[\]/.test(r.code))
      .map((r) => r.rel)
    expect(bad, `these turn a failed read into "you have none":\n${bad.join('\n')}`).toEqual([])
  })

  it('the library reports the failure and offers a retry', () => {
    const src = readFileSync(join(SRC, 'pages/artifacts/ArtifactsSection.tsx'), 'utf8')
    expect(src, 'the rejection is kept').toMatch(/catch \(e\) \{ setLoadErr\(e\) \}/)
    expect(src, 'and rendered, gated on there being nothing to show')
      .toMatch(/loadErr && artifacts\.length === 0[\s\S]{0,200}?<LoadError what="artifacts"[^>]*onRetry=\{load\}/)
  })

  it('the widget only drops a pin against a TRUSTED index', () => {
    const src = readFileSync(join(SRC, 'pages/dashboard/widgets/PinnedArtifacts.tsx'), 'utf8')
    expect(src, 'null means "the read failed", not "nothing exists"')
      .toMatch(/const resolved = byslug === null \? \(pins \?\? \[\]\)/)
    expect(src, 'and the row cannot crash on an unresolved artifact').not.toMatch(/\{art\.name\}/)
  })

  it('the files tree keeps its last known markers rather than asserting none', () => {
    const src = readFileSync(join(SRC, 'pages/files/FilesSection.tsx'), 'utf8')
    expect(src).toMatch(/catch \{ \/\* keep the last known set \*\/ \}/)
  })

  it('a failed content search says so instead of counting zero matches', () => {
    const src = readFileSync(join(SRC, 'pages/files/FilesSection.tsx'), 'utf8')
    expect(src, 'the search error is tracked').toMatch(/setSearchErr\(e\)/)
    expect(src, 'and reported before the no-matches line').toMatch(/error && results\.length === 0[\s\S]{0,300}?Search failed/)
  })
})
