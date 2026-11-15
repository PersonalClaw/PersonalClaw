// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { resetDataStore } from '../../lib/data'
import { ApiError, type InboxItem, type LearningProposalReview, type SkillProposal } from '../../lib/api'
import { ARM_MS } from '../../ui/dialog/DialogShell'
import { NeedsYouHarness, waitingList } from '../../test/needsYouHarness'

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
    applyInboxProposal, updateInboxItem, organizeAccept, organizeDecline]) f.mockResolvedValue({ ok: true })
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
