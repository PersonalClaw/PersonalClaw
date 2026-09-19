/**
 * #2263 — the widget error channel had a producer and no consumer on the HTML host.
 *
 * `widget-error {message}` is part of the documented wire contract, and three things in the child
 * post it: the react error boundary, a top-level script failure, and (now) a form submit the
 * sandbox cannot deliver. `useWidgetWire` dispatches it as `h.onError?.(msg.message)` — and
 * `WidgetFrame` passed no `onError`, so every one of them was dropped. A widget whose script threw,
 * or whose Submit could never post, failed in total silence: the field report is a user clicking
 * Submit twice, months apart, and getting nothing at all.
 *
 * Driven through the real host: render the frame, post from ITS contentWindow (the provenance the
 * bridge requires), and assert the failure reaches the user.
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, act, cleanup } from '@testing-library/react'
import { WidgetFrame } from './WidgetFrame'

const notified: unknown[][] = []
vi.mock('../../app/appSdk', () => ({
  notify: (...args: unknown[]) => { notified.push(args) },
  launchChat: () => {},
}))
vi.mock('../../lib/api', () => ({
  api: { artifactExists: vi.fn(async () => false), createArtifact: vi.fn(async () => ({})), pinTile: vi.fn(async () => ({})) },
}))

beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:widget-error-test'
    URL.revokeObjectURL = () => {}
  }
})
beforeEach(() => { notified.length = 0; cleanup() })

function mountAndPost(data: unknown, opts: { fromFrame?: boolean } = {}) {
  const view = render(<WidgetFrame html="<p>hi</p>" title="W" />)
  const frame = view.container.querySelector('iframe')
  act(() => {
    window.dispatchEvent(new MessageEvent('message', {
      data,
      source: (opts.fromFrame === false ? window : frame?.contentWindow) as MessageEventSource,
    }))
  })
  return view
}

describe('a widget error reaches the user', () => {
  it('surfaces the child’s message instead of dropping it', () => {
    mountAndPost({ type: 'widget-error', message: 'This widget cannot submit: use data-action' })
    expect(notified.length, 'onError was unwired, so this channel was mute').toBe(1)
    expect(String(notified[0][0])).toMatch(/cannot submit/)
    expect(notified[0][1], 'a failure is an error toast, not an informational one').toBe('error')
  })

  it('reports the sandbox-submit message verbatim', () => {
    // The exact string the child posts for a form it cannot deliver — the two halves an author
    // needs are the attribute and the reason.
    const message = 'This widget cannot submit: a widget sends data with data-action on the button (form submission is blocked in the widget sandbox).'
    mountAndPost({ type: 'widget-error', message })
    expect(String(notified[0][0])).toBe(message)
  })

  it('ignores a message that did not come from THIS frame', () => {
    // The provenance rule the bridge documents: the page itself, a sibling frame or an extension
    // must not be able to raise a widget error (or forge anything else).
    mountAndPost({ type: 'widget-error', message: 'forged' }, { fromFrame: false })
    expect(notified.length).toBe(0)
  })

  it('says nothing when the widget is fine', () => {
    // Vacuity guard: a height report must not toast.
    mountAndPost({ type: 'widget-height', height: 120, width: 300 })
    expect(notified.length).toBe(0)
  })
})
