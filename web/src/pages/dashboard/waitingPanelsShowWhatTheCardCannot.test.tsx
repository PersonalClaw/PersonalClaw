// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import { NeedsYouHarness, gateItem, waitingItem, waitingList } from '../../test/needsYouHarness'

// ── What waits: its panel shows what its card can't, once ──────────────────────────────────────
//
// An ask's panel never draws its card again. The pane's header is the card's tag row and its
// heading the ask's headline, so the body holds the ask's own lines, where it came from, its facts
// and one way out, and its answers in the decide bar at its foot. A hosted approval announces
// nothing (no alert), draws no header row or "From …" line, and draws its command once. A loop's
// merge or conflict shows its work, wrapping in place; its verbs stay in the bar, but for Resume.

const waiting = vi.fn()
const resumeWorkflowRun = vi.fn()
const answerChatQuestion = vi.fn()
const uLoopMergeReview = vi.fn()
const uLoopConflictReview = vi.fn()
const uLoopAction = vi.fn()
const uLoopConflict = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    status: () => Promise.resolve({}),
    running: () => Promise.resolve({ running: [], finished: [] }),
    chatSessions: () => Promise.resolve([]),
    chatSessionDetail: () => Promise.resolve({ key: 'chat-1', title: '', run_state: 'idle', messages: [] }),
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...a),
    answerChatQuestion: (...a: unknown[]) => answerChatQuestion(...a),
    uLoopMergeReview: (...a: unknown[]) => uLoopMergeReview(...a),
    uLoopConflictReview: (...a: unknown[]) => uLoopConflictReview(...a),
    uLoopAction: (...a: unknown[]) => uLoopAction(...a),
    uLoopConflict: (...a: unknown[]) => uLoopConflict(...a),
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
})

/** The detail pane: the region its heading names. */
const pane = async () => {
  const headings = await screen.findAllByRole('heading', { level: 2 })
  const heading = headings.find((h) => h.getAttribute('data-type') === 'title-l')!
  return heading.closest('[role="region"]') as HTMLElement
}

describe('a hosted approval in its panel', () => {
  it('mounts no alert, draws no header row or "From …" line, and draws its command once', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({ expires_at: new Date(Date.now() + 60 * 60_000).toISOString() })]))
    render(<NeedsYouHarness query={{ open: 'waiting:approval:appr-1' }} />)
    const p = await pane()
    const approval = await within(p).findByRole('group', { name: 'Permission needed to run shell.run' })
    expect(approval.getAttribute('data-approval-form')).toBe('hosted')
    expect(within(p).queryAllByRole('alert')).toEqual([])
    expect(within(approval).queryByText('Permission needed')).toBeNull()
    expect(within(p).queryByText(/^From chat/)).toBeNull()
    expect(within(approval).getAllByText(/^shell\.run\(/)).toHaveLength(1)
    // Its own Allow and Deny, and no second bar.
    expect(within(approval).getByRole('button', { name: /^Allow/ })).toBeInTheDocument()
    expect(p.querySelectorAll('[data-decide-bar]')).toHaveLength(0)
  })

  it('the header is the tag row: "Permission needed" once, there', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem()]))
    render(<NeedsYouHarness query={{ open: 'waiting:approval:appr-1' }} />)
    const p = await pane()
    await within(p).findByRole('group', { name: 'Permission needed to run shell.run' })
    expect(within(p).getAllByText('Permission needed')).toHaveLength(1)
  })
})

describe('any other ask’s panel', () => {
  it('its lines, where it came from, its facts and a way out, then its answers in the bar at its foot', async () => {
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'choice', prompt: 'Which branch should I push?\n\nThe release branch is cut.', choices: ['Ship it', 'Hold'] }, {
      asked_at: '2026-11-15T10:00:00Z',
    })]))
    render(<NeedsYouHarness query={{ open: 'waiting:gate:run-77:gate.push:1' }} />)
    const p = await pane()
    const bar = await within(p).findByRole('button', { name: /^Ship it: / }).then((b) => b.closest('[data-decide-bar]') as HTMLElement)
    expect(bar).not.toBeNull()
    expect(bar.parentElement!.lastElementChild).toBe(bar)
    expect(within(p).getByText('The release branch is cut.')).toBeInTheDocument()
    expect(within(p).getByText('Asked')).toBeInTheDocument()
    expect(within(p).getByRole('link', { name: 'Open where it asks' })).toHaveAttribute('href', '#/workflows/runs/run-77')
    // The card is not drawn again: no second tag row, no "Asked by".
    expect(within(p).queryByText(/^Asked by/)).toBeNull()
    expect(within(p).getAllByText('Workflow')).toHaveLength(1)
  })

  it('a chat’s question takes your own words in the panel, named for it, with no "Escape closes" promise', async () => {
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'question:q1', kind: 'question', consequence: 'asks', brief: { questions: [{ question: 'Which version?' }], asked_by: 'Release notes' },
      answer: { method: 'POST', path: '/api/chat/sessions/chat-1/questions/q1/answer', takes: ['answers', 'skip'], body: {} },
    })]))
    render(<NeedsYouHarness query={{ open: 'waiting:question:q1' }} />)
    const p = await pane()
    const field = await within(p).findByRole('textbox', { name: /^Reply to Which version\?/ })
    expect(field.getAttribute('placeholder')).toBe('Reply in one line: Enter sends')
  })
})

describe('after a decision', () => {
  it('the panel stays on it with its outcome, and Next ask opens the next one, its heading focused', async () => {
    resumeWorkflowRun.mockResolvedValue({ ok: true })
    const second = gateItem({ kind: 'choice', prompt: 'Which tag should I cut?', choices: ['v1.2', 'v1.3'] }, {
      id: 'gate:run-78:gate.tag:1', work: 'workflow:run-78',
      answer: { method: 'POST', path: '/api/workflows/runs/run-78/resume', takes: ['choice'], body: { resume_token: 'tok-8' } },
      route: '#/workflows/runs/run-78',
    })
    waiting.mockResolvedValue(waitingList([gateItem(), second]))
    render(<NeedsYouHarness query={{ open: 'waiting:gate:run-77:gate.push:1' }} />)
    const p = await pane()
    await userEvent.click(await within(p).findByRole('button', { name: /^Ship it: / }))
    expect(await within(p).findByText(/^Answered “Ship it”/)).toBeInTheDocument()
    await userEvent.click(within(p).getByRole('button', { name: 'Next ask' }))
    await waitFor(() => expect(document.activeElement?.textContent).toBe('Which tag should I cut?'))
    expect(document.activeElement?.tagName).toBe('H2')
  })
})

describe('a chat’s question with several parts is answered in its panel', () => {
  it('the chat’s own question card, hosted: every question and option, Send and Skip, no alert and no second bar', async () => {
    answerChatQuestion.mockResolvedValue({ ok: true, outcome: 'answered' })
    waiting.mockResolvedValue(waitingList([waitingItem({
      id: 'question:q2', kind: 'question', consequence: 'asks',
      brief: { questions: [
        { question: 'Which branch?', header: 'Branch', multiSelect: false, free_text: false, options: [{ label: 'main', description: '' }, { label: 'next', description: '' }] },
        { question: 'Which notes?', header: 'Notes', multiSelect: true, free_text: false, options: [{ label: 'Fixes', description: '' }, { label: 'Features', description: '' }] },
      ] },
      answer: { method: 'POST', path: '/api/chat/sessions/chat-1/questions/q2/answer', takes: ['answers', 'skip'], body: {} },
    })]))
    render(<NeedsYouHarness query={{ open: 'waiting:question:q2' }} />)
    const p = await pane()
    const card = await within(p).findByRole('group', { name: 'Question for you' })
    expect(within(p).queryAllByRole('alert')).toEqual([])
    expect(within(p).queryByText('Answer it in the panel')).toBeNull()
    expect(p.querySelectorAll('[data-decide-bar]')).toHaveLength(0)
    await userEvent.click(within(card).getByRole('radio', { name: /next/ }))
    await userEvent.click(within(card).getByRole('checkbox', { name: /Fixes/ }))
    await userEvent.click(within(card).getByRole('button', { name: /Send answer/ }))
    await waitFor(() => expect(answerChatQuestion).toHaveBeenCalledWith('chat-1', 'q2', { answers: [{ selected: [1], other: '' }, { selected: [0], other: '' }] }))
    expect(await within(p).findByText('Answered. The agent carries on.')).toBeInTheDocument()
  })
})

describe('a run’s form gate is answered in its panel', () => {
  it('the run’s own ask, hosted: its fields and Submit, sent with the gate’s token', async () => {
    resumeWorkflowRun.mockResolvedValue({ ok: true })
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'form', prompt: 'Fill in the release', fields: [{ name: 'version', type: 'text', label: 'Version' }] })]))
    render(<NeedsYouHarness query={{ open: 'waiting:gate:run-77:gate.push:1' }} />)
    const p = await pane()
    expect(p.querySelector('[data-ask-form="hosted"]')).not.toBeNull()
    expect(within(p).queryByText('Answer it in the panel')).toBeNull()
    await userEvent.type(within(p).getByRole('textbox', { name: 'Version' }), '1.2.0')
    await userEvent.click(within(p).getByRole('button', { name: /Submit/ }))
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledWith('run-77', { answer: { version: '1.2.0' }, resume_token: 'tok-9' }))
    expect(await within(p).findByText('Answered. The run is moving again.')).toBeInTheDocument()
  })
  it('a refused answer says so on the card, and nothing is left unhandled', async () => {
    resumeWorkflowRun.mockRejectedValue(new Error('the gateway is restarting'))
    waiting.mockResolvedValue(waitingList([gateItem({ kind: 'form', prompt: 'Fill in the release', fields: [{ name: 'version', type: 'text', label: 'Version' }] })]))
    render(<NeedsYouHarness query={{ open: 'waiting:gate:run-77:gate.push:1' }} />)
    const p = await pane()
    await userEvent.type(await within(p).findByRole('textbox', { name: 'Version' }), '1.2.0')
    await userEvent.click(within(p).getByRole('button', { name: /Submit/ }))
    expect(await within(p).findByText(/the gateway is restarting/)).toBeInTheDocument()
  })
})

const DIFF = ['--- a/README.md', '+++ b/README.md', '@@ -1,2 +1,2 @@', '-[docs](old)', '+[docs](new)'].join('\n')
const loopAsk = (brief: Record<string, unknown>, takes: string[]) => waitingItem({
  id: 'loop_question:l1', kind: 'loop_question', work: 'loop:l1', session: '', consequence: 'changes',
  brief: { name: 'Docs', question: 'Merge the finished task into main?', ...brief },
  answer: { method: 'POST', path: `/api/loops/l1/${takes.includes('redo') ? 'conflict' : 'merge'}`, takes, body: {} }, route: '#/loops/l1',
})
/** The hosted form *attr* names, once the pane draws it. */
const hostedIn = (p: HTMLElement, attr: string) => waitFor(() => {
  const el = p.querySelector<HTMLElement>(`[${attr}="hosted"]`)
  expect(el).not.toBeNull()
  return el!
})
const TASK = { task_id: 't1', title: 'Fix README links', branch: 'task/t1', tip: 'tip1', commits: ['a1 Fix the links'], stat: ' README.md | 2 +-', diff: DIFF }

describe('a loop’s merge or conflict shows its work in the panel', () => {
  it('the merge’s review, hosted: its commits and its diff, wrapping and open, with Merge only in the bar', async () => {
    uLoopMergeReview.mockResolvedValue({ into: 'main', cut: false, tasks: [TASK] })
    waiting.mockResolvedValue(waitingList([loopAsk({ merge: { into: 'main', tasks: [{ task_id: 't1', title: 'Fix README links', branch: 'task/t1', tip: 'tip1' }] } }, ['confirm'])]))
    render(<NeedsYouHarness query={{ open: 'waiting:loop_question:l1' }} />)
    const p = await pane()
    const review = await hostedIn(p, 'data-merge-form')
    expect(await within(review).findByText('a1 Fix the links')).toBeInTheDocument()
    expect(review.querySelector('details')!.open).toBe(true)
    const diff = within(review).getByLabelText('Changes of Fix README links')
    expect(diff.getAttribute('tabindex')).toBeNull()
    expect(diff.className).toContain('whitespace-pre-wrap')
    expect(within(review).getByLabelText('Files Fix README links changes').getAttribute('tabindex')).toBeNull()
    // No region inside the pane, and one Merge: the bar's, which reads the work again first.
    expect(within(p).queryAllByRole('region')).toEqual([])
    expect(within(review).queryByRole('button', { name: /Merge into main/ })).toBeNull()
    const bar = p.querySelector('[data-decide-bar]') as HTMLElement
    expect(within(bar).getByRole('button', { name: /^Merge into main: / })).toBeInTheDocument()
  })

  it('the conflict’s review, hosted: its work, and Resume beside it; Redo and Drop only in the bar', async () => {
    uLoopConflictReview.mockResolvedValue({ into: 'main', files: ['README.md'], cut: false, task: { ...TASK, path: '/w/t1' } })
    uLoopAction.mockResolvedValue({ ok: true })
    waiting.mockResolvedValue(waitingList([loopAsk({ question: 'Conflicts', conflict: { task_id: 't1', title: 'Fix README links', branch: 'task/t1', tip: 'tip1', into: 'main', files: ['README.md'] } }, ['redo', 'drop'])]))
    render(<NeedsYouHarness query={{ open: 'waiting:loop_question:l1' }} />)
    const p = await pane()
    const review = await hostedIn(p, 'data-conflict-form')
    expect(await within(review).findByText('a1 Fix the links')).toBeInTheDocument()
    expect(within(p).queryAllByRole('region')).toEqual([])
    expect(within(review).queryByRole('button', { name: /Redo|Drop/ })).toBeNull()
    const bar = p.querySelector('[data-decide-bar]') as HTMLElement
    expect(within(bar).getByRole('button', { name: /^Redo: / })).toBeInTheDocument()
    expect(within(bar).getByRole('button', { name: /^Drop: / })).toBeInTheDocument()
    // Resume says how it went on the card, and goes once it has.
    await userEvent.click(within(review).getByRole('button', { name: /Resume/ }))
    await waitFor(() => expect(uLoopAction).toHaveBeenCalledWith('l1', 'resume'))
    expect(await within(p).findByText('Resumed. The loop merges it, or asks again if it still conflicts.')).toBeInTheDocument()
    expect(within(review).queryByRole('button', { name: /Resume/ })).toBeNull()
    expect(uLoopAction).toHaveBeenCalledTimes(1)
  })

  it('a Resume refused says so on the card and keeps Resume to try again', async () => {
    uLoopConflictReview.mockResolvedValue({ into: 'main', files: ['README.md'], cut: false, task: { ...TASK, path: '/w/t1' } })
    uLoopAction.mockRejectedValue(new Error('the gateway is restarting'))
    waiting.mockResolvedValue(waitingList([loopAsk({ question: 'Conflicts', conflict: { task_id: 't1', title: 'Fix README links', branch: 'task/t1', tip: 'tip1', into: 'main', files: ['README.md'] } }, ['redo', 'drop'])]))
    render(<NeedsYouHarness query={{ open: 'waiting:loop_question:l1' }} />)
    const p = await pane()
    const review = await hostedIn(p, 'data-conflict-form')
    await userEvent.click(await within(review).findByRole('button', { name: /Resume/ }))
    expect(await within(p).findByText(/the gateway is restarting/)).toBeInTheDocument()
    expect(within(review).getByRole('button', { name: /Resume/ })).toBeEnabled()
  })
})
