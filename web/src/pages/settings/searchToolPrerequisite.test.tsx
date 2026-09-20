import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { SearchCapabilitiesInfo } from '../../lib/api'
import { SearchPanel } from './SearchPanel'

// ── A bound provider that still cannot search ────────────────────────────────────────────────────
//
// #278: a user installed a search provider, bound it, saw `available: true`, and chat still answered
// "no web-search tool in the catalog". Binding registers a PROVIDER; the thing a chat turn invokes is
// a TOOL, and `web_search` ships in a separate app. The panel that owns the binding is the only
// surface that can say so at the moment the user forms the wrong belief.
//
// 🔑 THE ASSERTION THAT MATTERS IS THE ONE ABOUT SILENCE. A note that appears whenever it cannot
// prove otherwise would accuse every user with a slow `/api/tools` of a missing app, so two of the
// four cases below pin the panel saying NOTHING. "No tools came back" and "the tool list says there
// is no web_search" are different claims and only the second may be rendered.

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
  returns_content: true, returns_answer: true, returns_highlights: false,
  supports_recency: true, supports_domains: false, supports_fetch: false, depths: [],
}
const PROVIDERS = [{ name: 'searxng', display_name: 'SearXNG', capabilities: CAPS, available: true }]

/** The note, found by the sentence a user reads rather than by a test id. */
const NOTE = /not enough on its own/i

describe('the search panel names the web_search tool as the prerequisite it is', () => {
  beforeEach(() => {
    localStorage.clear()   // `useQuery(persist: true)` would otherwise carry a prior test's data
    vi.clearAllMocks()
    searchProviders.mockResolvedValue(PROVIDERS)
    searchActive.mockResolvedValue({ 'search-general': ['searxng'] })
  })

  it('a registered provider with no web_search tool is told the binding is not enough', async () => {
    tools.mockResolvedValue([{ name: 'read_file', description: '', provider: 'files' }])
    render(<SearchPanel />)
    const note = await screen.findByText(NOTE)
    // Names the TOOL, because that is the string the user will see in the chat refusal, and offers
    // the one act that fixes it. A note that only said "something is missing" would not close #278.
    expect(note.textContent).toContain('web_search')
    const link = screen.getByRole('link', { name: /install it from the Store/i })
    expect(link.getAttribute('href')).toBe('#/apps?view=store&open=web-tools')
  })

  it('and is told nothing once the tool is there', async () => {
    tools.mockResolvedValue([{ name: 'web_search', description: '', provider: 'web-tools' }])
    render(<SearchPanel />)
    // Wait for the panel to have actually rendered its list before asserting an absence — asserting
    // on the skeleton would pass for the wrong reason. By ROLE, because the plain text "General"
    // also appears in each unbound row's "falls back to General" caption.
    await screen.findByRole('button', { name: /General search/ })
    expect(screen.queryByText(NOTE)).toBeNull()
  })

  it('an unreachable tool list accuses nobody', async () => {
    // The distinction the code is built on: `.catch(() => null)`, not `[]`. An empty array would be
    // indistinguishable from "the catalog genuinely has no web_search" and would show the note to a
    // user whose install is fine.
    tools.mockRejectedValue(new Error('gateway down'))
    render(<SearchPanel />)
    await screen.findByRole('button', { name: /General search/ })
    expect(screen.queryByText(NOTE)).toBeNull()
  })

  it('with no providers at all the empty state points at the Store, not at two hardcoded names', async () => {
    // The old copy named SearXNG and Tavily in prose. Providers arrive through installed apps, so
    // the actionable destination is the Store's search-tagged slice — 7 cards rather than 38.
    searchProviders.mockResolvedValue([])
    tools.mockResolvedValue([])
    render(<SearchPanel />)
    const empty = await screen.findByText(/No search providers configured/i)
    await waitFor(() => {
      expect(empty.querySelector('a')?.getAttribute('href')).toBe('#/apps?view=store&stag=search')
    })
    // The missing-tool note must not also fire here: with nothing registered, the binding is not yet
    // the user's next step and two notes would compete for one decision.
    expect(screen.queryByText(NOTE)).toBeNull()
  })

  // ── Both links sit INSIDE a running sentence, so colour alone cannot carry them ─────────────────
  //
  // 🔴 CAUGHT BY CI, NOT BY THIS FILE — which is why the rail is here now. The first revision used
  // `<TextLink ink="emphasis">` with no `className`, and `TextLink` underlines on **hover only**
  // (`ui/TextLink.tsx`: `'hover:underline …'`). At rest that leaves an in-sentence link distinguished
  // by nothing but its ink, and `e2e/a11y.spec.ts` red on `#/settings/search` in BOTH themes with
  // `[serious] link-in-text-block: Links must be distinguishable without relying on color`.
  //
  // 🪤 WHY THE FIX IS PER-CALL-SITE AND NOT IN `TextLink`. A persistent underline is correct for the
  // in-sentence case and wrong for the ~82 other call sites that are standalone chrome (a "Show more"
  // reveal, a row's "Open"), where `size` is `xs`/`sm` rather than the default `inherit`. Underlining
  // at the component would repaint all of them and move pixels on every committed visual baseline to
  // fix two links. `EvalsPanel.tsx:97` and `learning/EvalsOff.tsx:64` — the app's other two
  // in-sentence links — already carry exactly `ink="emphasis" className="underline"`, so this follows
  // the established idiom rather than inventing one.
  //
  // The assertion reads the RESTING class list, because `hover:underline` is what passed before and
  // would pass again: a check that merely found the substring "underline" cannot tell the two apart.
  //
  // 🔑 THE TWO LINKS NEVER RENDER TOGETHER, which a first draft of this rail got wrong and its own
  // vacuity floor caught — it looked for 2 anchors in one state and found 1. The empty state fires
  // only when NO provider is registered, and the prerequisite note fires only when one IS registered
  // and `web_search` is absent (the test above pins that the note stays silent in the empty case, so
  // the exclusion is deliberate, not incidental). A table-driven pair states that instead of hiding it.
  const IN_SENTENCE = [
    {
      what: 'the empty state\'s Store link',
      arrange: () => { searchProviders.mockResolvedValue([]); tools.mockResolvedValue([]) },
      settle: () => screen.findByText(/No search providers configured/i),
      name: /^Store$/,
    },
    {
      what: 'the prerequisite note\'s install link',
      arrange: () => { tools.mockResolvedValue([{ name: 'read_file', description: '', provider: 'files' }]) },
      settle: () => screen.findByText(NOTE),
      name: /install it from the Store/i,
    },
  ] as const

  for (const c of IN_SENTENCE) {
    it(`${c.what} carries a non-colour affordance at rest, not only on hover`, async () => {
      c.arrange()
      render(<SearchPanel />)
      await c.settle()
      const link = screen.getByRole('link', { name: c.name })
      expect(
        link.className.split(/\s+/),
        `"${link.textContent}" is distinguished only by colour at rest — axe's link-in-text-block ` +
          'reds on this. Add `className="underline"` (the EvalsPanel/EvalsOff idiom); ' +
          '`hover:underline` alone is the state that already failed CI.',
      ).toContain('underline')
    })
  }
})
