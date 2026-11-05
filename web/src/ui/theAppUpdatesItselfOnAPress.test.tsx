import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { AppUpdateStart, AppUpdateState, DesktopBridge } from '../lib/desktopBridge'

// ── The desktop app updates itself, on a press ──────────────────────────────────────────────────
//
// A new desktop version came only as a download from the releases page: the System popover and
// Home said so, and Settings › Updates said "Install the new version from the releases page, then
// reopen the app". The app now checks on launch and once a day, and offers the update in its own
// windows: Settings › Updates › This app, and a row in the System popover while a new version is
// on offer (`desktop/appUpdate.js`). It takes two presses: Update downloads and checks the release
// in the background and changes nothing beside the app; once it is ready, Restart to Finish the
// Update, confirmed, installs it, and the confirm names the restart and what it interrupts. A copy
// the app cannot replace here offers the releases page instead, as every failure does.
//
// These tests drive the pages against a fake bridge: what they pin is what each surface offers,
// says and asks the app, never what the app does with it (`desktop/test/appUpdate.test.js`).

const wire = vi.hoisted(() => ({ applied: 0, probes: 0, running: 0, probeFails: false }))

const SYSTEM = {
  hostname: 'studio.example.test', version: '0.3.7', os: 'Darwin 25.0.0', platform: 'darwin', python: '3.13.1',
  arch: 'arm64 (Apple Silicon)', pid: 4242, cpu_count: 18, cwd: '/home/user',
  mem_total_gb: 48, mem_free_gb: 16.2, mem_used_gb: 31.8, proc_mem_mb: 512.4,
  load_1m: 6.07, load_5m: 5.1, load_15m: 4.2, cpu_pct: 22.5, proc_cpu_pct: 3.2,
  disk_total_gb: 926, disk_free_gb: 623, net_rx_kbs: 12.5, net_tx_kbs: 3.1,
  thread_count: 31, child_processes: 4, mcp_total: 2,
}

vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      system: async () => SYSTEM,
      authStatus: async () => { throw new Error('not read in this test') },
      spawnedAgents: async () => [],
      restartProbe: async () => {
        wire.probes += 1
        if (wire.probeFails) throw new Error('gateway unreachable')
        return { ok: true, running_agents: wire.running, sessions: 0 }
      },
      updateCheck: () => new Promise(() => {}),
      applyUpdate: async () => { wire.applied += 1; return {} },
      status: async () => ({ uptime: '5m', version: '0.3.7', platform: 'darwin', triggers: 0, subagents: 0, update_available: true }),
      doctor: async () => ({ ok: true, core_ok: true, worst: '', capabilities: {} }),
    },
  }
})
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const { SystemWidget } = await import('./SystemWidget')
const { AppUpdateControls, appUpdateLine, appUpdateOnOffer } = await import('../app/AppUpdateControls')
const { useAppUpdate } = await import('../app/appUpdate')
const { GatewayConnectionProvider } = await import('../app/gatewayConnection')
const { resetDataStore, writeQuery } = await import('../lib/data/store')
const { RELEASES_PAGE_URL } = await import('../lib/repoDocs')

/** Where the app's update stands, as `desktop/appUpdate.js` says it. */
function stands(over: Partial<AppUpdateState> = {}): AppUpdateState {
  return {
    current: '0.3.7', how: 'mac', phase: 'current', available: null, checkedAt: 1, progress: null,
    sentence: '', last: null, releasesPage: RELEASES_PAGE_URL, restartsGateway: true, ...over,
  }
}
const OFFERED = stands({ phase: 'available', available: { version: '0.3.8', installable: true } })
const READY = stands({ ...OFFERED, phase: 'ready', ready: { version: '0.3.8' }, sentence: 'PersonalClaw 0.3.8 is downloaded and checked. Restart to finish the update.' })

/** A fake of the bridge's `appUpdate`: what the page asked, and a way to push a state to it. */
function fakeApp(state: AppUpdateState, answer: AppUpdateStart = { ok: true }) {
  const asked: string[] = []
  const listeners = new Set<(s: AppUpdateState) => void>()
  const appUpdate: NonNullable<DesktopBridge['appUpdate']> = {
    status: async () => state,
    check: async () => { asked.push('check'); return state },
    download: async (version) => { asked.push(`download ${version}`); return answer },
    finish: async (version) => { asked.push(`finish ${version}`); return answer },
    on: (cb) => { listeners.add(cb); return () => listeners.delete(cb) },
  }
  window.pclawDesktop = { appUpdate } as unknown as DesktopBridge
  return { asked, push: (s: AppUpdateState) => act(() => { for (const cb of listeners) cb(s) }) }
}

/** This install's kind, as the update check Settings › Updates reads already said it. */
function installedAs(kind: string) {
  writeQuery('settings:update-check', { available: true, changes: '', checked: true, auto: 'off', kind }, true)
}

async function openPopover(): Promise<HTMLElement> {
  render(<GatewayConnectionProvider><SystemWidget /></GatewayConnectionProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'System status — Gateway connected' }))
  return (await screen.findByText('studio.example.test')).closest('.fixed') as HTMLElement
}

beforeEach(() => {
  resetDataStore(); sessionStorage.clear()
  wire.applied = 0; wire.probes = 0; wire.running = 0; wire.probeFails = false
})
afterEach(() => { delete window.pclawDesktop })

describe('the System popover: the app row, only while a new version is on offer', () => {
  it('🔴 offers Update for the app, which downloads on the press and asks or restarts nothing', async () => {
    installedAs('desktop')
    wire.running = 2
    const app = fakeApp(OFFERED)
    const card = await openPopover()

    expect(await within(card).findByText('PersonalClaw 0.3.8 is available for this app.')).toBeTruthy()
    fireEvent.click(within(card).getByRole('button', { name: 'Update the app' }))
    await waitFor(() => expect(app.asked).toEqual(['download 0.3.8']))
    expect(within(card).queryByText(/^Restart PersonalClaw/)).toBeNull()
    expect(wire.probes).toBe(0)
    expect(wire.applied).toBe(0)
  })

  it('🔴 offers Restart to Finish the Update once it is ready, and restarts nothing before the confirm names the restart', async () => {
    installedAs('desktop')
    wire.running = 2
    const app = fakeApp(READY)
    const card = await openPopover()

    expect(await within(card).findByText('PersonalClaw 0.3.8 is ready. Restart to finish the update.')).toBeTruthy()
    expect(within(card).queryByRole('button', { name: 'Update the app' })).toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: 'Restart to finish the update of this app' }))
    expect(await within(card).findByText(
      'Restart PersonalClaw to finish updating to 0.3.8? Your gateway restarts with it, which interrupts 2 running agents.',
    )).toBeTruthy()
    expect(app.asked).toEqual([])

    fireEvent.click(within(card).getByRole('button', { name: 'Restart the App' }))
    await waitFor(() => expect(app.asked).toEqual(['finish 0.3.8']))
    expect(wire.applied).toBe(0)
  })

  it('says in the confirm when what the restart interrupts could not be counted, and still offers it', async () => {
    installedAs('desktop')
    wire.probeFails = true
    const app = fakeApp(READY)
    const card = await openPopover()
    fireEvent.click(await within(card).findByRole('button', { name: 'Restart to finish the update of this app' }))
    expect(await within(card).findByText(
      "Restart PersonalClaw to finish updating to 0.3.8? Your gateway restarts with it. What it would interrupt couldn't be read.",
    )).toBeTruthy()
    fireEvent.click(within(card).getByRole('button', { name: 'Restart the App' }))
    await waitFor(() => expect(app.asked).toEqual(['finish 0.3.8']))
  })

  it("leaves the gateway's own Update as it was on the app's own gateway: it is the gateway's, and the row stands beside it", async () => {
    installedAs('desktop')
    fakeApp(stands())
    const card = await openPopover()
    expect(within(card).getByRole('button', { name: 'Update the gateway' })).toBeTruthy()
    expect(within(card).queryByText(/for this app/)).toBeNull()
  })

  it("keeps the gateway's Update & Restart for a gateway the app did not start, beside the app's row", async () => {
    installedAs('pip')
    const app = fakeApp(stands({ ...OFFERED, restartsGateway: false }))
    const card = await openPopover()
    expect(await within(card).findByText('PersonalClaw 0.3.8 is available for this app.')).toBeTruthy()
    expect(within(card).getAllByRole('button', { name: /Update & Restart/ })).toHaveLength(1)
    fireEvent.click(within(card).getByRole('button', { name: 'Update the app' }))
    await waitFor(() => expect(app.asked).toEqual(['download 0.3.8']))
    app.push(stands({ ...READY, restartsGateway: false }))
    fireEvent.click(await within(card).findByRole('button', { name: 'Restart to finish the update of this app' }))
    expect(await within(card).findByText('Restart PersonalClaw to finish updating to 0.3.8?')).toBeTruthy()
    expect(wire.probes).toBe(0)
  })

  it("names the popover's two Update presses for whose they are: the app's and the gateway's", async () => {
    installedAs('desktop')
    fakeApp(OFFERED)
    const card = await openPopover()
    const app = await within(card).findByRole('button', { name: 'Update the app' })
    const gateway = within(card).getByRole('button', { name: 'Update the gateway' })
    expect(app).not.toBe(gateway)
    // The labels stay as the owner tried them: both read Update.
    expect([app.textContent?.trim(), gateway.textContent?.trim()]).toEqual(['Update', 'Update'])
  })

  it('says a Restart to Finish the update settings refused, and keeps saying it after the check that follows', async () => {
    installedAs('desktop')
    const refusal = "Your update settings no longer allow PersonalClaw 0.3.8, so it wasn't installed."
    const app = fakeApp(READY, { ok: false, sentence: refusal })
    const card = await openPopover()
    fireEvent.click(await within(card).findByRole('button', { name: 'Restart to finish the update of this app' }))
    fireEvent.click(await within(card).findByRole('button', { name: 'Restart the App' }))
    await waitFor(() => expect(app.asked).toEqual(['finish 0.3.8']))
    // The app drops the copy and checks: the row has nothing on offer, and still says why.
    app.push(stands({ phase: 'checking', dropped: { version: '0.3.8', sentence: refusal } }))
    app.push(stands({ phase: 'current', dropped: { version: '0.3.8', sentence: refusal } }))
    expect(await within(card).findByText(refusal)).toBeTruthy()
    expect(within(card).getByText('This app is up to date (v0.3.7).')).toBeTruthy()
  })

  it('offers the releases page beside a failure, and Update again', async () => {
    installedAs('desktop')
    fakeApp(stands({ ...OFFERED, phase: 'failed', sentence: "The new version couldn't be put in place. This copy is unchanged; install it from the releases page." }))
    const card = await openPopover()
    expect(await within(card).findByRole('link', { name: 'Open the releases page' })).toBeTruthy()
    expect(within(card).getByRole('button', { name: 'Update the app' })).toBeTruthy()
  })

  it('offers the releases page, not a press, for a copy the app cannot replace here', async () => {
    installedAs('desktop')
    fakeApp(stands({
      phase: 'available', how: 'page', available: { version: '0.3.8', installable: false },
      sentence: 'This copy was installed as a package. Install the new version from the releases page.',
    }))
    const card = await openPopover()
    expect(await within(card).findByText('This copy was installed as a package. Install the new version from the releases page.')).toBeTruthy()
    expect(within(card).getByRole('link', { name: 'Open the releases page' }).getAttribute('href')).toBe(RELEASES_PAGE_URL)
    expect(within(card).queryByRole('button', { name: 'Update the app' })).toBeNull()
  })

  it('follows the update as the app pushes it, and says a refused start', async () => {
    installedAs('desktop')
    const app = fakeApp(OFFERED, { ok: false, sentence: 'An update is already under way.' })
    const card = await openPopover()
    fireEvent.click(await within(card).findByRole('button', { name: 'Update the app' }))
    expect(await within(card).findByText('An update is already under way.')).toBeTruthy()

    app.push(stands({ ...OFFERED, phase: 'downloading', progress: { received: 50, total: 200 } }))
    expect(await within(card).findByText('Downloading PersonalClaw 0.3.8… 25%')).toBeTruthy()
    expect(within(card).queryByRole('button', { name: 'Update the app' })).toBeNull()
    app.push(READY)
    expect(await within(card).findByRole('button', { name: 'Restart to finish the update of this app' })).toBeTruthy()
    app.push(stands({ ...READY, phase: 'placing', sentence: 'Putting PersonalClaw 0.3.8 in place…' }))
    expect(await within(card).findByText('Putting PersonalClaw 0.3.8 in place…')).toBeTruthy()
    expect(within(card).queryByRole('button', { name: 'Restart to finish the update of this app' })).toBeNull()
  })

  it('shows no app row outside the app: a browser tab has no app to ask', async () => {
    installedAs('desktop')
    const card = await openPopover()
    expect(within(card).getByRole('button', { name: 'Update' })).toBeTruthy()
    expect(within(card).queryByText(/for this app/)).toBeNull()
  })
})

describe("Home's Update available, in the app", () => {
  it("stays the gateway's: it asks the gateway, and goes nowhere", async () => {
    installedAs('desktop')
    fakeApp(OFFERED)
    const navigate = vi.fn()
    const { SystemLiveProvider } = await import('../pages/settings/SystemLive')
    const { SystemHealth } = await import('../pages/dashboard/widgets/SystemHealth')
    render(
      <SystemLiveProvider>
        <SystemHealth navigate={navigate} sub="" navEpoch={0} query={{}} setQuery={() => {}} />
      </SystemLiveProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: /Update available/ }))
    await waitFor(() => expect(wire.applied).toBe(1))
    expect(navigate).not.toHaveBeenCalled()
  })
})

describe("Settings › Updates › This app's controls", () => {
  function ThisApp() {
    const update = useAppUpdate()
    return update ? <AppUpdateControls update={update} place="settings" /> : <p>no app</p>
  }

  it('checks when asked, and says how the update the last copy started ended', async () => {
    const app = fakeApp(stands({ last: { from: '0.3.6', to: '0.3.7', ok: true, sentence: 'Updated from 0.3.6 to 0.3.7.' } }))
    render(<ThisApp />)
    expect(await screen.findByText('This app is up to date (v0.3.7).')).toBeTruthy()
    expect(screen.getByText('Updated from 0.3.6 to 0.3.7.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Check now/ }))
    await waitFor(() => expect(app.asked).toEqual(['check']))
  })

  it('offers Update, then Restart to Finish the Update once it is ready, with the confirm in between', async () => {
    const app = fakeApp(OFFERED)
    render(<ThisApp />)
    fireEvent.click(await screen.findByRole('button', { name: 'Update the app' }))
    await waitFor(() => expect(app.asked).toEqual(['download 0.3.8']))
    app.push(READY)
    expect(await screen.findByText('PersonalClaw 0.3.8 is ready. Restart to finish the update.')).toBeTruthy()
    expect(screen.queryByText('PersonalClaw 0.3.8 is downloaded and checked. Restart to finish the update.')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Restart to finish the update of this app' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Restart the App' }))
    await waitFor(() => expect(app.asked).toEqual(['download 0.3.8', 'finish 0.3.8']))
  })

  it('says a failure with the releases page beside it, and offers Update again', async () => {
    fakeApp(stands({ ...OFFERED, phase: 'failed', sentence: "The new version couldn't be put in place. This copy is unchanged; install it from the releases page." }))
    render(<ThisApp />)
    expect(await screen.findByText(/couldn't be put in place/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open the releases page' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Update the app' })).toBeTruthy()
  })

  it('says a ready copy the update settings dropped, though nothing is on offer', async () => {
    fakeApp(stands({ phase: 'current', dropped: { version: '0.3.8', sentence: "Your update settings no longer allow PersonalClaw 0.3.8, so it wasn't installed." } }))
    render(<ThisApp />)
    expect(await screen.findByText("Your update settings no longer allow PersonalClaw 0.3.8, so it wasn't installed.")).toBeTruthy()
  })

  it('names Check now for what it does while a copy is ready: the settings are read again', async () => {
    fakeApp(READY)
    render(<ThisApp />)
    expect(await screen.findByRole('button', { name: "Check now: whether this app's update settings still allow the ready update" })).toBeTruthy()
  })

  it('says a failed swap with the releases page', async () => {
    fakeApp(stands({ last: { from: '0.3.7', to: '0.3.8', ok: false, sentence: "The new version couldn't be put in place. This copy is unchanged; install it from the releases page." } }))
    render(<ThisApp />)
    expect(await screen.findByText(/couldn't be put in place/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open the releases page' })).toBeTruthy()
  })

  it('is not there outside the app', () => {
    render(<ThisApp />)
    expect(screen.getByText('no app')).toBeTruthy()
  })
})

describe('Settings › Updates', () => {
  /** The panel's one snapshot, for the desktop app's own gateway with a new version out. */
  function primed() {
    writeQuery('settings:updates', {
      info: { available: true, changes: '', checked: true, auto: 'off', kind: 'desktop', current: '0.3.7', latest: '0.3.8', channel: 'stable', pin: '', check_enabled: true },
      changelog: '',
    }, true)
  }

  it('🔴 has This app in the app, and its Version row points there instead of at the releases page', async () => {
    primed()
    fakeApp(OFFERED)
    const { UpdatesPanel } = await import('../pages/settings/UpdatesPanel')
    render(<UpdatesPanel />)
    expect(await screen.findByRole('heading', { name: 'This app' })).toBeTruthy()
    expect(await screen.findByText('PersonalClaw 0.3.8 is available for this app.')).toBeTruthy()
    expect(screen.getByText('This app updates itself: see This app, below.')).toBeTruthy()
  })

  it('says the releases page, with no This app, in a browser tab', async () => {
    primed()
    const { UpdatesPanel } = await import('../pages/settings/UpdatesPanel')
    render(<UpdatesPanel />)
    expect(await screen.findByText(/Install the new version from the/)).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'This app' })).toBeNull()
  })
})

describe('the words', () => {
  it('say each phase in one line, and the popover row shows only while a release is on offer or an update runs', () => {
    expect(appUpdateLine(stands({ phase: 'checking' }))).toBe('Looking for a new version of the app…')
    expect(appUpdateLine(stands({ ...OFFERED, phase: 'verifying' }))).toBe('Checking PersonalClaw 0.3.8…')
    expect(appUpdateLine(stands({ ...OFFERED, phase: 'downloading', progress: { received: 0, total: 0 } }))).toBe('Downloading PersonalClaw 0.3.8…')
    expect(appUpdateLine(READY)).toBe('PersonalClaw 0.3.8 is ready. Restart to finish the update.')
    expect(appUpdateLine(stands({ ...READY, phase: 'placing' }))).toBe('Putting PersonalClaw 0.3.8 in place…')
    expect(appUpdateLine(stands({ phase: 'failed' }))).toBe('This app is v0.3.7.')
    expect(appUpdateOnOffer(null)).toBe(false)
    expect(appUpdateOnOffer(stands())).toBe(false)
    expect(appUpdateOnOffer(OFFERED)).toBe(true)
    expect(appUpdateOnOffer(stands({ phase: 'ready', ready: { version: '0.3.8' } }))).toBe(true)
    expect(appUpdateOnOffer(stands({ phase: 'placing' }))).toBe(true)
    expect(appUpdateOnOffer(stands({ phase: 'restarting' }))).toBe(true)
  })
})
