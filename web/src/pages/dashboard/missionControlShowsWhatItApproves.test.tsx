// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import type { PendingApproval } from '../../lib/api'

// ── Mission Control's approval card shows what it would allow ──────────────────────────────────
//
// Two calls an Attended loop's workers asked for sat in Needs approval as "Loop · write_file" and
// "Loop · bash", each with Approve and Reject and nothing else: no path, no content, no command.
// Their buttons were named "Approve write_file — Loop — loop-…". The Inbox, the bell, the cockpit,
// the phone and the channel all showed the call; here she approved it blind.
//
// The card is now the one approval card. The wire is the only fake: the real lane derivation folds
// the registry's entries, and what is asserted is what a user reads and presses.

const approvals = vi.fn()
const resolveApproval = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    inboxOpen: () => Promise.resolve([]),
    approvals: (...a: unknown[]) => approvals(...a),
    chatSessions: () => Promise.resolve([]),
    uLoops: () => Promise.resolve([]),
    workflowRuns: () => Promise.resolve({ runs: [], total: 0, limit: 200, offset: 0 }),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))

import { MissionControl } from './MissionControl'

const CONTENT = '## Storage\\n\\nSQLite by default; set DATABASE_URL for Postgres. See [migrations/](migrations/).'
// The registry's input is the call's arguments as the gateway serialises them (`tool_input_to_str`).
const WRITE = `{"path": "docs/README.md", "content": "${CONTENT}"}`

const worker = (over: Partial<PendingApproval>): PendingApproval => ({
  id: 'loop-0a1b2c3d:req-1', request_id: 'req-1', source: '', tool: 'write_file',
  tool_input: WRITE, tool_purpose: '',
  session: 'loop-0a1b2c3d', ts: 100, session_title: '', agent: 'personalclaw-coder', risk: 'caution',
  is_read_only: false, grant_agent: '', source_label: 'loop “Fix the README”',
  // What the call can touch, as the backend composed it from the same reading as its risk.
  blast_radius: { writes: true, network: false, shell: false, saysReadOnly: false, readOnly: false },
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  approvals.mockResolvedValue([
    worker({}),
    worker({
      id: 'loop-0a1b2c3d-t-4fe5:req-2', request_id: 'req-2', tool: 'bash', risk: 'destructive',
      session: 'loop-0a1b2c3d-t-4fe5', tool_input: '{"command": "git status --short"}',
      blast_radius: { writes: false, network: false, shell: true, saysReadOnly: false, readOnly: true },
    }),
  ])
})

const cardFor = async (tool: string) =>
  within(screen.getByRole('region', { name: 'Needs approval' }))
    .findByRole('group', { name: `Permission needed to run ${tool}` })

describe("Mission Control's approval card shows what it would allow", () => {
  it('🔴 shows the path a write_file would write, and all of its content a click away', async () => {
    render(<MissionControl />)
    const card = await cardFor('write_file')
    expect(within(card).getByText(/^write_file\(\{"path": "docs\/README\.md"/)).toBeTruthy()
    expect(within(card).getByText('Caution')).toBeTruthy()
    expect(within(card).getByRole('list', { name: /What this can touch/ }).textContent).toBe('Writes files')
    await userEvent.click(within(card).getByRole('button', { name: 'Show all of what write_file would run' }))
    expect(within(card).getByRole('group', { name: 'Tool arguments' }).textContent).toBe(WRITE)
  })

  it('shows the command a bash would run', async () => {
    render(<MissionControl />)
    const card = await cardFor('bash')
    expect(within(card).getByText('bash({"command": "git status --short"})')).toBeTruthy()
    expect(within(card).getByText('Destructive')).toBeTruthy()
  })

  it('says which loop asked, and opens it', async () => {
    render(<MissionControl />)
    const card = await cardFor('write_file')
    const from = within(card).getByRole('link', { name: 'Open loop “Fix the README”' })
    expect(from.getAttribute('href')).toBe('#/loops/0a1b2c3d')
    // The session key is not a line a person reads, on the card or in its buttons' names.
    expect(within(card).queryByText(/loop-0a1b2c3d/)).toBeNull()
    for (const b of within(card).getAllByRole('button')) {
      expect(b.getAttribute('aria-label') ?? '').not.toMatch(/loop-0a1b2c3d/)
    }
  })

  it('Allow answers that call by its registry id', async () => {
    resolveApproval.mockResolvedValue({ ok: true })
    render(<MissionControl />)
    const card = await cardFor('write_file')
    await userEvent.click(within(card).getByRole('button', { name: /^Allow write_file — loop “Fix the README”/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledWith('loop-0a1b2c3d:req-1', 'approve'))
  })
})
