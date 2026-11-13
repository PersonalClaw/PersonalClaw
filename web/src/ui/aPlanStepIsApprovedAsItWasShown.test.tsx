import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PlanSession, PlanStep } from '../lib/api'

// ── A plan step is approved as it was shown ──────────────────────────────────────────────────────
//
// An approval names the step as its review showed it: the step's `review_revision` as read, sent
// in `If-Match`. The gateway refuses an approval of a step that changed since (`409 stale_write`),
// and a fresh chat over a plan with a step not approved as it is now (`409
// plan_step_reviewed_again`). These pin the pages' half: both api calls send the revision, both
// gates send the one they painted, and a refused approval reads the plan again and says why.

const { gate } = vi.hoisted(() => ({
  gate: {
    chatPlanSession: vi.fn(),
    chatPlanEdit: vi.fn(),
    chatPlanComment: vi.fn(),
    chatPlanApprove: vi.fn(),
    chatPlanCancel: vi.fn(),
  },
}))

vi.mock('../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../lib/api')>()
  return { ...mod, api: { ...mod.api, ...gate } }
})

import { ChatPlanGate } from './chat/ChatPlanGate'
import { PlanningWalkthrough, type WalkthroughConfig } from './PlanningWalkthrough'

function refused(code: string, message: string) {
  return Object.assign(new Error(message), { status: 409, code })
}

// ── the api calls ─────────────────────────────────────────────────────────────────────────────────

describe('an approval names the step as it was read', () => {
  const real = vi.importActual<typeof import('../lib/api')>('../lib/api')
  let sent: Array<{ url: string; ifMatch?: string }>

  beforeEach(() => {
    sent = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      sent.push({ url, ifMatch: (init.headers as Record<string, string> | undefined)?.['If-Match'] })
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
  })
  afterEach(() => { vi.unstubAllGlobals() })

  it("a loop step's approve sends its revision in If-Match", async () => {
    await (await real).api.uLoopPlanApprove('l-1', 'step-0', 'v1')
    expect(sent).toEqual([{ url: '/api/loops/l-1/plan/approve', ifMatch: '"v1"' }])
  })

  it("a chat step's approve sends its revision in If-Match, run here or fresh", async () => {
    await (await real).api.chatPlanApprove('c1', 'chat-plan-1', 'here', 'v1')
    await (await real).api.chatPlanApprove('c1', 'chat-plan-1', 'fresh', 'v2')
    expect(sent).toEqual([
      { url: '/api/chat/sessions/c1/plan/approve', ifMatch: '"v1"' },
      { url: '/api/chat/sessions/c1/plan/approve', ifMatch: '"v2"' },
    ])
  })
})

// ── the planning walkthrough ──────────────────────────────────────────────────────────────────────

describe('the planning walkthrough', () => {
  let stored: PlanSession
  let approve: ReturnType<typeof vi.fn>
  let getSession: ReturnType<typeof vi.fn>
  const sessionWith = (markdown: string, review: string): PlanSession => ({
    project_id: 'l-1', created_at: Date.now() / 1000, updated_at: Date.now() / 1000,
    steps: [{ id: 'brief', kind: 'brief', title: 'Brief', status: 'awaiting_review', artifact: { markdown }, comments: [], revision: 'm1', review_revision: review }],
  })

  function mount() {
    const cfg: WalkthroughConfig = {
      planSessionKey: (id) => `loop-plan-${id}`,
      api: {
        getSession: getSession as unknown as WalkthroughConfig['api']['getSession'],
        start: vi.fn(() => Promise.resolve({})),
        approve: approve as unknown as WalkthroughConfig['api']['approve'],
        comment: vi.fn(() => Promise.resolve({})),
        edit: vi.fn(() => Promise.resolve({ session: stored })),
        isReady: () => Promise.resolve(false),
      },
      copy: { subtitle: 'Planning', activityLabel: 'Investigation', activityEmpty: 'Nothing yet.', cancel: 'Back' },
      renderArtifact: (_kind, artifact) => <p>{String((artifact as { markdown?: string })?.markdown ?? '')}</p>,
    }
    render(<PlanningWalkthrough id="l-1" cfg={cfg} onReady={() => {}} onBack={() => {}} onCancel={() => Promise.resolve(true)} onStop={() => Promise.resolve(true)} />)
  }

  beforeEach(() => {
    Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
    stored = sessionWith('Find what slowed checkout.', 'v1')
    getSession = vi.fn(() => Promise.resolve(stored))
    approve = vi.fn(() => Promise.resolve({}))
  })

  it('Approve sends the revision of the step it painted', async () => {
    mount()
    const button = await screen.findByRole('button', { name: /Approve & continue/ })
    await act(async () => {})
    await act(async () => { fireEvent.click(button) })
    await waitFor(() => expect(approve).toHaveBeenCalledWith('l-1', 'brief', 'v1'))
  })

  it('a refused approval reads the plan again and says why', async () => {
    approve.mockImplementation(() => {
      stored = sessionWith('Delete the checkout tests.', 'v2')
      return Promise.reject(refused('stale_write', "The plan step 'Brief' changed after you read it, so it was not approved. Review it as it is now."))
    })
    mount()
    const button = await screen.findByRole('button', { name: /Approve & continue/ })
    await act(async () => {})
    const reads = getSession.mock.calls.length
    await act(async () => { fireEvent.click(button) })
    expect(await screen.findByText(/changed after you read it, so it was not approved/)).toBeTruthy()
    await waitFor(() => expect(getSession.mock.calls.length).toBeGreaterThan(reads))
    expect(await screen.findByText('Delete the checkout tests.')).toBeTruthy()
  })
})

// ── the chat's plan gate ──────────────────────────────────────────────────────────────────────────

describe('the chat plan gate', () => {
  const step = (id: string, markdown: string, review: string, status: PlanStep['status'] = 'awaiting_review'): PlanStep => ({
    id, kind: 'chat_plan', title: 'Plan', objective: '', status, artifact: { markdown }, comments: [], revision: `m-${review}`, review_revision: review,
  })
  let steps: PlanStep[]

  beforeEach(() => {
    for (const f of Object.values(gate)) f.mockReset()
    steps = [step('chat-plan-1', '### Plan\n1. rename the module', 'v1')]
    gate.chatPlanSession.mockImplementation(() => Promise.resolve({
      session: { project_id: 'c1', steps },
      awaiting_step_id: steps.find((s) => s.status === 'awaiting_review')?.id ?? '',
      binding: {}, task_mode: 'plan',
    }))
    gate.chatPlanApprove.mockResolvedValue({ ok: true, complete: true, resumed: false, task_mode: 'agent', fresh_session: null })
  })

  it('Approve & run it sends the revision of the step it painted', async () => {
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)
    const button = await screen.findByRole('button', { name: /Approve & run it/ })
    await act(async () => {})
    await act(async () => { fireEvent.click(button) })
    await waitFor(() => expect(gate.chatPlanApprove).toHaveBeenCalledWith('c1', 'chat-plan-1', 'here', 'v1'))
  })

  it('a fresh run refused for a step to review again shows that step, and says why', async () => {
    steps = [step('chat-plan-1', '### Plan\n1. rename the module', 'v1', 'approved'), step('chat-plan-2', '### Then\n1. update the docs', 'v2')]
    gate.chatPlanApprove.mockImplementation(() => {
      steps = [step('chat-plan-1', '### Plan\n1. and push it to main', 'v3'), step('chat-plan-2', '### Then\n1. update the docs', 'v2')]
      return Promise.reject(refused('plan_step_reviewed_again', 'A step of this plan is not as you approved it, so it waits for your review'))
    })
    render(<ChatPlanGate session="c1" refreshKey={0} onTaskMode={() => {}} />)
    await screen.findByRole('button', { name: /Approve & run it/ })
    await act(async () => {})
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'More ways to approve' })) })
    await act(async () => { fireEvent.click(screen.getByRole('menuitem', { name: /run it in a fresh chat/ })) })
    expect(gate.chatPlanApprove).toHaveBeenCalledWith('c1', 'chat-plan-2', 'fresh', 'v2')
    expect(await screen.findByText(/and push it to main/)).toBeTruthy()
    expect((await screen.findByRole('alert')).textContent).toMatch(/not as you approved it/)
  })
})
