// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react'
import type { AppSummary, SidecarInstallStatus } from '../../lib/api'

// ── An app's Configure page installs its engine ─────────────────────────────────────────────────
//
// The Configure form rendered the app's settings schema and nothing else, so an app whose engine
// runs in its own Python environment had no button anywhere to put it there. Its Configure page now
// carries the same engine section as its card in Settings → Providers. Only the API is mocked.

function app(over: Partial<AppSummary> = {}): AppSummary {
  return {
    name: 'voice-clone-tts', displayName: 'Voice Clone TTS', version: '0.1.1', description: 'speaks in a cloned voice',
    enabled: true, origin: 'local', source: '/srv/apps/voice-clone-tts', icon: '', hasBackend: false, hasUI: false,
    uiPages: [], isProvider: true, providerType: 'model', hasConfig: true, permissions: {}, tags: [],
    backendRunning: false, backendPort: null, sidecar: true,
    ...over,
  }
}

const engine: SidecarInstallStatus = {
  provider: 'voice-clone-tts', installed: false, managed: false,
  install_dir: '/home/me/.personalclaw/apps/voice-clone-tts/venv', requirements: ['omnivoice>=0.2'],
  job: {
    id: '', state: 'idle', progress: 0,
    steps: [
      { name: 'venv', status: 'pending', detail: '', started_at: 0 },
      { name: 'deps', status: 'pending', detail: '', started_at: 0 },
      { name: 'weights', status: 'pending', detail: '', started_at: 0 },
    ],
    log_tail: [], error: '', reason: '', remediation: '', weights_progress: 0,
  },
}

const sidecarInstallStatus = vi.fn()

async function configure(summary: AppSummary) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      apps: () => Promise.resolve([summary]),
      appCatalog: () => Promise.resolve({ bundled: [], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [] }),
      appConfig: () => Promise.resolve({ name: summary.name, config: { device: 'cpu' }, schema: { properties: { device: { type: 'string', enum: ['cpu', 'mps'] } } }, _secret_set: [] }),
      channels: () => Promise.resolve([]),
      sidecarInstallStatus: (...a: unknown[]) => sidecarInstallStatus(...a),
    },
  }))
  const { AppsSection } = await import('./AppsSection')
  render(<AppsSection query={{ view: 'library', open: summary.name }} setQuery={() => {}} navigate={() => {}} />)
  fireEvent.click(await screen.findByRole('button', { name: /^Configure$/ }))
  await screen.findByRole('dialog')
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  sidecarInstallStatus.mockReset().mockResolvedValue(engine)
  vi.doMock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Configure', () => {
  it("offers a sidecar app's engine beside its settings", async () => {
    await configure(app())
    const dialog = screen.getByRole('dialog')
    expect(await screen.findByRole('button', { name: 'Install engine: Voice Clone TTS' })).toBeTruthy()
    expect(sidecarInstallStatus).toHaveBeenCalledWith('voice-clone-tts')
    expect(dialog.textContent).toContain('Install engine puts omnivoice>=0.2 there with pip.')
  })

  it('asks nothing about an engine for an app without one', async () => {
    await configure(app({ sidecar: false }))
    await waitFor(() => expect(screen.getByRole('dialog').textContent).not.toMatch(/Loading…/))
    expect(sidecarInstallStatus).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /Install engine/ })).toBeNull()
  })
})
