import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { ComputerUseLiveView } from '../../lib/api'

// ── Whether desktop control is armed is said in Settings › Security ─────────────────────────────
//
// Home's Desktop live view carried an Armed/Off pill and the apps it may drive; it now shows only
// while an agent drives the desktop, so the posture moved where desktop control is configured,
// beside the other desktop permissions: a fact, never a switch, since only a file the owner writes
// arms it.

const liveView = vi.fn<() => Promise<ComputerUseLiveView>>()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: { computerUseLiveView: () => liveView() },
}))

const { DesktopControlSection } = await import('./SecurityPanel')
const { resetDataStore } = await import('../../lib/data/store')

const VIEW: ComputerUseLiveView = { live: false, enabled: true, allowed_apps: ['TextEdit', 'Notes'], ttl_secs: 30, snapshots: [], trail: [], feed: [] }

beforeEach(() => {
  resetDataStore(); sessionStorage.clear()
  vi.clearAllMocks()
  liveView.mockResolvedValue(VIEW)
})

describe('Settings › Security › Desktop control', () => {
  it('says whether it is armed and which apps it may drive, with no switch', async () => {
    render(<DesktopControlSection />)

    expect(await screen.findByText('Armed')).toBeTruthy()
    expect(screen.getByText('TextEdit, Notes')).toBeTruthy()
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('off is said as off, with no apps listed', async () => {
    liveView.mockResolvedValue({ ...VIEW, enabled: false })
    render(<DesktopControlSection />)

    expect(await screen.findByText('Off')).toBeTruthy()
    expect(screen.queryByText('Apps it may drive')).toBeNull()
  })

  it('a read that failed says so, never that it is off', async () => {
    liveView.mockRejectedValue(new Error('boom'))
    render(<DesktopControlSection />)

    await waitFor(() => expect(screen.getByText(/Couldn’t read whether desktop control is on/)).toBeTruthy())
    expect(screen.queryByText('Off')).toBeNull()
  })
})
