import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'

// ── A pending chat approval: ONE actionable row on Home, counted once ─────────────────────────────
//
// Measured before the fix: a chat sat waiting on an approval while Home's approvals count read 0
// and "To triage" listed nothing — the approval existed only inside its chat. The registry now
// lists it (`GET /api/approvals`) AND raises its Inbox row, so it reaches Home on two slices.
// These rails mount the REAL DashboardLiveProvider around the real widgets and pin that:
//   1. To triage shows it ONCE, as the approval row that carries Approve/Reject, saying what the
//      call would do and who is asking — not as that row plus its Inbox listing;
//   2. an Inbox row whose approval is NOT listed stays visible — degrade, never vanish;
//   3. Home's pills count it once: 1 approval waiting, and the inbox pill counts it zero times;
//   4. Approve answers it by its REGISTRY id.

const APPROVAL = {
  id: 'chat-a:1', request_id: '1', source: '', tool: 'bash',
  tool_input: '{"command": "rm -rf /tmp/scratch"}', tool_purpose: '',
  session: 'chat-a', session_title: 'Clean the scratch dir', agent: 'researcher',
  risk: 'destructive', is_read_only: false, grant_agent: '', ts: 1,
}
const row = (id: string, message: string, refs: Record<string, string>, kind = 'agent_request') => ({
  id, channel: 'system', channel_name: 'system', message, sender_id: 'system', sender_name: 'system',
  classification: 'needs_reply', confidence: 'high', status: 'pending', item_kind: kind,
  can_reply: false, refs,
})
const INBOX = [
  row('inb-appr', 'Approval needed: bash — the listing of chat-a:1',
    { approval: 'chat-a:1', session: 'chat-a' }),
  row('inb-stale', 'Approval needed: write_file — its approval is not listed',
    { approval: 'chat-z:9', session: 'chat-z' }),
  { ...row('inb-real', 'hey, can you check the deploy?', {}, 'message'),
    channel: 'slack', channel_name: 'general', sender_name: 'Jordan', can_reply: true },
]

const resolveApproval = vi.fn(() => Promise.resolve({ ok: true }))

function mockApi() {
  vi.resetModules()
  resolveApproval.mockClear()
  vi.doMock('../../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve({ update_available: false }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve({ items: [] }),
      approvals: () => Promise.resolve([APPROVAL]),
      resolveApproval,
      inboxOpen: () => Promise.resolve(INBOX),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [], suppressed: 0 }),
    },
  }))
}

async function mount(Widget: 'ActionCenter' | 'HeroPulse') {
  const { DashboardLiveProvider } = await import('../DashboardLive')
  const mod = Widget === 'ActionCenter' ? await import('./ActionCenter') : await import('./HeroPulse')
  const C = (mod as Record<string, any>)[Widget]
  await act(async () => {
    render(
      <DashboardLiveProvider>
        <C navigate={() => {}} />
      </DashboardLiveProvider>,
    )
    await new Promise((res) => setTimeout(res, 0))
  })
}

describe('To triage lists a pending chat approval once, with enough to decide', () => {
  it('renders the approval row — what it would do, who asks — and not its Inbox listing', async () => {
    mockApi()
    await mount('ActionCenter')
    expect(screen.getByText('Run bash')).toBeTruthy()
    expect(screen.getByText(/rm -rf \/tmp\/scratch.*researcher in “Clean the scratch dir”/)).toBeTruthy()
    expect(screen.queryByText(/the listing of chat-a:1/)).toBeNull()
  })

  it('keeps an Inbox row whose approval is not listed', async () => {
    mockApi()
    await mount('ActionCenter')
    expect(screen.getByText(/its approval is not listed/)).toBeTruthy()
    expect(screen.getByText(/can you check the deploy/)).toBeTruthy()
  })

  it('answers it by its registry id', async () => {
    mockApi()
    await mount('ActionCenter')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Approve: Run bash/ }))
      await new Promise((res) => setTimeout(res, 0))
    })
    expect(resolveApproval).toHaveBeenCalledWith('chat-a:1', 'approve')
  })
})

describe('Home counts a pending approval once', () => {
  it('reads 1 approval waiting, and the inbox pill leaves the approval’s row out', async () => {
    mockApi()
    await mount('HeroPulse')
    expect(screen.getByRole('button', { name: '1 approval waiting' })).toBeTruthy()
    // The stale approval row and the real message — not the listed approval's own row.
    expect(screen.getByRole('button', { name: '2 inbox' })).toBeTruthy()
  })
})
