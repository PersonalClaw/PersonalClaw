// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import type { AppSummary } from '../../lib/api'

// ── An installed app the gateway does not run here says why, and offers nothing that runs it ──
//
// The desktop app cannot start an app's own Python server, worker, engine or parse scripts, nor
// install packages it does not carry, so it refuses such an app's enable and holds one already
// installed (a PersonalClaw folder the installed version also uses). The Apps page showed it as
// installed, with Activate, Open and Configure, and each could only fail. Now the Library card and
// the panel say the gateway's sentence (`AppSummary.refused`) where Open or Activate would be, and
// leave out what could only be refused. Deactivate stays for one that is on, and Update and
// Uninstall stay: a newer version may run here, and the owner may remove it.
//
// Only the API is mocked; the Library and its detail panel are the shipped components.

const REFUSED =
  "The desktop app can't start this app's own Python server; the version you install with " +
  '`uv tool install --python 3.13 personalclaw` can.'

function app(over: Partial<AppSummary> = {}): AppSummary {
  return {
    name: 'fixture-minutes', displayName: 'Fixture Minutes', version: '0.1.0',
    description: 'Takes minutes.',
    enabled: false, origin: 'local', source: '/apps/fixture-minutes', icon: '', hasBackend: true,
    hasUI: true, uiPages: [{ route: 'minutes', label: 'Minutes', icon: '' }], isProvider: false,
    providerType: '', hasConfig: true, permissions: {}, tags: [], backendRunning: false,
    backendPort: null, disclosure: null, refused: REFUSED,
    ...over,
  }
}

async function showLibrary(apps: AppSummary[], open?: string) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      apps: () => Promise.resolve([...apps]),
      appCatalog: () => Promise.resolve({ bundled: [], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [] }),
    },
  }))
  const { AppsSection } = await import('./AppsSection')
  render(<AppsSection query={{ view: 'library', ...(open ? { open } : {}) }} setQuery={() => {}} navigate={() => {}} />)
  await waitFor(() => expect(screen.getAllByText(apps[0].displayName).length).toBeGreaterThan(0))
}

const button = (name: RegExp) => screen.queryByRole('button', { name })

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  vi.doMock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('an installed app the gateway does not run here', () => {
  it("says the gateway's sentence on its panel and offers no Activate, Open or Configure", async () => {
    await showLibrary([app()], 'fixture-minutes')
    await waitFor(() => expect(screen.getAllByText(REFUSED).length).toBeGreaterThan(0))
    expect(button(/^Activate$/), 'Activate could only be refused').toBeNull()
    expect(button(/^Open$/)).toBeNull()
    expect(button(/^Configure$/)).toBeNull()
    expect(button(/^Update$/), 'a newer version may run here').toBeTruthy()
    expect(button(/^Uninstall$/), 'the owner may remove it').toBeTruthy()
  })

  it('still offers Deactivate when it is on, and nothing that would run it', async () => {
    await showLibrary([app({ enabled: true })], 'fixture-minutes')
    await waitFor(() => expect(button(/^Deactivate$/)).toBeTruthy())
    expect(screen.getAllByText(REFUSED).length).toBeGreaterThan(0)
    expect(button(/^Open$/)).toBeNull()
    expect(button(/^Configure$/)).toBeNull()
    expect(screen.queryByRole('switch', { name: 'Show in navigation' })).toBeNull()
  })

  it('says it on its Library card, where Activate would be', async () => {
    await showLibrary([app()])
    const note = await screen.findByTestId('store-listing-refused')
    expect(note.textContent).toBe(REFUSED)
    expect(button(/^Activate$/)).toBeNull()
  })

  it('an app the gateway runs keeps every action (the control)', async () => {
    await showLibrary([app({ refused: '' })], 'fixture-minutes')
    // The card and the panel each offer it.
    await waitFor(() => expect(screen.getAllByRole('button', { name: /^Activate$/ }).length).toBe(2))
    expect(screen.queryByTestId('store-listing-refused')).toBeNull()
  })
})
