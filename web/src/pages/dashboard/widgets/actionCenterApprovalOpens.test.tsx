import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'

// ── #258's THIRD 404: the one widget that could resolve the approval also opened nowhere ──────────
//
// The issue names three surfaces, and the Action Center is the one it calls out as working:
// *"only the inline Approve/Reject buttons work"* — because its row's OPEN target was
// `chat/${session}` for every approval (`ActionCenter.tsx:105` as filed). A workflow stage's
// approval carries `workflow:<run>:<node>`, which is not a chat, so a user who opened the row
// instead of pressing Approve landed on the same 404 the toast used to send them to.
//
// 🪤 ASSERTING THE ROW RENDERS WOULD PROVE NOTHING. The row already rendered; it is where it WENT
// that was wrong. The approval is now the one approval card, and it opens where it came from
// through its source link, so these legs assert where that link goes — and the chat leg below is
// the vacuity floor, because returning the run route unconditionally would satisfy the first leg
// while breaking every ordinary chat approval in the widget.

const approvals = vi.fn()

function mockApi() {
  vi.resetModules()
  vi.doMock('../../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve({ update_available: false }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve({ items: [] }),
      approvals,
      inboxOpen: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [], suppressed: 0 }),
    },
  }))
}

/** Mount the real Action Center over one pending approval. */
async function mountWith(session: string, sourceLabel: string) {
  cleanup()
  approvals.mockReset()
  approvals.mockResolvedValue([{
    id: 'spawn:b961a327', source: 'subagent',
    tool: 'subagent_run(Write ONE consolidated article)',
    tool_purpose: 'Consolidate the recalled material into one article',
    session, ts: 0, source_label: sourceLabel,
  }])
  mockApi()
  const { DashboardLiveProvider } = await import('../DashboardLive')
  const { ActionCenter } = await import('./ActionCenter')
  await act(async () => {
    render(
      <DashboardLiveProvider>
        {/* The full `RouteProps`: the widget's contract is the whole shape and `tsc` is right to
            insist (the sibling dedup rail escapes it only by indexing the module as `any`). */}
        <ActionCenter sub="" navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />
      </DashboardLiveProvider>,
    )
    await new Promise((res) => setTimeout(res, 0))
  })
}

/** Where the card's source link goes: the control a user activates to open the work that asked. */
async function whereItOpens(sourceLabel: string): Promise<string> {
  const link = await screen.findByRole('link', { name: `Open ${sourceLabel}` })
  return link.getAttribute('href') ?? ''
}

beforeEach(() => { cleanup() })

describe('opening an Action Center approval row lands where it can be answered', () => {
  it('a workflow stage approval opens the RUN, on the blocked node', async () => {
    const step = 'workflow “knowledge-synthesis” · step “synthesize”'
    await mountWith('workflow:11b9a34c:synthesize', step)
    // The same destination the nudge links to — one parse, so the widget and the toast cannot
    // disagree about where a given approval is answered.
    expect(await whereItOpens(step)).toBe('#/workflows/runs/11b9a34c?node=synthesize')
  })

  it('🪤 VACUITY: an ordinary chat approval still opens its chat', async () => {
    // Without this leg, hard-coding the run route would pass the test above and send every
    // chat/subagent approval in the widget to a workflow run that does not exist.
    await mountWith('main', 'chat “Main”')
    expect(await whereItOpens('chat “Main”')).toBe('#/chat/main')
  })

  it('🪤 and it never navigates to a chat route for a workflow session', async () => {
    // The defect stated directly: the shipped call was `chat/workflow%3A11b9a34c%3Asynthesize`.
    const step = 'workflow “knowledge-synthesis” · step “synthesize”'
    await mountWith('workflow:11b9a34c:synthesize', step)
    expect(await whereItOpens(step)).not.toMatch(/^#?\/?chat\//)
  })
})
