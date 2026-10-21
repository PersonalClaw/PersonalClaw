import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { BookOpen, Home, ListChecks, Settings, Smartphone } from 'lucide-react'
import { NavRail } from './NavRail'

// ── The rows pinned to the rail's foot never scroll (#3814) ──────────────────────────────────────
//
// The rail is two parts: one scroll area (the wordmark, the destinations, the threads and the
// disclosure control), and under it the rows pinned to the foot (Settings, and the phone's
// Companion), which stay where they are while the rest scrolls. With Settings at the foot of the one
// scroller, the rail overflowed a 720 px window by 14 px, which pushed Settings into the rail's own
// bottom padding and cut its focus ring off at the edge. The foot never meets the scroll area's
// edges, and the scroll area keeps a ring's reach clear at its bottom, so a row the focus moves to
// is scrolled clear of the fold. The threads, whose bar sticks to the top of the scroll area, are in
// it. `e2e/threadRailGeometry.spec.ts` measures both in a browser.

const ITEMS = [
  { id: 'dashboard', label: 'Home', icon: Home },
  { id: 'knowledge', label: 'Knowledge', icon: BookOpen },
  { id: 'tasks', label: 'Tasks', icon: ListChecks, section: 'Platform' },
  { id: 'companion', label: 'Companion', icon: Smartphone, pinBottom: true },
  { id: 'settings', label: 'Settings', icon: Settings, pinBottom: true },
]
const THREADS = <div data-testid="threads">threads</div>

/** Each element of the rail that scrolls: its vertical overflow scrolls (the utility classes jsdom
 *  does not compute, or an inline style). */
function scrollers(nav: HTMLElement): HTMLElement[] {
  return [nav, ...nav.querySelectorAll<HTMLElement>('*')].filter((e) =>
    /(^|\s)overflow(-y)?-(auto|scroll)(\s|$)/.test(e.getAttribute('class') ?? '') || /(auto|scroll)/.test(e.style.overflowY))
}
const labels = (el: Element) => [...el.querySelectorAll('button[aria-label]')].map((b) => b.getAttribute('aria-label'))

describe('the rail\'s foot', () => {
  it.each([
    ['the expanded rail', { collapsed: false }],
    ['the collapsed rail', { collapsed: true }],
    ['the phone\'s drawer', { collapsed: true, overlay: true, overlayOpen: true }],
  ])('in %s, the pinned rows sit under the one scroll area, outside it', (_, mode) => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} threads={THREADS} {...mode} />)
    const nav = container.querySelector<HTMLElement>('nav[data-tour="rail"]')!
    const scrolling = scrollers(nav)
    expect(scrolling, 'one part of the rail scrolls, and not the rail itself').toHaveLength(1)
    const [area] = scrolling
    expect(area).not.toBe(nav)
    expect(labels(area)).toEqual(['Home', 'Knowledge', 'Tasks'])
    expect(area.style.scrollPaddingBottom, 'it keeps a focus ring\'s reach clear at its fold').toBe('4px')
    const threads = container.querySelector('[data-testid="threads"]')
    if (!mode.collapsed || 'overlay' in mode) expect(threads && area.contains(threads), 'the threads are in the scroll area').toBe(true)
    const foot = nav.lastElementChild as HTMLElement
    expect(area.contains(foot), 'the foot is not in the scroll area').toBe(false)
    expect(labels(foot), 'the foot holds the pinned rows, in their order').toEqual(['Companion', 'Settings'])
    expect(foot.compareDocumentPosition(area) & Node.DOCUMENT_POSITION_PRECEDING, 'under the scroll area').toBeTruthy()
  })

  it('a rail with nothing pinned draws no foot', () => {
    const { container } = render(<NavRail items={ITEMS.filter((i) => !i.pinBottom)} activeId="dashboard" onSelect={() => {}} collapsed={false} />)
    const nav = container.querySelector<HTMLElement>('nav[data-tour="rail"]')!
    expect(nav.children).toHaveLength(1)
  })
})
