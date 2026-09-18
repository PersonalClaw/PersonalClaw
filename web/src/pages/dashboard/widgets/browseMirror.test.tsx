import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { BrowseMirror } from './BrowseMirror'
import type { WsMessage } from '../../../lib/useChatSocket'
import type { BrowsePendingGrant, BrowseStatus } from '../../../lib/api'

// ── The dashboard's "Browse live view" band (BA-5 + BA-9, BROWSE-AUTOMATION §(b)/(c)) ──
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
//  · **BA-9: a pending per-task grant is ANSWERABLE here.** The audit found `approve_grant` /
//    `reject_grant` / `pending_grants` with zero non-test callers and zero `web/src` references, so
//    every `user_browser` task could only ever end in the fail-closed 300s refusal. These tests are
//    the falsifier: they fail unless the card renders the scope and both verbs POST.

const browseStatus = vi.fn()
const browseKill = vi.fn()
const browseKillRelease = vi.fn()
const browseGrantResolve = vi.fn()

vi.mock('../../../lib/api', () => ({
  api: {
    browseStatus: () => browseStatus(),
    browseKill: () => browseKill(),
    browseKillRelease: () => browseKillRelease(),
    browseGrantResolve: (id: string, action: 'approve' | 'reject') => browseGrantResolve(id, action),
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

const IDLE: BrowseStatus = {
  kill: { active: false, reason: '', started_at: '' }, expired: [], grants: [],
}

const PENDING_GRANT: BrowsePendingGrant = {
  request_id: 'grant-abc123',
  task: 'Download my October invoices',
  scope: ['billing.example.com'],
  group: 'Download my October invoices',
  requested_at: 1_700_000_000,
  timeout: 300,
}

function statusWithGrants(...grants: BrowsePendingGrant[]): BrowseStatus {
  return { kill: { active: false, reason: '', started_at: '' }, expired: [], grants }
}

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
    browseGrantResolve.mockResolvedValue({ ok: true, request_id: 'grant-abc123', action: 'approve' })
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

  // ── BA-9: the per-task grant is answerable ────────────────────────────────────

  it('a pending grant renders a card naming the task, the sites, and that silence refuses', async () => {
    browseStatus.mockResolvedValue(statusWithGrants(PENDING_GRANT))
    render(<BrowseMirror />)

    const card = await waitFor(() => screen.getByRole('alert'))
    expect(card.textContent).toMatch(/allow this task to use your browser\?/i)
    // The task label and the host scope are the two facts a human decides on.
    expect(card.textContent).toContain('Download my October invoices')
    expect(card.textContent).toContain('billing.example.com')
    // A fail-closed gate must not read as an open-ended wait. 300s → "about 5 minutes".
    expect(card.textContent).toMatch(/not answering is a refusal/i)
    expect(card.textContent).toMatch(/about 5 minutes/i)
    // The no-credential invariant is stated where the human is deciding, not only in a doc.
    expect(card.textContent).toMatch(/never reads your passwords/i)
    // The posture pill stops claiming "Idle" while a task waits on the operator.
    expect(screen.getByText('Waiting for your answer')).toBeTruthy()
    expect(screen.queryByText(/no unattended browse is running/i)).toBeNull()
  })

  it('Allow POSTs approve for that request id', async () => {
    browseStatus.mockResolvedValue(statusWithGrants(PENDING_GRANT))
    render(<BrowseMirror />)
    const allow = await waitFor(() =>
      screen.getByRole('button', { name: /^Allow the browse task Download my October invoices/ }))

    fireEvent.click(allow)
    await waitFor(() => expect(browseGrantResolve).toHaveBeenCalledWith('grant-abc123', 'approve'))
  })

  it('Deny POSTs reject for that request id', async () => {
    browseStatus.mockResolvedValue(statusWithGrants(PENDING_GRANT))
    render(<BrowseMirror />)
    const deny = await waitFor(() =>
      screen.getByRole('button', { name: /^Deny the browse task Download my October invoices/ }))

    fireEvent.click(deny)
    await waitFor(() => expect(browseGrantResolve).toHaveBeenCalledWith('grant-abc123', 'reject'))
  })

  it('the answered card clears, because the write refetches the read model', async () => {
    browseStatus.mockResolvedValue(statusWithGrants(PENDING_GRANT))
    render(<BrowseMirror />)
    const allow = await waitFor(() =>
      screen.getByRole('button', { name: /^Allow the browse task/ }))

    // The gate resolves server-side, so the next read no longer lists it.
    browseStatus.mockResolvedValue(IDLE)
    fireEvent.click(allow)
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(screen.getByText(/no unattended browse is running/i)).toBeTruthy()
  })

  it('the browse_grant signal refetches rather than reading the frame', async () => {
    render(<BrowseMirror />)
    await waitFor(() => expect(screen.getByText('Idle')).toBeTruthy())
    const before = browseStatus.mock.calls.length

    // The frame carries only a COUNT — the task label and scope must come from the GET, so a panel
    // that rendered from the frame would show a card with nothing in it.
    browseStatus.mockResolvedValue(statusWithGrants(PENDING_GRANT))
    act(() => { socketCb?.({ type: 'browse_grant', data: { pending: 1 } }) })

    await waitFor(() => expect(browseStatus.mock.calls.length).toBeGreaterThan(before))
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('billing.example.com'))
  })

  it('two pending grants are two independently answerable cards', async () => {
    browseStatus.mockResolvedValue(statusWithGrants(PENDING_GRANT, {
      ...PENDING_GRANT, request_id: 'grant-second', task: 'Cancel my subscription',
      scope: ['account.example.com'],
    }))
    render(<BrowseMirror />)
    await waitFor(() => expect(screen.getAllByRole('alert').length).toBe(2))

    fireEvent.click(screen.getByRole('button', { name: /^Deny the browse task Cancel my subscription/ }))
    await waitFor(() => expect(browseGrantResolve).toHaveBeenCalledWith('grant-second', 'reject'))
    expect(browseGrantResolve).not.toHaveBeenCalledWith('grant-abc123', expect.anything())
  })

  it('a grant with no scope says so rather than implying an empty allowlist', async () => {
    browseStatus.mockResolvedValue(statusWithGrants({ ...PENDING_GRANT, scope: [] }))
    render(<BrowseMirror />)
    const card = await waitFor(() => screen.getByRole('alert'))
    expect(card.textContent).toMatch(/none named/i)
  })

  it('a grant with no request id is dropped, not drawn with dead buttons', async () => {
    browseStatus.mockResolvedValue(statusWithGrants({ ...PENDING_GRANT, request_id: '' }))
    render(<BrowseMirror />)
    await waitFor(() => expect(screen.getByText('Idle')).toBeTruthy())
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('an OLDER status read landing late does not resurrect an answered grant', async () => {
    // Measured in a real browser before the read-ordering guard: reads are concurrent (the 15s poll,
    // the socket signal, and the answering write all call the same loader), so a read issued BEFORE
    // the answer resolved could land last and put the card back for a full poll interval. On a
    // security prompt that invites a second click on a gate that is already closed.
    let releaseStale: (v: BrowseStatus) => void = () => {}
    const stale = new Promise<BrowseStatus>((res) => { releaseStale = res })

    // Read #1 (mount) hangs — it is the one that will land LAST carrying the pending grant.
    browseStatus.mockReturnValueOnce(stale)
    // Read #2 onward: the grant is answered and gone.
    browseStatus.mockResolvedValue(IDLE)

    render(<BrowseMirror />)
    // Nothing rendered yet: the first read has not resolved.
    expect(screen.queryByRole('alert')).toBeNull()

    // A signal arrives and triggers read #2, which answers first with the settled state.
    act(() => { socketCb?.({ type: 'browse_grant', data: { pending: 0 } }) })
    await waitFor(() => expect(screen.getByText('Idle')).toBeTruthy())

    // Now the stale read #1 finally answers, still carrying the pending grant.
    await act(async () => { releaseStale(statusWithGrants(PENDING_GRANT)); await stale })

    // It must be ignored — a superseded read cannot put an answered prompt back on screen.
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByText('Idle')).toBeTruthy()
  })
})
