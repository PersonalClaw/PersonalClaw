// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import type { AppDisclosure, AppSummary } from '../../lib/api'

// ── An installed app's panel says what it runs on this machine, as the Store did ─────────────
//
// The Store's panel and the install consent show one disclosure: the permissions the gateway
// enforces, the scheduled jobs, and "What it runs on this machine" (each provider module the
// gateway loads, the lifecycle hooks, the setup and doctor steps). Once the app was installed its
// panel in the Library showed the permissions and nothing it runs. It now renders the installed
// copy's disclosure with the consent's own component.
//
// Only the API is mocked; the Library and its detail panel are the shipped components.

const DISCLOSURE: AppDisclosure = {
  permissions: { network: true }, crons: [], pythonDependencies: [], sidecarDependencies: [],
  requires: [], launches: [], npmPackages: [], writes: [], hasUI: false, uiComponents: '', hasBackend: false, backendSandbox: '',
  providers: [{ type: 'search', implementation: 'provider:create_provider', execution: 'in-process' }],
  onInstall: 'npm run build', onUpdate: '', onEnable: '', onDisable: '', onUninstall: '',
  cliSetup: 'cli_setup:run', cliDoctor: '', sources: [], mcpServers: [], skills: [],
  runsAsYou: 'Its provider module runs as you on this machine.',
}

function app(over: Partial<AppSummary> = {}): AppSummary {
  return {
    name: 'fixture-search', displayName: 'Fixture Search', version: '0.1.1',
    description: 'Ranked links for a query.',
    enabled: true, origin: 'local', source: '/apps/fixture-search', icon: '', hasBackend: false,
    hasUI: false, uiPages: [], isProvider: true, providerType: 'search', hasConfig: true,
    permissions: { network: true }, tags: [], backendRunning: false, backendPort: null,
    disclosure: DISCLOSURE,
    ...over,
  }
}

async function openInLibrary(apps: AppSummary[]) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      apps: () => Promise.resolve([...apps]),
      appCatalog: () => Promise.resolve({ bundled: [], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [] }),
    },
  }))
  const { AppsSection } = await import('./AppsSection')
  render(<AppsSection query={{ view: 'library', open: apps[0].name }} setQuery={() => {}} navigate={() => {}} />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Deactivate/ })).toBeTruthy())
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  vi.doMock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe("the installed app's panel", () => {
  it('says what it runs on this machine, with the sentence saying what runs as you', async () => {
    await openInLibrary([app()])
    const row = screen.getByTestId('consent-runs')
    expect(within(row).getByText('What it runs on this machine')).toBeTruthy()
    expect(within(row).getByTestId('consent-runs-as-you').textContent).toBe(DISCLOSURE.runsAsYou)
    expect(row.textContent).toContain("Loads its search provider provider:create_provider into the gateway's own process.")
    expect(row.textContent).toContain('Runs cli_setup:run when you run personalclaw setup.')
  })

  it('says the install step as something that ran, not something about to run', async () => {
    await openInLibrary([app()])
    const row = screen.getByTestId('consent-runs')
    expect(row.textContent).toContain("Ran npm run build in the app's folder when it was installed.")
    expect(row.textContent).not.toContain('during the install')
  })

  it('still lists the permissions the gateway enforces', async () => {
    await openInLibrary([app()])
    expect(screen.getByText(/Network access/)).toBeTruthy()
  })

  it('says so when the installed manifest could not be read', async () => {
    await openInLibrary([app({ disclosure: null })])
    expect(screen.getByText(/could not be read from its manifest/)).toBeTruthy()
    expect(screen.queryByTestId('consent-runs')).toBeNull()
  })
})
