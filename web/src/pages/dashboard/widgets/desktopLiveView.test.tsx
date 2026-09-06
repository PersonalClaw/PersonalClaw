import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { DesktopLiveView } from './DesktopLiveView'
import type { ComputerUseLiveView } from '../../../lib/api'

// ── The dashboard's "Desktop live view" band (DCU-7, DESKTOP-COMPUTER-USE §3.7) ─
//
// The human-facing half of desktop computer use. What is pinned here, and why:
//
//  · a FAILED fetch must not render as a quiet desktop — "the agent is doing nothing" over a
//    dead endpoint is a confident false statement about the operator's machine (the exact
//    shape OnThisMachine and the Discover slot each shipped once).
//  · both views are OPTIONAL and OFF until toggled — the widget's default render is the feed
//    alone, and each stage layer appears only behind its own named switch.
//  · the fake cursor renders FROM SERVED DATA ONLY: the widget draws `trail` points the
//    gateway already recorded; there is no code path from a toggle to any capability. (The
//    tool-surface census itself lives server-side in tests/test_computer_use_live_view.py.)

const liveView = vi.fn()

vi.mock('../../../lib/api', () => ({
  api: { computerUseLiveView: () => liveView() },
}))
vi.mock('../../../lib/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    const [error, setError] = useState<unknown>(null)
    useEffect(() => { fn().then(setData).catch(setError) }, [])
    return { data, error, refresh: () => {} }
  },
}))
// The gated poll is exercised implicitly (it fires `refresh` on an interval); an interval in
// jsdom is noise, so the hook is inert here and its gating is documented at the source.
vi.mock('../../../lib/useVisiblePoll', () => ({ useVisiblePoll: () => {} }))

const QUIET: ComputerUseLiveView = {
  enabled: false, allowed_apps: [], ttl_secs: 30, snapshots: [], trail: [], feed: [],
}

const BUSY: ComputerUseLiveView = {
  enabled: true,
  allowed_apps: ['TextEdit'],
  ttl_secs: 30,
  snapshots: [{
    snapshot_id: 'abc123', app: 'TextEdit', age_secs: 2.5, expired: false, element_count: 1,
    elements: [{ index: 0, role: 'AXButton', title: 'Save', enabled: true, frame: { x: 100, y: 200, width: 50, height: 20 } }],
  }],
  trail: [
    { seq: 1, ts: 1000, tool: 'computer_click', app: 'TextEdit', method: 'ax_press', x: 125, y: 210, label: 'Save · AXButton' },
    { seq: 2, ts: 1001, tool: 'computer_type', app: 'TextEdit', method: 'ax_press', x: 140, y: 260, label: 'Subject · AXTextField' },
  ],
  feed: [
    { timestamp: new Date().toISOString(), operation: 'computer_click', outcome: 'approved', error: '', source: 'dashboard', caller_identity: 'dashboard:abc', app: 'TextEdit' },
    { timestamp: new Date().toISOString(), operation: 'computer_type', outcome: 'denied', error: 'ERR_COMPUTER_USE_APP_NOT_ALLOWED', source: 'dashboard', caller_identity: 'dashboard:abc', app: 'Terminal' },
  ],
}

describe('the dashboard Desktop live view band', () => {
  beforeEach(() => {
    localStorage.clear()
    liveView.mockResolvedValue(BUSY)
  })

  it('renders the posture and the attempt feed, refused rows included', async () => {
    render(<DesktopLiveView />)
    await waitFor(() => expect(screen.getByText('Armed')).toBeTruthy())
    // Both verdicts surface — a live view that hid refusals would under-report the agent.
    expect(screen.getByText('approved')).toBeTruthy()
    expect(screen.getByText('denied')).toBeTruthy()
    expect(screen.getByText('computer_click')).toBeTruthy()
    expect(screen.getByText('Terminal')).toBeTruthy()
  })

  it('a failed fetch says it could not read, instead of claiming a quiet desktop', async () => {
    liveView.mockRejectedValue(new Error('boom'))
    render(<DesktopLiveView />)
    await waitFor(() => expect(screen.getByText(/couldn’t read the desktop live view/i)).toBeTruthy())
    expect(screen.queryByText(/no desktop activity/i)).toBeNull()
  })

  it('a disarmed, quiet machine teaches the mechanism instead of stating a bare fact', async () => {
    liveView.mockResolvedValue(QUIET)
    render(<DesktopLiveView />)
    await waitFor(() => expect(screen.getByText('Off')).toBeTruthy())
    expect(screen.getByText(/desktop computer use is off\./i)).toBeTruthy()
    expect(screen.getByText(/an out-of-band step no agent can take/i)).toBeTruthy()
  })

  it('both views are optional: no stage renders until its named switch is on', async () => {
    render(<DesktopLiveView />)
    await waitFor(() => expect(screen.getByText('Armed')).toBeTruthy())
    expect(screen.queryByRole('img')).toBeNull()

    fireEvent.click(screen.getByRole('switch', { name: 'Live view' }))
    const mirror = await waitFor(() => screen.getByRole('img', { name: /live view of textedit/i }))
    // The mirror is the walked tree: the element's own title, as a wireframe box label.
    expect(mirror.textContent).toContain('Save')
    // The overlay is still off — no fake cursor in the accessible name yet.
    expect(screen.queryByRole('img', { name: /fake cursor/i })).toBeNull()
  })

  it('the cursor overlay draws the FAKE cursor and says out loud that it is one', async () => {
    render(<DesktopLiveView />)
    await waitFor(() => expect(screen.getByText('Armed')).toBeTruthy())
    fireEvent.click(screen.getByRole('switch', { name: 'Cursor overlay' }))
    await waitFor(() =>
      expect(screen.getByRole('img', { name: /fake cursor over subject/i })).toBeTruthy(),
    )
    // The one sentence that keeps the overlay honest for a watching human: this cursor is
    // drawn only here — it is not their pointer and never appears on their screen.
    expect(screen.getByText(/the cursor drawn here is a fake/i)).toBeTruthy()
  })

  it('a view toggled on with nothing to draw says so rather than rendering a void', async () => {
    liveView.mockResolvedValue({ ...QUIET, enabled: true })
    render(<DesktopLiveView />)
    await waitFor(() => expect(screen.getByText('Armed')).toBeTruthy())
    fireEvent.click(screen.getByRole('switch', { name: 'Live view' }))
    await waitFor(() => expect(screen.getByText(/nothing to draw yet/i)).toBeTruthy())
  })
})
