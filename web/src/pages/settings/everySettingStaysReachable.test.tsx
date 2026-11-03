/** Nothing is lost when Settings' forty pages fold into seventeen (#5897).
 *
 *  Each old page is now a section of the page it belongs with. Pinned here, as data a reviewer can
 *  read in one screen: which page holds each of the forty, that each still renders there under its
 *  own heading, and that its old address opens it, keeping the state its old link carried. The
 *  redirect rows themselves are held to the router's rules by `app/routeRedirects.test.tsx`, and every
 *  old address is opened in the real shell by `app/everyShippedRouteResolves.test.tsx`. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, within } from '@testing-library/react'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  // Every read stays on its way: what a section shows while it waits is that section, and the
  // heading it sits under is the page's, so it is there either way.
  const gateway = new Proxy({}, { get: () => () => new Promise(() => {}) })
  return { ...real, api: gateway }
})

const { SETTINGS_PAGES, SETTINGS_GROUPS, pageOfSection, sectionAddress } = await import('./settingsPages')
const { SECTIONS, SettingsPage } = await import('./SettingsPage')
const { redirectFor } = await import('../../app/routeRedirects')
const { ThemeProvider } = await import('../../app/theme')
const { AppearanceProvider } = await import('../../app/appearance')
const { PersonalityProvider } = await import('../../app/personality')
const { IdentityProvider } = await import('../../app/identity')

/** Every page Settings had, and the page that holds it now. */
const FOLD: Record<string, string> = {
  design: 'design', buddy: 'design',
  account: 'account',
  chat: 'chat', archive: 'chat',
  notifications: 'notifications', inbox: 'notifications',
  voice: 'voice',
  providers: 'models', models: 'models', routing: 'models', search: 'models',
  agent: 'agent', prompts: 'agent', 'tool-output': 'agent', legibility: 'agent',
  evals: 'evals', feedback: 'evals',
  workflows: 'automations', loops: 'automations', sources: 'automations', ambient: 'automations',
  apps: 'apps', packs: 'apps',
  usage: 'usage',
  security: 'security', guardrails: 'security', audit: 'security',
  devices: 'devices', 'sender-trust': 'devices', 'external-access': 'devices', companion: 'devices',
  system: 'health', doctor: 'health', diagnostics: 'health',
  secrets: 'secrets',
  durability: 'durability', portability: 'durability', documents: 'durability',
  updates: 'updates',
}

afterEach(cleanup)

describe('every one of the forty old pages is a section of exactly one page', () => {
  it('🪤 vacuity floor: forty sections, seventeen pages, three groups', () => {
    expect(Object.keys(FOLD)).toHaveLength(40)
    expect(SECTIONS).toHaveLength(40)
    expect(SETTINGS_PAGES).toHaveLength(17)
    expect([...SETTINGS_GROUPS]).toEqual(['General', 'AI & Automation', 'Security & System'])
  })

  it('🔑 each section has a panel and one page, the one this table names', () => {
    const held = SETTINGS_PAGES.flatMap((p) => p.sections)
    expect(new Set(held).size, 'a section held by two pages').toBe(held.length)
    expect([...held].sort()).toEqual(Object.keys(FOLD).sort())
    expect(SECTIONS.map((s) => s.id).sort()).toEqual(Object.keys(FOLD).sort())
    for (const [id, page] of Object.entries(FOLD)) expect(pageOfSection(id)?.id, id).toBe(page)
  })

  it('every page sits in a group, and every group holds a page', () => {
    expect(SETTINGS_PAGES.filter((p) => !SETTINGS_GROUPS.includes(p.group)).map((p) => p.id)).toEqual([])
    expect(SETTINGS_GROUPS.filter((g) => !SETTINGS_PAGES.some((p) => p.group === g))).toEqual([])
  })
})

describe('a retired page\'s address opens its section, on the page that holds it', () => {
  const folded = Object.entries(FOLD).filter(([id, page]) => id !== page)

  it('🪤 vacuity floor: twenty-five pages folded', () => {
    expect(folded).toHaveLength(25)
  })

  it.each(folded)('#/settings/%s → #/settings/%s?section=…', (id, page) => {
    expect(sectionAddress(id)).toBe(`settings/${page}?section=${id}`)
    expect(redirectFor(`settings/${id}`)).toBe(`settings/${page}?section=${id}`)
  })

  it('🔑 a page\'s own address is not retired', () => {
    for (const p of SETTINGS_PAGES) expect(redirectFor(`settings/${p.id}`), p.id).toBeNull()
  })

  it('🔑 the state an old link carried arrives under the name its panel reads', () => {
    expect(redirectFor('settings/providers?open=an-app')).toBe('settings/models?section=providers&open=an-app')
    expect(redirectFor('settings/routing?uc=reasoning&qc=long_reasoning'))
      .toBe('settings/models?section=routing&uc=reasoning&qc=long_reasoning')
  })
})

/** Settings at `#/settings/<page>`, as the router hands it a page. */
async function openPage(page: string, query: Record<string, string> = {}) {
  render(
    <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
      <SettingsPage sub={page} navigate={() => {}} navEpoch={0} query={query} setQuery={() => {}} />
    </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>,
  )
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
}

describe('each section renders on its page, under its own heading', () => {
  const multi = SETTINGS_PAGES.filter((p) => p.sections.length > 1)

  it('🪤 vacuity floor: twelve pages hold more than one section', () => {
    expect(multi).toHaveLength(12)
  })

  it.each(multi.map((p) => [p.id, p] as const))('#/settings/%s: one h1, an h2 per section, and a link to each', async (_id, page) => {
    await openPage(page.id)
    const h1 = screen.getAllByRole('heading', { level: 1 })
    expect(h1.map((h) => h.textContent)).toEqual([page.label])
    const h2 = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    const labels = page.sections.map((id) => SECTIONS.find((s) => s.id === id)!.label)
    for (const label of labels) expect(h2, `${page.id} has no heading for ${label}`).toContain(label)
    const nav = screen.getByRole('navigation', { name: 'On this page' })
    expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual(labels)
  })

  it.each(multi.flatMap((p) => p.sections.map((id) => [p.id, id] as const)))('#/settings/%s?section=%s focuses that section\'s heading', async (page, id) => {
    // `scrollIntoView` scrolls every scrollable ancestor, and moved the whole shell up under the
    // window: the page scrolls its own box instead, so this is never called.
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    await openPage(page, { section: id })
    expect(scrollIntoView, 'scroll the page\'s own box, not every ancestor').not.toHaveBeenCalled()
    const label = SECTIONS.find((s) => s.id === id)!.label
    expect(document.activeElement?.tagName).toBe('H2')
    expect(document.activeElement?.textContent).toBe(label)
  })
})
