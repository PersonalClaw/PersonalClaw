import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { BookOpen, Home, ListChecks, Settings } from 'lucide-react'
import { NavRail } from './NavRail'

// ── The rail holds the threads, below the leading destinations (#3814) ───────────────────────────
//
// The sidebar is a hierarchy: the leading destinations, then the projects with their threads, then
// the sectioned destinations, then the utilities at the bottom. The expanded rail shows the threads,
// and so does the phone's drawer, which is the compact path to them. The collapsed rail is icons
// only and shows none: a thread is reached from Home, the palette or a key there.

const ITEMS = [
  { id: 'dashboard', label: 'Home', icon: Home },
  { id: 'knowledge', label: 'Knowledge', icon: BookOpen },
  { id: 'tasks', label: 'Tasks', icon: ListChecks, section: 'Platform' },
  { id: 'settings', label: 'Settings', icon: Settings, pinBottom: true },
]
const THREADS = <div data-testid="threads">threads</div>

/** The rail's rows and the threads slot, top to bottom. */
function order(container: HTMLElement): string[] {
  return [...container.querySelectorAll('nav button[aria-label], [data-testid="threads"]')]
    .map((e) => (e.getAttribute('data-testid') === 'threads' ? 'THREADS' : e.getAttribute('aria-label') ?? ''))
}

describe('the rail and its threads', () => {
  it('the expanded rail shows them after the leading destinations, before the sectioned ones', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} threads={THREADS} />)
    expect(order(container)).toEqual(['Home', 'Knowledge', 'THREADS', 'Tasks', 'Settings'])
  })

  it("the phone's drawer shows them too", () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed overlay overlayOpen threads={THREADS} />)
    expect(order(container)).toEqual(['Home', 'Knowledge', 'THREADS', 'Tasks', 'Settings'])
  })

  it('the collapsed rail shows none', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed threads={THREADS} />)
    expect(order(container)).toEqual(['Home', 'Knowledge', 'Tasks', 'Settings'])
  })

  it('with no sectioned destination, they follow the last leading one', () => {
    const { container } = render(<NavRail items={ITEMS.filter((i) => !i.section)} activeId="dashboard" onSelect={() => {}} collapsed={false} threads={THREADS} />)
    expect(order(container)).toEqual(['Home', 'Knowledge', 'THREADS', 'Settings'])
  })
})
