import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { BookOpen, Home, ListChecks, Settings, Smartphone, Zap } from 'lucide-react'
import { NavRail } from './NavRail'

// ── The rail is a shell: its shortcuts hold the top and the bottom, and only the chat history scrolls
//    between them (#5878) ────────────────────────────────────────────────────────────────────────────
//
// The rail is three parts, top to bottom, which is also the order focus moves in:
//   · the top block: the wordmark and the leading destinations;
//   · the chat history (the `threads` slot), the one part that scrolls, taking the height that is left;
//   · the bottom block: every sectioned destination (Platform, the pinned apps) and the rows pinned to
//     the foot (the phone's Companion, then Settings).
// Each block keeps its own height, so a long chat history scrolls between them and never pushes a
// shortcut out of view. The rail itself scrolls only when a window is too short to hold both blocks
// and the history's floor (`e2e/sidebarShell.spec.ts` measures it in a browser). Before this, one
// scroller held the wordmark, every destination and the threads, so a long list carried Inbox,
// Automations and Capabilities away with it.

const ITEMS = [
  { id: 'dashboard', label: 'Home', icon: Home },
  { id: 'knowledge', label: 'Knowledge', icon: BookOpen },
  { id: 'tasks', label: 'Tasks', icon: ListChecks, section: 'Platform' },
  { id: 'automations', label: 'Automations', icon: Zap, section: 'Platform' },
  { id: 'companion', label: 'Companion', icon: Smartphone, pinBottom: true },
  { id: 'settings', label: 'Settings', icon: Settings, pinBottom: true },
]
const THREADS = <div data-testid="threads"><button type="button" aria-label="a chat">a chat</button></div>

const labels = (el: Element) => [...el.querySelectorAll('button[aria-label]')].map((b) => b.getAttribute('aria-label'))
const classes = (el: Element) => (el.getAttribute('class') ?? '').split(/\s+/)

describe('the rail is a shell', () => {
  it.each([
    ['the expanded rail', { collapsed: false }],
    ['the collapsed rail', { collapsed: true }],
    ['the phone\'s drawer', { collapsed: true, overlay: true, overlayOpen: true }],
  ])('in %s: a fixed top block, the chat history, a fixed bottom block, in that order', (_, mode) => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} threads={THREADS} {...mode} />)
    const nav = container.querySelector<HTMLElement>('nav[data-tour="rail"]')!
    const [top, history, bottom] = [...nav.children] as HTMLElement[]
    expect(nav.children).toHaveLength(3)
    expect(top.hasAttribute('data-rail-top') && history.hasAttribute('data-rail-scroll') && bottom.hasAttribute('data-rail-pinned')).toBe(true)

    expect(labels(top), 'the top block: the leading destinations').toEqual(['Home', 'Knowledge'])
    expect(labels(bottom), 'the bottom block: every sectioned destination, then the pinned rows')
      .toEqual(['Tasks', 'Automations', 'Companion', 'Settings'])
    for (const block of [top, bottom]) {
      expect(classes(block), 'a fixed block keeps its own height').toContain('shrink-0')
      expect(classes(block).some((c) => /^overflow(-y)?-(auto|scroll)$/.test(c)), 'and never scrolls').toBe(false)
    }
    expect(classes(history), 'the chat history takes the height that is left').toContain('flex-1')
    expect(classes(history), 'and it is what scrolls').toContain('overflow-y-auto')
    expect(history.style.scrollPaddingBottom, 'it keeps a focus ring\'s reach clear at its fold').toBe('4px')

    const threads = container.querySelector('[data-testid="threads"]')
    const full = !mode.collapsed || 'overlay' in mode
    expect(!!threads && history.contains(threads), 'the chat history holds the threads slot, and only it').toBe(full)
    if (full) expect(labels(history)).toEqual(['a chat'])
    else expect(history.children, 'the collapsed rail holds nothing between its blocks').toHaveLength(0)

    // Focus follows the document: the top block, then the chat history, then the bottom block.
    const order = labels(nav)
    expect(order).toEqual(full ? ['Home', 'Knowledge', 'a chat', 'Tasks', 'Automations', 'Companion', 'Settings']
      : ['Home', 'Knowledge', 'Tasks', 'Automations', 'Companion', 'Settings'])
  })

  it('the section headers stay over their rows in the bottom block', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} threads={THREADS} />)
    const bottom = container.querySelector<HTMLElement>('[data-rail-pinned]')!
    expect(bottom.textContent).toMatch(/^Platform/)
  })

  it('a rail with nothing sectioned or pinned draws no bottom block', () => {
    const { container } = render(<NavRail items={ITEMS.filter((i) => !i.pinBottom && !i.section)} activeId="dashboard" onSelect={() => {}} collapsed={false} />)
    expect(container.querySelector('[data-rail-pinned]')).toBeNull()
    expect(container.querySelector('nav[data-tour="rail"]')!.children).toHaveLength(2)
  })
})
