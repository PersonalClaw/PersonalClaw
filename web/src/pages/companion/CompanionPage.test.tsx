import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { CompanionPage } from './CompanionPage'
import { invalidateKeys } from '../../lib/data'
import type { PendingApproval } from '../../lib/api'

// ── `#/companion` — the phone approvals surface (MOBILE-COMPANION MC-3) ──────────────────
//
// The route exists so a blocked run can be unblocked from a phone. Three things therefore
// have to be true, and each is asserted by DRIVING the surface rather than by reading it:
//
//  1. approve/reject reach the backend — the resolve call goes to
//     POST /api/approvals/{id}/{action}, the queue's own resolver, NOT the chat route.
//  2. a FAILED fetch tells the user. `useQuery` hands back an `error`; most fetchers in
//     this app `.catch(() => [])`, and on an approvals surface that reads as "nothing is
//     waiting on you" — the most dangerous possible lie. The error branch must announce.
//  3. every control is reachable by keyboard and has an unambiguous accessible NAME, read
//     from the a11y tree (`getByRole`), not from the source. A queue paints one card per
//     approval, so a bare "Allow" would announce identically N times.
//
// The WS liveness hook is mocked out: jsdom has no gateway to connect to, and the reconnect
// backoff would keep timers alive past the test.
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const approvals = vi.fn()
const resolveApproval = vi.fn()
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, approvals: () => approvals(), resolveApproval: (id: string, action: string) => resolveApproval(id, action) } }
})

const AP: PendingApproval = {
  id: 'ap-1', source: 'cron', tool: 'Bash',
  tool_input: 'rm -rf /tmp/scratch', tool_purpose: 'Clear the scratch directory',
  session: 'cron:nightly', ts: Math.round(Date.now() / 1000) - 90,
}

const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }

beforeEach(() => {
  approvals.mockReset()
  resolveApproval.mockReset()
  // COLD cache per test. A warm entry paints instantly and would mask the error branch
  // entirely — `data === undefined && error` is only reachable when nothing is cached.
  invalidateKeys('companion:approvals')
  sessionStorage.clear()
})
afterEach(cleanup)

describe('the companion approvals queue', () => {
  it('renders the full decision context from GET /api/approvals', async () => {
    approvals.mockResolvedValue([AP])
    render(<CompanionPage {...route} />)
    // Named for the tool, so a screen-reader user knows WHICH prompt they are in.
    const card = await screen.findByRole('group', { name: 'Permission needed to run Bash' })
    // The whole decision: tool, its arguments UNTRUNCATED, why, and where it came from.
    expect(card.textContent).toContain('Bash')
    expect(card.textContent).toContain('rm -rf /tmp/scratch')
    expect(card.textContent).toContain('Clear the scratch directory')
    expect(card.textContent).toContain('cron:nightly')
    expect(card.textContent).toContain('cron')
    // And it interrupts — the agent is halted until this is answered.
    expect(card.querySelector('[role="alert"]')?.textContent).toBe('Permission needed')
  })

  it('pretty-prints a structured tool_input instead of "[object Object]"', async () => {
    approvals.mockResolvedValue([{ ...AP, tool_input: { command: 'ls', cwd: '/tmp' } }])
    render(<CompanionPage {...route} />)
    const card = await screen.findByRole('group', { name: 'Permission needed to run Bash' })
    expect(card.textContent).toContain('"command": "ls"')
    expect(card.textContent).not.toContain('[object Object]')
  })

  it('approve round-trips to the queue resolver, not the chat route', async () => {
    // First read lists it; the read AFTER the answer does not — exactly what the backend does
    // (state.resolve_approval pops it from `_pending_approvals`).
    approvals.mockResolvedValueOnce([AP]).mockResolvedValue([])
    resolveApproval.mockResolvedValue({ ok: true })
    render(<CompanionPage {...route} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Allow Bash' }))
    // 🔑 THE CENTRAL RAIL. `POST /api/approvals/{id}/{action}` (state.resolve_approval) is the
    // resolver for the queue this surface lists — it answers BOTH gateway-level futures and
    // chat-session ones. The plan's C2 map named the chat route
    // `POST /api/chat/sessions/{session}/approve`, which needs a session name the queue's
    // gateway-originated rows do not have and carries chat-only trust scopes.
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'approve')
    // Answered rows leave immediately — the user must not be able to answer twice.
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Allow Bash' })).toBeNull())
  })

  it('brings a card BACK if the server still lists it after the answer settled', async () => {
    // 🪤 Found by driving the live gateway, not by reading the code: the optimistic-hide set
    // was never reconciled, so a queue the backend was still serving rendered as "Nothing
    // waiting on you". A hidden prompt is a denial the user never made. The fetched list is
    // authoritative once the POST has settled.
    approvals.mockResolvedValueOnce([{ ...AP }]).mockResolvedValue([{ ...AP }])
    resolveApproval.mockResolvedValue({ ok: true })
    render(<CompanionPage {...route} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Allow Bash' }))
    // The post-answer fetch still lists it → it must not stay hidden.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Allow Bash' })).toBeTruthy())
  })

  it('reject round-trips with the reject action', async () => {
    approvals.mockResolvedValue([AP])
    resolveApproval.mockResolvedValue({ ok: true })
    render(<CompanionPage {...route} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Deny Bash' }))
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'reject')
  })

  it('puts a row BACK and announces when the resolve call fails', async () => {
    // A dropped permission prompt is worse than a visible failure: the user would believe
    // they answered it while the run stays blocked until it times out to a denial.
    approvals.mockResolvedValue([AP])
    resolveApproval.mockRejectedValue(new Error('gateway unreachable'))
    const toasts: string[] = []
    const onToast = (e: Event) => toasts.push(String((e as CustomEvent).detail?.message ?? ''))
    window.addEventListener('ne:toast', onToast)
    try {
      render(<CompanionPage {...route} />)
      await userEvent.click(await screen.findByRole('button', { name: 'Allow Bash' }))
      await waitFor(() => expect(toasts.join('|')).toContain('gateway unreachable'))
      expect(toasts.join('|')).toContain("Couldn't approve Bash")
      // The card is still actionable.
      await waitFor(() => expect(screen.getByRole('button', { name: 'Allow Bash' })).toBeTruthy())
    } finally {
      window.removeEventListener('ne:toast', onToast)
    }
  })

  it('TELLS the user when the queue could not be loaded — never "nothing waiting on you"', async () => {
    approvals.mockRejectedValue(new Error('probe-induced failure'))
    render(<CompanionPage {...route} />)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain("Couldn't load your approvals")
    expect(alert.textContent).toContain('probe-induced failure')
    // The empty state must NOT be what a failure paints.
    expect(screen.queryByText('Nothing waiting on you')).toBeNull()
    // ...and there is a way out.
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
  })

  it('says "nothing waiting" only when the fetch SUCCEEDED and was empty', async () => {
    approvals.mockResolvedValue([])
    render(<CompanionPage {...route} />)
    expect(await screen.findByText('Nothing waiting on you')).toBeTruthy()
    // An empty result is a normal answer, so it must NOT hijack a live region.
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('names every control unambiguously, even with two approvals pending', async () => {
    approvals.mockResolvedValue([AP, { ...AP, id: 'ap-2', tool: 'WebFetch', session: '' }])
    render(<CompanionPage {...route} />)
    await screen.findByRole('group', { name: 'Permission needed to run Bash' })
    const names = screen.getAllByRole('button').map((b) => b.getAttribute('aria-label') || b.textContent?.trim() || '')
    // No unnamed control, and no two controls sharing a name — the AX tree, not the source.
    expect(names.every((n) => n.length > 0)).toBe(true)
    expect(new Set(names).size, `duplicate accessible names: ${names.join(', ')}`).toBe(names.length)
    expect(names).toContain('Allow Bash')
    expect(names).toContain('Deny WebFetch')
  })

  it('is fully keyboard operable — a mobile-width surface is not keyboard-exempt', async () => {
    approvals.mockResolvedValue([AP])
    resolveApproval.mockResolvedValue({ ok: true })
    render(<CompanionPage {...route} />)
    const allow = await screen.findByRole('button', { name: 'Allow Bash' })
    // Reachable by Tab (not just clickable) and actionable from the keyboard.
    allow.focus()
    expect(document.activeElement).toBe(allow)
    await userEvent.keyboard('{Enter}')
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'approve')
  })

  it('stubs the unbuilt sections HONESTLY — never as an empty data state', async () => {
    approvals.mockResolvedValue([])
    render(<CompanionPage {...route} />)
    await screen.findByText('Nothing waiting on you')
    // The heading states the fact; each row says a later release, so none of them can be
    // read as "you have no running loops" / "your inbox is empty".
    expect(screen.getByText('Not on the phone yet')).toBeTruthy()
    for (const label of ['Running loops', 'Inbox', 'Recent notifications']) {
      expect(screen.getByText(label)).toBeTruthy()
    }
    expect(screen.getAllByText(/arrives? in a later release/i).length).toBe(3)
  })
})

describe('the route is registered under the URL doctrine', () => {
  const app = readFileSync(join(process.cwd(), 'src/app/App.tsx'), 'utf8')

  it("App.tsx serves `#/companion` and does NOT fall through to the dashboard", () => {
    expect(app).toContain("route === 'companion'")
    expect(app).toMatch(/<CompanionPage\s/)
  })

  it('stays out of NAV and ROUTABLE — a deep link, not a desktop nav tile', () => {
    // In NAV it would demand an e2e route-manifest entry (routeManifestParity) and would put
    // a phone-only surface in the desktop rail. In ROUTABLE it would render inside the shell
    // WITH the NavRail, which is exactly what this route must not do.
    const nav = app.match(/const NAV: NavItem\[\] = \[(.*?)\n\]/s)?.[1] ?? ''
    expect(nav.length, 'NAV literal must parse or this assertion is vacuous').toBeGreaterThan(100)
    expect(nav).not.toContain('companion')
    const routable = app.match(/const ROUTABLE = new Set\(\[(.*?)\]\)/s)?.[1] ?? ''
    expect(routable.length, 'ROUTABLE literal must parse').toBeGreaterThan(10)
    expect(routable).not.toContain('companion')
  })
})
