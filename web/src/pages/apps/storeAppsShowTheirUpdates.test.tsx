// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react'
import type { AppSummary } from '../../lib/api'

// ── An app installed from the Store shows its update, and its Update starts where it came from ──
//
// F-46. The gateway reads what an app's Store source offers now from what the Store's catalog
// read last found (it never polls), so the Library has to re-read the apps list once that read
// lands: the two start together, and the list answered first. Every route to "Update…" starts
// from where the gateway found the newer version, else from where the app was installed from.
//
// Only the API is mocked; the Library, its card menu, the detail panel and the dialog are the
// shipped components.

const POINTER = 'https://github.com/personalclaw/PersonalClawApps#growth'

function app(over: Partial<AppSummary> = {}): AppSummary {
  return {
    name: 'growth', displayName: 'Growth Tracker', version: '1.0.0', description: 'evidenced growth',
    enabled: true, origin: 'external', source: POINTER, icon: '', hasBackend: false, hasUI: false,
    uiPages: [], isProvider: false, providerType: '', hasConfig: false, permissions: {}, tags: [],
    backendRunning: false, backendPort: null,
    updateAvailable: false, latestVersion: '', latestSource: '', updateSource: POINTER,
    ...over,
  }
}

const found = { updateAvailable: true, latestVersion: '1.2.0', latestSource: POINTER }
const emptyCatalog = { bundled: [], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [] }

function mockApi(apps: () => AppSummary[], catalog: () => Promise<unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      apps: () => Promise.resolve(apps()),
      appCatalog: () => catalog(),
      previewApp: vi.fn(),
      updateApp: vi.fn(),
    },
  }))
}

async function renderLibrary(query: Record<string, string> = { view: 'library' }) {
  const { AppsSection } = await import('./AppsSection')
  render(<AppsSection query={query} setQuery={() => {}} navigate={() => {}} />)
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  vi.doMock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('an app installed from the Store shows its update', () => {
  it('re-reads the apps list once the Store\'s catalog read lands', async () => {
    // The gateway answers the first read before the catalog read has looked at GitHub.
    let reads = 0
    let land: (v: unknown) => void = () => {}
    const catalogRead = new Promise((resolve) => { land = resolve })
    mockApi(() => (reads++ === 0 ? [app()] : [app(found)]), () => catalogRead)
    await renderLibrary()
    await screen.findByText('Growth Tracker')
    expect(screen.queryByTitle('Update to v1.2.0 available')).toBeNull()

    land(emptyCatalog)
    expect(
      await screen.findByTitle('Update to v1.2.0 available', {}, { timeout: 3000 }),
      'the Library kept the answer it got before the Store looked',
    ).toBeTruthy()
  })
})

describe('every route to Update starts from where the app came from', () => {
  it('a card\'s menu starts from where the gateway found the newer version', async () => {
    mockApi(() => [app(found)], () => Promise.resolve(emptyCatalog))
    await renderLibrary()
    fireEvent.click(await screen.findByRole('button', { name: 'Actions for Growth Tracker' }))
    fireEvent.click(await screen.findByRole('button', { name: /Update…/ }))

    const field = await screen.findByLabelText(/New source/) as HTMLInputElement
    expect(field.value, 'the card menu opened an empty field over an update it had just badged').toBe(POINTER)
    expect(screen.getByText('PersonalClaw found version 1.2.0 there. Change it to update from somewhere else.')).toBeTruthy()
  })

  it('with no newer version found, it starts from where the app was installed from', async () => {
    mockApi(() => [app()], () => Promise.resolve(emptyCatalog))
    await renderLibrary({ view: 'library', open: 'growth' })
    await waitFor(() => expect(screen.getByRole('button', { name: /Deactivate/ })).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /^Update$/ })[0])

    const field = await screen.findByLabelText(/New source/) as HTMLInputElement
    expect(field.value, 'the dialog made the owner retype where the app came from').toBe(POINTER)
    expect(screen.getByText('Growth Tracker was installed from there. Change it to update from somewhere else.')).toBeTruthy()
    expect(screen.queryByText(/PersonalClaw found/)).toBeNull()
  })
})
