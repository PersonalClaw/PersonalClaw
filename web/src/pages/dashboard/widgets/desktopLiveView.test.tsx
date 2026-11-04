import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DesktopLiveView } from './DesktopLiveView'
import type { ComputerUseLiveView } from '../../../lib/api'

// ── The dashboard's "Desktop live view" band, while the desktop is live ──
//
// The band is drawn only while the agent drives the desktop (`homeOverviewShowsLiveViewsOnlyWhileLive`
// pins that), so what it draws is the drive: the walked window and the fake cursor together, with
// no switch for either, and the attempt feed. What is pinned here, and why:
//
//  · both layers draw at once: the two toggles that chose between them are gone, and with them
//    the per-browser preference they kept;
//  · the fake cursor renders FROM SERVED DATA ONLY, and says out loud that it is a fake;
//  · the feed shows refused attempts beside approved ones — a live view that hid refusals would
//    under-report the agent;
//  · the posture (Armed/Off, the apps it may drive) is not here: it is said in Settings › Security.

const LIVE: ComputerUseLiveView = {
  live: true,
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

describe('the dashboard Desktop live view band, while live', () => {
  it('draws the walked window and the fake cursor together, with no switch for either', () => {
    render(<DesktopLiveView view={LIVE} />)

    const stage = screen.getByRole('img', { name: /live view of textedit — fake cursor over subject/i })
    // The mirror is the walked tree: the element's own title, as a wireframe box label.
    expect(stage.textContent).toContain('Save')
    expect(screen.getByText(/the cursor drawn here is a fake/i)).toBeTruthy()
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('lists every attempt, refused ones included', () => {
    render(<DesktopLiveView view={LIVE} />)

    expect(screen.getByText('approved')).toBeTruthy()
    expect(screen.getByText('denied')).toBeTruthy()
    expect(screen.getByText('computer_click')).toBeTruthy()
    expect(screen.getByText('Terminal')).toBeTruthy()
  })

  it('says nothing of the posture, which Settings › Security says', () => {
    render(<DesktopLiveView view={LIVE} />)

    expect(screen.queryByText('Armed')).toBeNull()
    expect(screen.queryByText(/may drive/i)).toBeNull()
  })

  it('a drive with no window walked yet draws its cursor trail alone', () => {
    render(<DesktopLiveView view={{ ...LIVE, snapshots: [] }} />)

    expect(screen.getByRole('img', { name: /cursor motion trail — fake cursor over subject/i })).toBeTruthy()
  })
})
