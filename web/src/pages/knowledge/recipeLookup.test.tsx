import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SourceCreatePage } from './SourceCreatePage'
import { api, type SourceKind, type SourceRecipe } from '../../lib/api'

// ── §7.2's recipe lookup in the create flow (WS-8) ───────────────────────────────────
//
// The html2rss feed-directory workflow: before anyone tunes a selector, check whether the
// site is already worked out. Three properties, and the negative ones are the ones that
// matter — a lookup that always offered something, or that offered a recipe this install
// cannot poll, would pass every "it appears" assertion.
//
// 1. A matched recipe SEEDS THE FORM the user then reviews, and it seeds the URL the recipe
//    actually watches — which is usually NOT the one pasted (a repo page becomes its releases
//    feed). Asserting the seeded field is what proves the resolved spec crossed over rather
//    than the page re-deriving it.
// 2. NO MATCH IS A REAL ANSWER and is said out loud. Silence there reads as a broken lookup.
// 3. A recipe naming a provider this install has not registered is FILTERED OUT, because
//    offering it would create a source nothing could ever poll.

const WEB_KIND: SourceKind = {
  provider: 'watched-page', display_name: 'Watched Page', kind: 'web_page', form: 'web_page',
  previewable: true, poll_interval_secs: 3600, default_item_type: 'bookmark',
  detectors: ['json_ld', 'semantic_html'],
}
const FEED_KIND: SourceKind = {
  provider: 'watched-feed', display_name: 'Watched Feed', kind: 'feed', form: 'feed',
  previewable: false, poll_interval_secs: 1800, default_item_type: 'bookmark',
  formats: ['csv', 'json', 'rss'], presets: ['atom', 'hn_algolia', 'rss'],
}

function recipe(over: Partial<SourceRecipe> = {}): SourceRecipe {
  return {
    id: 'github-releases', displayName: 'GitHub releases',
    description: 'Every published release of one repository.',
    provider: 'watched-feed', kind: 'feed', itemType: 'bookmark', enrichment: '',
    spec: { preset: 'atom', url: 'https://github.com/astral-sh/uv/releases.atom' },
    groups: { owner: 'astral-sh', repo: 'uv' },
    ...over,
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api, 'knowledgeSources').mockResolvedValue({
    sources: [], kinds: [WEB_KIND, FEED_KIND],
    health_statuses: ['ok', 'degraded', 'error', 'needs render tier'], raw_enrichment: 'raw',
  })
})

async function lookUp(url: string) {
  const user = userEvent.setup()
  render(<SourceCreatePage onBack={() => {}} onCreated={() => {}} />)
  const box = await screen.findByLabelText('A URL to look up in the recipe directory')
  await user.type(box, url)
  await user.click(screen.getByRole('button', { name: /Check/ }))
  return user
}

describe('the recipe lookup answers "is this site already covered?"', () => {
  it('offers a matching recipe and names the URL it will actually watch', async () => {
    vi.spyOn(api, 'knowledgeSourceRecipes').mockResolvedValue({
      recipes: [recipe()], matches: [recipe()], url: 'https://github.com/astral-sh/uv',
    })

    await lookUp('https://github.com/astral-sh/uv')

    expect(await screen.findByText('GitHub releases')).toBeTruthy()
    // The recipe watches a DIFFERENT URL than the one pasted; hiding that would make the
    // saved source look like it points where the user pointed it.
    expect(screen.getByText('https://github.com/astral-sh/uv/releases.atom')).toBeTruthy()
  })

  it('seeds the form with the resolved spec when the recipe is used', async () => {
    vi.spyOn(api, 'knowledgeSourceRecipes').mockResolvedValue({
      recipes: [recipe()], matches: [recipe()], url: 'https://github.com/astral-sh/uv',
    })

    const user = await lookUp('https://github.com/astral-sh/uv')
    await user.click(await screen.findByRole('button', { name: 'Use the GitHub releases recipe' }))

    // The form the user reviews, pre-filled — not a hidden spec riding alongside it.
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Name' })).toBeTruthy())
    expect((screen.getByRole('textbox', { name: 'Name' }) as HTMLInputElement).value)
      .toBe('GitHub releases')
    const url = screen.getByRole('textbox', { name: /Feed URL|URL/ }) as HTMLInputElement
    expect(url.value).toBe('https://github.com/astral-sh/uv/releases.atom')
  })

  it('says plainly when nothing covers the URL', async () => {
    // The vacuity half: a lookup that always suggested something would pass the case above
    // while telling the user nothing true.
    vi.spyOn(api, 'knowledgeSourceRecipes').mockResolvedValue({
      recipes: [recipe()], matches: [], url: 'https://example.com/',
    })

    await lookUp('https://example.com/')

    expect(await screen.findByText(/No recipe covers that URL yet/)).toBeTruthy()
    expect(screen.queryByText('GitHub releases')).toBeNull()
  })

  it('hides a recipe whose provider this install has not registered', async () => {
    // Offering it would create a source nothing could ever poll — the failure would land at
    // save time (or worse, at the first poll that never happens).
    vi.spyOn(api, 'knowledgeSourceRecipes').mockResolvedValue({
      recipes: [], matches: [recipe({ id: 'ghost', displayName: 'Ghost kind', provider: 'not-registered' })],
      url: 'https://ghost.example/',
    })

    await lookUp('https://ghost.example/')

    expect(await screen.findByText(/No recipe covers that URL yet/)).toBeTruthy()
    expect(screen.queryByText('Ghost kind')).toBeNull()
  })

  it('surfaces a lookup failure instead of reading as "not covered"', async () => {
    vi.spyOn(api, 'knowledgeSourceRecipes').mockRejectedValue(new Error('offline'))

    await lookUp('https://github.com/astral-sh/uv')

    expect(await screen.findByRole('alert')).toHaveTextContent('offline')
    expect(screen.queryByText(/No recipe covers that URL yet/)).toBeNull()
  })
})
