// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { resetDataStore } from '../../lib/data'
import { ApiError, type InboxItem, type LearningProposalReview, type SkillProposal } from '../../lib/api'
import { ARM_MS } from '../../ui/dialog/DialogShell'
import { NeedsYouHarness, gateItem, waitingItem, waitingList } from '../../test/needsYouHarness'

// ── Needs you's cards answer every decision ─────────────────────────────────────────────────────
//
// A proposal is decided on its card in its panel's words: its card's verb and its reject. Every
// learning Accept reads the proposal's review first and sends only what that read allows: an accept
// that replaces or removes asks once more inline, one nothing here can do is its sentence, and a
// read that fails sends nothing. A refusal says what it means for the card by the gateway's code.

const FIXTURE = JSON.parse(readFileSync(join(process.cwd(), 'src/test/fixtures/needsYouKinds.json'), 'utf8')) as {
  learning: { variant: string; card: Record<string, unknown> }[]
}

const waiting = vi.fn()
const inboxOpen = vi.fn()
const skillProposals = vi.fn()
const learningProposalReview = vi.fn()
const acceptLearningProposal = vi.fn()
const rejectLearningProposal = vi.fn()
const skillProposalDetail = vi.fn()
const acceptSkillProposal = vi.fn()
const rejectSkillProposal = vi.fn()
const applyInboxProposal = vi.fn()
const updateInboxItem = vi.fn()
const organizeAccept = vi.fn()
const organizeDecline = vi.fn()
const learningProposals = vi.fn()
const answerChatQuestion = vi.fn()
const uLoopPlanApprove = vi.fn()
const chatPlanApprove = vi.fn()
const uLoopPlanComment = vi.fn()
const uLoopAction = vi.fn()
const uLoopMergeReview = vi.fn()
const uLoopMerge = vi.fn()
const uLoopConflictReview = vi.fn()
const uLoopConflict = vi.fn()
const resumeWorkflowRun = vi.fn()
const cancelWorkflowRun = vi.fn()
const workflowRun = vi.fn()
const forkWorkflowRun = vi.fn()
const startDraftWorkflowRun = vi.fn()
const usageBudget = vi.fn()
const autonomyLadder = vi.fn()
const autonomyGrant = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => inboxOpen(),
    skillProposals: () => skillProposals(),
    status: () => Promise.resolve({}),
    running: () => Promise.resolve({ running: [], finished: [] }),
    chatSessions: () => Promise.resolve([]),
    learningProposalReview: (...a: unknown[]) => learningProposalReview(...a),
    acceptLearningProposal: (...a: unknown[]) => acceptLearningProposal(...a),
    rejectLearningProposal: (...a: unknown[]) => rejectLearningProposal(...a),
    skillProposalDetail: (...a: unknown[]) => skillProposalDetail(...a),
    acceptSkillProposal: (...a: unknown[]) => acceptSkillProposal(...a),
    rejectSkillProposal: (...a: unknown[]) => rejectSkillProposal(...a),
    applyInboxProposal: (...a: unknown[]) => applyInboxProposal(...a),
    updateInboxItem: (...a: unknown[]) => updateInboxItem(...a),
    organizeAccept: (...a: unknown[]) => organizeAccept(...a),
    organizeDecline: (...a: unknown[]) => organizeDecline(...a),
    learningProposals: (...a: unknown[]) => learningProposals(...a),
    answerChatQuestion: (...a: unknown[]) => answerChatQuestion(...a),
    uLoopPlanApprove: (...a: unknown[]) => uLoopPlanApprove(...a),
    chatPlanApprove: (...a: unknown[]) => chatPlanApprove(...a),
    uLoopPlanComment: (...a: unknown[]) => uLoopPlanComment(...a),
    uLoopAction: (...a: unknown[]) => uLoopAction(...a),
    uLoopMergeReview: (...a: unknown[]) => uLoopMergeReview(...a),
    uLoopMerge: (...a: unknown[]) => uLoopMerge(...a),
    uLoopConflictReview: (...a: unknown[]) => uLoopConflictReview(...a),
    uLoopConflict: (...a: unknown[]) => uLoopConflict(...a),
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...a),
    cancelWorkflowRun: (...a: unknown[]) => cancelWorkflowRun(...a),
    workflowRun: (...a: unknown[]) => workflowRun(...a),
    forkWorkflowRun: (...a: unknown[]) => forkWorkflowRun(...a),
    startDraftWorkflowRun: (...a: unknown[]) => startDraftWorkflowRun(...a),
    usageBudget: (...a: unknown[]) => usageBudget(...a),
    autonomyLadder: (...a: unknown[]) => autonomyLadder(...a),
    autonomyGrant: (...a: unknown[]) => autonomyGrant(...a),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  waiting.mockResolvedValue(waitingList())
  inboxOpen.mockResolvedValue([])
  skillProposals.mockResolvedValue({ proposals: [], lastReview: null })
  for (const f of [acceptLearningProposal, rejectLearningProposal, acceptSkillProposal, rejectSkillProposal,
    applyInboxProposal, updateInboxItem, organizeAccept, organizeDecline, uLoopPlanApprove, chatPlanApprove,
    uLoopPlanComment, uLoopAction, uLoopMerge, uLoopConflict, resumeWorkflowRun, cancelWorkflowRun, startDraftWorkflowRun,
    autonomyGrant]) f.mockResolvedValue({ ok: true })
  learningProposals.mockResolvedValue({ proposals: [] })
  answerChatQuestion.mockResolvedValue({ ok: true, outcome: 'answered' })
})

const needsYou = () => screen.getByRole('region', { name: 'Needs you' })

/** An Inbox row as `GET /api/inbox` serves one, with *refs*. */
function row(id: string, refs: Record<string, unknown>, over: Partial<InboxItem> = {}): InboxItem {
  return {
    id, item_kind: 'proposal', status: 'pending', message: 'A proposal', context_summary: '', created_at: 1,
    sender_name: 'learning', channel_name: 'learning', refs, ...over,
  } as unknown as InboxItem
}

/** A learning proposal's row, with its pinned card (or none, a row filed before cards). */
function learning(variant: string | null, pid = `lp-${variant}`): InboxItem {
  const card = variant ? FIXTURE.learning.find((e) => e.variant === variant)!.card : undefined
  return row(`row-${pid}`, card ? { learning_proposal: pid, card } : { learning_proposal: pid })
}

/** The review a proposal's panel reads, its Accept as the card's (or as *accept* says). */
function review(variant: string, accept: Partial<LearningProposalReview['accept']> = {}): LearningProposalReview {
  const card = FIXTURE.learning.find((e) => e.variant === variant)!.card as Record<string, unknown> & {
    accept: LearningProposalReview['accept']
  }
  return {
    id: `lp-${variant}`, kind: 'lesson', variant, status: 'pending', created_at: '', updated_at: '',
    headline: String(card.headline), detail: '', name: '', label: '', why: '', content: { type: 'lesson' },
    accept: { ...card.accept, ...accept }, reject: { verb: 'Reject', sentence: '' },
    session_key: '', source_gone: false, runs: [],
  }
}

/** A press on InlineConfirm's Yes once it has been in view and quiet long enough to answer. */
async function yes(name: string) {
  await act(() => new Promise((r) => setTimeout(r, ARM_MS + 50)))
  await userEvent.click(within(needsYou()).getByRole('button', { name }))
}

describe('a learning proposal is decided on its card', () => {
  it('Save template reads its review, then accepts it; the card says what was done', async () => {
    inboxOpen.mockResolvedValue([learning('template.named')])
    learningProposalReview.mockResolvedValue(review('template.named'))
    render(<NeedsYouHarness />)
    const save = await within(needsYou()).findByRole('button', { name: /^Save template: New template weekly-status-report/ })
    await userEvent.click(save)
    await waitFor(() => expect(acceptLearningProposal).toHaveBeenCalledWith('lp-template.named'))
    expect(learningProposalReview).toHaveBeenCalledWith('lp-template.named')
    expect(await within(needsYou()).findByText('Template saved.')).toBeTruthy()
  })

  it('one that replaces what is yours asks once more in its review’s words, and sends nothing before its yes', async () => {
    inboxOpen.mockResolvedValue([learning('lesson.conflict')])
    learningProposalReview.mockResolvedValue(review('lesson.conflict'))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Use the learned one: / }))
    expect(await within(needsYou()).findByRole('group', { name: 'It replaces yours in your memory.' })).toBeTruthy()
    expect(acceptLearningProposal).not.toHaveBeenCalled()
    await yes('Yes, use the learned one')
    await waitFor(() => expect(acceptLearningProposal).toHaveBeenCalledWith('lp-lesson.conflict'))
    expect(await within(needsYou()).findByText('The learned one replaced yours.')).toBeTruthy()
  })

  it('its reject is named as its panel names it', async () => {
    inboxOpen.mockResolvedValue([learning('lesson.conflict')])
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Keep mine: / }))
    await waitFor(() => expect(rejectLearningProposal).toHaveBeenCalledWith('lp-lesson.conflict'))
    expect(await within(needsYou()).findByText('Kept yours.')).toBeTruthy()
  })

  it('one nothing here can accept is its sentence beside Reject, no Accept', async () => {
    inboxOpen.mockResolvedValue([learning('retirement')])
    render(<NeedsYouHarness />)
    expect(await within(needsYou()).findByText('Nothing removes it from here yet.')).toBeTruthy()
    expect(within(needsYou()).queryByRole('button', { name: /^Accept: / })).toBeNull()
    expect(within(needsYou()).getByRole('button', { name: /^Reject: / })).toBeEnabled()
  })

  it('a change since the list was read is caught: an accept that now does nothing sends nothing', async () => {
    inboxOpen.mockResolvedValue([learning('template.named')])
    learningProposalReview.mockResolvedValue(review('template.named', {
      effect: 'none', verb: '', sentence: 'A workflow already has this name, so it can’t be saved here.',
    }))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Save template: / }))
    expect(await within(needsYou()).findByText('A workflow already has this name, so it can’t be saved here.')).toBeTruthy()
    expect(acceptLearningProposal).not.toHaveBeenCalled()
  })

  it('a row filed before its card always asks once more, since its kind can’t say what Accept does', async () => {
    inboxOpen.mockResolvedValue([learning(null, 'lp-old')])
    learningProposalReview.mockResolvedValue({ ...review('template.named'), id: 'lp-old' })
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Accept: / }))
    expect(await within(needsYou()).findByRole('group', { name: /^Saves the template weekly-status-report/ })).toBeTruthy()
    expect(acceptLearningProposal).not.toHaveBeenCalled()
    await yes('Yes, save template')
    await waitFor(() => expect(acceptLearningProposal).toHaveBeenCalledWith('lp-old'))
  })

  it('a read that fails sends nothing, says so, and keeps its verbs to try again', async () => {
    inboxOpen.mockResolvedValue([learning('template.named')])
    learningProposalReview.mockRejectedValueOnce(new Error('the gateway is restarting'))
    render(<NeedsYouHarness />)
    const save = () => within(needsYou()).findByRole('button', { name: /^Save template: / })
    await userEvent.click(await save())
    expect((await within(needsYou()).findByRole('alert')).textContent)
      .toBe('Could not read this proposal: the gateway is restarting. Nothing was recorded, so it still needs you; try again.')
    expect(acceptLearningProposal).not.toHaveBeenCalled()
    learningProposalReview.mockResolvedValue(review('template.named'))
    await userEvent.click(await save())
    await waitFor(() => expect(acceptLearningProposal).toHaveBeenCalledTimes(1))
  })

  it('Learning off says so and its verbs go', async () => {
    inboxOpen.mockResolvedValue([learning('template.named')])
    learningProposalReview.mockRejectedValue(new ApiError('Learning is turned off.', 404, 'learning_disabled'))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Save template: / }))
    expect(await within(needsYou()).findByText("Learning is off, so this can't be decided here.")).toBeTruthy()
    expect(within(needsYou()).queryByRole('button', { name: /^Save template: |^Reject: / })).toBeNull()
  })

  it('a refused install keeps the card with the gateway’s sentence, no "try again"', async () => {
    inboxOpen.mockResolvedValue([learning('template.named')])
    learningProposalReview.mockResolvedValue(review('template.named'))
    acceptLearningProposal.mockRejectedValue(new ApiError(
      'A workflow named weekly-status-report already exists. Nothing changed, and it is still pending.', 409,
      'learning_proposal_not_installed'))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Save template: / }))
    expect(await within(needsYou()).findByText(
      'A workflow named weekly-status-report already exists. Nothing changed, and it is still pending.')).toBeTruthy()
    expect(within(needsYou()).queryByText(/try again/)).toBeNull()
    expect(within(needsYou()).getByRole('button', { name: /^Save template: / })).toBeEnabled()
  })
})

const REFINE: SkillProposal = {
  id: 'sp-1', slug: 'auto/metrics-summary', description: 'Summarize the week’s metrics', triggers: '', kind: 'refine',
  refine_target: 'metrics-summary', session_key: 's-1', created_at: '2026-11-15T10:00:00Z', status: 'pending', procedure_preview: '',
  card: { headline: '“Always link the metrics dashboard in the summary”', detail: 'Refinement v3 · you corrected it',
    name: 'metrics-summary', label: 'Skill change', accept: { effect: 'applies', verb: 'Add refinement', sentence: '' } },
  accept_target: 'metrics-summary', target_resolves: true,
} as unknown as SkillProposal

describe('a skill proposal is decided on its card', () => {
  it('its card’s verb reads it again, and accepts when its skill is still there', async () => {
    skillProposals.mockResolvedValue({ proposals: [REFINE], lastReview: null })
    skillProposalDetail.mockResolvedValue({ ...REFINE })
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Add refinement: / }))
    await waitFor(() => expect(acceptSkillProposal).toHaveBeenCalledWith('sp-1'))
    expect(await within(needsYou()).findByText('Refinement added.')).toBeTruthy()
  })

  it('a skill removed since the list was read asks once more, since accepting installs it new', async () => {
    skillProposals.mockResolvedValue({ proposals: [REFINE], lastReview: null })
    skillProposalDetail.mockResolvedValue({ ...REFINE, target_resolves: false })
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Add refinement: / }))
    expect(await within(needsYou()).findByRole('group', {
      name: 'It is no longer installed, so accepting installs auto/metrics-summary as a new skill.' })).toBeTruthy()
    expect(acceptSkillProposal).not.toHaveBeenCalled()
    await yes('Yes, install it')
    await waitFor(() => expect(acceptSkillProposal).toHaveBeenCalledWith('sp-1'))
    expect(await within(needsYou()).findByText('Skill installed.')).toBeTruthy()
  })

  it('a refinement whose skill was removed reads Install skill and says where it lands; a new skill joining one says so', async () => {
    const gone = { ...REFINE, id: 'sp-2', accept_target: '', target_resolves: false,
      card: { ...REFINE.card!, accept: { effect: 'installs', verb: 'Install skill', sentence: 'metrics-summary is no longer installed, so accepting adds it as a new skill' } } }
    const joins = { ...REFINE, id: 'sp-3', kind: 'new', slug: 'auto/csv-dedupe', accept_target: 'csv-dedupe', target_resolves: true,
      card: { ...REFINE.card!, headline: 'Remove duplicate rows from a CSV', accept: { effect: 'installs', verb: 'Install skill', sentence: 'Adds this to the existing csv-dedupe' } } }
    skillProposals.mockResolvedValue({ proposals: [gone, joins], lastReview: null })
    render(<NeedsYouHarness />)
    expect(await within(needsYou()).findByText('metrics-summary is no longer installed, so accepting adds it as a new skill')).toBeTruthy()
    expect(within(needsYou()).getByText('Adds this to the existing csv-dedupe')).toBeTruthy()
    expect(within(needsYou()).getAllByRole('button', { name: /^Install skill: / })).toHaveLength(2)
    expect(within(needsYou()).queryByRole('button', { name: /^Add refinement: / })).toBeNull()
  })

  it('a read that fails sends nothing blind', async () => {
    skillProposals.mockResolvedValue({ proposals: [REFINE], lastReview: null })
    skillProposalDetail.mockRejectedValue(new Error('the gateway is restarting'))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Add refinement: / }))
    expect(await within(needsYou()).findByRole('alert')).toBeTruthy()
    expect(acceptSkillProposal).not.toHaveBeenCalled()
  })

  it('one decided somewhere else since says so, and its verbs go', async () => {
    skillProposals.mockResolvedValue({ proposals: [REFINE], lastReview: null })
    skillProposalDetail.mockRejectedValue(new ApiError('This proposal is gone.', 404, 'skill_proposal_gone'))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Add refinement: / }))
    expect(await within(needsYou()).findByText('It is no longer waiting: it was decided or ended somewhere else.')).toBeTruthy()
    expect(within(needsYou()).queryByRole('button', { name: /^Add refinement: / })).toBeNull()
    expect(acceptSkillProposal).not.toHaveBeenCalled()
  })
})

describe('an app’s proposal and a chat to file are decided on their cards', () => {
  const app = () => row('row-app', { app: 'notes', proposal: { op: 'add' } }, { message: 'Add a “Groceries” list', sender_name: 'notes' })
  const organize = () => row('row-org', {
    session: 's-9', session_organize: 'session_organize:s-9:f-2:work', folder_id: 'f-2', tags: 'work,ops',
  }, { message: '“Release notes”: folder “Work”' })

  it('Approve applies an app’s proposal through the Inbox’s apply', async () => {
    inboxOpen.mockResolvedValue([app()])
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Approve: / }))
    await waitFor(() => expect(applyInboxProposal).toHaveBeenCalledWith('row-app'))
    expect(await within(needsYou()).findByText('Approved. It was applied.')).toBeTruthy()
  })

  it('an apply that answers not ok says why and keeps the card', async () => {
    inboxOpen.mockResolvedValue([app()])
    applyInboxProposal.mockResolvedValue({ ok: false, error: 'the app is not running' })
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Approve: / }))
    expect((await within(needsYou()).findByRole('alert')).textContent).toMatch(/^Could not approve this: the app is not running\./)
    expect(within(needsYou()).getByRole('button', { name: /^Approve: / })).toBeEnabled()
  })

  it('Reject dismisses an app’s proposal’s row', async () => {
    inboxOpen.mockResolvedValue([app()])
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Reject: / }))
    await waitFor(() => expect(updateInboxItem).toHaveBeenCalledWith('row-app', { status: 'dismissed' }))
  })

  it('a chat to file sends its folder and tags as its row carries them', async () => {
    inboxOpen.mockResolvedValue([organize()])
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Accept: / }))
    await waitFor(() => expect(organizeAccept).toHaveBeenCalledWith('s-9', expect.objectContaining({ folder_id: 'f-2', tags: ['work', 'ops'] })))
    expect(await within(needsYou()).findByText('Accepted. The chat is organized as suggested.')).toBeTruthy()
  })

  it('Decline remembers it', async () => {
    inboxOpen.mockResolvedValue([organize()])
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Decline: / }))
    await waitFor(() => expect(organizeDecline).toHaveBeenCalledWith('s-9', expect.objectContaining({ folder_id: 'f-2' })))
    expect(await within(needsYou()).findByText('Declined. It won’t be suggested again.')).toBeTruthy()
  })

  it('your own note is not decided here: it opens in the Inbox', async () => {
    inboxOpen.mockResolvedValue([row('row-note', {}, { item_kind: 'user_note', message: 'Call the plumber' } as Partial<InboxItem>)])
    render(<NeedsYouHarness />)
    expect(await within(needsYou()).findByRole('link', { name: /^Open it in the Inbox: / })).toHaveAttribute('href', '#/inbox?open=row-note')
    expect(within(needsYou()).queryByRole('button', { name: /^Accept: |^Approve: / })).toBeNull()
  })
})

describe('Learning off', () => {
  it('no learning card draws a verb, from one read a page, and says how to turn it on', async () => {
    inboxOpen.mockResolvedValue([learning('template.named'), learning('lesson.failed_run')])
    learningProposals.mockResolvedValue({ enabled: false })
    render(<NeedsYouHarness />)
    expect(await within(needsYou()).findAllByText(/^Learning is off, so this can't be decided here\./)).toHaveLength(2)
    expect(within(needsYou()).queryByRole('button', { name: /^(Save template|Accept|Reject): / })).toBeNull()
    expect(learningProposals).toHaveBeenCalledTimes(1)
  })
})

const chatAsk = (questions: unknown[]) => waitingItem({
  id: 'question:q1', kind: 'question', consequence: 'asks', brief: { questions },
  answer: { method: 'POST', path: '/api/chat/sessions/chat-1/questions/q1/answer', takes: ['answers', 'skip'], body: {} },
})
const BRANCH = { question: 'Which branch should I cut the release from?', header: 'Branch', multiSelect: false, free_text: true,
  options: [{ label: 'main', description: 'Everything merged so far' }, { label: 'release/0.3', description: 'Fixes only' }] }

describe('a chat’s question is answered on its card', () => {
  it('one question with one choice: each option answers in one press, with its description', async () => {
    waiting.mockResolvedValue(waitingList([chatAsk([BRANCH])]))
    render(<NeedsYouHarness />)
    const pick = await within(needsYou()).findByRole('button', { name: /^release\/0\.3: / })
    expect(pick).toHaveAccessibleDescription('Fixes only')
    await userEvent.click(pick)
    await waitFor(() => expect(answerChatQuestion).toHaveBeenCalledWith('chat-1', 'q1', { answers: [{ selected: [1], other: '' }] }))
    expect(await within(needsYou()).findByText('Answered “release/0.3”. The agent carries on.')).toBeTruthy()
  })

  it('its own words where it takes them, and Skip', async () => {
    waiting.mockResolvedValue(waitingList([chatAsk([BRANCH])]))
    render(<NeedsYouHarness />)
    await userEvent.type(await within(needsYou()).findByRole('textbox', { name: /^Reply to / }), 'the hotfix branch{Enter}')
    await waitFor(() => expect(answerChatQuestion).toHaveBeenCalledWith('chat-1', 'q1', { answers: [{ selected: [], other: 'the hotfix branch' }] }))
  })

  it('several questions, or several choices: Skip, and the rest in the panel', async () => {
    waiting.mockResolvedValue(waitingList([chatAsk([{ ...BRANCH, multiSelect: true }])]))
    render(<NeedsYouHarness />)
    const skip = await within(needsYou()).findByRole('button', { name: /^Skip: / })
    expect(within(needsYou()).getByText('Answer it in the panel')).toBeTruthy()
    expect(within(needsYou()).queryByRole('button', { name: /^main: / })).toBeNull()
    await userEvent.click(skip)
    await waitFor(() => expect(answerChatQuestion).toHaveBeenCalledWith('chat-1', 'q1', { skip: true }))
    expect(await within(needsYou()).findByText('Skipped. The agent goes on without your answer.')).toBeTruthy()
  })

  it('one answered somewhere else since says so in the gateway’s words', async () => {
    answerChatQuestion.mockRejectedValue(new ApiError('You already answered this question in the chat.', 409, 'question_answered'))
    waiting.mockResolvedValue(waitingList([chatAsk([BRANCH])]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^main: / }))
    expect(await within(needsYou()).findByText('You already answered this question in the chat.')).toBeTruthy()
    expect(within(needsYou()).queryByRole('button', { name: /^main: / })).toBeNull()
  })
})

const loopStep = waitingItem({
  id: 'plan_review:loop:l2:s1', kind: 'plan_review', work: 'loop:l2', session: '', consequence: 'asks',
  brief: { name: 'Split', step: { id: 's1', kind: 'design', title: 'Design', objective: 'Split the module into a client and a server package' } },
  answer: { method: 'POST', path: '/api/loops/l2/plan/approve', takes: ['approve', 'comment'], body: { step_id: 's1' }, headers: { 'If-Match': '"rev-7"' } },
  route: '#/loops/l2',
})

describe('a plan is answered on its card', () => {
  it('a step: Approve names the step as the list read it', async () => {
    waiting.mockResolvedValue(waitingList([loopStep]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Approve: / }))
    await waitFor(() => expect(uLoopPlanApprove).toHaveBeenCalledWith('l2', 's1', 'rev-7'))
    expect(await within(needsYou()).findByText('Approved. The plan goes on.')).toBeTruthy()
  })

  it('a chat’s step goes to the chat’s route', async () => {
    waiting.mockResolvedValue(waitingList([{ ...loopStep, id: 'plan_review:chat:c-1:s1', work: 'chat:c-1', session: 'c-1',
      answer: { ...loopStep.answer, path: '/api/chat/sessions/c-1/plan/approve' } }]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Approve: / }))
    await waitFor(() => expect(chatPlanApprove).toHaveBeenCalledWith('c-1', 's1', 'here', 'rev-7'))
  })

  it('its comment line sends {step_id, text}, and an empty one sends nothing', async () => {
    waiting.mockResolvedValue(waitingList([loopStep]))
    render(<NeedsYouHarness />)
    const field = await within(needsYou()).findByRole('textbox', { name: /^Reply to / })
    expect(field).toHaveAttribute('placeholder', 'Ask for a change in one line: Enter sends')
    await userEvent.type(field, '   {Enter}')
    expect(uLoopPlanComment).not.toHaveBeenCalled()
    await userEvent.type(field, 'Keep the shared types in one package{Enter}')
    await waitFor(() => expect(uLoopPlanComment).toHaveBeenCalledWith('l2', 's1', 'Keep the shared types in one package'))
  })

  it('a step whose route names no revision is not answered here', async () => {
    waiting.mockResolvedValue(waitingList([{ ...loopStep, answer: { ...loopStep.answer, headers: {} } }]))
    render(<NeedsYouHarness />)
    expect(await within(needsYou()).findByRole('link', { name: /^Answer it where it asks/ })).toBeTruthy()
    expect(within(needsYou()).queryByRole('button', { name: /^Approve: / })).toBeNull()
  })

  it('a plan waiting for its launch: Launch starts the loop', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({ id: 'plan_review:loop:l3', kind: 'plan_review', work: 'loop:l3', session: '',
      consequence: 'asks', brief: { name: 'Split', task: 'Split the module' },
      answer: { method: 'PATCH', path: '/api/loops/l3', takes: ['start'], body: { action: 'start' } }, route: '#/loops/l3' })]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Launch: / }))
    await waitFor(() => expect(uLoopAction).toHaveBeenCalledWith('l3', 'start'))
  })
})

const mergeAsk = waitingItem({
  id: 'loop_question:l1', kind: 'loop_question', work: 'loop:l1', session: '', consequence: 'changes',
  brief: { name: 'Docs', question: 'Merge 2 finished tasks into main?', merge: { into: 'main', tasks: [{ task_id: 't1', title: 'Fix README links', tip: 'old1' }] } },
  answer: { method: 'POST', path: '/api/loops/l1/merge', takes: ['confirm'], body: {} }, route: '#/loops/l1',
})
const REVIEW_TASK = (id: string, title: string, tip: string, n: number) => ({ task_id: id, title, branch: `task/${id}`, tip, commits: Array.from({ length: n }, (_, i) => `c${i}`), stat: '', diff: '' })

describe('a loop’s merge and its conflicts are answered on their cards', () => {
  it('Merge reads the work again, asks once more naming each task, and merges at the tips that read returned', async () => {
    uLoopMergeReview.mockResolvedValue({ into: 'main', cut: false, tasks: [REVIEW_TASK('t1', 'Fix README links', 'new1', 2), REVIEW_TASK('t2', 'Add a quickstart', 'new2', 1)] })
    waiting.mockResolvedValue(waitingList([mergeAsk]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Merge into main: / }))
    expect(await within(needsYou()).findByRole('group', {
      name: 'Merge 2 tasks into main: “Fix README links” (2 commits), “Add a quickstart” (1 commit).' })).toBeTruthy()
    // A double click on Yes before it has armed sends nothing.
    await userEvent.dblClick(within(needsYou()).getByRole('button', { name: 'Yes, merge them' }))
    expect(uLoopMerge).not.toHaveBeenCalled()
    await yes('Yes, merge them')
    await waitFor(() => expect(uLoopMerge).toHaveBeenCalledWith('l1', { t1: 'new1', t2: 'new2' }))
    expect(await within(needsYou()).findByText('Merged into main. The loop carries on.')).toBeTruthy()
  })

  it('work that moved after the read says it changed, and its answer goes', async () => {
    uLoopMergeReview.mockResolvedValue({ into: 'main', cut: false, tasks: [REVIEW_TASK('t1', 'Fix README links', 'new1', 1)] })
    uLoopMerge.mockRejectedValue(new ApiError('The work moved since you read it.', 409, 'loop_merge_moved'))
    waiting.mockResolvedValue(waitingList([mergeAsk]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Merge into main: / }))
    await yes('Yes, merge it')
    expect(await within(needsYou()).findByText('It changed since it asked. Review it in the panel.')).toBeTruthy()
    expect(within(needsYou()).queryByRole('button', { name: /^Merge into main: / })).toBeNull()
  })

  it('a conflict’s Redo asks in its review’s words, and sends its own review’s task and tip', async () => {
    uLoopConflictReview.mockResolvedValue({ into: 'main', files: ['src/fetch.py'], cut: false,
      task: { ...REVIEW_TASK('t9', 'Add retry to fetch', 'tip9', 3), path: '/w/t9' } })
    waiting.mockResolvedValue(waitingList([{ ...mergeAsk, brief: { name: 'Docs', question: 'Conflicts', conflict: { task_id: 't9', title: 'Add retry to fetch', branch: 'task/t9', tip: 'old9', into: 'main', files: ['src/fetch.py'] } },
      answer: { method: 'POST', path: '/api/loops/l1/conflict', takes: ['redo', 'drop'], body: {} } }]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Redo: / }))
    expect(await within(needsYou()).findByRole('group', { name: /^The task runs again from main as it is now\. The work it did, 3 commits on task\/t9/ })).toBeTruthy()
    expect(uLoopConflict).not.toHaveBeenCalled()
    await yes('Yes, redo it')
    await waitFor(() => expect(uLoopConflict).toHaveBeenCalledWith('l1', 'redo', 't9', 'tip9'))
  })
})

const inboxAsk = (kind: string, brief: Record<string, unknown>) => waitingItem({
  id: `${kind}:row-1`, kind: kind as never, work: 'workflow:run-5', session: '', consequence: 'asks',
  brief: { title: 'release-notes waits', body: '', inbox_item: 'row-1', ...brief },
  answer: { method: '', path: '', takes: [], body: {} }, route: '#/inbox?open=row-1',
})
const FAILED_RUN = (failure: Record<string, unknown>) => ({
  status: 'failed', escalations: [{ kind: 'escalation', node_id: 'summarize' }],
  nodes: [{ node_id: 'summarize', state: 'failed', failure }],
})

describe('what only the Inbox asked is answered on its card', () => {
  it('a run waiting after a declined step: Resume it resumes it', async () => {
    waiting.mockResolvedValue(waitingList([inboxAsk('declined_wait', { workflow: 'run-5' })]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Resume it: / }))
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledWith('run-5', {}))
    expect(await within(needsYou()).findByText('Resumed. The run is moving again.')).toBeTruthy()
  })

  it('Cancel it asks once more in the run page’s words, and sends nothing before its yes', async () => {
    waiting.mockResolvedValue(waitingList([inboxAsk('declined_wait', { workflow: 'run-5' })]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Cancel it: / }))
    expect(within(needsYou()).getByRole('group', { name: 'Cancel this run? In-flight steps are stopped. Completed work is kept.' })).toBeTruthy()
    expect(cancelWorkflowRun).not.toHaveBeenCalled()
    await yes('Yes, cancel run')
    await waitFor(() => expect(cancelWorkflowRun).toHaveBeenCalledWith('run-5'))
  })

  it('a resume a run’s caps refuse says the gateway’s sentence and keeps its answers', async () => {
    resumeWorkflowRun.mockRejectedValue(new ApiError('This run is at its budget. Raise it on the run’s page to resume it.', 409, 'at_budget'))
    waiting.mockResolvedValue(waitingList([inboxAsk('declined_wait', { workflow: 'run-5' })]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Resume it: / }))
    expect(await within(needsYou()).findByText('This run is at its budget. Raise it on the run’s page to resume it.')).toBeTruthy()
    expect(within(needsYou()).getByRole('button', { name: /^Resume it: / })).toBeEnabled()
  })

  it('an escalated run the engine calls retryable: Retry forks it and starts the new run', async () => {
    workflowRun.mockResolvedValue(FAILED_RUN({ retryable: true, retry_at: 0 }))
    forkWorkflowRun.mockResolvedValue({ child_run_id: 'run-6', shared_axes: [] })
    waiting.mockResolvedValue(waitingList([inboxAsk('escalated_run', { workflow: 'run-5' })]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Retry: / }))
    await waitFor(() => expect(startDraftWorkflowRun).toHaveBeenCalledWith('run-6'))
    expect(await within(needsYou()).findByText('Started again as a new run.')).toBeTruthy()
    expect(within(needsYou()).getByRole('link', { name: 'Open the new run' })).toHaveAttribute('href', '#/workflows/runs/run-6')
  })

  it('one a retry can’t fix takes Dismiss alone', async () => {
    workflowRun.mockResolvedValue(FAILED_RUN({ retryable: false }))
    waiting.mockResolvedValue(waitingList([inboxAsk('escalated_run', { workflow: 'run-5' })]))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Dismiss: / }))
    expect(within(needsYou()).queryByRole('button', { name: /^Retry: / })).toBeNull()
    await waitFor(() => expect(updateInboxItem).toHaveBeenCalledWith('row-1', { status: 'dismissed' }))
  })

  it('a held action takes Dismiss, and opens in the Inbox', async () => {
    waiting.mockResolvedValue(waitingList([inboxAsk('held_action', { action_type: 'action.notify', rung: 'ask' })]))
    render(<NeedsYouHarness />)
    expect(await within(needsYou()).findByRole('link', { name: /^Open it in the Inbox: / })).toHaveAttribute('href', '#/inbox?open=row-1')
    await userEvent.click(within(needsYou()).getByRole('button', { name: /^Dismiss: / }))
    await waitFor(() => expect(updateInboxItem).toHaveBeenCalledWith('row-1', { status: 'dismissed' }))
    expect(await within(needsYou()).findByText('Dismissed. Nothing ran.')).toBeTruthy()
  })
})

describe('autonomy an action earned is answered on its card', () => {
  const earned = () => row('row-aut', { action_type: 'action.notify', rung: 'auto', surface: 'settings/security?section=guardrails' },
    { message: 'action.notify has earned “Runs on its own”' })

  it('Promote it asks once more with what the rung lets it do, and grants it on its yes', async () => {
    inboxOpen.mockResolvedValue([earned()])
    autonomyLadder.mockResolvedValue({ rungs: [], rung_meta: [{ key: 'auto', label: 'Runs on its own', hint: 'It acts without asking first.' }], incident_active: false, types: [], reversals: [] })
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Promote it: / }))
    expect(await within(needsYou()).findByRole('group', { name: '“Runs on its own”: It acts without asking first.' })).toBeTruthy()
    expect(autonomyGrant).not.toHaveBeenCalled()
    await yes('Yes, promote it')
    await waitFor(() => expect(autonomyGrant).toHaveBeenCalledWith('action.notify', 'auto'))
    expect(await within(needsYou()).findByText('Promoted to “Runs on its own”.')).toBeTruthy()
  })

  it('Leave it closes the row and changes nothing', async () => {
    inboxOpen.mockResolvedValue([earned()])
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Leave it: / }))
    await waitFor(() => expect(updateInboxItem).toHaveBeenCalledWith('row-aut', { status: 'dismissed' }))
    expect(autonomyGrant).not.toHaveBeenCalled()
  })

  it('a refused grant says the gateway’s reason and keeps its answers', async () => {
    inboxOpen.mockResolvedValue([earned()])
    autonomyLadder.mockResolvedValue({ rungs: [], rung_meta: [], incident_active: false, types: [], reversals: [] })
    autonomyGrant.mockRejectedValue(new ApiError('An incident is open, so nothing is promoted until it closes.', 400, 'autonomy_refused'))
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Promote it: / }))
    await yes('Yes, promote it')
    expect(await within(needsYou()).findByText('An incident is open, so nothing is promoted until it closes.')).toBeTruthy()
    expect(within(needsYou()).getByRole('button', { name: /^Promote it: / })).toBeEnabled()
  })
})

describe('an expired ask draws no answers, and a gate still answers as before', () => {
  it('an expired step offers nothing to press', async () => {
    waiting.mockResolvedValue(waitingList([{ ...loopStep, expires_at: '2020-01-01T00:00:00Z' }]))
    render(<NeedsYouHarness />)
    await within(needsYou()).findByText(/can no longer be answered here/)
    expect(within(needsYou()).queryByRole('button', { name: /^Approve: / })).toBeNull()
    expect(within(needsYou()).queryByRole('textbox')).toBeNull()
  })

  it('a choice gate’s choices still answer in one press', async () => {
    waiting.mockResolvedValue(waitingList([gateItem()]))
    render(<NeedsYouHarness />)
    expect(await within(needsYou()).findByRole('button', { name: /^Ship it: / })).toBeTruthy()
  })
})
