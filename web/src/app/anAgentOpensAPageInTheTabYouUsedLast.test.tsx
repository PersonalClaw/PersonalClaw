/**
 * A local agent's `open_cockpit` opens the page it names in the dashboard tab you used last.
 *
 * 🔴 Measured before: the control bridge broadcast a `navigate` frame and answered the agent as if
 * the page had opened, but nothing in the dashboard read the frame, so no page ever changed. Now
 * the gateway sends it to the one tab on this computer that took focus last
 * (`tests/test_an_external_agents_open_a_page_opens_a_dashboard_page.py`), each tab says when it
 * takes focus, and the shell reads the frame (`useBridgeNavigation`):
 *  - it builds the path from the frame's page and id, and moves only to one of the dashboard's own
 *    pages; anything else moves nothing;
 *  - a tab you clicked or typed in a moment ago, or are typing in, does not move under your hand: it
 *    says which client asked for which page, with a link;
 *  - a tab that moves says which client moved it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import type { WsMessage } from '../lib/useChatSocket'

const sockets: Array<(m: WsMessage) => void> = []
const statuses: Array<(connected: boolean) => void> = []
const sent: Array<Record<string, unknown>> = []
vi.mock('../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: WsMessage) => void, _r: unknown, onStatus?: (c: boolean) => void) => {
    sockets.push(onMessage)
    if (onStatus) statuses.push(onStatus)
  },
  sendFrame: (frame: Record<string, unknown>) => { sent.push(frame) },
}))

const { useBridgeNavigation, UNDER_YOUR_HAND_MS, SETTLING_MS } = await import('./useBridgeNavigation')
const shellSource = (await import('./App.tsx?raw')).default

const PAGES = new Set(['loops', 'chat', 'settings', 'mission-control'])
const routable = (page: string) => PAGES.has(page)

function frame(data: Record<string, unknown>): void {
  act(() => { for (const s of sockets) s({ type: 'navigate', data: { source: 'control_bridge', client: 'Editor extension', ...data } }) })
}

function shell() {
  const navigate = vi.fn()
  renderHook(() => useBridgeNavigation(navigate, routable))
  return navigate
}

const listeners: Array<(e: Event) => void> = []

function toasts(): unknown[] {
  const heard: unknown[] = []
  const listen = (e: Event) => { heard.push((e as CustomEvent).detail) }
  listeners.push(listen)
  window.addEventListener('ne:toast', listen)
  return heard
}

beforeEach(() => {
  sockets.length = 0
  statuses.length = 0
  sent.length = 0
  vi.spyOn(document, 'hasFocus').mockReturnValue(false)
})

afterEach(() => {
  for (const listen of listeners.splice(0)) window.removeEventListener('ne:toast', listen)
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('a navigate frame from the control bridge', () => {
  it('opens the page it names, and says which client opened it', () => {
    const navigate = shell()
    const heard = toasts()

    frame({ kind: 'loops', id: 'abc12345' })

    expect(navigate).toHaveBeenCalledExactlyOnceWith('loops/abc12345')
    expect(heard).toContainEqual({ level: 'info', message: 'Opened by Editor extension.' })
  })

  it('opens a page with no id at the page', () => {
    const navigate = shell()

    frame({ kind: 'chat', id: '' })

    expect(navigate).toHaveBeenCalledExactlyOnceWith('chat')
  })

  it.each([
    ['a page the dashboard does not have', { kind: 'nowhere' }],
    ['a script as the page', { kind: 'javascript:alert(1)' }],
    ['a route as the page', { kind: '#/loops' }],
    ['a parent segment as the id', { kind: 'loops', id: '..' }],
    ['a path as the id', { kind: 'loops', id: 'a/b' }],
    ['a query in the id', { kind: 'loops', id: 'a?b=1' }],
    ['a letter outside ASCII in the id', { kind: 'loops', id: 'café' }],
    ['no page', {}],
  ])('moves nothing for %s', (_why, data) => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const navigate = shell()

    frame(data)

    expect(navigate).not.toHaveBeenCalled()
    expect(warn).toHaveBeenCalled()
  })

  it('is not read from anything but the bridge', () => {
    const navigate = shell()

    frame({ kind: 'loops', source: 'someone_else' })

    expect(navigate).not.toHaveBeenCalled()
  })

  it('does not move a tab you clicked in a moment ago, and says who asked for what', () => {
    const navigate = shell()
    const heard = toasts()
    vi.spyOn(performance, 'now').mockReturnValue(10_000)
    act(() => { window.dispatchEvent(new Event('pointerdown')) })
    vi.spyOn(performance, 'now').mockReturnValue(10_000 + 500)

    frame({ kind: 'mission-control' })

    expect(navigate).not.toHaveBeenCalled()
    expect(heard).toEqual([{
      level: 'info',
      message: 'Editor extension asked to open Mission control.',
      href: '#/mission-control',
      hrefLabel: 'Open it',
    }])
  })

  it('moves a tab whose last click was longer ago than that', () => {
    const navigate = shell()
    vi.spyOn(performance, 'now').mockReturnValue(10_000)
    act(() => { window.dispatchEvent(new Event('keydown')) })
    vi.spyOn(performance, 'now').mockReturnValue(10_000 + UNDER_YOUR_HAND_MS + 1)

    frame({ kind: 'loops' })

    expect(navigate).toHaveBeenCalledExactlyOnceWith('loops')
  })

  it('ignores a click just after it moved the page, and takes one after that', () => {
    shell()
    const pressed = vi.fn()
    const button = document.createElement('button')
    button.addEventListener('click', pressed)
    document.body.appendChild(button)
    vi.spyOn(performance, 'now').mockReturnValue(50_000)
    frame({ kind: 'loops' })

    vi.spyOn(performance, 'now').mockReturnValue(50_000 + SETTLING_MS - 1)
    button.click()
    vi.spyOn(performance, 'now').mockReturnValue(50_000 + SETTLING_MS)
    button.click()

    expect(pressed).toHaveBeenCalledTimes(1)
  })

  it('does not move a tab you are typing in', () => {
    const navigate = shell()
    vi.spyOn(document, 'hasFocus').mockReturnValue(true)
    const box = document.createElement('textarea')
    document.body.appendChild(box)
    box.focus()

    frame({ kind: 'chat' })

    expect(navigate).not.toHaveBeenCalled()
  })
})

describe('the shell', () => {
  it('reads the frame on every page, against the routes it serves', () => {
    expect(shellSource).toContain('useBridgeNavigation(navigate, (page) => ROUTABLE.has(page))')
  })
})

describe('the tab says when it takes focus', () => {
  it('when the window takes focus, and when its socket opens while it has focus', () => {
    shell()

    act(() => { window.dispatchEvent(new Event('focus')) })
    act(() => { for (const s of statuses) s(true) })  // unfocused: the open says nothing
    vi.spyOn(document, 'hasFocus').mockReturnValue(true)
    act(() => { for (const s of statuses) s(true) })

    expect(sent).toEqual([{ type: 'focused' }, { type: 'focused' }])
  })
})
