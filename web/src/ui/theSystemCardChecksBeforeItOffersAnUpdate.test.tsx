import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'

// ── The System card offered Update & Restart with no update to install ─────────────────────────
//
// The card's update button read "Update & Restart" whether or not an update was known, and its
// confirm said only "Pull latest, rebuild, and restart the gateway?": pressing it on an install
// already up to date restarted the gateway for nothing, and nothing said which version it was for.
//
// Now, on an install PersonalClaw updates in place (a checkout or a wheel), the card reads the
// update check Settings › Updates reads. With no update known it offers Check for Updates, which
// runs a check and says what it found where it was pressed; once an update is known it offers
// Update & Restart, and its confirm names the version it updates to. A container or desktop
// install keeps its Update, which says how it is updated (`anUpdateThisInstallCannotApplySaysHow`).

const wire = vi.hoisted(() => ({
  checks: 0,
  found: {} as Record<string, unknown>,
}))

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
      checkForUpdatesNow: async () => { wire.checks += 1; return wire.found },
      applyUpdate: async () => ({ ok: true, status: 'started' }),
      status: async () => ({ uptime: '5m', version: '0.3.6', platform: 'darwin', triggers: 0, subagents: 0 }),
      doctor: async () => ({ ok: true, core_ok: true, worst: '', capabilities: {} }),
    },
  }
})
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const { SystemWidget } = await import('./SystemWidget')
const { GatewayConnectionProvider } = await import('../app/gatewayConnection')
const { resetDataStore, writeQuery } = await import('../lib/data/store')

const UP_TO_DATE = { available: false, changes: '', checked: true, auto: 'off', kind: 'git', version: '0.3.6', latest: '0.3.6' }
const NEWER = { ...UP_TO_DATE, available: true, latest: '0.4.0', checked_now: true }

/** What the update check Settings › Updates reads already said. */
function checkSaid(answer: Record<string, unknown>) {
  writeQuery('settings:update-check', answer, true)
}

beforeEach(() => {
  resetDataStore(); sessionStorage.clear()
  wire.checks = 0; wire.found = {}
})

/** What the card says in place, as a status it announces. */
async function said(card: HTMLElement, text: string): Promise<HTMLElement> {
  const line = (await within(card).findByText(text)).closest('[role="status"]') as HTMLElement | null
  expect(line, `"${text}" is not said as a status`).not.toBeNull()
  return line as HTMLElement
}

async function openPopover(): Promise<HTMLElement> {
  render(<GatewayConnectionProvider><SystemWidget /></GatewayConnectionProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'System status — Gateway connected' }))
  return (await screen.findByText('studio.example.test')).closest('.fixed') as HTMLElement
}

describe('the System card on an install PersonalClaw updates in place', () => {
  it('🔴 offers Check for Updates while no update is known, not Update & Restart', async () => {
    checkSaid(UP_TO_DATE)
    const card = await openPopover()

    expect(within(card).getByRole('button', { name: 'Check for Updates' })).toBeTruthy()
    expect(within(card).queryByRole('button', { name: 'Update & Restart' })).toBeNull()
    expect(within(card).getByRole('button', { name: 'Restart' })).toBeTruthy()
  })

  it('🔴 a check that finds one says so in place and offers Update & Restart', async () => {
    checkSaid(UP_TO_DATE)
    wire.found = NEWER
    const card = await openPopover()

    fireEvent.click(within(card).getByRole('button', { name: 'Check for Updates' }))

    expect(await within(card).findByRole('button', { name: 'Update & Restart' })).toBeTruthy()
    await said(card, 'PersonalClaw 0.4.0 is available.')
    expect(within(card).queryByRole('button', { name: 'Check for Updates' })).toBeNull()
    expect(wire.checks).toBe(1)
  })

  it('🔴 a check that finds none says it is up to date, and still offers the check', async () => {
    checkSaid(UP_TO_DATE)
    wire.found = { ...UP_TO_DATE, checked_now: true }
    const card = await openPopover()

    fireEvent.click(within(card).getByRole('button', { name: 'Check for Updates' }))

    await said(card, 'PersonalClaw 0.3.6 is up to date.')
    expect(within(card).getByRole('button', { name: 'Check for Updates' })).toBeTruthy()
    expect(within(card).queryByRole('button', { name: 'Update & Restart' })).toBeNull()
  })

  it('🔴 with an update known, its confirm names the version it updates to', async () => {
    checkSaid(NEWER)
    const card = await openPopover()

    fireEvent.click(within(card).getByRole('button', { name: 'Update & Restart' }))

    expect(await within(card).findByText('Pull 0.4.0, rebuild, and restart the gateway?')).toBeTruthy()
  })

  it('a check that reached nothing says so', async () => {
    checkSaid(UP_TO_DATE)
    wire.found = { ...UP_TO_DATE, checked_now: false }
    const card = await openPopover()

    fireEvent.click(within(card).getByRole('button', { name: 'Check for Updates' }))

    await said(card, "Couldn't reach the releases to check. Try again later.")
  })
})
