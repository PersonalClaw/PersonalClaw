/**
 * A run a spend cap refused reads as PAUSED, with the cap's own sentence, where it is changed, and
 * Resume — never "Drafting this step…" or a question to answer.
 *
 * 🔴 Measured on a Code loop's walkthrough at 2 of 3 steps approved: the daily dollar cap refused
 * the planner three times and the page stayed on "Planning… · Drafting this step…" with a spinner,
 * while the refusal's only words were in the planner session and the gateway log. The gateway now
 * pauses the walkthrough (`session.paused`, the step's `error` holding the cap's sentence) and a
 * running loop asks as `needs_input` with `pending_question.spend_cap`; both read the same notice.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { CodeProject, PlanSession } from '../lib/api'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return { ...actual, api: { ...actual.api, approvals: () => Promise.resolve([]), uLoopAction: vi.fn(() => Promise.resolve({})) } }
})
vi.mock('../lib/useChatSocket', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/useChatSocket')>()
  return { ...actual, useChatSocket: () => {} }
})

const { PlanningWalkthrough } = await import('./PlanningWalkthrough')
const { ProjectFooter } = await import('../pages/code/CodeCockpitPage')
const { api } = await import('../lib/api')
type WalkthroughConfig = import('./PlanningWalkthrough').WalkthroughConfig

const CAP = 'The daily dollar budget has $3.94 of $4.00 spent, and a call to cloud:example-large may cost $0.30, more than the $0.06 left: raise Max dollars / day in Settings → Guardrails (0 removes the cap), or wait for it to reset at midnight.'
const nowS = () => Date.now() / 1000

function paused(): PlanSession {
  return {
    project_id: 'l-1',
    created_at: nowS() - 30,
    updated_at: nowS() - 5,
    steps: [
      { id: 'step-0', kind: 'problem_framing', title: 'Frame it', status: 'approved' } as never,
      { id: 'step-1', kind: 'tasks', title: 'Ordered tasks', status: 'pending', error: CAP } as never,
    ],
    paused: { by: 'spend_cap', settings: 'guardrails' },
  } as PlanSession
}

function cfg(sess: PlanSession, retry = vi.fn(() => Promise.resolve({}))): WalkthroughConfig {
  return {
    planSessionKey: (id: string) => `loop-plan-${id}`,
    api: {
      getSession: () => Promise.resolve(sess),
      start: vi.fn(() => Promise.resolve({})),
      approve: vi.fn(() => Promise.resolve({})),
      comment: vi.fn(() => Promise.resolve({})),
      edit: vi.fn(() => Promise.resolve({ session: sess })),
      isReady: () => Promise.resolve(false),
      retry,
    },
    copy: { subtitle: 'Planning', activityLabel: 'Investigation', activityEmpty: 'Nothing yet.', cancel: 'Cancel and edit the task' },
    renderArtifact: () => null,
  }
}

beforeEach(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} takeRecords() { return [] } })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('a planning walkthrough a spend cap paused', () => {
  it('🔴 says it is paused by the cap, with its sentence, its Settings page and Resume', async () => {
    const retry = vi.fn(() => Promise.resolve({}))
    render(<PlanningWalkthrough id="l-1" cfg={cfg(paused(), retry)} onReady={() => {}} onBack={() => {}} onCancel={() => Promise.resolve(true)} onStop={() => Promise.resolve(true)} />)

    expect(await screen.findByText(CAP)).toBeTruthy()
    expect(screen.getByText('Paused by a spend cap')).toBeTruthy()
    expect(screen.queryByText(/drafting this step/i), 'it claims the planner is drafting').toBeNull()
    expect(screen.getByText('Planning paused')).toBeTruthy()
    expect(document.querySelectorAll('.animate-spin').length, 'a spinner still claims work in flight').toBe(0)
    const link = screen.getByRole('link', { name: /open settings → guardrails/i })
    expect(link.getAttribute('href')).toBe('#/settings/guardrails')
    expect(screen.queryByRole('button', { name: /retry this step/i }), 'the cap is read as a failed pass').toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /resume planning/i }))
    expect(retry).toHaveBeenCalledWith('l-1')
  })

  it('a design pass the cap paused says the same', async () => {
    const sess = { ...paused(), steps: [], design_error: CAP } as PlanSession
    render(<PlanningWalkthrough id="l-1" cfg={cfg(sess)} onReady={() => {}} onBack={() => {}} onCancel={() => Promise.resolve(true)} onStop={() => Promise.resolve(true)} />)

    expect(await screen.findByText(CAP)).toBeTruthy()
    expect(screen.getByText('Paused by a spend cap')).toBeTruthy()
    expect(screen.queryByText(/didn.t produce a plan/i)).toBeNull()
  })
})

describe("a running loop's cockpit a spend cap paused", () => {
  const project = (): CodeProject => ({
    id: '6a6fcaf6', name: 'Fix the digest', status: 'needs_input', kind: 'code', stages: [],
    total_cycles: 2, max_cycles: 30, elapsed_seconds: 600,
    pending_question: { question: CAP, why: 'The loop does not try the refused call again by itself. Resume it once the cap has room.', spend_cap: true, settings: 'guardrails' },
  }) as unknown as CodeProject

  it('🔴 shows the pause and Resume, not a question to answer', async () => {
    const onNudged = vi.fn()
    render(<ProjectFooter project={project()} gateFail={null} stalled={null} onNudged={onNudged} />)

    expect(await screen.findByText(CAP)).toBeTruthy()
    expect(screen.getByText('Paused by a spend cap')).toBeTruthy()
    expect(screen.queryByText(/the worker needs your input/i)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Use your best judgment' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /^resume$/i }))
    expect(api.uLoopAction).toHaveBeenCalledWith('6a6fcaf6', 'resume')
    expect(onNudged).toHaveBeenCalled()
  })
})
