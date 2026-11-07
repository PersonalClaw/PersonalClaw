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
import { closeDialog, subscribeDialogs } from '../dialog/dialogStore'
import {
  MAX_ACTION_TEXT_BYTES,
  WIDGET_ACTION_EVENT,
  coolWidgetHost,
  composeWidgetActionText,
  publishWidgetAction,
  readWidgetMessage,
  declineWidgetHost,
  forgetPendingWidgetAction,
  forgetWidgetHost,
  frameName,
  isStagedWidgetNonce,
  takePendingWidgetAction,
  useWidgetActionBridge,
  useWidgetActionLauncher,
} from './useWidgetActionBridge'

/** Each `ne:launch-chat` the bridge raised, by its detail: the nonce its new chat drains by. */
const launched: { widgetAction?: string }[] = []
const recordLaunch = (e: Event) => { launched.push({ ...(e as CustomEvent).detail }) }
window.addEventListener('ne:launch-chat', recordLaunch)
/** The nonce of the last launch, which only its own chat can drain the stage with. */
const lastNonce = () => launched[launched.length - 1]?.widgetAction

function publish(detail: unknown) {
  act(() => { window.dispatchEvent(new CustomEvent(WIDGET_ACTION_EVENT, { detail })) })
}

function ChatHost({ send, offer, told }: { send?: (t: string, m: unknown) => void; offer?: (t: string, m: unknown) => void; told?: (said: string) => void }) {
  useWidgetActionBridge({ send: send ?? (() => {}), offer: offer ?? (() => {}), told })
  return null
}

/** The dialogs the shell has open, as the dialog host would render them. */
function openDialogs(): { id: number; title: string; body?: unknown }[] {
  let seen: { id: number; title: string; body?: unknown }[] = []
  subscribeDialogs((list) => { seen = list as typeof seen })()
  return seen
}

function Shell({ at }: { at?: string }) {
  useWidgetActionLauncher(at)
  return null
}

beforeEach(async () => {
  launched.length = 0
  forgetPendingWidgetAction()
  for (const d of openDialogs()) closeDialog(d.id, false)
  await Promise.resolve()
})

describe('useWidgetActionBridge — the chat host consumer', () => {
  it('passes the text through UNTRIMMED and ignores blank or non-string details', () => {
    const offer = vi.fn()
    render(<ChatHost offer={offer} />)
    publish({ text: '  [UI] refresh  ' })
    publish({ text: '   ' })
    publish({ text: 42 })
    publish({})
    publish(undefined)
    expect(offer.mock.calls.map((c) => c[0])).toEqual(['  [UI] refresh  '])
  })

  it('stops consuming once the host unmounts', () => {
    const send = vi.fn()
    const offer = vi.fn()
    const view = render(<ChatHost send={send} offer={offer} />)
    view.unmount()
    publish({ text: '[UI] after' })
    expect(send).not.toHaveBeenCalled()
    expect(offer).not.toHaveBeenCalled()
  })

  it('carries the saved-artifact slug, the frame and its mount through as meta', () => {
    const offer = vi.fn()
    render(<ChatHost offer={offer} />)
    publish({ text: '[UI] refresh', slug: 'sales-view', frame: 'Sales\u202e', host: 'frame-7' })
    expect(offer).toHaveBeenCalledWith('[UI] refresh', { slug: 'sales-view', pressed: false, frame: 'Sales\\u202e', host: 'frame-7' })
  })

  it('offers, and asks nothing, while a chat host and the shell are both mounted', () => {
    const offer = vi.fn()
    render(<Shell />)
    render(<ChatHost offer={offer} />)
    publish({ text: '[UI] refresh', host: 'frame-8' })
    expect(offer).toHaveBeenCalledTimes(1)
    expect(openDialogs()).toEqual([])
  })

  it('offers a frame\'s action and sends only one pressed in the page', () => {
    // The chat host has two ways in: an offer, for what a frame posts whatever it claims, and a
    // send, for a press in the page.
    const send = vi.fn()
    const offer = vi.fn()
    render(<ChatHost send={send} offer={offer} />)
    publish({ text: '[UI] from-a-frame' })
    publish({ text: '[UI] claims-a-press', pressed: 'yes' })
    expect(send).not.toHaveBeenCalled()
    expect(offer.mock.calls.map((c) => c[0])).toEqual(['[UI] from-a-frame', '[UI] claims-a-press'])
    publish({ text: '[UI] pressed', label: 'Pressed', pressed: true })
    expect(send).toHaveBeenCalledWith('[UI] pressed', { label: 'Pressed', pressed: true })
  })
})

describe('non-chat hosts route through the ONE ne:launch-chat path', () => {
  it('stages the turn and launches a chat for an action pressed in the page', () => {
    render(<Shell />)
    publish({ text: '[UI] refresh: {"range":"30d"}', pressed: true })
    expect(launched).toHaveLength(1)
    expect(takePendingWidgetAction()).toBeNull()
    expect(takePendingWidgetAction('not-its-nonce')).toBeNull()
    expect(takePendingWidgetAction(lastNonce())?.text).toBe('[UI] refresh: {"range":"30d"}')
  })

  it('drops a staged turn once the page goes anywhere but the chat its launch opens', () => {
    const view = render(<Shell />)
    publish({ text: '[UI] refresh', pressed: true })
    const nonce = lastNonce()
    view.rerender(<Shell at="dashboard" />)
    expect(takePendingWidgetAction(nonce)).toBeNull()
  })

  it('🔴 asks before a frame\'s action, and stages it only on the owner\'s yes', async () => {
    // Red before: a frame's action was staged and a chat opened without the owner's yes.
    render(<Shell />)
    publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-1' })
    expect(launched).toEqual([])
    const [ask] = openDialogs()
    expect(ask?.title).toBe('A widget titled “Sales” asks to send this')
    publish({ text: '[UI] again', frame: 'Sales', host: 'frame-2' })
    expect(openDialogs(), 'one ask at a time').toHaveLength(1)
    await act(async () => { closeDialog(ask.id, true) })
    expect(launched).toHaveLength(1)
    expect(takePendingWidgetAction(lastNonce())?.text).toBe('[UI] refresh')
  })

  it('asks again after the moment a yes keeps', async () => {
    render(<Shell />)
    const now = vi.spyOn(Date, 'now')
    try {
      now.mockReturnValue(50_000)
      publish({ text: '[UI] first', frame: 'Sales', host: 'frame-41' })
      await act(async () => { closeDialog(openDialogs()[0].id, true) })
      now.mockReturnValue(54_900)
      publish({ text: '[UI] soon', frame: 'Sales', host: 'frame-41' })
      expect(openDialogs(), 'within the 5 s after a yes').toEqual([])
      now.mockReturnValue(55_100)
      publish({ text: '[UI] later', frame: 'Sales', host: 'frame-41' })
      expect(openDialogs(), '5.1 s after it').toHaveLength(1)
    } finally {
      now.mockRestore()
      forgetWidgetHost('frame-41')
    }
  })

  it('🔴 remembers a "Don\'t send" for that frame, and keeps a moment after any answer', async () => {
    // A "Don't send" holds for that frame while it is mounted, so a frame posting in a loop
    // asks once.
    render(<Shell />)
    publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-3' })
    expect(openDialogs(), 'premise: it asked').toHaveLength(1)
    await act(async () => { closeDialog(openDialogs()[0].id, false) })
    publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-3' })
    expect(openDialogs(), 'the frame the owner said no to').toEqual([])
    publish({ text: '[UI] other', frame: 'Notes', host: 'frame-4' })
    expect(openDialogs(), 'another frame still asks').toHaveLength(1)
    await act(async () => { closeDialog(openDialogs()[0].id, true) })
    publish({ text: '[UI] at once', frame: 'Notes', host: 'frame-4' })
    expect(openDialogs(), 'a moment after a yes').toEqual([])
    // A chat host's no (an offered draft emptied or dismissed) is the same no.
    declineWidgetHost('frame-5')
    const offer = vi.fn()
    render(<ChatHost offer={offer} />)
    publish({ text: '[UI] refresh', host: 'frame-5' })
    expect(offer).not.toHaveBeenCalled()
  })

  it('tells the owner why a post of theirs did nothing, and says nothing for a frame\'s own', async () => {
    const toasts: string[] = []
    const onToast = (e: Event) => { toasts.push(String((e as CustomEvent).detail?.message)) }
    window.addEventListener('ne:toast', onToast)
    const now = vi.spyOn(Date, 'now')
    try {
      now.mockReturnValue(80_000)
      const shell = render(<Shell />)
      // An ask is open: another frame's post waits for it.
      publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-61', earned: true })
      expect(openDialogs(), 'premise: it asked').toHaveLength(1)
      publish({ text: '[UI] note', frame: 'Notes', host: 'frame-62', earned: true })
      publish({ text: '[UI] note', frame: 'Notes', host: 'frame-62' })
      expect(toasts).toEqual(['“Notes” can ask once the open question is answered.'])
      // Turned down, and a moment after a yes.
      await act(async () => { closeDialog(openDialogs()[0].id, false) })
      publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-61', earned: true })
      publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-61' })
      now.mockReturnValue(86_000)
      publish({ text: '[UI] note', frame: 'Notes', host: 'frame-62', earned: true })
      await act(async () => { closeDialog(openDialogs()[0].id, true) })
      publish({ text: '[UI] note', frame: 'Notes', host: 'frame-62', earned: true })
      publish({ text: '[UI] note', frame: 'Notes', host: 'frame-62' })
      expect(toasts.slice(1)).toEqual(['“Sales” is turned down while it is shown here.', '“Notes” can offer another message in a moment.'])
      // A chat says it on its own line.
      shell.unmount()
      const told = vi.fn()
      render(<ChatHost told={told} />)
      now.mockReturnValue(92_000)
      publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-61', earned: true })
      expect(told).toHaveBeenCalledWith('“Sales” is turned down while this chat is open.')
      expect(toasts).toHaveLength(3)
    } finally {
      now.mockRestore()
      window.removeEventListener('ne:toast', onToast)
      forgetWidgetHost('frame-61')
      forgetWidgetHost('frame-62')
    }
  })

  it('tells the owner once about a frame that posts in a loop, and again only after a while', async () => {
    const toasts: string[] = []
    const onToast = (e: Event) => { toasts.push(String((e as CustomEvent).detail?.message)) }
    window.addEventListener('ne:toast', onToast)
    const now = vi.spyOn(Date, 'now')
    try {
      now.mockReturnValue(200_000)
      render(<Shell />)
      declineWidgetHost('frame-71')
      for (let i = 0; i < 20; i++) publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-71', earned: true })
      expect(toasts).toEqual(['“Sales” is turned down while it is shown here.'])
      now.mockReturnValue(205_100)
      for (let i = 0; i < 20; i++) publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-71', earned: true })
      expect(toasts, 'once more after the cooldown').toHaveLength(2)
    } finally {
      now.mockRestore()
      window.removeEventListener('ne:toast', onToast)
      forgetWidgetHost('frame-71')
    }
  })

  it('tells the owner the frame is turned down right after their no, though a tell just went', () => {
    // The owner's own no forgets the frame's bound, whatever the host: here the bridge's rule on
    // the shell, a decline right after a cooling tell, then a press, says the no at once.
    const toasts: string[] = []
    const onToast = (e: Event) => { toasts.push(String((e as CustomEvent).detail?.message)) }
    window.addEventListener('ne:toast', onToast)
    const now = vi.spyOn(Date, 'now')
    try {
      now.mockReturnValue(300_000)
      render(<Shell />)
      coolWidgetHost('frame-91')
      publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-91', earned: true })
      expect(toasts, 'premise: the cooling tell').toEqual(['“Sales” can offer another message in a moment.'])
      now.mockReturnValue(301_000)
      declineWidgetHost('frame-91')
      publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-91', earned: true })
      expect(toasts.slice(1)).toEqual(['“Sales” is turned down while it is shown here.'])
    } finally {
      now.mockRestore()
      window.removeEventListener('ne:toast', onToast)
      forgetWidgetHost('frame-91')
    }
  })

  it('tells the owner a frame can offer another message in a moment right after their answer to it, though a tell just went', async () => {
    // An answer of the owner's to a frame (an Add, a yes, a Send of its draft: coolWidgetHost)
    // forgets that frame's bound, as a no does.
    const toasts: string[] = []
    const onToast = (e: Event) => { toasts.push(String((e as CustomEvent).detail?.message)) }
    window.addEventListener('ne:toast', onToast)
    const now = vi.spyOn(Date, 'now')
    try {
      now.mockReturnValue(400_000)
      render(<Shell />)
      publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-95', earned: true })
      expect(openDialogs(), 'premise: it asked').toHaveLength(1)
      publish({ text: '[UI] note', frame: 'Notes', host: 'frame-96', earned: true })
      expect(toasts, 'premise: the open-ask tell').toEqual(['“Notes” can ask once the open question is answered.'])
      await act(async () => { closeDialog(openDialogs()[0].id, false) })
      now.mockReturnValue(401_000)
      coolWidgetHost('frame-96')
      publish({ text: '[UI] note', frame: 'Notes', host: 'frame-96', earned: true })
      expect(toasts.slice(1)).toEqual(['“Notes” can offer another message in a moment.'])
    } finally {
      now.mockRestore()
      window.removeEventListener('ne:toast', onToast)
      forgetWidgetHost('frame-95')
      forgetWidgetHost('frame-96')
    }
  })

  it('tells a chat host that passes no line of its own by a toast', () => {
    const toasts: string[] = []
    const onToast = (e: Event) => { toasts.push(String((e as CustomEvent).detail?.message)) }
    window.addEventListener('ne:toast', onToast)
    try {
      render(<ChatHost />)
      declineWidgetHost('frame-81')
      publish({ text: '[UI] refresh', frame: 'Sales', host: 'frame-81', earned: true })
      expect(toasts).toEqual(['“Sales” is turned down while this chat is open.'])
    } finally {
      window.removeEventListener('ne:toast', onToast)
      forgetWidgetHost('frame-81')
    }
  })

  it('does not put the auto-send authority in the URL', () => {
    render(<Shell />)
    publish({ text: '[UI] refresh', pressed: true })
    // The launch carries NO prompt: a `?seed=…&send=1` link would let anyone who can
    // get the user to open a URL fire a turn. The text rides in-process instead, and the
    // launch only a nonce that drains it.
    expect(launched).toEqual([{ widgetAction: expect.any(String) }])
  })

  it('expires a staged turn that never reached a chat host', () => {
    render(<Shell />)
    const now = vi.spyOn(Date, 'now')
    now.mockReturnValue(1_000)
    publish({ text: '[UI] stale', pressed: true })
    now.mockReturnValue(1_000 + 20_001)
    expect(takePendingWidgetAction(lastNonce())).toBeNull()
    now.mockRestore()
  })

  it('drains the staged turn exactly once', () => {
    render(<Shell />)
    publish({ text: '[UI] once', pressed: true })
    expect(takePendingWidgetAction(lastNonce())?.text).toBe('[UI] once')
    expect(takePendingWidgetAction(lastNonce())).toBeNull()
  })

  it('lets a mounted chat host claim the action instead of the shell launcher', () => {
    const send = vi.fn()
    render(<Shell />)
    const chat = render(<ChatHost send={send} />)
    publish({ text: '[UI] in-chat', pressed: true })
    expect(send).toHaveBeenCalledWith('[UI] in-chat', { pressed: true })
    expect(launched).toEqual([])
    // …and hands the wire back when the conversation goes away.
    chat.unmount()
    publish({ text: '[UI] after-chat', pressed: true })
    expect(launched).toHaveLength(1)
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

  it('🔴 composes what it shows: hidden characters escaped, a name\'s whitespace one space', () => {
    // Red before: 200 leading newlines pushed the words out of view, and a bidi override made
    // the text read otherwise than it sends.
    const text = composeWidgetActionText(`${'\n'.repeat(200)}open\u202e\u200b  now`, { href: 'x\u202ey\u2066' })
    expect(text).toBe('[UI] open now: {"href":"x\\u202ey\\u2066"}')
    expect(text?.split('\n')).toHaveLength(1)
    expect(composeWidgetActionText('\u200b\n\t', undefined), 'a name that is all hidden names nothing').toBeNull()
  })

  it('🔴 collapses a run the hidden characters split, and leaves no run of spaces in the payload', () => {
    // Hidden characters are dropped before the collapse, so a run they split is one space, and a
    // payload carries no wall of blank space.
    expect(composeWidgetActionText(`a${'\u200b\n'.repeat(200)}b`, undefined)).toBe('[UI] a b')
    const text = composeWidgetActionText('note', { t: `x${' '.repeat(400)}y` }) ?? ''
    expect(text).not.toMatch(/ {3}/)
    expect(JSON.parse(text.slice('[UI] note: '.length)).t).toBe(`x${' '.repeat(400)}y`)
    // Every other kind of space is written out, the value unchanged.
    const nb = composeWidgetActionText('note', { t: 'a\u00a0b\u3000c' }) ?? ''
    expect(nb).toBe('[UI] note: {"t":"a\\u00a0b\\u3000c"}')
  })

  it('🔴 drops or writes out every default-ignorable character, and keeps an emoji\'s own selector', () => {
    // Red before: hidden characters passed as they were.
    expect(composeWidgetActionText('ok\ufe01\u3164', undefined)).toBe('[UI] ok')
    const payload = composeWidgetActionText('note', { t: `x${'\ufe01'.repeat(40)}\u007f` }) ?? ''
    expect(payload).toMatch(/^[\x20-\x7e]*$/)
    expect(payload).toContain('\\ufe01')
    expect(payload).toContain('\\u007f')
    expect(composeWidgetActionText('like \u2764\ufe0f', undefined)).toBe('[UI] like \u2764\ufe0f')
  })

  it('🔴 counts the braille blank as a space in a name, and writes it out in a payload', () => {
    // It draws as nothing, as a space does, and isn't one.
    expect(composeWidgetActionText(`a${'\u2800'.repeat(300)}b`, undefined)).toBe('[UI] a b')
    expect(composeWidgetActionText('note', { t: 'x\u2800y' })).toBe('[UI] note: {"t":"x\\u2800y"}')
  })

  it('refuses a payload it cannot serialize instead of throwing at its host', () => {
    // postMessage's structured clone carries cycles; JSON.stringify does not. An
    // unhandled throw inside the window listener is a widget crashing the host.
    const cyclic: Record<string, unknown> = { a: 1 }
    cyclic.self = cyclic
    expect(composeWidgetActionText('submit', cyclic)).toBeNull()
  })
})

describe('what a frame cannot put in the shell\'s launch', () => {
  it('mints its nonce where randomUUID does not exist (a plain-http remote link)', () => {
    // randomUUID exists only in a secure context. The nonce comes from what every context has,
    // so over plain http a staged turn still stages: the ask's yes, a page press on a non-chat
    // host, and the rail.
    const had = Object.getOwnPropertyDescriptor(crypto, 'randomUUID')
    Object.defineProperty(crypto, 'randomUUID', { value: undefined, configurable: true })
    try {
      render(<Shell />)
      publish({ text: '[UI] plain', pressed: true })
      expect(launched).toHaveLength(1)
      expect(lastNonce()).toMatch(/^[0-9a-hjkmnp-tv-z]{26}$/)
      expect(takePendingWidgetAction(lastNonce())?.text).toBe('[UI] plain')
    } finally {
      if (had) Object.defineProperty(crypto, 'randomUUID', had)
      else delete (crypto as { randomUUID?: unknown }).randomUUID
    }
  })

  it('names a nonce only on its own launch: one an app\'s event names is no stage', () => {
    render(<Shell />)
    expect(isStagedWidgetNonce('a-guess')).toBe(false)
    publish({ text: '[UI] x', pressed: true })
    expect(isStagedWidgetNonce(lastNonce())).toBe(true)
    expect(isStagedWidgetNonce('a-guess')).toBe(false)
    expect(isStagedWidgetNonce(undefined)).toBe(false)
  })

  it('gives an unpressed action no label, and a no to one that names no frame holds', () => {
    const offer = vi.fn()
    render(<ChatHost offer={offer} />)
    try {
      publish({ text: '[UI] a', label: 'Looks pressed' })
      expect(offer).toHaveBeenCalledTimes(1)
      const meta = offer.mock.calls[0][1] as { label?: string; host?: string }
      expect(meta.label).toBeUndefined()
      declineWidgetHost(meta.host)
      publish({ text: '[UI] b' })
      expect(offer).toHaveBeenCalledTimes(1)
    } finally { forgetWidgetHost('frame-unnamed') }
  })

  it('names a frame by at most 80 code points, its quotes and hidden characters escaped', () => {
    expect(frameName('😀'.repeat(100))).toBe('😀'.repeat(80))
    expect(frameName('Sales” and “more')).toBe('Sales\\u201d and \\u201cmore')
    expect(frameName('a\u202eb')).toBe('a\\u202eb')
    expect(frameName('  ')).toBe('Widget')
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
