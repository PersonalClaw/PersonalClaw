import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { BrowseMirror, useBrowseLive } from './BrowseMirror'
import type { WsMessage } from '../../../lib/useChatSocket'
import type { BrowseStatus } from '../../../lib/api'

// ── The dashboard's "Browse live view" band, and when it is live ──
//
// The browser-side sibling of DesktopLiveView, pinned by the thing that would falsify it:
//
//  · it CONSUMES the `browse_step` WS frame — url + last action + the screenshot PATH — and renders
//    it live;
//  · a browse is live while it takes steps: from a step until 30 s after the last one, and from
//    the status read's `live` for a page that opened mid-browse; a read that fails says nothing
//    is live rather than guessing;
//  · it is observe-only: the grant prompt is answered in Attention, and the kill switch and the
//    expired sign-ins are in Settings › Guardrails, so the band has no control at all.

const browseStatus = vi.fn<() => Promise<BrowseStatus>>()

vi.mock('../../../lib/api', () => ({
  api: { browseStatus: () => browseStatus() },
}))

// Capture the socket callback so a test can deliver a frame — a WebSocket in jsdom is noise, and
// the hook's contract is what it does with a delivered `browse_step`.
let socketCb: ((m: WsMessage) => void) | null = null
vi.mock('../../../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: WsMessage) => void) => { socketCb = onMessage },
}))

// The real hook fires once on mount then on an interval; the mount fire is what loads the status.
vi.mock('../../../lib/useVisiblePoll', () => ({
  useVisiblePoll: (fn: () => void) => { useEffect(() => { fn() }, []) },
}))

const IDLE: BrowseStatus = { live: false, kill: { active: false, reason: '', started_at: '' }, expired: [], grants: [] }

function pushStep(over: Partial<{ run_id: string; step_n: number; url: string; action: string; screenshot: string; note: string }> = {}) {
  act(() => {
    socketCb?.({
      type: 'browse_step',
      data: {
        run_id: 'run-1', step_n: 3, url: 'https://example.com/pricing',
        action: 'CLICK a1b2 (Pricing)', screenshot: '/runs/run-1/step-3.png', note: 'noted the plan tiers',
        ...over,
      },
    } as WsMessage)
  })
}

beforeEach(() => {
  socketCb = null
  browseStatus.mockResolvedValue(IDLE)
})
afterEach(() => { vi.useRealTimers() })

describe('whether a browse is live', () => {
  it('is not, before any step and while the status read says not', async () => {
    const { result } = renderHook(() => useBrowseLive())
    await waitFor(() => expect(browseStatus).toHaveBeenCalled())
    expect(result.current.live).toBe(false)
  })

  it('is from a step, and for 30 s after the last one', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { result } = renderHook(() => useBrowseLive())
    pushStep()
    expect(result.current.live).toBe(true)
    expect(result.current.steps.map((s) => s.url)).toEqual(['https://example.com/pricing'])

    act(() => { vi.advanceTimersByTime(29_000) })
    expect(result.current.live).toBe(true)
    act(() => { vi.advanceTimersByTime(2_000) })
    expect(result.current.live).toBe(false)
  })

  it('is, on a page that opened mid-browse, from the status read', async () => {
    browseStatus.mockResolvedValue({ ...IDLE, live: true })
    const { result } = renderHook(() => useBrowseLive())
    await waitFor(() => expect(result.current.live).toBe(true))
  })

  it('is not guessed from a read that failed', async () => {
    browseStatus.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useBrowseLive())
    await waitFor(() => expect(browseStatus).toHaveBeenCalled())
    expect(result.current.live).toBe(false)
  })
})

describe('the band, while a browse is live', () => {
  it('renders each step: url, last action and the screenshot path', () => {
    const { result } = renderHook(() => useBrowseLive())
    pushStep()
    render(<BrowseMirror steps={result.current.steps} />)

    expect(screen.getByText('https://example.com/pricing')).toBeTruthy()
    expect(screen.getByText('CLICK a1b2 (Pricing)')).toBeTruthy()
    // The screenshot enters as a PATH reference — never an <img>, never fetched.
    expect(screen.getByText(/\[SCREENSHOT: \/runs\/run-1\/step-3\.png\]/)).toBeTruthy()
    expect(screen.getByText('noted the plan tiers')).toBeTruthy()
    expect(screen.getByText('Browsing')).toBeTruthy()
  })

  it('has no control of its own: no Stop, no Resume, no grant to answer', () => {
    render(<BrowseMirror steps={[]} />)

    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText(/a browse is running/i)).toBeTruthy()
  })
})
