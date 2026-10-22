// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { api, type DiscoverResponse } from '../../lib/api'
import { resetDataStore } from '../../lib/data/store'
import { KnowledgeListPage } from '../knowledge/KnowledgeListPage'
import { TriggersSection } from '../triggers/TriggersSection'
import { SkillsPage } from '../skills/SkillsPage'

// ── An empty Library, Automations or Capabilities page offers one Discover tip (#3818) ───────────
//
// Discover is no longer a sidebar row; the places it helps carry it. The real pages are mounted
// with every list they read answering empty, as a fresh home answers, and each shows its own empty
// state with one tip under it, pointing at another page. A page with something in it shows none.

const tip = (id: string, route: string, title: string) => ({
  id, area: 'Area', title, lesson: `What ${title} does.`, try_it: { route, label: `Open ${id}` },
})
const DISCOVER = {
  enabled: true, visible_count: 3, total: 3, dismissed_count: 0,
  areas: [{ area: 'Area', tips: [
    tip('knowledge', 'knowledge', 'Build a knowledge base'),
    tip('triggers', 'triggers', 'Run something on a schedule'),
    tip('skills', 'skills', 'Teach it a skill'),
  ] }],
} as DiscoverResponse

beforeEach(() => {
  resetDataStore()
  localStorage.clear()
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.stubGlobal('fetch', () => new Promise(() => {}))
  vi.spyOn(api, 'discover').mockResolvedValue(DISCOVER)
  vi.spyOn(api, 'knowledgeStats').mockResolvedValue({ items: 0, entities: 0, relations: 0, embeddings: { enabled: false } } as never)
  vi.spyOn(api, 'knowledgeItems').mockResolvedValue({ items: [], total: 0, page: 1, limit: 100 } as never)
  vi.spyOn(api, 'knowledgeCollections').mockResolvedValue([])
  vi.spyOn(api, 'automations').mockResolvedValue({ jobs: [], hooks: [], stores: [], callbacks: [], server_tz: 'UTC', unreadable: [] })
  vi.spyOn(api, 'triggerReview').mockResolvedValue([] as never)
  vi.spyOn(api, 'actionProviders').mockResolvedValue([] as never)
  vi.spyOn(api, 'skills').mockResolvedValue([])
  vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [] } as never)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const PAGES = [
  ['Library: an empty knowledge base', 'Knowledge base is empty', 'knowledge',
    () => <KnowledgeListPage onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}} onOpenReports={() => {}} onOpenChat={() => {}} query={{}} setQuery={() => {}} />],
  ['Automations: no triggers', 'No triggers', 'triggers',
    () => <TriggersSection sub="" navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />],
  ['Capabilities: no skills installed', 'No skills installed', 'skills',
    () => <SkillsPage query={{}} setQuery={() => {}} />],
] as const

describe('an empty page offers one Discover tip under its own empty state', () => {
  it.each(PAGES)('%s', async (_, heading, here, page) => {
    render(page())
    await screen.findByRole('heading', { name: heading })
    const aside = await screen.findByRole('complementary', { name: 'A tip from Discover' })
    const link = within(aside).getByRole('link')
    expect(link.getAttribute('href'), 'a tip for another page').not.toBe(`#/${here}`)
    expect(link.getAttribute('href')).toMatch(/^#\/(knowledge|triggers|skills)$/)
    expect(screen.getAllByRole('complementary', { name: 'A tip from Discover' }), 'one tip').toHaveLength(1)
  })

  it('a page with something in it shows no tip', async () => {
    vi.spyOn(api, 'skills').mockResolvedValue([{ name: 'summarise', source: 'user', description: 'Summarise a page.', loaded_by_agents: [] }] as never)
    render(<SkillsPage query={{}} setQuery={() => {}} />)
    await screen.findByText('summarise')
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByRole('complementary', { name: 'A tip from Discover' })).toBeNull()
  })
})
