/** Every Capabilities tab shows its list in its named order (#3817, the `named-order` rule).
 *
 *  Catalogues are looked up by name, so name is the default everywhere:
 *
 *  | Tab     | Default order                                                        | Tie-breaker      |
 *  |---------|----------------------------------------------------------------------|------------------|
 *  | Agents  | by display name, A–Z, case-insensitive                               | the agent's id   |
 *  | Skills  | by name, A–Z, case-insensitive                                       | the skill's id   |
 *  | Tools   | built-in providers by label, then connected servers by name, each    | the tool's full  |
 *  |         | provider's tools by name, all A–Z                                    | name             |
 *  | Prompts | by title (or name), A–Z, with the sort control                       | name             |
 *  | Apps    | by display name, A–Z, with the sort control                          | the app's name   |
 *  | Store   | by display name within each source group, the groups in the order    | the listing's id |
 *  |         | the sources are listed                                               |                  |
 *
 *  Each page is fed its rows shuffled, with equal names among them, and the order is read off the
 *  screen. A tab with a sort control names the order it shows. A refresh that reorders the list
 *  keeps the selected item's detail where it is. No route orders these rows for the page (the
 *  skills route lists in the order a copy wins, which is not an order to read by), so every order
 *  here is the page's own.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'

/** What the gateway answers; every other read stays on its way. */
const answers: Record<string, unknown> = {}

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const gateway = new Proxy({}, {
    get: (_t, method: string) => () => method in answers ? Promise.resolve(structuredClone(answers[method])) : new Promise(() => {}),
  })
  return { ...real, api: gateway }
})

const { useHashRoute } = await import('../../app/useHashRoute')
const { CapabilitiesSection } = await import('./CapabilitiesSection')
const { AgentsSection } = await import('../agents/AgentsSection')
const { SkillsPage } = await import('../skills/SkillsPage')
const { ToolsPage } = await import('../tools/ToolsPage')
const { PromptsSection } = await import('../prompts/PromptsSection')
const { AppsSection } = await import('../apps/AppsSection')
const { resetDataStore, invalidateKeys } = await import('../../lib/data')

function Capabilities() {
  const r = useHashRoute('capabilities')
  const page = r.route === 'skills' ? <SkillsPage {...r} />
    : r.route === 'tools' ? <ToolsPage {...r} />
    : r.route === 'prompts' ? <PromptsSection {...r} />
    : r.route === 'apps' ? <AppsSection {...r} />
    : <AgentsSection {...r} />
  return <CapabilitiesSection route={r.route} query={r.query} navigate={r.navigate} fallback={<p>Loading</p>}>{page}</CapabilitiesSection>
}

beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {}))
  localStorage.clear()
  sessionStorage.clear()
  resetDataStore()
  for (const key of Object.keys(answers)) delete answers[key]
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function open(address: string) {
  cleanup()
  resetDataStore()
  history.replaceState(null, '', `#/${address}`)
  render(<Capabilities />)
  await settle()
}

async function settle() {
  for (let i = 0; i < 12; i++) await act(async () => { await new Promise((r) => setTimeout(r, 15)) })
}

/** The controls that open `names`' rows, in the order the page shows them. */
const rowOrder = (names: readonly string[]) => screen.getAllByRole('button')
  .map((b) => b.getAttribute('aria-label') ?? '')
  .filter((n) => names.includes(n))

/** The elements titled with one of `names`, in the order the page shows them. */
const titledOrder = (names: readonly string[]) => [...document.querySelectorAll<HTMLElement>('[title]')]
  .map((e) => e.title)
  .filter((t) => names.includes(t))

/** The descriptions the cards show, `id:<name>`, in the order the page shows them. */
const cardOrder = () => [...document.querySelectorAll('p')]
  .map((p) => (p.textContent ?? '').trim())
  .filter((t) => t.startsWith('id:'))
  .map((t) => t.slice(3))

/** The sort control's own words. */
const sortControl = () => screen.getByRole('button', { name: 'Filter & sort' }).textContent ?? ''

describe('Agents: by display name, A–Z, case-insensitive; equal names by id', () => {
  it('native agents and each runtime\'s agents', async () => {
    Object.assign(answers, {
      agents: {
        agents: ['zeta', 'Beta', 'alpha', 'Alpha'].map((name) => ({ name, revision: name })),
        default_agent: 'zeta',
      },
      agentProviders: [{ provider_id: 'acp:kiro', type: 'acp', label: 'Kiro', ready: true, state: 'ready', detail: '' }],
      agentProviderAgents: {
        agents: [
          { id: 'w2', name: 'Writer', runtime: 'kiro', description: '', provider_agent: '', reasoning_effort: '', models: [] },
          { id: 'w1', name: 'Writer', runtime: 'kiro', description: '', provider_agent: '', reasoning_effort: '', models: [] },
          { id: 'c', name: 'coder', runtime: 'kiro', description: '', provider_agent: '', reasoning_effort: '', models: [] },
        ],
      },
    })
    await open('agents')
    expect(rowOrder(['zeta', 'Beta', 'alpha', 'Alpha'])).toEqual(['Alpha', 'alpha', 'Beta', 'zeta'])
    // Two runtime agents named alike: the one with the smaller id first.
    const writers = screen.getAllByRole('button', { name: 'Writer' })
    expect(rowOrder(['coder', 'Writer'])).toEqual(['coder', 'Writer', 'Writer'])
    fireEvent.click(writers[0])
    await settle()
    expect(location.hash).toBe('#/agents?open=acp%7Cacp%3Akiro%7Cw1')
  })

  it('a refresh that reorders the list keeps the selected agent\'s detail', async () => {
    Object.assign(answers, {
      agents: { agents: ['beta', 'alpha'].map((name) => ({ name, revision: name })), default_agent: 'alpha' },
      agentProviders: [],
    })
    await open('agents?open=native%3Abeta')
    expect(screen.getByRole('region', { name: 'beta' })).toBeTruthy()
    answers.agents = { agents: ['gamma', 'beta', 'Aaron', 'alpha'].map((name) => ({ name, revision: `${name}2` })), default_agent: 'alpha' }
    await act(async () => { invalidateKeys('agents:groups') })
    await settle()
    expect(rowOrder(['gamma', 'beta', 'Aaron', 'alpha'])).toEqual(['Aaron', 'alpha', 'beta', 'gamma'])
    expect(screen.getByRole('region', { name: 'beta' }), 'the detail stays on the agent it showed').toBeTruthy()
    expect(location.hash).toBe('#/agents?open=native%3Abeta')
  })
})

describe('Skills: by name, A–Z, case-insensitive; equal names by id', () => {
  it('the installed skills', async () => {
    const skill = (key: string, name: string) => ({ key, name, description: `${name} skill`, always: false, source: 'local', type: 'skill', loaded_by_agents: [], integrity: 'intact' })
    Object.assign(answers, {
      skills: [skill('zed', 'zed'), skill('b2', 'Brief'), skill('b1', 'Brief'), skill('apply', 'Apply'), skill('apt', 'apt')],
      skillProposals: { proposals: [], lastReview: null },
    })
    await open('skills')
    expect(rowOrder(['zed', 'Brief', 'Apply', 'apt'])).toEqual(['Apply', 'apt', 'Brief', 'Brief', 'zed'])
    fireEvent.click(screen.getAllByRole('button', { name: 'Brief' })[0])
    await settle()
    expect(location.hash, 'the first Brief is the one whose id sorts first').toBe('#/skills?open=b1')
  })
})

describe('Tools: built-in providers by label, then servers by name, each provider\'s tools by name', () => {
  it('the providers, the servers and their tools', async () => {
    const tool = (name: string, provider: string, serverTool?: string) => ({ name, provider, description: `${name} tool`, risk_level: 'safe', ...(serverTool ? { serverTool } : {}) })
    Object.assign(answers, {
      toolsIndex: {
        tools: [
          tool('web_fetch', 'web'), tool('write_file', 'Core'), tool('read_file', 'Core'), tool('Read_file', 'Core'),
          tool('mcp/zulu/b', 'zulu', 'b'), tool('agent_run', 'agents'), tool('mcp/zulu/a', 'zulu', 'a'), tool('mcp/Alpha/x', 'Alpha', 'x'),
        ],
        load_failures: [],
      },
      importableMcp: { servers: [], unreadable: [], tools: [] },
      mcpPoolStats: null,
      toolGroups: null,
      mcpElicitationServers: [],
      // Configuration order, which no longer decides where a server sits.
      mcpServers: [
        { name: 'zulu', status: 'ok', tools: ['b', 'a'], enabled: true, allowed: true },
        { name: 'Alpha', status: 'ok', tools: ['x'], enabled: true, allowed: true },
      ],
    })
    await open('tools')
    expect(titledOrder(['web_fetch', 'write_file', 'read_file', 'Read_file', 'mcp/zulu/b', 'agent_run', 'mcp/zulu/a', 'mcp/Alpha/x'])).toEqual([
      'agent_run', 'Read_file', 'read_file', 'write_file', 'web_fetch', 'mcp/Alpha/x', 'mcp/zulu/a', 'mcp/zulu/b',
    ])
  })
})

describe('Prompts: by title, or name, A–Z; equal titles by name', () => {
  const PROMPTS = [
    { name: 'b', title: 'Same', kind: 'user', source: 'user', updated_at: 30 },
    { name: 'a', title: 'Same', kind: 'user', source: 'user', updated_at: 10 },
    { name: 'c', title: 'apple', kind: 'user', source: 'user', updated_at: 20 },
    { name: 'd', kind: 'user', source: 'user', updated_at: 40 },
  ]

  it('by name, and the sort control says so', async () => {
    Object.assign(answers, { prompts: PROMPTS, snippets: [] })
    await open('prompts')
    expect(rowOrder(['a', 'b', 'c', 'd'])).toEqual(['c', 'd', 'a', 'b'])
    expect(sortControl()).toContain('Sorted by name')
  })

  it('another order from the sort control is named on it', async () => {
    Object.assign(answers, { prompts: PROMPTS, snippets: [] })
    await open('prompts?sort=updated')
    expect(rowOrder(['a', 'b', 'c', 'd'])).toEqual(['d', 'b', 'c', 'a'])
    expect(sortControl()).toContain('Recently updated first')
  })
})

describe('Apps: by display name, A–Z; equal names by the app\'s name', () => {
  it('the installed apps, and the sort control says so', async () => {
    const app = (name: string, displayName: string) => ({
      name, displayName, version: '1.0.0', description: `id:${name}`, enabled: true, origin: 'local', source: `/apps/${name}`,
      icon: '', hasBackend: false, hasUI: false, uiPages: [], isProvider: false, providerType: '', hasConfig: false,
      permissions: {}, tags: [], backendRunning: false, backendPort: null, disclosure: null,
    })
    Object.assign(answers, {
      apps: [app('b', 'Notes'), app('a', 'Notes'), app('c', 'archive'), app('d', 'Zebra')],
      appCatalog: { bundled: [], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [] },
    })
    await open('apps')
    expect(cardOrder()).toEqual(['c', 'a', 'b', 'd'])
    expect(sortControl()).toContain('Sorted by name')
  })
})

describe('Store: by display name within each source group, the groups as the sources are listed', () => {
  it('the listings, and the sort control says so', async () => {
    const listing = (name: string, displayName: string, source = '', sourceKind = 'bundled') => ({
      name, displayName, description: `id:${name}`, version: '1.0.0', icon: '', author: '',
      source, sourceKind, isProvider: false, providerType: '', tags: [],
    })
    Object.assign(answers, {
      apps: [],
      appCatalog: {
        bundled: [listing('z2', 'Zip'), listing('m', 'maps'), listing('z1', 'Zip')],
        gitSources: [], localSources: ['/srv/apps'],
        localApps: [listing('l2', 'Ledger', '/srv/apps/l2', 'local'), listing('l1', 'Ledger', '/srv/apps/l1', 'local')],
        remoteApps: [], gitApps: [],
      },
    })
    await open('apps?view=store')
    expect(cardOrder()).toEqual(['m', 'z1', 'z2', 'l1', 'l2'])
    expect(sortControl()).toContain('Sorted by name')
    // The Sources rail lists the groups in the order the dividers show them.
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    const entries = within(rail).getAllByRole('button').map((b) => (b.textContent ?? '').replace(/\d+$/, '').trim())
    expect(entries.slice(entries.indexOf('All sources') + 1, entries.indexOf('Add source'))).toEqual(['Built-in', 'apps'])
  })
})
