/**
 * A planning step whose pass came back with nothing usable says why, at once, with its Retry.
 *
 * 🔴 Before: the server reverted the step to pending with no reason kept, so the walkthrough could
 * only wait out its quiet clock and then guess — "This step has been quiet for a while — the
 * planner may still be working, or it may have errored". Measured on a step whose planner had
 * written its file three times, each time with an unescaped quote in the JSON: nothing on the page
 * said the planner wrote a file PersonalClaw could not read. The step now carries the reason.
 *
 * And the walkthrough's Cancel is its own control: it stops the planning (the host deletes the
 * draft and brings the task back to the composer), where the "loop is gone" exit only leaves.
 */
import { it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PlanningWalkthrough, type WalkthroughConfig } from './PlanningWalkthrough'
import type { PlanSession } from '../lib/api'

const WHY = 'The planner wrote step_artifact.json, but it is not valid JSON: Expecting \',\' delimiter at line 2, column 47.'
const nowS = () => Date.now() / 1000

function session(error: string): PlanSession {
  return {
    project_id: 'l-1',
    created_at: nowS() - 30,
    updated_at: nowS() - 5, // a moment ago: the quiet clock alone would not offer a Retry yet
    steps: [{ id: 'step-0', kind: 'problem_framing', title: 'Frame it', status: 'pending', error } as never],
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
  // jsdom implements no scrolling, and the component scrolls its activity feed on update.
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
})
afterEach(() => cleanup())

it('a step whose pass failed says why and offers its Retry straight away', async () => {
  const retry = vi.fn(() => Promise.resolve({}))
  render(<PlanningWalkthrough id="l-1" cfg={cfg(session(WHY), retry)} onReady={() => {}} onBack={() => {}} onCancel={() => Promise.resolve(true)} onStop={() => Promise.resolve(true)} />)

  expect(await screen.findByText(WHY)).toBeTruthy()
  expect(screen.queryByText(/quiet for a while/i), 'it guesses instead of saying').toBeNull()
  expect(screen.queryByText(/drafting this step/i), 'it claims the planner is drafting').toBeNull()
  expect(screen.getByText('Planning paused'), 'the header still claims the planner is working').toBeTruthy()
  expect(document.querySelectorAll('.animate-spin').length, 'a spinner still claims work in flight').toBe(0)
  await userEvent.click(screen.getByRole('button', { name: /retry this step/i }))
  expect(retry).toHaveBeenCalledWith('l-1')
})

it('a step with no failure still reads as being drafted', async () => {
  render(<PlanningWalkthrough id="l-1" cfg={cfg(session(''))} onReady={() => {}} onBack={() => {}} onCancel={() => Promise.resolve(true)} onStop={() => Promise.resolve(true)} />)

  expect(await screen.findByText(/drafting this step/i)).toBeTruthy()
})

it('Cancel stops the planning through its own control, not the gone-loop exit', async () => {
  const onBack = vi.fn()
  const onCancel = vi.fn(() => Promise.resolve(true))
  render(<PlanningWalkthrough id="l-1" cfg={cfg(session(''))} onReady={() => {}} onBack={onBack} onCancel={onCancel} onStop={() => Promise.resolve(true)} />)

  await userEvent.click(await screen.findByRole('button', { name: 'Cancel and edit the task' }))

  expect(onCancel).toHaveBeenCalledTimes(1)
  expect(onBack).not.toHaveBeenCalled()
})

it('the draft going away while Cancel deletes it is not read as "deleted elsewhere"', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  try {
    let deleted = false
    const c = cfg(session(''))
    c.api.getSession = () => deleted
      ? Promise.reject(Object.assign(new Error('Not found'), { status: 404 }))
      : Promise.resolve(session(''))
    let leave: (left: boolean) => void = () => {}
    const onCancel = vi.fn(() => { deleted = true; return new Promise<boolean>((r) => { leave = r }) })
    const onBack = vi.fn()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    render(<PlanningWalkthrough id="l-1" cfg={c} onReady={() => {}} onBack={onBack} onCancel={onCancel} onStop={() => Promise.resolve(true)} />)

    await user.click(await screen.findByRole('button', { name: 'Cancel and edit the task' }))
    await act(() => vi.advanceTimersByTimeAsync(10_000)) // three polls while the host is leaving

    expect(onBack, 'a second exit raced the cancel to the composer').not.toHaveBeenCalled()
    leave(true)
  } finally {
    vi.useRealTimers()
  }
})

it('the walkthrough has its own Stop, beside Cancel', async () => {
  const onCancel = vi.fn(() => Promise.resolve(true))
  const onStop = vi.fn(() => Promise.resolve(true))
  render(<PlanningWalkthrough id="l-1" cfg={cfg(session(''))} onReady={() => {}} onBack={() => {}} onCancel={onCancel} onStop={onStop} />)

  await userEvent.click(await screen.findByRole('button', { name: /^stop$/i }))

  expect(onStop).toHaveBeenCalledTimes(1)
  expect(onCancel).not.toHaveBeenCalled()
})
