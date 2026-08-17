/** The host half of the widget bridge: who consumes an action, and what a widget
 *  cannot make the host do.
 *
 *  The consumer assertions below are the SAME ones that were run green against
 *  ChatPage's pre-extraction inline listener before this hook existed — that is what
 *  "behaviour-identical extraction" means here: the text arrives UNTRIMMED, blank and
 *  non-string details are ignored, and the listener unbinds on unmount. */
import { useEffect } from 'react'
import { render, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  MAX_ACTION_TEXT_BYTES,
  WIDGET_ACTION_EVENT,
  composeWidgetActionText,
  publishWidgetAction,
  readWidgetMessage,
  takePendingWidgetAction,
  useWidgetActionBridge,
  useWidgetActionLauncher,
} from './useWidgetActionBridge'

const launched: unknown[] = []
vi.mock('../../app/appSdk', () => ({
  launchChat: (opts?: unknown) => { launched.push(opts ?? {}) },
}))

function publish(detail: unknown) {
  act(() => { window.dispatchEvent(new CustomEvent(WIDGET_ACTION_EVENT, { detail })) })
}

function ChatHost({ onAction }: { onAction: (t: string) => void }) {
  useWidgetActionBridge(onAction)
  return null
}

function Shell() {
  useWidgetActionLauncher()
  return null
}

beforeEach(() => { launched.length = 0; takePendingWidgetAction() })

describe('useWidgetActionBridge — the chat host consumer', () => {
  it('passes the text through UNTRIMMED and ignores blank or non-string details', () => {
    const onAction = vi.fn()
    render(<ChatHost onAction={onAction} />)
    publish({ text: '  [UI] refresh  ' })
    publish({ text: '   ' })
    publish({ text: 42 })
    publish({})
    publish(undefined)
    expect(onAction.mock.calls.map((c) => c[0])).toEqual(['  [UI] refresh  '])
  })

  it('stops consuming once the host unmounts', () => {
    const onAction = vi.fn()
    const view = render(<ChatHost onAction={onAction} />)
    view.unmount()
    publish({ text: '[UI] after' })
    expect(onAction).not.toHaveBeenCalled()
  })

  it('carries the saved-artifact slug through as meta', () => {
    const onAction = vi.fn()
    render(<ChatHost onAction={onAction} />)
    publish({ text: '[UI] refresh', slug: 'sales-view' })
    expect(onAction).toHaveBeenCalledWith('[UI] refresh', { slug: 'sales-view' })
  })
})

describe('non-chat hosts route through the ONE ne:launch-chat path', () => {
  it('stages the turn and launches a chat when no chat host is mounted', () => {
    render(<Shell />)
    publish({ text: '[UI] refresh: {"range":"30d"}' })
    expect(launched).toEqual([{}])
    expect(takePendingWidgetAction()).toBe('[UI] refresh: {"range":"30d"}')
  })

  it('does not put the auto-send authority in the URL', () => {
    render(<Shell />)
    publish({ text: '[UI] refresh' })
    // launchChat carries NO prompt: a `?seed=…&send=1` link would let anyone who can
    // get the user to open a URL fire a turn. The text rides in-process instead.
    expect(launched).toEqual([{}])
  })

  it('expires a staged turn that never reached a chat host', () => {
    render(<Shell />)
    const now = vi.spyOn(Date, 'now')
    now.mockReturnValue(1_000)
    publish({ text: '[UI] stale' })
    now.mockReturnValue(1_000 + 20_001)
    expect(takePendingWidgetAction()).toBeNull()
    now.mockRestore()
  })

  it('drains the staged turn exactly once', () => {
    render(<Shell />)
    publish({ text: '[UI] once' })
    expect(takePendingWidgetAction()).toBe('[UI] once')
    expect(takePendingWidgetAction()).toBeNull()
  })

  it('lets a mounted chat host claim the action instead of the shell launcher', () => {
    const onAction = vi.fn()
    render(<Shell />)
    const chat = render(<ChatHost onAction={onAction} />)
    publish({ text: '[UI] in-chat' })
    expect(onAction).toHaveBeenCalledWith('[UI] in-chat', { slug: undefined })
    expect(launched).toEqual([])
    expect(takePendingWidgetAction()).toBeNull()
    // …and hands the wire back when the conversation goes away.
    chat.unmount()
    publish({ text: '[UI] after-chat' })
    expect(launched).toEqual([{}])
  })
})

describe('readWidgetMessage — the iframe trust boundary', () => {
  function frameWithChild(): { frame: HTMLIFrameElement; child: Window } {
    const frame = document.createElement('iframe')
    document.body.appendChild(frame)
    return { frame, child: frame.contentWindow as Window }
  }
  const ev = (data: unknown, source: Window | null) =>
    new MessageEvent('message', { data, source: source as MessageEventSource })

  it('accepts each contract message from THIS frame', () => {
    const { frame, child } = frameWithChild()
    expect(readWidgetMessage(ev({ type: 'widget-action', action: 'go', payload: { a: 1 } }, child), frame))
      .toEqual({ type: 'widget-action', action: 'go', payload: { a: 1 } })
    expect(readWidgetMessage(ev({ type: 'widget-height', height: 12, width: 30 }, child), frame))
      .toEqual({ type: 'widget-height', height: 12, width: 30 })
    expect(readWidgetMessage(ev({ type: 'widget-error', message: 'x' }, child), frame))
      .toEqual({ type: 'widget-error', message: 'x' })
  })

  it('refuses a message from any window that is not this frame', () => {
    const { frame } = frameWithChild()
    const other = frameWithChild().child
    const action = { type: 'widget-action', action: 'go' }
    expect(readWidgetMessage(ev(action, other), frame)).toBeNull()
    expect(readWidgetMessage(ev(action, window), frame)).toBeNull()
    expect(readWidgetMessage(ev(action, null), frame)).toBeNull()
    expect(readWidgetMessage(ev(action, frameWithChild().child), null)).toBeNull()
  })

  it('refuses an out-of-contract shape rather than guessing', () => {
    const { frame, child } = frameWithChild()
    const refused: unknown[] = [
      null,
      'widget-action',
      42,
      { type: 'widget-exec', action: 'rm -rf /' },
      { action: 'go' },
      { type: 42 },
      { type: 'widget-action' },
      { type: 'widget-action', action: '' },
      { type: 'widget-action', action: 7 },
      { type: 'widget-height' },
      { type: 'widget-height', height: '9999' },
      { type: 'widget-height', height: Number.NaN },
      { type: 'widget-height', height: Number.POSITIVE_INFINITY },
    ]
    for (const data of refused) expect(readWidgetMessage(ev(data, child), frame)).toBeNull()
  })

  it('refuses a child that claims the reserved parent→child namespace', () => {
    const { frame, child } = frameWithChild()
    expect(readWidgetMessage(ev({ type: '__edit_mode_set_keys', edits: {} }, child), frame)).toBeNull()
  })

  it('drops a width that is not a positive finite number', () => {
    const { frame, child } = frameWithChild()
    for (const width of [0, -3, '300', Number.NaN]) {
      expect(readWidgetMessage(ev({ type: 'widget-height', height: 10, width }, child), frame))
        .toEqual({ type: 'widget-height', height: 10, width: undefined })
    }
  })
})

describe('composeWidgetActionText — what a widget can put in a turn', () => {
  it('clips an oversized payload at 16 KiB with an honest marker', () => {
    const text = composeWidgetActionText('submit', { blob: 'x'.repeat(64 * 1024) })
    expect(text).not.toBeNull()
    expect(new TextEncoder().encode(text as string).length).toBeLessThanOrEqual(MAX_ACTION_TEXT_BYTES)
    expect(text?.endsWith('…truncated')).toBe(true)
    expect(text?.startsWith('[UI] submit: {"blob":"xxx')).toBe(true)
  })

  it('measures the cap in BYTES, not characters', () => {
    // 12 KiB of 3-byte characters is 36 KiB on the wire — a char count would pass it.
    const text = composeWidgetActionText('submit', { blob: '☃'.repeat(12 * 1024) })
    expect(new TextEncoder().encode(text as string).length).toBeLessThanOrEqual(MAX_ACTION_TEXT_BYTES)
    expect(text?.endsWith('…truncated')).toBe(true)
  })

  it('leaves an in-contract payload untouched', () => {
    expect(composeWidgetActionText('refresh', { a: 1 })).toBe('[UI] refresh: {"a":1}')
    expect(composeWidgetActionText('refresh', {})).toBe('[UI] refresh')
    expect(composeWidgetActionText('refresh', undefined)).toBe('[UI] refresh')
    expect(composeWidgetActionText('refresh', { a: 1 }, { saved: true, slug: 'v' }))
      .toBe('[UI] refresh: {"a":1} (refresh artifact "v" in place)')
    expect(composeWidgetActionText('refresh', { a: 1 }, { saved: false, slug: 'v' }))
      .toBe('[UI] refresh: {"a":1}')
  })

  it('refuses a payload it cannot serialize instead of throwing at its host', () => {
    // postMessage's structured clone carries cycles; JSON.stringify does not. An
    // unhandled throw inside the window listener is a widget crashing the host.
    const cyclic: Record<string, unknown> = { a: 1 }
    cyclic.self = cyclic
    expect(composeWidgetActionText('submit', cyclic)).toBeNull()
  })
})

describe('the bridge only exists while a consumer is mounted', () => {
  it('ignores an action published with nothing listening', () => {
    const onAction = vi.fn()
    function Late() { useEffect(() => { onAction('mounted') }, []); return null }
    publishWidgetAction('[UI] into the void')
    render(<Late />)
    expect(launched).toEqual([])
    expect(takePendingWidgetAction()).toBeNull()
  })
})
