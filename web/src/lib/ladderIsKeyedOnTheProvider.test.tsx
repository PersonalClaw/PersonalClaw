import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'

// ── The autonomy ladder is keyed on the ACTION PROVIDER, never on a tool's name ─────────────────
//
// A rung says what a dispatched action may do on its own, and it is declared per action provider
// (`guardrails/rungs.py`: `action.execute_code` claims the providers `bash` and `run-script`). A
// tool can carry the very same word: `bash` is also the chat's shell tool. The chat's approval
// card and the phone's approval queue looked the TOOL's name up in the provider index, so a `bash`
// call that was waiting for permission wore the `bash` provider's chip — "runs on its own".
//
// A tool call dispatches no action provider, so no rung describes it: neither approval surface
// shows one, whatever the tool is called. A trigger row holds the provider it dispatches, and
// still shows that provider's rung — the same ladder, read by the key it is declared under.

// The ladder as `GET /api/autonomy` serves it, for three providers whose names are also tool
// names. The rung words are `RUNG_LABELS`/`RUNG_HINTS`, in the server's own case.
const LADDER = {
  rungs: ['draft_only', 'one_tap', 'auto_with_undo', 'autonomous'],
  rung_meta: [
    { key: 'draft_only', label: 'drafts only', hint: 'Never executes on its own. Files a proposal describing what it would do.' },
    { key: 'one_tap', label: 'asks first', hint: 'Never executes on its own. Raises a request for you to decide.' },
    { key: 'auto_with_undo', label: 'runs with undo', hint: 'Executes, then tells you and keeps a handle so you can undo it.' },
    { key: 'autonomous', label: 'runs on its own', hint: 'Executes silently. The audit log is the record.' },
  ],
  incident_active: false,
  reversals: [],
  types: [
    { key: 'action.execute_code', floor: 'autonomous', ceiling: 'autonomous', leaves_machine: true, providers: ['bash', 'run-script'], resolved_rung: 'autonomous', granted_rung: 'autonomous', held_by_incident: false, authority: 'Declared floor.', granted_at: '' },
    { key: 'action.notify', floor: 'autonomous', ceiling: 'autonomous', leaves_machine: false, providers: ['notify'], resolved_rung: 'autonomous', granted_rung: 'autonomous', held_by_incident: false, authority: 'Declared floor.', granted_at: '' },
    { key: 'action.browse', floor: 'one_tap', ceiling: 'one_tap', leaves_machine: true, providers: ['browse'], resolved_rung: 'one_tap', granted_rung: 'one_tap', held_by_incident: false, authority: 'Declared floor.', granted_at: '' },
  ],
}
const RUNG_WORDS = LADDER.rung_meta.map((r) => r.label)
const TOOLS_NAMED_LIKE_PROVIDERS = ['bash', 'notify', 'browse']

function mockApi(stubs: Record<string, unknown>) {
  vi.doMock('./api', async (orig) => {
    const real = await orig<typeof import('./api')>()
    return { ...real, api: { ...real.api, autonomyLadder: () => Promise.resolve(LADDER), ...stubs } }
  })
  vi.doMock('./useChatSocket', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    useChatSocket: () => {},
  }))
}

/** The ladder already in the cache, so a surface that reads it has it on its first paint: an
 *  absent chip below is the surface's answer, not a read still in flight. */
async function seedLadder() {
  const { writeQuery } = await import('./data')
  writeQuery('autonomy:ladder', LADDER, true)
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
})

describe('a tool approval shows no rung, even for a tool named like a provider', () => {
  it.each(TOOLS_NAMED_LIKE_PROVIDERS)('the chat card for %s', async (tool) => {
    mockApi({})
    await seedLadder()
    const { ApprovalCard } = await import('../pages/chat/ApprovalCard')
    render(<ApprovalCard seg={{ kind: 'approval', id: 'a1', tool, input: 'echo hi', risk: 'caution' }} onAct={() => {}} />)
    const card = screen.getByRole('group', { name: `Permission needed to run ${tool}` })
    // The risk chip is still there: the two chips are independent facts.
    expect(within(card).getByText('Caution')).toBeInTheDocument()
    for (const word of RUNG_WORDS) expect(card.textContent).not.toContain(word)
  })

  it.each(TOOLS_NAMED_LIKE_PROVIDERS)('the phone queue for %s', async (tool) => {
    mockApi({
      approvals: () => Promise.resolve([{
        id: `ap-${tool}`, request_id: `ap-${tool}`, source: 'subagent', tool, tool_input: 'echo hi',
        tool_purpose: '', session: 'dashboard:mine', ts: Math.round(Date.now() / 1000),
        session_title: '', agent: '', risk: '', grant_agent: '',
      }]),
      uLoops: () => Promise.resolve([]),
      tasks: () => Promise.resolve({ tasks: [], total: 0 }),
      inboxOpen: () => Promise.resolve([]),
      notifications: () => Promise.resolve({ notifications: [], unread: 0 }),
    })
    await seedLadder()
    const { CompanionPage } = await import('../pages/companion/CompanionPage')
    render(<CompanionPage sub="" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={vi.fn()} />)
    const card = await screen.findByRole('group', { name: `Permission needed to run ${tool}` })
    for (const word of RUNG_WORDS) expect(card.textContent).not.toContain(word)
  })
})

describe('a trigger row shows the rung of the provider it dispatches', () => {
  it('a bash action runs on its own', async () => {
    mockApi({
      schedules: () => Promise.resolve({ jobs: [] }),
      hooks: () => Promise.resolve([]),
      storeTriggers: () => Promise.resolve([{
        kind: 'store', store_kind: 'file', id: 'store:file:tidy', raw_id: 'file:tidy', name: 'Tidy downloads',
        enabled: true, spec: {}, action: { provider: 'bash', config: { command: 'echo hi' } }, health: 'healthy',
        state: 'active', run_count: 0, last_error: '', broken: [], warnings: [],
      }]),
      callbacks: () => Promise.resolve([]),
      triggerReview: () => Promise.resolve([]),
      actionProviders: () => Promise.resolve([]),
      triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
    })
    await seedLadder()
    const { TriggersSection } = await import('../pages/triggers/TriggersSection')
    render(<TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={() => {}} />)
    await waitFor(() => expect(screen.getByText('Tidy downloads')).toBeInTheDocument())
    const chip = await screen.findByText('runs on its own')
    expect(chip.closest('[title]')?.getAttribute('title')).toContain('action.execute_code')
  })
})
