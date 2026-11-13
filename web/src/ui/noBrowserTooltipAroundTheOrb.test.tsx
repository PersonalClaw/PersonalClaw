import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ThreadRail } from './ThreadRail'
import { TOOLTIP_DELAY_MS } from './Tooltip'
import { SHOW_DELAY_MS, TurnStatusRow } from './TurnStatusRow'
import { ProjectThreadRow } from '../pages/projects/ProjectThreads'
import type { Thread, ThreadPip } from '../lib/useThreads'

// ── No browser tooltip on the sidebar's rows, a project's rows or the live line's tokens (#6117) ──
//
// Four of the orb's places draw no browser tooltip: a sidebar row, its mark, a project's chat row
// and the live line's tokens each say their words in the app's own tooltip (`ui/Tooltip.tsx`), and
// none of them carries a native `title`. A sidebar row's tooltip is its name in words: what the
// name already says is no description, so a screen reader hears it once, and what the name lacks
// (an app's permissions, a helper's ask) is the row's description, as the browser's title made it.
// A project row's tooltip is its title, shown only once the title is cut short. A row's tooltip
// sits beside the row, or on its own row where neither side has room, so it covers no other row of
// its list and takes no other row's pointer.

const t = (key: string, pip: ThreadPip | null): Thread => ({
  key, kind: 'chat', id: key, title: `Chat ${key}`, projectId: '', route: `chat/${key}`,
  pip, pinned: false, activeAt: 0, folderId: '', tagIds: [],
})
const PIPS: Array<[ThreadPip, string]> = [['running', 'Running'], ['waiting', 'Waiting on you'], ['unread', 'Unread'], ['failed', 'Failed']]

function media() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: false, media: q, onchange: null,
    addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  }))
}
const bubble = () => document.querySelector('[data-tooltip-bubble]')
/** Hover `el` for the tooltip's delay, as a mouse does, and read what the app's tooltip says. */
function hover(el: Element): string | null {
  fireEvent.pointerEnter(el, { pointerType: 'mouse' })
  act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS + 10) })
  const said = bubble()?.textContent ?? null
  fireEvent.pointerLeave(el, { pointerType: 'mouse' })
  act(() => { vi.advanceTimersByTime(500) })
  return said
}
/** Every native title under `root`, itself included. */
const titled = (root: Element) => [root, ...root.querySelectorAll('*')].filter((el) => el.hasAttribute('title'))

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('the sidebar', () => {
  const rail = () => render(<ThreadRail rows={[...PIPS.map(([pip]) => t(pip, pip)), t('calm', null)]} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}} />)

  it('no row and no mark carries a native title, and a plain row adds no description to its name', () => {
    rail()
    const rows = [...document.querySelectorAll<HTMLElement>('[data-thread-key]')]
    expect(rows, 'the control: the rows are there').toHaveLength(PIPS.length + 1)
    for (const row of rows) {
      expect(titled(row).map((el) => el.outerHTML.slice(0, 60)), row.getAttribute('aria-label')!).toEqual([])
      expect(row, `${row.getAttribute('aria-label')} is read twice`).not.toHaveAccessibleDescription()
    }
  })

  it('what a row’s name lacks, an app’s permissions or a helper’s ask, is its description', () => {
    const app: Thread = { ...t('zap', 'unread'), startedBy: { created_by_app: 'zap', created_by_app_name: 'Zapier' } }
    const ask: Thread = { ...t('ask', 'waiting'), pipNote: 'A helper is waiting for your approval: Bash' }
    render(<ThreadRail rows={[app, ask]} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}} />)
    const zap = screen.getByRole('button', { name: /^Chat zap, started by Zapier, unread$/ })
    expect(zap).toHaveAccessibleDescription(/runs with Zapier's permissions/)
    expect(zap.getAttribute('aria-label') ?? '', 'the name took the note').not.toMatch(/permissions/)
    expect(screen.getByRole('button', { name: /^Chat ask, waiting on you$/ })).toHaveAccessibleDescription('A helper is waiting for your approval: Bash')
  })

  it('hovering a row shows the app’s tooltip: its name, and its state word where it has a mark', () => {
    vi.useFakeTimers()
    rail()
    for (const [pip, word] of PIPS) {
      const row = screen.getByRole('button', { name: new RegExp(`^Chat ${pip},`) })
      expect(hover(row), pip).toBe(`Chat ${pip} — ${word}`)
    }
    expect(hover(screen.getByRole('button', { name: 'Chat calm' })), 'a calm row').toBe('Chat calm')
  })
})

/** A project row's title as the page lays it out: `cut` when it is cut short, whole when not. */
function laidOut(row: HTMLElement, cut: boolean) {
  const title = row.querySelector<HTMLElement>('.truncate')!
  Object.defineProperty(title, 'scrollWidth', { configurable: true, get: () => (cut ? 400 : 120) })
  Object.defineProperty(title, 'clientWidth', { configurable: true, get: () => 120 })
}

describe('a project’s chat row', () => {
  it('carries no native title, and its tooltip is its whole title once the title is cut short', () => {
    vi.useFakeTimers()
    render(<ul><ProjectThreadRow thread={t('p1', 'running')} onOpen={() => {}} /></ul>)
    const row = screen.getByRole('button', { name: 'Chat: Chat p1, running' })
    expect(titled(row)).toEqual([])
    expect(row.hasAttribute('aria-describedby')).toBe(false)
    laidOut(row, true)
    expect(hover(row)).toBe('Chat p1')
  })

  it('shows no tooltip while its whole title shows, since the tip would only repeat it', () => {
    vi.useFakeTimers()
    render(<ul><ProjectThreadRow thread={t('p2', 'running')} onOpen={() => {}} /></ul>)
    const row = screen.getByRole('button', { name: 'Chat: Chat p2, running' })
    laidOut(row, false)
    expect(hover(row), 'a whole title had a tooltip').toBeNull()
  })
})

describe('a row’s tooltip sits beside it, or on its own row, over no other row of its list', () => {
  /** The rows of `selector` laid out down a list, `gap` px apart from `y` 120, each `width` wide
   *  from `x` and 32 px tall, and each bubble `bubble` wide by 24. */
  const ORIGINAL = { rect: HTMLElement.prototype.getBoundingClientRect, width: window.innerWidth }
  afterEach(() => {
    HTMLElement.prototype.getBoundingClientRect = ORIGINAL.rect
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: ORIGINAL.width })
  })
  let bubbleWidth = 140
  function layout({ selector = '[data-thread-key]', x = 8, width = 256, gap = 32, bubble = 140 } = {}) {
    bubbleWidth = bubble
    HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
      const rows = [...document.querySelectorAll(selector)]
      const i = rows.indexOf(this)
      if (i >= 0) return DOMRect.fromRect({ x, y: 120 + i * gap, width, height: 32 })
      if (this.matches('[data-tooltip-bubble]')) return DOMRect.fromRect({ x: 0, y: 0, width: bubble, height: 24 })
      return DOMRect.fromRect({ x: 0, y: 0, width: 0, height: 0 })
    }
  }
  const rectOfBubble = () => {
    const b = document.querySelector<HTMLElement>('[data-tooltip-bubble]')!
    return DOMRect.fromRect({ x: parseFloat(b.style.left), y: parseFloat(b.style.top), width: bubbleWidth, height: 24 })
  }
  const overlaps = (a: DOMRect, b: DOMRect) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom

  it('an open tip covers no other row, and the row above opens its own', () => {
    vi.useFakeTimers()
    render(<ThreadRail rows={[t('one', null), t('two', 'running'), t('three', 'waiting')]} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}} />)
    layout()
    const rows = [...document.querySelectorAll<HTMLElement>('[data-thread-key]')]
    fireEvent.pointerEnter(rows[1], { pointerType: 'mouse' })
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS + 10) })
    act(() => { vi.advanceTimersByTime(20) })
    expect(bubble()?.textContent, 'the control: the tip opened').toBe('Chat two — Running')
    const tip = rectOfBubble()
    rows.forEach((row, i) => {
      if (i !== 1) expect(overlaps(tip, row.getBoundingClientRect()), `the tip covers row ${i}`).toBe(false)
    })
    // Up onto the row above: its own tip opens, for nothing of the first one's is in the way.
    fireEvent.pointerLeave(rows[1], { pointerType: 'mouse' })
    fireEvent.pointerEnter(rows[0], { pointerType: 'mouse' })
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS + 10) })
    expect(bubble()?.textContent, 'the row above did not open its own tip').toBe('Chat one')
  })

  it('in a list docked at the window’s end, the tip flips to the row’s start', () => {
    vi.useFakeTimers()
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 600 })
    render(<ThreadRail rows={[t('one', null), t('two', 'running'), t('three', 'waiting')]} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}} />)
    layout({ x: 300, width: 256 })
    const rows = [...document.querySelectorAll<HTMLElement>('[data-thread-key]')]
    fireEvent.pointerEnter(rows[1], { pointerType: 'mouse' })
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS + 10) })
    act(() => { vi.advanceTimersByTime(20) })
    expect(bubble()?.textContent, 'the control: the tip opened').toBe('Chat two — Running')
    const tip = rectOfBubble()
    expect(tip.left, 'the tip did not flip to the row’s start').toBe(300 - 8 - 140)
    rows.forEach((row) => expect(overlaps(tip, row.getBoundingClientRect()), 'the tip covers a row').toBe(false))
  })

  it('a project row as wide as the window has its tip on its own row, taking no pointer, and the row above opens its own', () => {
    vi.useFakeTimers()
    render(<ul>{['p1', 'p2', 'p3'].map((k) => <ProjectThreadRow key={k} thread={t(k, 'running')} onOpen={() => {}} />)}</ul>)
    layout({ selector: 'ul > li > button', x: 212, width: window.innerWidth - 16 - 212, gap: 36, bubble: 320 })
    const rows = [...document.querySelectorAll<HTMLElement>('ul > li > button')]
    for (const row of rows) laidOut(row, true)
    fireEvent.pointerEnter(rows[1], { pointerType: 'mouse' })
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS + 10) })
    act(() => { vi.advanceTimersByTime(20) })
    expect(bubble()?.textContent, 'the control: the tip opened').toBe('Chat p2')
    const tip = rectOfBubble()
    rows.forEach((row, i) => {
      if (i !== 1) expect(overlaps(tip, row.getBoundingClientRect()), `the tip covers project row ${i}`).toBe(false)
    })
    expect(bubble()!.className, 'a tip on its own row takes the row’s pointer').toMatch(/\bpointer-events-none\b/)
    fireEvent.pointerLeave(rows[1], { pointerType: 'mouse' })
    fireEvent.pointerEnter(rows[0], { pointerType: 'mouse' })
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS + 10) })
    expect(bubble()?.textContent, 'the row above did not open its own tip').toBe('Chat p1')
  })
})

describe('the live line’s tokens', () => {
  it('carry no native title, say what they are in words, and their tooltip says it to a pointer', async () => {
    vi.useFakeTimers()
    media()
    render(<TurnStatusRow running phase="tool" line="Search the web" startedAt={Date.now()} usage={{ input: 1200, output: 34 }} onStop={() => {}} />)
    await act(async () => { vi.advanceTimersByTime(SHOW_DELAY_MS) })
    const line = document.querySelector('[data-turn-status]')!
    const tokens = line.querySelector('[data-turn-tokens]')!
    expect(tokens.textContent, 'the control: the tokens are shown').toBe('1,200 in · 34 out')
    expect(titled(line)).toEqual([])
    // The words go before the numbers, for a screen reader: the tokens are text, not a control,
    // so they take no focus and carry no description.
    const label = tokens.previousElementSibling as HTMLElement | null
    expect(label?.textContent, 'the tokens are not said in words').toBe('Tokens the model reported for this turn:')
    expect(label?.className).toMatch(/\bsr-only\b/)
    expect(tokens.hasAttribute('aria-describedby'), 'static text carries a description').toBe(false)
    expect(tokens.hasAttribute('tabindex'), 'static text is a tab stop').toBe(false)
    expect(hover(tokens)).toBe('Tokens the model reported for this turn')
  })
})
