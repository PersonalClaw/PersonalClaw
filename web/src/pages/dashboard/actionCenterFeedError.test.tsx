import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── "All clear" must not mask a lane that failed to load ──────────────────────────────────────────
//
// ActionCenter is the dashboard's unified triage queue: it merges pending tool approvals, inbox
// items, and skill proposals into ONE list, and shows "All clear — nothing waiting on you" when that
// merged list is empty. Its three feeds used to end in `.catch(() => {})` inside DashboardLive, so a
// transient read failure left the lane's array empty — indistinguishable from a genuinely empty
// lane — and a failed approvals read folded straight into "All clear". A PENDING TOOL APPROVAL is
// safety-relevant: a run is blocked on the user's yes/no, and a queue that claims "nothing waiting"
// while hiding one is the exact failure this guards.
//
// The fix mirrors the already-correct `discoverErr`/`doctorErr` split in the same file: each lane's
// read failure is tracked, published on the context, and rendered as the app's standard InlineError
// (role="alert") with a per-lane Retry — the same primitive #/inbox's TriageDigestCard uses for the
// same "the read failed → say so, offer a retry" fact. A failed lane says so; an empty lane stays
// silent.

const boom = () => Promise.reject(new Error('gateway down'))

/** Every slice DashboardLive polls on mount — an unmocked one throws before the widget renders,
 *  which would make the absence assertions below vacuously true. */
function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      approvals: () => Promise.resolve([]),
      inboxPending: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [], suppressed: 0 }),
      status: () => Promise.resolve({ update_available: false }),
      notifications: () => Promise.resolve({ notifications: [] }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      discover: () => Promise.resolve({ enabled: true, areas: [] }),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      ...over,
    },
  }))
}

/** Mount the REAL provider around the real widget, so the error state and its retry travel the
 *  actual context path a consumer receives — not a hand-built stub. */
async function mount() {
  const { DashboardLiveProvider } = await import('./DashboardLive')
  const { ActionCenter } = await import('./widgets/ActionCenter')
  const route = { navigate: vi.fn(), sub: '', navEpoch: 0, query: {}, setQuery: () => {} }
  render(
    <DashboardLiveProvider>
      <ActionCenter {...route} />
    </DashboardLiveProvider>,
  )
}

const ALL_CLEAR = /All clear/i

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('ActionCenter tells a failed lane apart from an empty queue', () => {
  it('a failed approvals read surfaces an alert + Retry, never "All clear"', async () => {
    mockApi({ approvals: boom })
    await mount()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names the lane that failed').toMatch(/pending approvals/i)
    expect(screen.getByRole('button', { name: /Retry/i }), 'and offers recovery').toBeInTheDocument()
    // The crux: a swallowed safety-relevant approval must not read as "nothing waiting on you".
    expect(screen.queryByText(ALL_CLEAR), 'a failed lane is not an empty queue').toBeNull()
  })

  it('Retry re-runs just that lane and clears the failure', async () => {
    let ok = false
    const appr = { id: 'a1', tool: 'shell', tool_purpose: 'run ls', source: '', session: 's1' }
    mockApi({ approvals: () => (ok ? Promise.resolve([appr]) : Promise.reject(new Error('down'))) })
    await mount()
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    ok = true
    await userEvent.click(screen.getByRole('button', { name: /Retry/i }))
    // The recovered read renders the row that the failure had hidden, and the alert clears.
    await waitFor(() => expect(screen.getByText('Run shell')).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a successful retry clears the failure').toBeNull()
  })

  it('a failed lane does not bury the lanes that loaded', async () => {
    const item = { id: 'i1', sender_name: 'Alice from Ops', message: 'ping' }
    mockApi({ approvals: boom, inboxPending: () => Promise.resolve([item]) })
    await mount()
    // The failed approvals lane is announced …
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent).toMatch(/pending approvals/i)
    // … AND the healthy inbox lane still renders its item (a partial failure is not a blank queue).
    expect(screen.getByRole('button', { name: /^Reply:/ }), 'the loaded inbox row survives').toBeInTheDocument()
    expect(screen.queryByText(ALL_CLEAR)).toBeNull()
  })

  it('a genuinely empty queue still says "All clear", with no alert', async () => {
    // THE OTHER DIRECTION: the fix must not turn every empty lane into an error.
    mockApi({})
    await mount()
    await waitFor(() => expect(screen.getByText(ALL_CLEAR)).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty queue is not a failure').toBeNull()
    expect(screen.queryByRole('button', { name: /Retry/i })).toBeNull()
  })
})

describe('the lane failures reach the queue instead of being swallowed', () => {
  it('DashboardLive publishes each lane error + retry, and the silent catch is gone', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/dashboard/DashboardLive.tsx'), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    // Without these the InlineError rows would be INERT — the error state permanently falsy and the
    // failed branch unreachable, so every DOM test above would pass against a branch that can't fire.
    for (const err of ['approvalsErr', 'inboxErr', 'proposalsErr']) {
      expect(code, `the context carries ${err}`).toMatch(new RegExp(`${err}[,:]`))
    }
    expect(code, 'and the per-lane retries are wired to the loaders')
      .toMatch(/retryApprovals: loadApprovals[\s\S]*retryInbox: loadInbox[\s\S]*retryProposals: loadProposals/)
    // The swallow this fix removes must not come back on any of the three reads.
    for (const call of ['approvals', 'inboxPending', 'skillProposals']) {
      expect(code, `api.${call}() no longer swallows its rejection`)
        .not.toMatch(new RegExp(`api\\.${call}\\(\\)[\\s\\S]{0,90}?catch\\(\\(\\) => \\{\\}\\)`))
    }
  })
})
