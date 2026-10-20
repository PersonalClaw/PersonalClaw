import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'

// ── A reading the gateway did not take ───────────────────────────────────────────────────────────
//
// `/api/system` LEAVES OUT a host reading its probe could not take: memory comes from `sysctl` +
// `vm_stat` and CPU from `ps`, each with a 2 s timeout, and on a loaded host (load 64) those time out.
// The next poll usually has them again. The card formatted `mem_used_gb.toFixed(1)` and
// `load_1m.toFixed(1)` unguarded, so opening System status on that one poll threw "Cannot read
// properties of undefined (reading 'toFixed')" — and with no boundary anywhere above the shell corner,
// React unmounted the whole app to an empty body until a reload.
//
// The card now shows the placeholder where a reading is missing, and a meter with no reading
// announces "not measured" instead of a level.

const FULL = {
  hostname: 'studio.example.test', version: '0.2.0', os: 'Darwin 25.0.0', platform: 'darwin', python: '3.13.1',
  arch: 'arm64 (Apple Silicon)', pid: 4242, cpu_count: 18, cwd: '/home/user',
  mem_total_gb: 48, mem_free_gb: 16.2, mem_used_gb: 31.8, proc_mem_mb: 512.4,
  load_1m: 6.07, load_5m: 5.1, load_15m: 4.2, cpu_pct: 22.5, proc_cpu_pct: 3.2,
  disk_total_gb: 926, disk_free_gb: 623, net_rx_kbs: 12.5, net_tx_kbs: 3.1,
  thread_count: 31, child_processes: 4, mcp_total: 2,
}

// What one poll under load answered: the static total survived (it was read at startup), the live
// memory, CPU and load readings did not.
const { mem_used_gb: _u, mem_free_gb: _f, load_1m: _l1, load_5m: _l5, load_15m: _l15, cpu_pct: _c, ...PARTIAL } = FULL

const payload = vi.hoisted(() => ({ current: {} as Record<string, unknown> }))

vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      system: async () => payload.current,
      authStatus: async () => { throw new Error('not read in this test') },
      spawnedAgents: async () => [],
    },
  }
})

const { SystemWidget } = await import('./SystemWidget')

async function openCard(): Promise<HTMLElement> {
  render(<SystemWidget />)
  const trigger = await screen.findByRole('button', { name: 'System status — Gateway connected' })
  fireEvent.click(trigger)
  // The hostname heads the card, so finding it proves the readings section rendered.
  const host = await screen.findByText('studio.example.test')
  return host.closest('.fixed') as HTMLElement
}

beforeEach(() => { payload.current = FULL })

describe('the System status card on a poll that is missing readings', () => {
  it('🔴 opens, and shows the placeholder where memory, CPU and load were not read', async () => {
    payload.current = PARTIAL
    const card = await openCard()

    const cpu = within(card).getByRole('progressbar', { name: 'CPU usage' })
    const mem = within(card).getByRole('progressbar', { name: 'Memory usage' })
    // A meter with no reading reports no level: no fabricated 0%, and a name for the state.
    for (const meter of [cpu, mem]) {
      expect(meter.getAttribute('aria-valuenow')).toBeNull()
      expect(meter.getAttribute('aria-valuetext')).toBe('not measured')
    }
    // The figures beside them: the placeholder, never NaN and never a made-up zero.
    expect(within(card).getAllByText('—')).toHaveLength(2)
    expect(within(card).getByText('18 cores · load —')).toBeTruthy()
    expect(within(card).getByText('— / 48 GB')).toBeTruthy()
    expect(card.textContent).not.toMatch(/NaN|undefined/)
    // The readings that WERE taken still render.
    expect(within(card).getByText('623 GB free')).toBeTruthy()
    expect(within(card).getByText('512 MB · 3.2% CPU · 31 thr')).toBeTruthy()
  })

  it('says "not measured" when memory was never read at all', async () => {
    const { mem_total_gb: _t, ...noMemory } = PARTIAL
    payload.current = noMemory
    const card = await openCard()
    expect(within(card).getByText('not measured')).toBeTruthy()
  })

  it('a readings section that cannot render keeps its place in the card, and Restart stays reachable', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    // A shape the readings cannot draw: the boundary around them, not the app, catches it.
    payload.current = { ...FULL, os: null }
    render(<SystemWidget />)
    fireEvent.click(await screen.findByRole('button', { name: 'System status — Gateway connected' }))
    const notice = (await screen.findByText("Couldn't show the system readings.")).closest('[role="alert"]') as HTMLElement
    expect(within(notice).getByRole('button', { name: 'Retry' })).toBeTruthy()
    const card = notice.closest('.fixed') as HTMLElement
    expect(within(card).getByRole('button', { name: 'Restart' })).toBeTruthy()
    // The dot itself is untouched.
    expect(screen.getByRole('button', { name: 'System status — Gateway connected' })).toBeTruthy()
    spy.mockRestore()
  })

  it('and the full reading is unchanged', async () => {
    const card = await openCard()
    expect(within(card).getByRole('progressbar', { name: 'CPU usage' }).getAttribute('aria-valuenow')).toBe('23')
    expect(within(card).getByRole('progressbar', { name: 'Memory usage' }).getAttribute('aria-valuenow')).toBe('66')
    expect(within(card).getByText('18 cores · load 6.1')).toBeTruthy()
    expect(within(card).getByText('31.8 / 48 GB')).toBeTruthy()
    expect(within(card).queryByText('—')).toBeNull()
  })
})
