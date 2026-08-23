// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import type { InboxItem, PendingApproval } from '../../lib/api'

// ── AMBIENT-SURFACES AS-8: Mission Control is a CONTROL surface ──────────────────────────────
//
// The atom's done_when has three clauses and only the first is about rendering. These tests are
// weighted the same way: one covers the four lanes, and the rest cover the two verbs, because a
// four-lane view that lists work without resolving it is the thing this atom exists to replace.
//
// 🔑 THE FAILED-ACTION TEST IS THE LOAD-BEARING ONE. This repo has a whole family of defects
// where a mutation fails and the surface renders as if nothing happened, and here that defect is
// invisible by construction: the "success" rendering of a swallowed failure (card unchanged, item
// still pending on the next read) is byte-identical to a pending card. So the assertion is BOTH
// halves — the words appear AND the card does not read as resolved. Asserting only the first half
// would pass on a card that showed the error and then also claimed success.
//
// `lib/attentionLanes` is mocked at the boundary so this suite runs standalone, and — see the
// vacuity floor at the bottom — so that a component which classified items ITSELF could not pass.

const inboxPending = vi.fn()
const approvals = vi.fn()
const chatSessions = vi.fn()
const resolveApproval = vi.fn()
const resumeWorkflowRun = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  // Keep the real module: `ApiError` is what a failed call throws and the failure copy renders
  // its `.message`, so a stubbed error class would let a broken message path pass.
  ...(await orig<Record<string, unknown>>()),
  api: {
    inboxPending: (...a: unknown[]) => inboxPending(...a),
    approvals: (...a: unknown[]) => approvals(...a),
    chatSessions: (...a: unknown[]) => chatSessions(...a),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...a),
  },
}))

const toLanes = vi.fn()
const laneFor = vi.fn()

// `LANES` is read at module-init time, so it is INLINE here: a factory closing over a top-level
// const of this file throws "Cannot access before initialization" (vi.mock is hoisted above it).
// The two functions are fine to close over — they are only reached when called.
vi.mock('../../lib/attentionLanes', () => ({
  LANES: ['needs-approval', 'your-turn', 'working', 'idle'] as const,
  toLanes: (...a: unknown[]) => toLanes(...a),
  laneFor: (...a: unknown[]) => laneFor(...a),
}))

/** The same four lanes, for assertions. Deliberately a separate literal from the factory's: if the
 *  sibling reorders `LANES`, the order test below fails loudly instead of following it silently. */
const LANES_FIXTURE = ['needs-approval', 'your-turn', 'working', 'idle'] as const

import { MissionControl, LANE_REFS, MISSION_CONTROL_VIEW_ID, questionOf } from './MissionControl'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Fixtures ────────────────────────────────────────────────────────────────────────────────
const approval = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  id: 'appr-1', source: 'chat', tool: 'shell.run', session: 'nightly-sweep', ts: 1, ...over,
})

/** A live session row, shaped the way `ChatSession.to_dict()` sends it — including the two fields
 *  `ChatSessionSummary` does not declare. */
const session = (over: Record<string, unknown> = {}) => ({
  key: 'chat-1', title: 'nightly sweep', messages: 4,
  running: true, stopping: false, pending_approval: false, ...over,
})

/** A parked run's inbox row, shaped the way `workflows/needs_input.card_refs()` writes it. */
const questionItem = (choices: string[] = ['Ship it', 'Hold']): InboxItem => ({
  id: 'inbox-1', channel: 'native', channel_name: 'loop-worker',
  message: 'Which branch should I push?', sender_id: 'agent', sender_name: 'agent',
  classification: 'needs_reply', confidence: 'needs_review', status: 'pending',
  item_kind: 'needs_input',
  refs: {
    workflow: 'run-77',
    workflow_node: 'gate.push',
    resume_token: 'tok-9',
    needs_input: {
      run_id: 'run-77', node_id: 'gate.push', block_kind: 'needs_input',
      blocker: 'Which branch should I push?', choices, resume_token: 'tok-9', actionable: true,
    },
  },
})

/** All four keys, always — `toLanes`' own contract. */
const lanes = (over: Partial<Record<string, unknown[]>> = {}) => ({
  'needs-approval': [], 'your-turn': [], working: [], idle: [], ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  // The data layer is a module-level cache: without this, a lane split from the previous test
  // paints before the new fetch lands and an emptiness assertion could pass on stale bytes.
  resetDataStore()
  inboxPending.mockResolvedValue([])
  approvals.mockResolvedValue([])
  chatSessions.mockResolvedValue([])
  toLanes.mockReturnValue(lanes())
})

// ── Clause 1: four lanes, always ────────────────────────────────────────────────────────────
describe('the four lanes', () => {
  it('renders all four headings even when every lane is empty', async () => {
    render(<MissionControl />)
    await waitFor(() => expect(toLanes).toHaveBeenCalled())

    for (const name of ['Needs approval', 'Your turn', 'Working', 'Idle']) {
      expect(screen.getByRole('heading', { name })).toBeTruthy()
    }
  })

  it('an EMPTY lane says it is empty rather than vanishing', async () => {
    // A lane that disappears when it empties reads as "nothing needs me" — the user cannot tell
    // an empty queue from one that failed to render. So each empty lane states its own emptiness.
    approvals.mockResolvedValue([approval()])
    toLanes.mockReturnValue(lanes({ 'needs-approval': [{ id: 'c1', title: 'shell.run', approval: approval() }] }))
    render(<MissionControl />)

    expect(await screen.findByText('Nothing is waiting on an answer from you.')).toBeTruthy()
    expect(screen.getByText('Nothing is running right now.')).toBeTruthy()
    expect(screen.getByText('Nothing is idle.')).toBeTruthy()
    // ...and the lane that is NOT empty says no such thing.
    expect(screen.queryByText('Nothing is waiting on your approval.')).toBeNull()
  })
})

// ── Clause 2: approving from a lane resolves the approval ───────────────────────────────────
describe('approving from a lane', () => {
  const card = { id: 'c1', title: 'shell.run', detail: 'rm -rf ./build', approval: approval() }

  beforeEach(() => {
    approvals.mockResolvedValue([approval()])
    toLanes.mockReturnValue(lanes({ 'needs-approval': [card] }))
  })

  it('names WHICH item the approve button acts on', async () => {
    render(<MissionControl />)
    // Asked through the accessibility tree, not by class or test id: "Approve" alone is ambiguous
    // the moment two cards are on screen, and this view guarantees four lanes of them.
    const btn = await screen.findByRole('button', { name: /^Approve .*shell\.run/ })
    expect(btn).toBeTruthy()
    // The reject verb is distinguishable from approve by NAME, not only by position.
    expect(screen.getByRole('button', { name: /^Reject .*shell\.run/ })).toBeTruthy()
  })

  it('POSTs the approval id and the approve action, then shows the card resolved', async () => {
    resolveApproval.mockResolvedValue({ ok: true })
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))

    // The call itself — not merely that a handler ran.
    expect(resolveApproval).toHaveBeenCalledWith('appr-1', 'approve')
    // ...and the outcome is on the card, announced, in words.
    expect(await screen.findByRole('status')).toHaveTextContent('Approved.')
    // The verbs are gone, so a resolved id cannot be approved a second time.
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull())
  })

  it('rejecting posts the reject action', async () => {
    resolveApproval.mockResolvedValue({ ok: true })
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /^Reject/ }))
    expect(resolveApproval).toHaveBeenCalledWith('appr-1', 'reject')
  })

  it('a FAILED approve says so on the card, and the card does NOT read as resolved', async () => {
    // The defect family this test exists for: the call fails, the surface renders nothing, and the
    // user clicks again — on an id whose approval may already have landed.
    resolveApproval.mockRejectedValue(new Error('approval appr-1 has expired'))
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))

    const alert = await screen.findByRole('alert')
    // The gateway's own sentence, verbatim — it is the only part that says which knob to turn.
    expect(alert).toHaveTextContent('approval appr-1 has expired')
    // ...and something to do about it.
    expect(alert).toHaveTextContent(/try again/i)
    // NOT resolved: no success announcement, and the verb is still there to retry with.
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByRole('button', { name: /^Approve/ })).toBeTruthy()
  })
})

// ── Clause 3: a question answered from a card unblocks its loop ─────────────────────────────
describe('answering a pending question', () => {
  const card = { id: 'q1', title: 'loop-worker', item: questionItem() }

  beforeEach(() => {
    inboxPending.mockResolvedValue([questionItem()])
    toLanes.mockReturnValue(lanes({ 'your-turn': [card] }))
  })

  it('renders the options as buttons, each naming its item', async () => {
    render(<MissionControl />)
    expect(await screen.findByRole('button', { name: /^Answer .*Ship it$/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Answer .*Hold$/ })).toBeTruthy()
    // The question itself is on the card — an option list with no question is a guess.
    expect(screen.getByText('Which branch should I push?')).toBeTruthy()
  })

  it('answering resumes the run with the CHOSEN option and its resume token', async () => {
    resumeWorkflowRun.mockResolvedValue({ resumed: true })
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /Hold$/ }))

    expect(resumeWorkflowRun).toHaveBeenCalledWith('run-77', { answer: 'Hold', resume_token: 'tok-9' })
    expect(await screen.findByRole('status')).toHaveTextContent('the run is moving again')
  })

  it('a FAILED answer says so and keeps the options clickable', async () => {
    resumeWorkflowRun.mockRejectedValue(new Error('run-77 is no longer parked'))
    render(<MissionControl />)
    await userEvent.click(await screen.findByRole('button', { name: /Ship it$/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('run-77 is no longer parked')
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByRole('button', { name: /Ship it$/ })).toBeTruthy()
  })

  it('a question with NO options says where to answer it instead of faking a text box', async () => {
    // The wire DOES carry `choices[]`, but a freeform gate legitimately has none. A text box here
    // would submit prose the run's gate never offered.
    const bare = { id: 'q2', title: 'loop-worker', item: questionItem([]) }
    toLanes.mockReturnValue(lanes({ 'your-turn': [bare] }))
    render(<MissionControl />)

    expect(await screen.findByText(/no preset options/)).toBeTruthy()
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: /^Answer/ })).toBeNull()
  })
})

// ── The read path ───────────────────────────────────────────────────────────────────────────
describe('a failed read', () => {
  it('says it could not load rather than painting four empty lanes', async () => {
    // Four empty lanes is this surface's "all clear" — the single most misleading thing a failed
    // read could show.
    approvals.mockRejectedValue(new Error('gateway is restarting'))
    render(<MissionControl />)
    expect(await screen.findByRole('alert')).toHaveTextContent('gateway is restarting')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })
})

// ── The vacuity floor ───────────────────────────────────────────────────────────────────────
describe('the lane split comes from lib/attentionLanes, not from this view', () => {
  it('consults the mocked toLanes with the items, the approvals AND the session activity', async () => {
    // Without this, a component that classified items itself — ignoring the sibling entirely —
    // would pass every test above, because the fixtures would still be on screen.
    const item = questionItem()
    const appr = approval()
    inboxPending.mockResolvedValue([item])
    approvals.mockResolvedValue([appr])
    chatSessions.mockResolvedValue([session()])
    render(<MissionControl />)

    await waitFor(() =>
      expect(toLanes).toHaveBeenCalledWith(
        [item],
        [appr],
        [{ key: 'chat-1', title: 'nightly sweep', running: true, stopping: false, pending_approval: false }],
      ),
    )
  })

  it('hands the three lists over UNMERGED — a mirrored approval is on the wire twice', async () => {
    // `_mirror_approval_to_inbox()` raises an `agent_request` row carrying `refs.approval` equal to
    // the PendingApproval id, so concatenating the lists here would double-count every mirrored
    // approval. Suppressing the duplicate is the sibling's job; this view must not pre-merge.
    const appr = approval({ id: 'appr-9' })
    const mirror: InboxItem = {
      ...questionItem(), id: 'inbox-mirror', item_kind: 'agent_request',
      refs: { session: 'chat-1', approval: 'appr-9' },
    }
    inboxPending.mockResolvedValue([mirror])
    approvals.mockResolvedValue([appr])
    render(<MissionControl />)

    await waitFor(() => expect(toLanes).toHaveBeenCalled())
    const [gotItems, gotApprovals] = toLanes.mock.calls[toLanes.mock.calls.length - 1]
    // Each list arrives whole and separate: no concatenation, no de-duplication done here.
    expect(gotItems).toEqual([mirror])
    expect(gotApprovals).toEqual([appr])
  })

  it('normalizes a session the list endpoint typed WITHOUT stopping/pending_approval', async () => {
    // `ChatSessionSummary` declares neither field and types `running` as optional, while the wire
    // carries all three for a live session. Absent must become `false`, not `undefined`: the
    // sibling's input type declares them required, and a disk-only session is not running.
    chatSessions.mockResolvedValue([{ key: 'chat-2', title: 'old chat', messages: 3 }])
    render(<MissionControl />)

    await waitFor(() => expect(toLanes).toHaveBeenCalled())
    const activity = toLanes.mock.calls[toLanes.mock.calls.length - 1][2]
    expect(activity).toEqual([
      { key: 'chat-2', title: 'old chat', running: false, stopping: false, pending_approval: false },
    ])
  })

  it('feeds the Working lane from the session activity, not from the attention store', async () => {
    // `laneFor` never returns 'working' — the attention store has no in-flight status — so this
    // argument is the ONLY path to that lane. Without it the lane renders permanently empty while
    // wearing a confident label, and the atom's four lanes are really three.
    chatSessions.mockResolvedValue([session()])
    toLanes.mockReturnValue(lanes({ working: [{ id: 'session:chat-1', title: 'nightly sweep' }] }))
    render(<MissionControl />)

    expect(await screen.findByText('nightly sweep')).toBeTruthy()
    expect(screen.queryByText('Nothing is running right now.')).toBeNull()
  })

  it('renders the lanes the sibling returned, in the sibling’s declared order', async () => {
    toLanes.mockReturnValue(lanes({ working: [{ id: 'w1', title: 'nightly sweep' }] }))
    render(<MissionControl />)
    await screen.findByText('nightly sweep')

    const headings = screen.getAllByRole('heading').map((h) => h.textContent)
    expect(headings).toEqual(['Mission Control', 'Needs approval', 'Your turn', 'Working', 'Idle'])
  })
})

// ── The view-registry seam ──────────────────────────────────────────────────────────────────
describe('LANE_REFS — the one reconciliation point with views_store', () => {
  it('covers every lane exactly once, in the sibling’s order', () => {
    // A preset ref map that silently omits a lane registers a view with a hole in it.
    expect(Object.keys(LANE_REFS)).toEqual([...LANES_FIXTURE])
    expect(new Set(Object.values(LANE_REFS)).size).toBe(LANES_FIXTURE.length)
    for (const ref of Object.values(LANE_REFS)) expect(ref.startsWith('core:')).toBe(true)
  })
})

describe('questionOf — reading the options off the wire', () => {
  it('reads choices and the resume token from refs.needs_input', () => {
    const q = questionOf(questionItem(['a', 'b']))
    expect(q).toMatchObject({ runId: 'run-77', resumeToken: 'tok-9', choices: ['a', 'b'] })
  })

  it('is null for a row that carries no needs_input card', () => {
    // A row raised before this contract existed has no card; synthesizing an empty one would put
    // a blank decision in front of the user.
    expect(questionOf({ ...questionItem(), refs: { workflow: 'run-1' } })).toBeNull()
    expect(questionOf({ ...questionItem(), refs: undefined })).toBeNull()
    expect(questionOf(undefined)).toBeNull()
  })
})

// ── The mount ────────────────────────────────────────────────────────────────
//
// Everything above tests the component in isolation, which is silent about the one thing the
// atom's `done_when` actually requires: that a locked Mission Control view RENDERS. A page
// component that nothing routes to renders nowhere. This file's own suite passed at full green
// while `App.tsx` had never heard of it.
//
// Parsed from source for the reason `design/routeManifestParity.test.ts` gives: importing
// `App.tsx` pulls in the whole SPA, and the assertion is about a switch case and a set — not
// about rendering the shell.
describe('the route is mounted in the shell', () => {
  const app = readFileSync(join(process.cwd(), 'src/app/App.tsx'), 'utf8')

  it('parses App.tsx (guards against a vacuous sweep)', () => {
    expect(app.length).toBeGreaterThan(1000)
    expect(app).toContain('function renderPage')
  })

  it('lazy-imports the page', () => {
    expect(app).toMatch(/const MissionControl = lazy\(/)
  })

  it(`dispatches '${MISSION_CONTROL_VIEW_ID}' to it, not to the coming-soon fallback`, () => {
    // Without the case, the id falls through to renderPage's default — a "— coming soon"
    // placeholder that looks like a deliberately unbuilt page rather than a broken route.
    expect(app).toMatch(new RegExp(`case '${MISSION_CONTROL_VIEW_ID}': return <MissionControl`))
  })

  it('is in ROUTABLE, so the hash route is not rejected before it renders', () => {
    const routable = app.match(/const ROUTABLE = new Set\(\[(.*?)\]\)/s)
    expect(routable, 'the ROUTABLE literal moved — re-point this rail').toBeTruthy()
    expect(routable![1]).toContain(`'${MISSION_CONTROL_VIEW_ID}'`)
  })

  it('is reachable by a user, via the command palette', () => {
    // It is deliberately NOT in NAV (it is a server-registered dashboard VIEW, and nothing yet
    // reads the registry's `nav_pinned`/`icon`). The palette is therefore the only door, so a
    // missing entry means a surface only a URL-typer can find.
    expect(app).toMatch(/id: 'go:mission-control'/)
    expect(app).toMatch(/label: 'Mission Control'/)
  })
})
