import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { PlanSession, PlanStep } from '../lib/api'

// ── A plan step's edit is saved only over the draft it was made on ─────────────────────────────────
//
// Both plan gates — the chat's inline one and the full-page planning walkthrough — let the user
// rewrite a step's markdown and save it whole. A comment sent from another tab redrafts the step
// while the editor is open, and the save used to put the OLD draft (plus the edit) back over the new
// one without a word. The gateway now refuses an edit whose base is stale (`409 stale_write`,
// `chat_plan.py` / `loop_routes.py`); these pin the gates' half: the notice, the typing kept, the
// revision each edit names, and a reapply that keeps the redraft.

const { api } = vi.hoisted(() => ({
  api: {
    chatPlanSession: vi.fn(),
    chatPlanEdit: vi.fn(),
    chatPlanComment: vi.fn(),
    chatPlanApprove: vi.fn(),
    chatPlanCancel: vi.fn(),
  },
}))

vi.mock('../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../lib/api')>()
  return { ...mod, api: { ...mod.api, ...api } }
})

import { ChatPlanGate } from './chat/ChatPlanGate'
import { PlanningWalkthrough, type WalkthroughConfig } from './PlanningWalkthrough'

/** Open the editor once the first paint has SETTLED. Both gates close an open editor when the step
 *  they are on changes, from an effect — and the session's arrival is that change. A click landing
 *  between the paint and its effect is closed again by it, which only a loaded test runner is fast
 *  enough to do. */
async function openEditor(button: HTMLElement) {
  await act(async () => {})
  fireEvent.click(button)
}

function staleWrite() {
  return Object.assign(new Error('This write replaces the plan step, which changed after the copy it was built from was read.'), { status: 409, code: 'stale_write' })
}

// The draft the page painted, what the user makes of its first line, and the redraft that landed
// meanwhile — which changed only the LAST line, so the user's edit re-applies cleanly onto it.
const DRAFT = '### Plan\n1. read the code\n2. write the fix\n3. run the tests'
const MINE = '### The plan\n1. read the code\n2. write the fix\n3. run the tests'
const REDRAFT = '### Plan\n1. read the code\n2. write the fix\n3. run the whole suite'
const MERGED = '### The plan\n1. read the code\n2. write the fix\n3. run the whole suite'

const step = (markdown: string, revision: string): PlanStep => ({
  id: 'chat-plan-1', kind: 'chat_plan', title: 'Plan', objective: 'Plan the work before anything runs.',
  status: 'awaiting_review', artifact: { markdown }, comments: [], revision,
})

// ── the chat's plan gate ──────────────────────────────────────────────────────────────────────────

describe('the chat plan gate', () => {
  let stored: PlanStep
  beforeEach(() => {
    for (const f of Object.values(api)) f.mockReset()
    stored = step(DRAFT, 'p1')
    api.chatPlanSession.mockImplementation(() => Promise.resolve({
      session: { project_id: 'c1', steps: [stored] }, awaiting_step_id: 'chat-plan-1', binding: {}, task_mode: 'plan',
    }))
    api.chatPlanEdit.mockImplementation((_s: string, _id: string, markdown: string, base: string) =>
      base === stored.revision
        ? Promise.resolve({ ok: true, session: { project_id: 'c1', steps: [(stored = step(markdown, 'p3'))] } })
        : Promise.reject(staleWrite()))
  })

  async function editAndSave() {
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)
    await openEditor(await screen.findByLabelText('Edit this plan'))
    fireEvent.change(await screen.findByLabelText('Plan markdown'), { target: { value: MINE } })
    // Another tab's comment redrafts the plan while the editor is open.
    stored = step(REDRAFT, 'p2')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save edits/ })) })
  }

  it('an edit of a draft that was replaced is refused with the notice, and the typing is kept', async () => {
    await editAndSave()
    const alert = await screen.findByText(/This plan changed elsewhere/)
    expect(alert.closest('[role="alert"]')).toBeTruthy()
    expect((screen.getByLabelText('Plan markdown') as HTMLTextAreaElement).value, 'the typing survives').toBe(MINE)
    expect(api.chatPlanEdit).toHaveBeenCalledTimes(1)
    expect(api.chatPlanEdit.mock.calls[0]).toEqual(['c1', 'chat-plan-1', MINE, 'p1'])
  })

  it('Reload and reapply puts the edit onto the redraft instead of over it', async () => {
    await editAndSave()
    const alert = (await screen.findByText(/This plan changed elsewhere/)).closest('[role="alert"]') as HTMLElement
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(api.chatPlanEdit).toHaveBeenCalledTimes(2))
    expect(api.chatPlanEdit.mock.calls[1]).toEqual(['c1', 'chat-plan-1', MERGED, 'p2'])
    // Saving returns to review.
    await waitFor(() => expect(screen.queryByLabelText('Plan markdown')).toBeNull())
  })

  it('an edit of the current draft lands and names the painted revision', async () => {
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)
    await openEditor(await screen.findByLabelText('Edit this plan'))
    fireEvent.change(await screen.findByLabelText('Plan markdown'), { target: { value: MINE } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save edits/ })) })
    await waitFor(() => expect(screen.queryByLabelText('Plan markdown')).toBeNull())
    expect(api.chatPlanEdit.mock.calls[0]).toEqual(['c1', 'chat-plan-1', MINE, 'p1'])
    expect(screen.queryByText(/changed elsewhere/)).toBeNull()
  })
})

// ── the planning walkthrough ──────────────────────────────────────────────────────────────────────

describe('the planning walkthrough', () => {
  let stored: PlanSession
  const sessionWith = (markdown: string, revision: string): PlanSession => ({
    project_id: 'l-1', created_at: Date.now() / 1000, updated_at: Date.now() / 1000,
    steps: [{ id: 'brief', kind: 'brief', title: 'Brief', status: 'awaiting_review', artifact: { markdown }, comments: [], revision }],
  })
  let edit: ReturnType<typeof vi.fn>

  function mount() {
    const cfg: WalkthroughConfig = {
      planSessionKey: (id) => `loop-plan-${id}`,
      api: {
        getSession: () => Promise.resolve(stored),
        start: vi.fn(() => Promise.resolve({})),
        approve: vi.fn(() => Promise.resolve({})),
        comment: vi.fn(() => Promise.resolve({})),
        edit: edit as unknown as WalkthroughConfig['api']['edit'],
        isReady: () => Promise.resolve(false),
      },
      copy: { subtitle: 'Planning', activityLabel: 'Investigation', activityEmpty: 'Nothing yet.', cancel: 'Back' },
      renderArtifact: () => null,
    }
    render(<PlanningWalkthrough id="l-1" cfg={cfg} onReady={() => {}} onBack={() => {}} />)
  }

  beforeEach(() => {
    // jsdom implements no scrolling, and the component scrolls its activity feed on update.
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    stored = sessionWith(DRAFT, 'r1')
    edit = vi.fn((_id: string, _step: string, markdown: string, base: string) =>
      base === stored.steps[0].revision
        ? Promise.resolve({ session: (stored = sessionWith(markdown, 'r3')) })
        : Promise.reject(staleWrite()))
  })

  async function editAndSave() {
    mount()
    await openEditor(await screen.findByTitle('Edit this artifact'))
    fireEvent.change(await screen.findByPlaceholderText("Write the step's prose body in markdown…"), { target: { value: MINE } })
    // A comment from another tab redrafts the step while the editor is open.
    stored = sessionWith(REDRAFT, 'r2')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save edits/ })) })
  }

  it('an edit of a draft that was replaced is refused with the notice, and the typing is kept', async () => {
    await editAndSave()
    const alert = await screen.findByText(/The step “Brief” changed elsewhere/)
    expect(alert.closest('[role="alert"]')).toBeTruthy()
    expect((screen.getByPlaceholderText("Write the step's prose body in markdown…") as HTMLTextAreaElement).value).toBe(MINE)
    expect(edit).toHaveBeenCalledTimes(1)
    expect(edit.mock.calls[0]).toEqual(['l-1', 'brief', MINE, 'r1'])
  })

  it('Reload and reapply puts the edit onto the redraft instead of over it', async () => {
    await editAndSave()
    const alert = (await screen.findByText(/The step “Brief” changed elsewhere/)).closest('[role="alert"]') as HTMLElement
    const reapply = within(alert).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(edit).toHaveBeenCalledTimes(2))
    expect(edit.mock.calls[1]).toEqual(['l-1', 'brief', MERGED, 'r2'])
    await waitFor(() => expect(screen.queryByPlaceholderText("Write the step's prose body in markdown…")).toBeNull())
  })

  it('an edit of the current draft lands and names the painted revision', async () => {
    mount()
    await openEditor(await screen.findByTitle('Edit this artifact'))
    fireEvent.change(await screen.findByPlaceholderText("Write the step's prose body in markdown…"), { target: { value: MINE } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Save edits/ })) })
    await waitFor(() => expect(screen.queryByPlaceholderText("Write the step's prose body in markdown…")).toBeNull())
    expect(edit.mock.calls[0]).toEqual(['l-1', 'brief', MINE, 'r1'])
    expect(screen.queryByText(/changed elsewhere/)).toBeNull()
  })
})
