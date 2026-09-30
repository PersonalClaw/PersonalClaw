import { describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'

// ── Home's To triage names who raised a row, never the notification source it rode ──────────────
//
// A row the platform raised (`emit_attention_item`) carries its notification pair's SOURCE as its
// sender: `app:demo-proposer` for an app's proposal, `loop` for a loop's, a workflow's and a
// trigger's question. To triage printed that as the row's title, so an app's proposal read
// "app:demo-proposer" and every question a run asked read "loop". The row is now named the way
// Mission Control's Your turn names it (`inboxRaisedBy`): the work its refs name, the app by the name
// it goes by, and a channel message by its sender, as before.

const row = (id: string, sender: string, message: string, refs: Record<string, unknown>, kind: string) => ({
  id, channel: sender, channel_name: sender, message, sender_id: sender, sender_name: sender,
  classification: 'needs_reply', confidence: 'high', status: 'pending', item_kind: kind,
  can_reply: false, refs,
})

const INBOX = [
  row('i-app', 'app:demo-proposer', 'Rename the invoices folder?',
    { app: 'demo-proposer', app_display_name: 'Demo Proposer' }, 'proposal'),
  row('i-app-old', 'app:plant-care', 'Water the fern today?', { app: 'plant-care' }, 'proposal'),
  row('i-loop', 'loop', 'Which branch should I push?', { loop: 'abc123', loop_kind: 'general' }, 'needs_input'),
  row('i-gate', 'loop', 'Ship the release to production?',
    { workflow: 'run-1', workflow_name: 'release', workflow_node: 'approve', resume_token: 't1' }, 'needs_input'),
  { ...row('i-msg', 'Jordan', 'hey, can you check the deploy?', {}, 'message'),
    channel: 'slack', channel_name: 'general', can_reply: true },
]

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
      approvals: () => Promise.resolve([]),
      inboxOpen: () => Promise.resolve(INBOX),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [], suppressed: 0 }),
    },
  }))
}

async function mount() {
  const { DashboardLiveProvider } = await import('../DashboardLive')
  // Only `navigate` is read, as in `actionCenterApprovalOnce.test.tsx`.
  const C = (await import('./ActionCenter') as Record<string, any>).ActionCenter
  await act(async () => {
    render(
      <DashboardLiveProvider>
        <C navigate={() => {}} />
      </DashboardLiveProvider>,
    )
    await new Promise((res) => setTimeout(res, 0))
  })
}

describe('To triage names who raised a row', () => {
  it('names an app by the name it goes by, not its notification source', async () => {
    mockApi()
    await mount()
    expect(screen.getByText('Demo Proposer')).toBeTruthy()
    expect(screen.queryByText('app:demo-proposer')).toBeNull()
    // A row raised before its app's name was kept on it still names the app, not the source.
    expect(screen.getByText('plant-care')).toBeTruthy()
    expect(screen.queryByText('app:plant-care')).toBeNull()
  })

  it('names the work a question is for, not the "loop" it rode', async () => {
    mockApi()
    await mount()
    expect(screen.getByText('Loop')).toBeTruthy()
    expect(screen.getByText('Workflow · release')).toBeTruthy()
    expect(screen.queryByText('loop')).toBeNull()
  })

  it('keeps a channel message named by its sender', async () => {
    mockApi()
    await mount()
    expect(screen.getByText('Jordan')).toBeTruthy()
  })
})
