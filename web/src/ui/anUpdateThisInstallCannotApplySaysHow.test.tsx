import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

// ── Update & Restart did nothing on the desktop app ─────────────────────────────────────────────
//
// The System popover offered "Update & Restart" on every install, asked "Pull latest, rebuild, and
// restart the gateway?", and on a yes called `/api/update` and dropped its answer. A container or
// desktop install is not updated in place: the gateway answers `status: 'instructions'` with how it
// is updated (install the new app from the releases page, pull the new image), and starts nothing.
// So on the desktop app the press closed the popover and nothing happened, nothing was said, and
// no progress ever opened. Home's "Update available" dropped the same answer.
//
// Now the popover asks by how this install is updated (`app/applyUpdate`): a checkout pulls and
// rebuilds, a wheel installs the new version, and a container or desktop install gets "Update",
// with no restart to confirm, since none happens. The answer of how to update is said where it was
// asked for, the popover inline and Home in a dialog, with the releases page for the desktop app:
// a toast is gone before its link can be followed.

const DESKTOP_HOW = 'This is a desktop install — install the new version from the PersonalClaw releases page, then reopen the app.'
const CONTAINER_HOW = 'This is a container install — update by pulling the new image and recreating.'
const DESKTOP_ANSWER = { ok: true, status: 'instructions', kind: 'desktop', apply_method: 'desktop_delegate', detail: DESKTOP_HOW, instructions: [], image_tag: '' }

const wire = vi.hoisted(() => ({
  answer: {} as Record<string, unknown>,
  fail: null as Error | null,
  applied: 0,
}))
const dialogs = vi.hoisted(() => ({ confirmed: 0, alerts: [] as { title: string; body?: unknown }[] }))

const SYSTEM = {
  hostname: 'studio.example.test', version: '0.3.6', os: 'Darwin 25.0.0', platform: 'darwin', python: '3.13.1',
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
      restartProbe: async () => ({ running_agents: 0, sessions: 0 }),
      updateCheck: () => new Promise(() => {}),
      applyUpdate: async () => {
        wire.applied += 1
        if (wire.fail) throw wire.fail
        return wire.answer
      },
      status: async () => ({ uptime: '5m', version: '0.3.6', platform: 'darwin', triggers: 0, subagents: 0, update_available: true }),
      doctor: async () => ({ ok: true, core_ok: true, worst: '', capabilities: {} }),
    },
  }
})
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('./dialog', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  confirm: async () => { dialogs.confirmed += 1; return true },
  alertDialog: async (opts: { title: string; body?: unknown }) => { dialogs.alerts.push(opts) },
}))

const { SystemWidget } = await import('./SystemWidget')
const { GatewayConnectionProvider } = await import('../app/gatewayConnection')
const { resetDataStore, writeQuery } = await import('../lib/data/store')
const { RELEASES_PAGE_URL } = await import('../lib/repoDocs')

let toasts: { message: string; level: string }[] = []
const onToast = (e: Event) => { toasts.push((e as CustomEvent).detail) }

/** This install's kind, as the update check Settings › Updates reads already said it. */
function installedAs(kind: string) {
  writeQuery('settings:update-check', { available: true, changes: '', checked: true, auto: 'off', kind }, true)
}

beforeEach(() => {
  resetDataStore(); sessionStorage.clear()
  wire.applied = 0; wire.fail = null; toasts = []
  dialogs.confirmed = 0; dialogs.alerts.length = 0
  window.addEventListener('ne:toast', onToast)
})
afterEach(() => window.removeEventListener('ne:toast', onToast))

async function openPopover(): Promise<HTMLElement> {
  render(<GatewayConnectionProvider><SystemWidget /></GatewayConnectionProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'System status — Gateway connected' }))
  return (await screen.findByText('studio.example.test')).closest('.fixed') as HTMLElement
}

/** The popover's answer of how this install updates, once it is said. */
async function saidIn(card: HTMLElement, text: string): Promise<HTMLElement> {
  return (await within(card).findByText(text, { exact: false })).closest('[role="status"]') as HTMLElement
}

describe('the System popover on an install PersonalClaw does not update in place', () => {
  it('🔴 offers Update, with no restart to confirm, and says how this install updates, with the releases page', async () => {
    installedAs('desktop')
    wire.answer = DESKTOP_ANSWER
    const card = await openPopover()

    const update = within(card).getByRole('button', { name: 'Update' })
    expect(within(card).queryByRole('button', { name: /Update & Restart/ })).toBeNull()
    fireEvent.click(update)

    const said = await saidIn(card, DESKTOP_HOW)
    const link = within(said).getByRole('link', { name: 'Open the releases page' })
    expect(link.getAttribute('href')).toBe(RELEASES_PAGE_URL)
    expect(link.getAttribute('target')).toBe('_blank')
    expect(wire.applied).toBe(1)
    expect(toasts).toEqual([])
    expect(card.isConnected).toBe(true)
    expect(document.body.textContent).not.toMatch(/Pull latest|restart the gateway\?/)
  })

  it('closes what it said on Close, and offers Update again', async () => {
    installedAs('desktop')
    wire.answer = DESKTOP_ANSWER
    const card = await openPopover()
    fireEvent.click(within(card).getByRole('button', { name: 'Update' }))
    const said = await saidIn(card, DESKTOP_HOW)
    fireEvent.click(within(said).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(within(card).queryByText(DESKTOP_HOW, { exact: false })).toBeNull())
    expect(within(card).getByRole('button', { name: 'Update' })).toBeTruthy()
  })

  it("a container is told its own way, without the desktop app's link", async () => {
    installedAs('container')
    wire.answer = { ok: true, status: 'instructions', kind: 'container', detail: CONTAINER_HOW, instructions: [] }
    const card = await openPopover()
    fireEvent.click(within(card).getByRole('button', { name: 'Update' }))
    const said = await saidIn(card, 'pulling the new image')
    expect(within(said).queryByRole('link')).toBeNull()
  })
})

describe('the System popover on an install it does update', () => {
  it.each([
    ['git', 'Pull latest, rebuild, and restart the gateway?'],
    ['pip', 'Install the new version and restart the gateway?'],
  ])('a %s install asks what it will do, and hands over to the update overlay on a yes', async (kind, asked) => {
    installedAs(kind)
    wire.answer = { ok: true, status: 'updating', kind }
    const card = await openPopover()

    expect(within(card).queryByRole('button', { name: 'Update' })).toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: 'Update & Restart' }))
    expect(await within(card).findByText(asked)).toBeTruthy()
    expect(wire.applied).toBe(0)
    fireEvent.click(within(card).getByRole('button', { name: 'Update & Restart' }))

    await waitFor(() => expect(wire.applied).toBe(1))
    await waitFor(() => expect(card.isConnected).toBe(false))
    expect(toasts, 'the progress overlay says the rest').toEqual([])
  })

  it('an answer of how to update that arrives anyway is still said', async () => {
    installedAs('git')
    wire.answer = DESKTOP_ANSWER
    const card = await openPopover()
    fireEvent.click(within(card).getByRole('button', { name: 'Update & Restart' }))
    fireEvent.click(await within(card).findByRole('button', { name: 'Update & Restart' }))
    await saidIn(card, DESKTOP_HOW)
  })

  it('still says a refused start in a toast', async () => {
    installedAs('pip')
    wire.fail = new Error('An update is already running.')
    const card = await openPopover()
    fireEvent.click(within(card).getByRole('button', { name: 'Update & Restart' }))
    fireEvent.click(await within(card).findByRole('button', { name: 'Update & Restart' }))
    await waitFor(() => expect(toasts.map((t) => t.message)).toEqual(['Update blocked: An update is already running.']))
  })
})

describe("Home's Update available", () => {
  async function pressUpdateAvailable() {
    const { SystemLiveProvider } = await import('../pages/settings/SystemLive')
    const { SystemHealth } = await import('../pages/dashboard/widgets/SystemHealth')
    render(
      <SystemLiveProvider>
        <SystemHealth navigate={vi.fn()} sub="" navEpoch={0} query={{}} setQuery={() => {}} />
      </SystemLiveProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: /Update available/ }))
  }

  it('🔴 says how a desktop install updates, with the releases page, and asks nothing first', async () => {
    installedAs('desktop')
    wire.answer = DESKTOP_ANSWER
    await pressUpdateAvailable()

    await waitFor(() => expect(dialogs.alerts).toHaveLength(1))
    expect(dialogs.confirmed, 'nothing restarts, so there is nothing to confirm').toBe(0)
    render(<>{dialogs.alerts[0].body}</>)
    expect(screen.getByText(DESKTOP_HOW, { exact: false })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open the releases page' }).getAttribute('href')).toBe(RELEASES_PAGE_URL)
  })

  it('asks first where the gateway updates itself, and then says nothing more: the update overlay has it', async () => {
    installedAs('pip')
    wire.answer = { ok: true, status: 'updating', kind: 'pip' }
    await pressUpdateAvailable()

    await waitFor(() => expect(wire.applied).toBe(1))
    expect(dialogs.confirmed).toBe(1)
    expect(dialogs.alerts).toHaveLength(0)
  })
})
