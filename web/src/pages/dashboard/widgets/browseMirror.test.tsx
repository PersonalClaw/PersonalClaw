import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { BrowseMirror } from './BrowseMirror'
import type { WsMessage } from '../../../lib/useChatSocket'
import type { BrowseStatus } from '../../../lib/api'

// ── The dashboard's "Browse live view" band (BA-5, BROWSE-AUTOMATION §(b)/(c)) ──
//
// The browser-side sibling of DesktopLiveView (DCU-7), pinned by the thing that would falsify it:
//
//  · it CONSUMES the `browse_step` WS frame — url + last action + the screenshot PATH — and renders
//    it live. This is the clause the 2026-09-10 audit found missing: on origin/main there is no
//    web/src consumer of `browse_step` at all, so this test cannot pass without the panel.
//  · the kill control POSTs `/api/browse/kill` in ONE click (a running browse stops within a step).
//  · an expired session raises a PERSISTENT banner (role=alert), from the status read the panel
//    polls, refreshed by the `browse_auth_expired` signal.
//  · a FAILED status read says it could not read — it does not impersonate a calm, idle browser.

const browseStatus = vi.fn()
const browseKill = vi.fn()
const browseKillRelease = vi.fn()

vi.mock('../../../lib/api', () => ({
  api: {
    browseStatus: () => browseStatus(),
    browseKill: () => browseKill(),
    browseKillRelease: () => browseKillRelease(),
  },
}))

// Capture the socket callback so a test can deliver a frame — a WebSocket in jsdom is noise, and
// the panel's contract is what it does with a delivered `browse_step`.
let socketCb: ((m: WsMessage) => void) | null = null
vi.mock('../../../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: WsMessage) => void) => { socketCb = onMessage },
}))

// The real hook fires once on mount then on an interval; the mount fire is what loads the status.
// An interval in jsdom is noise, so this stand-in reproduces only the mount fetch.
vi.mock('../../../lib/useVisiblePoll', () => ({
  useVisiblePoll: (fn: () => void) => { useEffect(() => { fn() }, []) },
}))

// reportingWrite is the app's write-with-error-report funnel; here it must still CALL the write, so
// the POST assertion holds, and report nothing on success.
vi.mock('../../../app/reportingWrite', () => ({
  reportingWrite: async (_what: string, run: () => Promise<unknown>) => { await run(); return true },
}))

const IDLE: BrowseStatus = { kill: { active: false, reason: '', started_at: '' }, expired: [] }

function pushStep(over: Partial<{ run_id: string; step_n: number; url: string; action: string; screenshot: string; note: string }> = {}) {
  act(() => {
    socketCb?.({
      type: 'browse_step',
      data: {
        run_id: 'run-1', step_n: 3, url: 'https://example.com/pricing',
        action: 'CLICK a1b2 (Pricing)', screenshot: '/runs/run-1/step-3.png', note: 'noted the plan tiers',
        ...over,
      },
    })
  })
}

describe('the dashboard Browse live view band', () => {
  beforeEach(() => {
    socketCb = null
    browseStatus.mockResolvedValue(IDLE)
    browseKill.mockResolvedValue({ kill: { active: true, reason: '', started_at: 't' } })
    browseKillRelease.mockResolvedValue({ kill: { active: false, reason: '', started_at: '' } })
  })

  it('consumes a browse_step frame and renders url + last action + the screenshot path', async () => {
    render(<BrowseMirror />)
    await waitFor(() => expect(screen.getByText('Idle')).toBeTruthy())
    // The empty state stands until a step arrives.
    expect(screen.getByText(/no unattended browse is running/i)).toBeTruthy()

    pushStep()
    // The url and the rendered action line both surface, straight off the frame.
    await waitFor(() => expect(screen.getByText('https://example.com/pricing')).toBeTruthy())
    expect(screen.getByText('CLICK a1b2 (Pricing)')).toBeTruthy()
    // The screenshot enters as a PATH reference — never an <img>, never fetched.
    expect(screen.getByText(/\[SCREENSHOT: \/runs\/run-1\/step-3\.png\]/)).toBeTruthy()
    expect(screen.getByText('noted the plan tiers')).toBeTruthy()
    // Once a step is in flight the posture reads "Browsing", not "Idle".
    expect(screen.getByText('Browsing')).toBeTruthy()
  })

  it('the kill control POSTs /api/browse/kill in one click', async () => {
    render(<BrowseMirror />)
    await waitFor(() => expect(screen.getByText('Idle')).toBeTruthy())
    expect(browseKill).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Stop all browsing' }))
    await waitFor(() => expect(browseKill).toHaveBeenCalledTimes(1))
  })

  it('a stopped switch shows the stop and Resume re-enables it (confirm-gated)', async () => {
    browseStatus.mockResolvedValue({
      kill: { active: true, reason: 'that page looked wrong', started_at: 't' }, expired: [],
    })
    render(<BrowseMirror />)
    await waitFor(() => expect(screen.getByText('Browsing stopped')).toBeTruthy())
    expect(screen.getByText('that page looked wrong')).toBeTruthy()
    // No stop button while already stopped — the control is the undo now.
    expect(screen.queryByRole('button', { name: 'Stop all browsing' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Re-enable browsing' }))
    await waitFor(() => expect(browseKillRelease).toHaveBeenCalledTimes(1))
  })

  it('an expired session raises a persistent banner naming the site', async () => {
    browseStatus.mockResolvedValue({
      kill: { active: false, reason: '', started_at: '' },
      expired: [{ site: 'crunchbase.com', key_present: true }],
    })
    render(<BrowseMirror />)
    const banner = await waitFor(() => screen.getByRole('alert'))
    expect(banner.textContent).toMatch(/sign-in needed for crunchbase\.com/i)
    // key_present → the copy promises the existing profile is reused, not re-created.
    expect(banner.textContent).toMatch(/reuses the existing profile/i)
  })

  it('the browse_auth_expired signal refetches the status read', async () => {
    render(<BrowseMirror />)
    await waitFor(() => expect(screen.getByText('Idle')).toBeTruthy())
    const before = browseStatus.mock.calls.length
    // Newly-expired: the next read carries the site, and the signal is what triggers the read.
    browseStatus.mockResolvedValue({
      kill: { active: false, reason: '', started_at: '' },
      expired: [{ site: 'wsj.com', key_present: false }],
    })
    act(() => { socketCb?.({ type: 'browse_auth_expired', data: { site: 'wsj.com' } }) })
    await waitFor(() => expect(browseStatus.mock.calls.length).toBeGreaterThan(before))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/wsj\.com/i))
  })

  it('a failed status read says it could not read, not a quiet idle', async () => {
    browseStatus.mockRejectedValue(new Error('boom'))
    render(<BrowseMirror />)
    await waitFor(() => expect(screen.getByText(/couldn’t read the browse mirror/i)).toBeTruthy())
    expect(screen.queryByText(/no unattended browse is running/i)).toBeNull()
  })
})
