/** Each Capabilities tab shows its list beside the selected item's detail (#3817).
 *
 *  `docs/design/information-architecture.md` gives Capabilities one layout for all six tabs: the
 *  list beside the selected item's detail, with the item's permissions in the right pane, and a
 *  header holding only Add (Install on the Store tab). From the 840 px width class up the list
 *  keeps a fixed width and the detail takes the rest; below it they show one at a time, the
 *  detail in place of the list, with Back returning to it. The selection is part of the address,
 *  so a reload, Back and a copied link restore it.
 *
 *  Each tab is drawn as the shell draws it: the real hash router under the Capabilities strip,
 *  around the tab's own page, over a gateway that answers the reads each page makes. What is
 *  asserted is what a person sees and the address the bar holds.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { AppCatalogEntry, AppDisclosure, AppSummary } from '../../lib/api'

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
const { resetDataStore } = await import('../../lib/data')

/** The tab pages under the strip, routed as `App.tsx`'s `renderPage` routes them. */
function Capabilities() {
  const r = useHashRoute('capabilities')
  const page = r.route === 'skills' ? <SkillsPage {...r} />
    : r.route === 'tools' ? <ToolsPage {...r} />
    : r.route === 'prompts' ? <PromptsSection {...r} />
    : r.route === 'apps' ? <AppsSection {...r} />
    : <AgentsSection {...r} />
  return <CapabilitiesSection route={r.route} query={r.query} navigate={r.navigate} fallback={<p>Loading</p>}>{page}</CapabilitiesSection>
}

const DISCLOSURE: AppDisclosure = {
  permissions: { network: true }, crons: [], pythonDependencies: [], sidecarDependencies: [],
  requires: [], launches: [], npmPackages: [], writes: [], hasUI: false, uiComponents: '', hasBackend: false, backendSandbox: '',
  providers: [], onInstall: '', onUpdate: '', onEnable: '', onDisable: '', onUninstall: '',
  cliSetup: '', cliDoctor: '', sources: [], mcpServers: [], skills: [], runsAsYou: '',
}

const app = (name: string, displayName: string): AppSummary => ({
  name, displayName, version: '1.0.0', description: `${displayName} app`, enabled: true, origin: 'local',
  source: `/apps/${name}`, icon: '', hasBackend: false, hasUI: false, uiPages: [], isProvider: false, providerType: '',
  hasConfig: false, permissions: { network: true }, tags: [], backendRunning: false, backendPort: null, disclosure: DISCLOSURE,
})

const listing = (name: string, displayName: string): AppCatalogEntry => ({
  name, displayName, description: `${displayName} listing`, version: '1.0.0', icon: '', author: 'core',
  source: '', sourceKind: 'bundled', isProvider: false, providerType: '', tags: [],
  consentKnown: true, permissions: { network: true },
})

/** A gateway with two of everything each tab lists. */
function seed() {
  Object.assign(answers, {
    agents: {
      agents: [
        { name: 'scout', description: 'Finds things', tools: [], skills: ['tidy-notes'], approval_mode: 'auto', revision: 'a1' },
        { name: 'probe', description: 'Pokes things', tools: ['bash', 'read_file'], skills: [], revision: 'a2' },
      ],
      default_agent: 'scout',
    },
    agentProviders: [],
    skills: [
      { key: 'tidy-notes', name: 'tidy-notes', description: 'Tidies notes', always: true, source: 'local', type: 'skill', loaded_by_agents: ['scout'], integrity: 'intact' },
      { key: 'brief', name: 'brief', description: 'Writes a brief', always: false, source: 'local', type: 'skill', loaded_by_agents: [], integrity: 'intact' },
    ],
    skillProposals: { proposals: [], lastReview: null },
    toolsIndex: {
      tools: [
        { name: 'read_file', provider: 'core', description: 'Reads a file', risk_level: 'safe' },
        { name: 'mcp/files/hello', provider: 'files', serverTool: 'hello', description: 'Says hello', risk_level: 'caution', requires_approval: true },
      ],
      load_failures: [],
    },
    importableMcp: { servers: [], unreadable: [], tools: [] },
    mcpPoolStats: null,
    toolGroups: null,
    mcpElicitationServers: ['files'],
    mcpServers: [{ name: 'files', status: 'ok', tools: ['hello'], enabled: true, allowed: true, readOnlyTrust: { trusted: true, listed: { hello: 'd1' } } }],
    prompts: [
      { name: 'greeting', title: 'Greeting', kind: 'user', source: 'user', content: 'Hello {{name}}', variables: [], tags: [] },
      { name: 'farewell', title: 'Farewell', kind: 'user', source: 'user', content: 'Bye', variables: [], tags: [] },
    ],
    snippets: [],
    apps: [app('notes', 'Notes'), app('clips', 'Clips')],
    appCatalog: { bundled: [listing('weather', 'Weather'), listing('maps', 'Maps')], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [] },
  })
}

/** Each tab: its address, the one action its header holds, a row of its list (`row`, the name its
 *  detail carries; `pick`, the name of the control that selects it) with the address that row
 *  selects, and a second row that stays in the list. */
const TABS = [
  { tab: 'Agents', address: 'agents', add: 'New agent', row: 'scout', pick: 'scout', open: 'native:scout', other: 'probe', noun: 'agents' },
  { tab: 'Skills', address: 'skills', add: 'New skill', row: 'tidy-notes', pick: 'tidy-notes', open: 'tidy-notes', other: 'brief', noun: 'skills' },
  { tab: 'Tools', address: 'tools', add: 'Add tool server', row: 'mcp/files/hello', pick: /^mcp\/files\/hello/, open: 'mcp/files/hello', other: 'read_file', noun: 'tools' },
  { tab: 'Prompts', address: 'prompts', add: 'New prompt', row: 'greeting', pick: 'greeting', open: 'greeting', other: 'farewell', noun: 'prompts' },
  { tab: 'Apps', address: 'apps', add: 'Install from URL', row: 'Notes', pick: 'Notes — details', open: 'notes', other: 'Clips', noun: 'apps' },
  { tab: 'Store', address: 'apps?view=store', add: 'Install from URL', row: 'Weather', pick: 'Weather — details', open: 'weather', other: 'Maps', noun: 'apps' },
] as const

/** The address with the selection `open` added to it. */
const withOpen = (address: string, open: string) => `${address}${address.includes('?') ? '&' : '?'}open=${encodeURIComponent(open)}`

/** The window is narrower than the 840 px width class while this is true. */
let compactWindow = false

beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise(() => {}))
  compactWindow = false
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: compactWindow && query.includes('max-width: 839px'),
    media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    dispatchEvent: () => false,
  }))
  localStorage.clear()
  sessionStorage.clear()
  resetDataStore()
  for (const key of Object.keys(answers)) delete answers[key]
  seed()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** Open `address` as a copied link or a reload does, and let the page settle. */
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

/** The tab page's own header: the bar under the Capabilities strip. */
const pageHeader = () => document.querySelectorAll('header')[1] as HTMLElement

/** The buttons a person can reach in it, by what they say. */
const headerActions = () => within(pageHeader()).queryAllByRole('button').map((b) => (b.getAttribute('aria-label') ?? b.textContent ?? '').trim())

/** The list pane, and the detail open beside it or in its place. */
const listPane = () => document.querySelector('[data-capability-list]') as HTMLElement | null
const detail = (name: string) => screen.queryByRole('region', { name })

/** The rows of the Permissions section in `region`, as label → what it says. */
function permissions(region: HTMLElement): Record<string, string> {
  const heading = within(region).getByRole('heading', { name: 'Permissions' })
  const section = heading.closest('section') as HTMLElement
  const out: Record<string, string> = {}
  for (const dt of section.querySelectorAll('dt')) out[dt.textContent ?? ''] = (dt.nextElementSibling?.textContent ?? '').trim()
  return out
}

describe('the header holds the one action that adds to the tab', () => {
  it.each(TABS)('$tab: $add, and nothing else', async ({ address, add }) => {
    await open(address)
    expect(headerActions()).toEqual([add])
  })

  it('what left the Agents header is in the list\'s More menu, and still acts', async () => {
    await open('agents')
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('button', { name: 'Sync agents' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Export to Claude Code' }))
    await settle()
    expect(location.hash).toBe('#/agents?export=1')
  })

  it('what left the Tools header is in the list\'s More menu', async () => {
    await open('tools')
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('button', { name: 'Re-probe MCP servers' })).toBeTruthy()
  })

  it('what left the Store header is in the list\'s More menu, and opens the sources', async () => {
    await open('apps?view=store')
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(screen.getByRole('button', { name: 'Manage Sources' }))
    await settle()
    expect(detail('Manage Sources')).toBeTruthy()
  })

  it('Proposals is a view of Skills beside Installed and Browse', async () => {
    await open('skills')
    const views = screen.getByRole('radiogroup', { name: 'Skills view' })
    expect(within(views).getAllByRole('radio').map((r) => r.textContent)).toEqual(['Installed', 'Browse', 'Proposals'])
    fireEvent.click(within(views).getByRole('radio', { name: 'Proposals' }))
    await settle()
    expect(location.hash).toBe('#/skills?mode=proposals')
  })

  it('the Prompts view switch sits beside the title, out of the actions', async () => {
    await open('prompts')
    const views = within(pageHeader()).getByRole('radiogroup', { name: 'Prompts view' })
    expect(within(views).getAllByRole('radio').map((r) => r.textContent)).toEqual(['User', 'System', 'Bundled', 'Snippets'])
    expect(views.closest('[data-header-left]'), 'beside the title, not in the actions cluster').toBeTruthy()
  })
})

describe('the list sits beside the selected item\'s detail', () => {
  it.each(TABS)('$tab: the list keeps a fixed width, and the detail takes the rest', async ({ address, open: id, row, other }) => {
    await open(withOpen(address, id))
    const pane = detail(row)
    expect(pane, `no detail named ${row}`).toBeTruthy()
    const list = listPane()
    expect(list, 'no list pane').toBeTruthy()
    expect(list!.style.width, 'the list keeps its fixed width beside a detail').toBe('22.5rem')
    expect(list!.parentElement, 'the list and the detail share one row').toBe(pane!.parentElement)
    expect(list!.compareDocumentPosition(pane!) & Node.DOCUMENT_POSITION_FOLLOWING, 'the detail is to the right').toBeTruthy()
    expect(pane!.className).toContain('flex-1')
    expect(within(list!).getAllByText(other).length, 'the rest of the list stays in view').toBeGreaterThan(0)
  })

  it('Close and Escape each close the detail, and the list takes the whole width again', async () => {
    await open(withOpen('agents', 'native:scout'))
    fireEvent.click(within(detail('scout')!).getByRole('button', { name: 'Close' }))
    await settle()
    expect(location.hash).toBe('#/agents')
    expect(detail('scout')).toBeNull()
    expect(listPane()!.style.width).toBe('')

    await open(withOpen('agents', 'native:scout'))
    fireEvent.keyDown(window, { key: 'Escape' })
    await settle()
    expect(location.hash).toBe('#/agents')
    expect(detail('scout')).toBeNull()
  })

  it('with nothing selected the list takes the whole width and no detail is open', async () => {
    await open('agents')
    expect(listPane()!.style.width).toBe('')
    expect(screen.queryByRole('region')).toBeNull()
  })

  it.each(TABS)('$tab: below the 840 px width class the detail shows in place of the list, and Back returns to it', async ({ address, open: id, row, other, noun }) => {
    compactWindow = true
    await open(withOpen(address, id))
    expect(detail(row), `no detail named ${row}`).toBeTruthy()
    expect(listPane()!.hidden, 'the list is out of view while the detail shows').toBe(true)
    fireEvent.click(screen.getByRole('button', { name: `Back to ${noun}` }))
    await settle()
    expect(location.hash, 'Back clears the selection from the address').toBe(`#/${address}`)
    expect(detail(row)).toBeNull()
    expect(listPane()!.hidden).toBe(false)
    expect(within(listPane()!).getAllByText(other).length).toBeGreaterThan(0)
  })
})

describe('the focus follows a choice below the width class', () => {
  it('a row chosen there hands the focus to the detail\'s heading, and Back hands it back to the row', async () => {
    compactWindow = true
    await open('agents')
    const row = screen.getByRole('button', { name: 'probe' })
    row.focus()
    fireEvent.click(row)
    await settle()
    expect(document.activeElement, 'the chosen row left the screen, so the focus is on what took its place')
      .toBe(within(detail('probe')!).getByRole('heading', { name: 'probe' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back to agents' }))
    await settle()
    expect(document.activeElement, 'back on the row it came from').toBe(screen.getByRole('button', { name: 'probe' }))
  })

  it('a detail an address opens takes no focus', async () => {
    compactWindow = true
    await open(withOpen('agents', 'native:probe'))
    expect(document.activeElement).toBe(document.body)
  })
})

describe('the right pane says what the selected item may do', () => {
  it('an agent: its approval, and the tools and skills it may use', async () => {
    await open(withOpen('agents', 'native:scout'))
    expect(permissions(detail('scout')!)).toEqual({ Approval: 'Auto-approve all', Tools: 'All available tools', Skills: 'tidy-notes' })
    await open(withOpen('agents', 'native:probe'))
    expect(permissions(detail('probe')!)).toEqual({ Approval: 'Default (hook-based)', Tools: 'bash, read_file', Skills: 'All available skills' })
  })

  it('an agent on an agent CLI: its lists do not hold there', async () => {
    (answers.agents as { agents: Record<string, unknown>[] }).agents[0].provider = 'acp:kiro-cli'
    await open(withOpen('agents', 'native:scout'))
    const rows = permissions(detail('scout')!)
    expect(rows.Tools).toMatch(/^Not applied: .* runs its own tools, and a tool list holds only on PersonalClaw's own agent\.$/)
    expect(rows.Skills).toMatch(/^Not applied: .* loads its own skills, and a skill list holds only on PersonalClaw's own agent\.$/)
  })

  it('a skill: where it loads, and the agents it reaches', async () => {
    await open(withOpen('skills', 'tidy-notes'))
    expect(permissions(detail('tidy-notes')!)).toEqual({ Loads: 'Into every session', Agents: 'scout, and every agent with no skill list' })
    await open(withOpen('skills', 'brief'))
    expect(permissions(detail('brief')!)).toEqual({ Loads: 'When it is relevant', Agents: 'Every agent with no skill list' })
  })

  it('a tool server\'s tool: what it is, and the trust its server holds', async () => {
    await open(withOpen('tools', 'mcp/files/hello'))
    expect(permissions(detail('mcp/files/hello')!)).toEqual({
      Risk: 'Caution', Approval: 'Needed before it runs',
      Server: 'files', 'Read-only labels': 'Trusted', Questions: 'May ask you questions',
    })
    await open(withOpen('tools', 'read_file'))
    expect(permissions(detail('read_file')!)).toEqual({ Risk: 'Safe', Approval: 'Not needed' })
  })

  it.each([['Apps', 'apps', 'notes', 'Notes'], ['Store', 'apps?view=store', 'weather', 'Weather']] as const)(
    '%s: the app\'s declared permissions are in the right pane', async (_tab, address, id, name) => {
      await open(withOpen(address, id))
      expect(within(detail(name)!).getByTestId('app-disclosure').textContent).toMatch(/Network access/)
    })
})

describe('the selection is part of the address', () => {
  it.each(TABS)('$tab: choosing a row writes it, Back closes it, and a reload or a copied link opens it again', async ({ address, open: id, row, pick }) => {
    await open(address)
    fireEvent.click(screen.getAllByRole('button', { name: pick })[0])
    await settle()
    expect(location.hash).toBe(`#/${withOpen(address, id)}`)
    expect(detail(row)).toBeTruthy()

    // Choosing a row is a step of its own, so Back undoes it.
    history.back()
    await act(async () => {
      for (let i = 0; i < 100 && location.hash !== `#/${address}`; i++) await new Promise((r) => setTimeout(r, 10))
    })
    await settle()
    expect(location.hash).toBe(`#/${address}`)
    expect(detail(row), 'Back closes the detail').toBeNull()

    await open(withOpen(address, id))
    expect(detail(row), 'the copied link opens the same item').toBeTruthy()
  })
})
