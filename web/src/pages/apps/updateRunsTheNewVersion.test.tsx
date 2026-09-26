// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent, act } from '@testing-library/react'
import type { AppInstallResult, AppSummary } from '../../lib/api'

// ── An app update runs the new version at once — and the UI says so, or says why not ──────
//
// The gateway now unloads an app's code before an update and loads the new files after it, so
// what the owner sees has to keep up: the Update dialog starts from where the gateway FOUND the
// newer version (it used to open an empty "New source" field even when the Library had just
// said "Version 1.2.0 is available"), the app page imports the NEW bundle (same URL ⇒ the tab
// kept the module it had), and when part of the old version could not be taken out of the
// process the toast and the app's panel say what, instead of a success toast.
//
// Only the API is mocked; the Library, the detail panel, the dialog and the consent hook are
// the shipped components.

const FOUND = '/srv/first-party/growth'

const updateApp = vi.fn()
const previewApp = vi.fn()
/** What the tab's socket calls when it reopens — how a tab learns the gateway came back. */
const reconnects: (() => void)[] = []

function app(over: Partial<AppSummary> = {}): AppSummary {
  return {
    name: 'growth', displayName: 'Growth Tracker', version: '1.0.0', description: 'evidenced growth',
    enabled: true, origin: 'local', source: '/old/place/growth', icon: '', hasBackend: false, hasUI: false,
    uiPages: [], isProvider: false, providerType: '', hasConfig: false, permissions: {}, tags: [],
    backendRunning: false, backendPort: null,
    updateAvailable: true, latestVersion: '1.2.0', latestSource: FOUND,
    ...over,
  }
}

function mockApi(apps: AppSummary[]) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      apps: () => Promise.resolve([...apps]),
      appCatalog: () => Promise.resolve({ bundled: [], gitSources: [], localSources: [], localApps: [], remoteApps: [], gitApps: [] }),
      previewApp: (...a: unknown[]) => previewApp(...a),
      updateApp: (...a: unknown[]) => updateApp(...a),
    },
  }))
}

/** The Library with Growth Tracker's detail panel open — ready once its Deactivate shows. */
async function openInLibrary(apps: AppSummary[]) {
  mockApi(apps)
  const { AppsSection } = await import('./AppsSection')
  render(<AppsSection query={{ view: 'library', open: 'growth' }} setQuery={() => {}} navigate={() => {}} />)
  await waitFor(() => expect(screen.getByRole('button', { name: /Deactivate/ })).toBeTruthy())
}

/** The panel's own Update — the banner's when the gateway found one, else the action row's. */
const updateButton = () => screen.getAllByRole('button', { name: /^Update$/ })[0]

const toasts: { level: string; message: string }[] = []
const onToast = (e: Event) => { toasts.push((e as CustomEvent).detail) }

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  toasts.length = 0
  reconnects.length = 0
  vi.doMock('../../lib/useChatSocket', () => ({
    useChatSocket: (_frame: unknown, onReconnect?: () => void) => { if (onReconnect) reconnects.push(onReconnect) },
  }))
  window.addEventListener('ne:toast', onToast)
})
afterEach(() => { window.removeEventListener('ne:toast', onToast); cleanup(); vi.restoreAllMocks() })

describe('Update starts from the source the gateway found', () => {
  it('prefills the newer version\'s location, says so, and keeps it editable', async () => {
    await openInLibrary([app()])
    expect(screen.getByText('Update available')).toBeTruthy()
    fireEvent.click(updateButton())

    const field = await screen.findByLabelText(/New source/) as HTMLInputElement
    expect(field.value, 'the dialog makes the owner retype where the gateway just looked').toBe(FOUND)
    expect(screen.getByText('PersonalClaw found version 1.2.0 there. Change it to update from somewhere else.')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Review update/ })).toHaveProperty('disabled', false)

    fireEvent.change(field, { target: { value: '/somewhere/else/growth' } })
    expect(field.value).toBe('/somewhere/else/growth')
  })

  it('starts empty when the gateway found no newer version', async () => {
    await openInLibrary([app({ updateAvailable: false, latestVersion: '', latestSource: '' })])
    expect(screen.queryByText('Update available')).toBeNull()
    fireEvent.click(updateButton())
    const field = await screen.findByLabelText(/New source/) as HTMLInputElement
    expect(field.value).toBe('')
    expect(screen.queryByText(/PersonalClaw found/)).toBeNull()
  })
})

describe('what an update could not take out of the gateway is said, not hidden', () => {
  const REASON = 'a thread its previous version started is still running (growth-poller)'

  it('the update toast names the reason instead of reading as a plain success', async () => {
    const review: AppInstallResult = {
      ok: false, name: 'growth', error: '', needs_consent: true, scan: { verdict: 'clean', findings: [] },
      displayName: 'Growth Tracker', version: '1.2.0', disclosure: null, previous: null, consent: 'd'.repeat(64),
    }
    previewApp.mockResolvedValue(review)
    updateApp.mockResolvedValue({
      ok: true, name: 'growth', error: '', needs_consent: false, scan: null, displayName: 'Growth Tracker',
      restart_required: true, restart_reason: REASON,
    })
    mockApi([app()])
    const { InstallDialogHarness } = await import('../../test/installDialogHarness')
    render(<InstallDialogHarness target={{ source: FOUND, label: 'Growth Tracker', update: 'growth' }} />)
    fireEvent.click(await screen.findByRole('button', { name: /^Update$/ }))

    await waitFor(() => expect(toasts).toContainEqual(expect.objectContaining({
      level: 'info',
      message: `Updated Growth Tracker. Restart the gateway to finish: ${REASON}.`,
    })))
  })

  it("the app's panel keeps saying it until a restart", async () => {
    await openInLibrary([app({ restartReason: REASON })])
    expect(screen.getByText('Restart the gateway to finish')).toBeTruthy()
    expect(screen.getByText(`The installed version is running, but ${REASON}. Restart it from System status, top right.`)).toBeTruthy()
  })

  it('says nothing of the kind when nothing is left over', async () => {
    await openInLibrary([app({ restartReason: '' })])
    expect(screen.queryByText('Restart the gateway to finish')).toBeNull()
  })

  it('stops saying it once the gateway is back from that restart', async () => {
    // Measured driving the real gateway: System status → Restart, the overlay said "Restart
    // complete", /api/apps had no restartReason any more, and the open panel still said
    // "Restart the gateway to finish" 11.5s later — nothing re-read the list, so the notice
    // outlived the restart it asked for and read as a restart that had not worked.
    const apps = [app({ restartReason: REASON })]
    await openInLibrary(apps)
    expect(screen.getByText('Restart the gateway to finish')).toBeTruthy()

    apps[0] = app({ restartReason: '' })  // what the restarted gateway answers
    act(() => { for (const reopened of reconnects) reopened() })
    await waitFor(() => expect(
      screen.queryByText('Restart the gateway to finish'),
      'the notice outlived the restart it asked for',
    ).toBeNull())
  })
})

describe('the app page imports the bundle of the version installed now', () => {
  it('versions the bundle URL with the app\'s UI revision', async () => {
    const loads: string[] = []
    vi.doMock('../../app/appSdk', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      loadContributedModule: (src: string) => { loads.push(src); return new Promise(() => {}) },
    }))
    vi.doMock('../../lib/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        app: () => Promise.resolve({
          name: 'growth', installed: {}, config: {}, configSchema: {}, backendRunning: false, backendPort: null,
          manifest: { displayName: 'Growth Tracker', ui: { pages: [{ route: '/apps/growth', label: 'Growth', entryPoint: 'dist/index.mjs' }] } },
          uiRevision: 'abc123def456',
        }),
      },
    }))
    const { AppHostPage } = await import('./AppHostPage')
    render(<AppHostPage sub="growth" navigate={() => {}} />)
    await waitFor(() => expect(loads).toEqual(['/apps/growth/ui/dist/index.mjs?v=abc123def456']))
  })
})
