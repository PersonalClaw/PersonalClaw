import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import type { Thread } from '../lib/useThreads'
import { ThreadRail, type ThreadRailGroup } from './ThreadRail'

// ── New thread and the search stay pinned at the top of the rail while the threads scroll (#3814) ──
//
// They are one bar at the top of the threads, pinned to the top of the rail's scroll area while the
// threads scroll under it, so a long list never takes away the way to start a thread or find one.
// The bar paints the rail's solid surface and sits above the rows, so no row shows through it.
//
// The rail's scroll area is the sidebar itself (`NavRail`), whose surface is `--color-rail`. While
// the bar is there, the scroll area keeps the bar's height clear at its top (`scroll-padding-top`),
// the room the browser leaves when focus moves to a row; and a row the address opens is scrolled just
// far enough to show it below the bar, not under it.
//
// jsdom lays nothing out, so the sizes below are set by hand: the scroll area, the bar's height
// (72 px), and the open thread's row scrolled just above the scroll area.

function thread(key: string, title: string): Thread {
  const id = key.split(':')[1]
  return { kind: 'chat', id, projectId: 'p-garden', route: `chat/${id}`, pip: null, pinned: false, activeAt: 0, key, title }
}

const GARDEN: ThreadRailGroup = {
  projectId: 'p-garden',
  name: 'Garden',
  threads: Array.from({ length: 30 }, (_, i) => thread(`chat:chat-${i}`, `Garden thread ${i}`)),
}

const rect = (top: number, bottom: number) => ({ top, bottom, left: 0, right: 240, width: 240, height: bottom - top, x: 0, y: top, toJSON() {} }) as DOMRect

afterEach(() => { cleanup(); vi.restoreAllMocks() })

/** The bar New thread sits in. */
const bar = () => screen.getByRole('button', { name: 'New thread' }).parentElement as HTMLElement
/** Whether *el* is the bar: it holds New thread and the search, and no row. */
const isBar = (el: Element) => el instanceof HTMLElement && !!el.querySelector('input[type="search"]')
  && !!el.querySelector('button') && !el.querySelector('[data-thread-key]') && el.parentElement?.getAttribute('role') === 'group'

/** The bar measures 72 px tall. */
function barIs72() {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) { return isBar(this) ? 72 : 0 })
}

describe('the top of the thread rail', () => {
  it('New thread and the search are one bar, pinned to the top of the scroll area', () => {
    render(<ThreadRail groups={[GARDEN]} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}} onNewThread={() => {}} />)
    const top = bar()
    expect(top.contains(screen.getByRole('searchbox', { name: 'Search threads' })), 'the search is in the bar').toBe(true)
    const style = getComputedStyle(top)
    expect(style.position).toBe('sticky')
    // Flush with the sidebar's top edge: the sidebar pads its top by --spacing-l, and a sticky box
    // sticks inside that padding.
    expect(style.top).toBe('calc(-1 * var(--spacing-l))')
  })

  it('paints the rail\'s solid surface, above the rows, so no row shows through it', () => {
    render(<ThreadRail groups={[GARDEN]} selectedKey="chat:chat-3" status="ready" onOpen={() => {}} onRetry={() => {}} onNewThread={() => {}} />)
    const style = getComputedStyle(bar())
    expect(style.background).toBe('var(--color-rail)')
    // A row's label is raised over its selected fill; the bar sits above every part of a row.
    const row = document.querySelector<HTMLElement>('[data-thread-key="chat:chat-3"]')!
    const raised = [...row.querySelectorAll<HTMLElement>('span')].map((sp) => Number(getComputedStyle(sp).zIndex) || 0)
    expect(Math.max(...raised), 'the row raises its label').toBeGreaterThan(0)
    expect(Number(style.zIndex)).toBeGreaterThan(Math.max(...raised))
  })

  it('keeps the bar\'s height clear at the top of the scroll area while the rail shows it', () => {
    barIs72()
    const { unmount } = render(
      <div data-test-scroller="" style={{ overflowY: 'auto', height: 600 }}>
        <ThreadRail groups={[GARDEN]} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}} onNewThread={() => {}} />
      </div>,
    )
    const scroller = document.querySelector<HTMLElement>('[data-test-scroller]')!
    expect(scroller.style.scrollPaddingTop, 'the room the browser leaves when focus moves to a row').toBe('72px')
    unmount()
    expect(scroller.style.scrollPaddingTop, 'and gives it back when the rail goes').toBe('')
  })

  it('scrolls the open thread\'s row into view below the pinned bar, not under it', () => {
    barIs72()
    let scrollTop = 400
    const scroller = (el: HTMLDivElement | null) => {
      if (el) Object.defineProperty(el, 'scrollTop', { configurable: true, get: () => scrollTop, set: (v: number) => { scrollTop = v } })
    }
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      if (this instanceof HTMLElement && this.dataset.testScroller !== undefined) return rect(0, 600)
      if (this instanceof HTMLElement && this.dataset.threadKey === 'chat:chat-12') return rect(-20, 12)
      return rect(0, 0)
    })
    render(
      <div ref={scroller} data-test-scroller="" style={{ overflowY: 'auto', height: 600 }}>
        <ThreadRail groups={[GARDEN]} selectedKey="chat:chat-12" status="ready" onOpen={() => {}} onRetry={() => {}} onNewThread={() => {}} />
      </div>,
    )
    // The row's top was 20 px above the scroll area, so 92 px above the 72 px the bar covers: the
    // rail scrolls up by 92, so the row starts where the bar ends.
    expect(scrollTop).toBe(308)
  })
})
