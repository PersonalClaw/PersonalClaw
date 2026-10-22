// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import type { InboxItem, SkillProposal } from '../../lib/api'
import { proposalSummary, proposalTitle } from '../../lib/attentionLanes'
import { HomeHarness, gateItem, waitingItem, waitingList } from '../../test/homeHarness'

// ── Home answers each ask at its own route ──────────────────────────────────────────────────────
//
// Needs You is a CONTROL surface: the answer is on the card. Every answer goes through the same
// `api` call the item's own page makes, to the route the waiting list names for it (`answer.path`),
// so no answer is invented here. A resolved card looks resolved when its answer returns; a failed
// one says why in the gateway's words and keeps its verbs; an ask that ended elsewhere says that
// nothing ran rather than "try again".

const waiting = vi.fn()
const inboxOpen = vi.fn()
const skillProposals = vi.fn()
const resolveApproval = vi.fn()
const resumeWorkflowRun = vi.fn()
const confirmWorkflowRun = vi.fn()
const answerTriggerPark = vi.fn()
const browseGrantResolve = vi.fn()
const answerBridgeConfirmation = vi.fn()
const acceptSkillProposal = vi.fn()
const rejectSkillProposal = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => inboxOpen(),
    skillProposals: () => skillProposals(),
    status: () => Promise.resolve({}),
    running: () => Promise.resolve({ running: [], finished: [] }),
    chatSessions: () => Promise.resolve([]),
    resolveApproval: (...a: unknown[]) => resolveApproval(...a),
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...a),
    confirmWorkflowRun: (...a: unknown[]) => confirmWorkflowRun(...a),
    answerTriggerPark: (...a: unknown[]) => answerTriggerPark(...a),
    browseGrantResolve: (...a: unknown[]) => browseGrantResolve(...a),
    answerBridgeConfirmation: (...a: unknown[]) => answerBridgeConfirmation(...a),
    acceptSkillProposal: (...a: unknown[]) => acceptSkillProposal(...a),
    rejectSkillProposal: (...a: unknown[]) => rejectSkillProposal(...a),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))
vi.mock('./widgets/Suggestions', () => ({ Suggestions: () => null }))

const ApiErrorOf = async () => (await import('../../lib/api')).ApiError

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  waiting.mockResolvedValue(waitingList())
  inboxOpen.mockResolvedValue([])
  skillProposals.mockResolvedValue({ proposals: [], lastReview: null })
  for (const f of [resolveApproval, resumeWorkflowRun, confirmWorkflowRun, answerTriggerPark, browseGrantResolve,
    answerBridgeConfirmation, acceptSkillProposal, rejectSkillProposal]) f.mockResolvedValue({ ok: true })
})

const needsYou = () => screen.getByRole('region', { name: 'Needs You' })

/** A loop worker's write, as the registry's row names it for its card. */
const WRITE = '{"path": "docs/README.md", "content": "# PersonalClaw\\n\\nA local agent."}'
const loopWrite = waitingItem({
  id: 'approval:loop-0a1b2c3d:req-1', work: 'loop:0a1b2c3d', session: '', risk: 'caution',
  brief: {
    tool: 'write_file', tool_input: WRITE, source_label: 'loop “Fix the README”', confirms: [],
    blast_radius: { writes: true, network: false, shell: false, saysReadOnly: false, readOnly: false },
  },
  answer: { method: 'POST', path: '/api/approvals/loop-0a1b2c3d%3Areq-1/{answer}', takes: ['approve', 'reject'], body: {} },
  route: '#/loops/0a1b2c3d',
})

describe('an approval', () => {
  const card = (tool: string) => within(needsYou()).findByRole('group', { name: `Permission needed to run ${tool}` })

  it('shows what it would allow, the whole input a click away, and which loop asked', async () => {
    waiting.mockResolvedValue(waitingList([loopWrite]))
    render(<HomeHarness />)
    const c = await card('write_file')
    expect(within(c).getByText(/^write_file\(\{"path": "docs\/README\.md"/)).toBeTruthy()
    expect(within(c).getByText('Caution')).toBeTruthy()
    expect(within(c).getByRole('list', { name: /What this can touch/ }).textContent).toBe('Writes files')
    await userEvent.click(within(c).getByRole('button', { name: 'Show all of what write_file would run' }))
    expect(within(c).getByRole('group', { name: 'Tool arguments' }).textContent).toBe(WRITE)
    expect(within(c).getByRole('link', { name: 'Open loop “Fix the README”' }).getAttribute('href')).toBe('#/loops/0a1b2c3d')
  })

  it('Allow answers the call by the registry id its own route names, and the card reads answered', async () => {
    waiting.mockResolvedValue(waitingList([loopWrite]))
    render(<HomeHarness />)
    await userEvent.click(within(await card('write_file')).getByRole('button', { name: /^Allow write_file/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledWith('loop-0a1b2c3d:req-1', 'approve'))
    expect(await within(needsYou()).findByText('Approved.')).toBeTruthy()
  })

  it('Deny sends the reject', async () => {
    waiting.mockResolvedValue(waitingList([loopWrite]))
    render(<HomeHarness />)
    await userEvent.click(within(await card('write_file')).getByRole('button', { name: /^Deny write_file/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledWith('loop-0a1b2c3d:req-1', 'reject'))
  })

  it('a failed answer says why and leaves the card answerable, and a second press sends again', async () => {
    resolveApproval.mockRejectedValueOnce(new Error('the gateway is restarting'))
    waiting.mockResolvedValue(waitingList([loopWrite]))
    render(<HomeHarness />)
    const allow = () => within(needsYou()).getByRole('button', { name: /^Allow write_file/ })
    await card('write_file')
    await userEvent.click(allow())
    expect(await within(needsYou()).findByText(/Could not approve this: the gateway is restarting\. Nothing was recorded/)).toBeTruthy()
    await userEvent.click(allow())
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledTimes(2))
  })

  it('says nothing ran when the work that asked has ended, not "try again"', async () => {
    const ApiError = await ApiErrorOf()
    resolveApproval.mockRejectedValue(new ApiError('The loop that asked has ended, so nothing ran.', 409, 'approval_owner_ended'))
    waiting.mockResolvedValue(waitingList([loopWrite]))
    render(<HomeHarness />)
    await userEvent.click(within(await card('write_file')).getByRole('button', { name: /^Allow write_file/ }))
    expect(await within(needsYou()).findByText('The loop that asked has ended, so nothing ran.')).toBeTruthy()
    expect(within(needsYou()).queryByText(/try again/)).toBeNull()
  })

  it('is not answered here when its route is not an approval’s', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({ answer: { method: 'POST', path: '/api/elsewhere', takes: [], body: {} } })]))
    render(<HomeHarness />)
    expect(await within(needsYou()).findByRole('link', { name: /^Answer it where it asks/ })).toHaveAttribute('href', '#/chat/chat-1')
    expect(within(needsYou()).queryByRole('button', { name: /^Allow/ })).toBeNull()
  })
})

describe('a run’s gate', () => {
  it('offers its options as answers, and resumes the run with the chosen one and its token', async () => {
    waiting.mockResolvedValue(waitingList([gateItem()]))
    render(<HomeHarness />)
    expect(await within(needsYou()).findByText('Workflow · release')).toBeTruthy()
    await userEvent.click(within(needsYou()).getByRole('button', { name: /Ship it$/ }))
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledWith('run-77', { answer: 'Ship it', resume_token: 'tok-9' }))
    expect(await within(needsYou()).findByText('Answered “Ship it” — the run is moving again.')).toBeTruthy()
  })

  it('a failed answer says so and keeps the options clickable', async () => {
    resumeWorkflowRun.mockRejectedValue(new Error('the run moved on'))
    waiting.mockResolvedValue(waitingList([gateItem()]))
    render(<HomeHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /Hold$/ }))
    expect(await within(needsYou()).findByText(/Could not send that answer: the run moved on/)).toBeTruthy()
    expect(within(needsYou()).getByRole('button', { name: /Hold$/ })).toBeEnabled()
  })

  it('with no preset options, says where to answer it instead of faking a text box', async () => {
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'text', prompt: 'Name the release' })]))
    render(<HomeHarness />)
    const link = await within(needsYou()).findByRole('link', { name: /^Open the run/ })
    expect(link.getAttribute('href')).toBe('#/workflows/runs/run-77')
    expect(within(needsYou()).queryByRole('textbox')).toBeNull()
  })

  it('an approval gate is a yes or a no: Approve sends true, Deny false', async () => {
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'approval', prompt: 'Push to main?' })]))
    render(<HomeHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Deny/ }))
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledWith('run-77', { answer: false, resume_token: 'tok-9' }))
  })

  it('a confirmation is answered at its confirm route, with a verb', async () => {
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'approval', prompt: 'Send the email?' }, {
      answer: { method: 'POST', path: '/api/workflows/runs/run-77/confirm', takes: ['approve', 'reject', 'skip', 'quit'], body: { resume_token: 'tok-9' } },
    })]))
    render(<HomeHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Approve/ }))
    await waitFor(() => expect(confirmWorkflowRun).toHaveBeenCalledWith('run-77', { verb: 'approve', resume_token: 'tok-9' }))
    expect(resumeWorkflowRun).not.toHaveBeenCalled()
  })

  it('a parked run is woken, and offers nothing to decide', async () => {
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'event', prompt: 'Waiting for the next check' })]))
    render(<HomeHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Wake .* now$/ }))
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledWith('run-77', { answer: true, resume_token: 'tok-9' }))
    expect(within(needsYou()).queryByRole('button', { name: /^Deny/ })).toBeNull()
  })
})

describe('the other asks', () => {
  it("a trigger's parked action is answered at the trigger, and a refusal says why", async () => {
    answerTriggerPark.mockResolvedValueOnce({ refused: 'the trigger was edited since it asked' })
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'park:t1', kind: 'park', work: 'trigger:t1', session: '', brief: { trigger_name: 'Check my balance', question: 'Send the summary?', card: {} },
      answer: { method: 'POST', path: '/api/triggers/t1/answer', takes: [true, false], body: { resume_token: 'pk' } },
      route: '#/automations?open=t1',
    })]))
    render(<HomeHarness />)
    expect(await within(needsYou()).findByText('Trigger · Check my balance')).toBeTruthy()
    await userEvent.click(within(needsYou()).getByRole('button', { name: /^Approve/ }))
    await waitFor(() => expect(answerTriggerPark).toHaveBeenCalledWith('t1', { resume_token: 'pk', answer: true }))
    expect(await within(needsYou()).findByText(/Could not send that answer: the trigger was edited since it asked/)).toBeTruthy()
    expect(within(needsYou()).getByRole('button', { name: /^Approve/ })).toBeEnabled()
  })

  it('a browser grant is allowed or refused at its own route', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'browse_grant:g1', kind: 'browse_grant', work: '', session: '', brief: { task: 'Order the groceries', scope: 'shop.example' },
      answer: { method: 'POST', path: '/api/browse/grants/g1/{answer}', takes: ['approve', 'reject'], body: {} }, route: '#/dashboard',
    })]))
    render(<HomeHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Refuse Order the groceries/ }))
    await waitFor(() => expect(browseGrantResolve).toHaveBeenCalledWith('g1', 'reject'))
  })

  it('a control bridge action runs once, or is dropped', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'bridge_confirmation:abc', kind: 'bridge_confirmation', work: '', session: '', brief: { action: 'Open the garage', asked_by: 'Home assistant' },
      answer: { method: 'POST', path: '/api/external-access/bridge/confirmations/tok-bridge', takes: [true, false], body: {} }, route: '#/inbox',
    })]))
    render(<HomeHarness />)
    expect(await within(needsYou()).findByText('Home assistant')).toBeTruthy()
    await userEvent.click(within(needsYou()).getByRole('button', { name: /^Run it once/ }))
    await waitFor(() => expect(answerBridgeConfirmation).toHaveBeenCalledWith('tok-bridge', true))
  })

  it('an agent’s question, a loop’s question and a plan open where they ask, and nothing is sent from here', async () => {
    waiting.mockResolvedValue(waitingList([
      waitingItem({ id: 'question:q1', kind: 'question', consequence: 'asks', brief: { questions: [{ question: 'Which branch?' }] },
        answer: { method: 'POST', path: '/api/chat/sessions/chat-1/questions/q1/answer', takes: ['answers', 'skip'], body: {} } }),
      waitingItem({ id: 'loop_question:l1', kind: 'loop_question', work: 'loop:l1', session: '', consequence: 'asks', brief: { name: 'Docs', question: 'Keep the old page?' },
        answer: { method: 'POST', path: '/api/loops/l1/nudge', takes: ['text'], body: {} }, route: '#/loops/l1' }),
      waitingItem({ id: 'plan_review:loop:l2', kind: 'plan_review', work: 'loop:l2', session: '', consequence: 'asks', brief: { name: 'Split', task: 'Split the module' },
        answer: { method: 'PATCH', path: '/api/loops/l2', takes: ['start'], body: { action: 'start' } }, route: '#/loops/l2' }),
    ]))
    render(<HomeHarness />)
    const links = await within(needsYou()).findAllByRole('link', { name: /^Answer it where it asks/ })
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['#/chat/chat-1', '#/loops/l1', '#/loops/l2'])
    expect(within(needsYou()).getByText('Loop · Docs')).toBeTruthy()
    expect(within(needsYou()).getByText('Review the plan: Split the module')).toBeTruthy()
    expect(within(needsYou()).queryAllByRole('button', { name: /^(Approve|Allow|Deny)/ })).toEqual([])
  })
})

describe('a skill proposal and a note, after every waiting item', () => {
  const proposal = (id: string, slug: string, description: string): SkillProposal => ({
    id, slug, description, triggers: '', kind: 'refine', session_key: 's1', created_at: '1970-01-01T00:01:40Z',
    status: 'pending', procedure_preview: '',
  })
  const WRITEUP = proposal('proposal_a', 'incident-writeup', 'Refined after you corrected this turn')
  const mirror = (p: SkillProposal) => ({
    id: `row_${p.id}`, channel: 'skills', channel_name: 'skills', sender_id: 'skills', sender_name: 'skills',
    message: `Refine a skill\n\n${p.slug}`, classification: 'needs_reply', confidence: 'high', status: 'pending',
    item_kind: 'proposal', created_at: 100, refs: { skill_proposal: p.id },
  }) as InboxItem

  it('names the skill, shows it once, and accepts or rejects through the Skills calls', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    skillProposals.mockResolvedValue({ proposals: [WRITEUP], lastReview: null })
    inboxOpen.mockResolvedValue([mirror(WRITEUP), {
      id: 'note-1', channel: 'native', channel_name: '', sender_id: 'me', sender_name: 'me', message: 'Call the bank',
      classification: '', confidence: '', status: 'pending', item_kind: 'user_note', created_at: 200, refs: {},
    } as InboxItem])
    render(<HomeHarness />)
    expect(await within(needsYou()).findByText(proposalTitle(WRITEUP))).toBeTruthy()
    expect(within(needsYou()).getByText(proposalSummary(WRITEUP))).toBeTruthy()
    expect(within(needsYou()).queryByText('Refine a skill')).toBeNull()
    const text = needsYou().textContent ?? ''
    expect(text.indexOf('shell.run')).toBeLessThan(text.indexOf(proposalTitle(WRITEUP)))
    expect(text.indexOf(proposalTitle(WRITEUP))).toBeLessThan(text.indexOf('Call the bank'))
    expect(within(needsYou()).getByRole('link', { name: /^Open it in the Inbox: Call the bank/ })).toHaveAttribute('href', '#/inbox?open=note-1')

    await userEvent.click(within(needsYou()).getByRole('button', { name: new RegExp(`^Accept ${proposalTitle(WRITEUP)}`) }))
    await waitFor(() => expect(acceptSkillProposal).toHaveBeenCalledWith('proposal_a'))
    expect(await within(needsYou()).findByText('Accepted — the skill is saved.')).toBeTruthy()
  })

  it('says why an Accept failed, and keeps the card to try again', async () => {
    acceptSkillProposal.mockRejectedValue(new Error('the skill folder is read-only'))
    skillProposals.mockResolvedValue({ proposals: [WRITEUP], lastReview: null })
    render(<HomeHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: new RegExp(`^Accept ${proposalTitle(WRITEUP)}`) }))
    expect(await within(needsYou()).findByText(/Could not accept this proposal: the skill folder is read-only/)).toBeTruthy()
    expect(within(needsYou()).getByRole('button', { name: new RegExp(`^Reject ${proposalTitle(WRITEUP)}`) })).toBeEnabled()
  })
})

describe('an item is answered on its own route alone', () => {
  it('an item whose route is another kind’s, or climbs out of its own, offers no answer and sends nothing', async () => {
    const asA = (path: string) => ({ method: 'POST', path, takes: ['approve', 'reject'], body: { resume_token: 'tok' } })
    waiting.mockResolvedValue(waitingList([
      // An approval handed a browser grant's route.
      waitingItem({ answer: asA('/api/browse/grants/g-1/{answer}') }),
      // A gate whose run id decodes to `..`, and a park handed a run's route.
      gateItem({ kind: 'approval', prompt: 'Ship v1.2?' }, { answer: asA('/api/workflows/runs/%2E%2E/confirm') }),
      waitingItem({
        id: 'park:t1', kind: 'park', work: 'trigger:t1', session: '', brief: { trigger_name: 'Check my balance', question: 'Send the summary?', card: {} },
        answer: asA('/api/workflows/runs/r1/resume'), route: '#/automations?open=t1',
      }),
    ]))
    render(<HomeHarness />)
    await within(needsYou()).findByText('Ship v1.2?')
    for (const verb of [/^Allow/, /^Deny/, /^Approve/, /^Decline/, /^Confirm/]) {
      expect(within(needsYou()).queryByRole('button', { name: verb })).toBeNull()
    }
    expect(resolveApproval).not.toHaveBeenCalled()
    expect(browseGrantResolve).not.toHaveBeenCalled()
    expect(confirmWorkflowRun).not.toHaveBeenCalled()
    expect(resumeWorkflowRun).not.toHaveBeenCalled()
    expect(answerTriggerPark).not.toHaveBeenCalled()
  })
})
