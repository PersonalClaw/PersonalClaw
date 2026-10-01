/** The cockpit's "worker needs your input" toast never stands over the panel it points at.
 *
 *  The toast sat bottom-right, exactly over the Tasks rail's answer panel and its "Use your best
 *  judgment" button, stayed until dismissed and came back on the next Resume, so the click it asked
 *  for could not be made (a browser drive: the toast's subtree intercepted the pointer). And it
 *  said "The worker has a question" while the panel held a status ("ran out of cycles").
 *
 *  Now the answer panel reports whether it is on screen, and a toast that points at it (a question
 *  or a merge conflict, whose Respond focuses the panel's steer box) stands only while it is not —
 *  the rail collapsed or a task's detail open in its place. When it does stand, it repeats what the
 *  panel says, and its Respond opens the rail if it has to and puts the cursor in the steer box.
 *  A toast about something else (a failed action) is not the panel's and still shows.
 *  jsdom has no layout, so the occlusion itself was measured with `elementFromPoint` in a browser;
 *  this pins the rule and the panel's report.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import type { CodeProject } from '../../lib/api'

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, api: { ...actual.api, approvals: () => Promise.resolve([]) } }
})
vi.mock('../../lib/useChatSocket', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/useChatSocket')>()
  return { ...actual, useChatSocket: () => {} }
})

const { ProjectFooter } = await import('./CodeCockpitPage')
const { CockpitToast } = await import('./CodeToast')

/** An IntersectionObserver the test drives: each observed element's callback, by hand. */
const observers: Array<{ cb: IntersectionObserverCallback; el?: Element; disconnected: boolean }> = []
class FakeObserver {
  entry: (typeof observers)[number]
  constructor(cb: IntersectionObserverCallback) { this.entry = { cb, disconnected: false }; observers.push(this.entry) }
  observe(el: Element) { this.entry.el = el }
  unobserve() {}
  disconnect() { this.entry.disconnected = true }
  takeRecords() { return [] }
}
function scroll(onScreen: boolean) {
  for (const o of observers) {
    if (o.disconnected || !o.el) continue
    act(() => o.cb([{ isIntersecting: onScreen, target: o.el } as unknown as IntersectionObserverEntry], o as unknown as IntersectionObserver))
  }
}

beforeEach(() => {
  observers.length = 0
  vi.stubGlobal('IntersectionObserver', FakeObserver)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const STATUS = 'The task ran out of cycles before its check passed. Steer it or tell it to use its best judgment, then Resume.'

const project = (over: Partial<CodeProject> = {}): CodeProject => ({
  id: '6a6fcaf6', name: 'Fix the digest', status: 'needs_input', kind: 'code', stages: [],
  total_cycles: 2, max_cycles: 30, elapsed_seconds: 600,
  pending_question: { question: STATUS }, ...over,
}) as CodeProject

describe('the answer panel says whether it is on screen', () => {
  it('🔴 reports on screen, off screen, and gone', async () => {
    const report = vi.fn()
    const { unmount } = render(<ProjectFooter project={project()} gateFail={null} stalled={null}
      onNudged={() => {}} onAnswerOnScreen={report} />)
    expect(await screen.findByRole('button', { name: 'Use your best judgment' })).toBeTruthy()
    scroll(true)
    expect(report).toHaveBeenLastCalledWith(true)
    scroll(false)
    expect(report).toHaveBeenLastCalledWith(false)
    scroll(true)
    unmount()
    expect(report).toHaveBeenLastCalledWith(false)
  })
})

describe('Respond brings the answer panel to her', () => {
  it('🔴 puts the cursor in the steer box, also when the panel mounts after the click', () => {
    // With the rail collapsed (the case the toast now stands for), Respond dispatched an event
    // only a mounted panel listened for: the click did nothing. The request now stands until the
    // panel, mounted or mounting, has taken it.
    Element.prototype.scrollIntoView = vi.fn()
    const shown = vi.fn()
    render(<ProjectFooter project={project()} gateFail={null} stalled={null} onNudged={() => {}}
      answerWanted onAnswerShown={shown} />)
    expect(document.activeElement?.tagName).toBe('TEXTAREA')
    expect(shown).toHaveBeenCalledTimes(1)
  })

  it('leaves the cursor where it is when nothing was asked for', () => {
    Element.prototype.scrollIntoView = vi.fn()
    const shown = vi.fn()
    render(<ProjectFooter project={project()} gateFail={null} stalled={null} onNudged={() => {}}
      onAnswerShown={shown} />)
    expect(document.activeElement?.tagName).not.toBe('TEXTAREA')
    expect(shown).not.toHaveBeenCalled()
  })
})

describe('the cockpit toast', () => {
  const noop = () => {}

  it('🔴 does not stand over the answer panel while the panel is on screen', () => {
    render(<CockpitToast toast={{ kind: 'question', text: '' }} answerOnScreen question={STATUS}
      onDismiss={noop} onRespond={noop} />)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('🔴 says what the panel says when the panel is off screen', () => {
    render(<CockpitToast toast={{ kind: 'question', text: '' }} answerOnScreen={false} question={STATUS}
      onDismiss={noop} onRespond={noop} />)
    const toast = screen.getByRole('alert')
    expect(toast.textContent).toContain('The worker needs your input')
    expect(toast.textContent).toContain('The task ran out of cycles before its check passed.')
    expect(toast.textContent).not.toContain('has a question')
    expect(screen.getByRole('button', { name: 'Respond' })).toBeTruthy()
  })

  it('says it is waiting when the worker left no words', () => {
    render(<CockpitToast toast={{ kind: 'question', text: '' }} answerOnScreen={false}
      onDismiss={noop} onRespond={noop} />)
    expect(screen.getByRole('alert').textContent).toContain('It paused and is waiting on you.')
  })

  it('a merge conflict points at the same panel, and keeps out of its way too', () => {
    render(<CockpitToast toast={{ kind: 'conflict', text: 'CONFLICT in digest.py' }} answerOnScreen
      onDismiss={noop} onRespond={noop} />)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a failed action is not the panel’s, and still shows', () => {
    render(<CockpitToast toast={{ kind: 'error', text: "Couldn't pause this project: offline" }} answerOnScreen
      onDismiss={noop} onRespond={noop} />)
    expect(screen.getByRole('alert').textContent).toContain("Couldn't pause this project: offline")
    expect(screen.queryByRole('button', { name: 'Respond' })).toBeNull()
  })
})
