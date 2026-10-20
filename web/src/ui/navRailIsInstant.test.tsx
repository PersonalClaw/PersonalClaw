import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { Home, ListChecks, Settings } from 'lucide-react'
import type { ElementType } from 'react'

// ── A rail click is instant ─────────────────────────────────────────────────────────────────────
//
// The rail is the control the owner clicks most, and every click used to start two springs: the
// item shrank under the pointer (`whileTap` scale 0.98 on `spring.spatialFast`), and the active pill
// slid from the old item to the new one (`layoutId="nav-active-pill"` on `spring.spatialDefault`).
// Neither explains anything: where the pill came from is not information. Now the active state
// changes at once, by colour, and the rail hands framer no gesture and no shared layout.
//
// `layoutId` is not a DOM attribute (framer consumes it and jsdom has no layout to animate), so the
// rail's motion elements are RECORDED instead: `framer-motion` is wrapped so every `motion.*` the
// rail renders logs the props it was handed. The press half is read off the DOM as well, where a
// `whileTap` writes `transform: scale(…)` at pointer-down.
//
// The phone drawer is not covered here and keeps its spring: it is a panel sliding in, which is
// exactly where a spring belongs.

const { rendered } = vi.hoisted(() => ({ rendered: [] as { tag: string; props: Record<string, unknown> }[] }))

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>()
  const React = await import('react')
  const wrapped = new Map<string, unknown>()
  const motion = new Proxy(actual.motion, {
    get(target, key, receiver) {
      const real = Reflect.get(target, key, receiver) as unknown
      if (typeof key !== 'string' || real == null) return real
      if (!wrapped.has(key)) {
        wrapped.set(key, React.forwardRef<unknown, Record<string, unknown>>((props, ref) => {
          rendered.push({ tag: key, props })
          return React.createElement(real as ElementType, { ...props, ref })
        }))
      }
      return wrapped.get(key)
    },
  })
  return { ...actual, motion }
})

const { NavRail } = await import('./NavRail')

const ITEMS = [
  { id: 'dashboard', label: 'Home', icon: Home },
  { id: 'tasks', label: 'Tasks', icon: ListChecks },
  { id: 'settings', label: 'Settings', icon: Settings, pinBottom: true },
]

/** The props on every motion element rendered since the last reset that would animate. */
const ANIMATING = ['whileTap', 'whileHover', 'layoutId', 'layout', 'animate', 'initial', 'exit'] as const
const animated = () => rendered
  .filter((r) => ANIMATING.some((k) => r.props[k] !== undefined && r.props[k] !== false))
  .map((r) => `motion.${r.tag} ${ANIMATING.filter((k) => r.props[k] !== undefined && r.props[k] !== false).join(',')}`)

beforeEach(() => { rendered.length = 0 })

describe('a rail click schedules no animation', () => {
  it('the rail renders no motion element that animates, its disclosure row included', () => {
    render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false}
      disclosure={{ expanded: false, moreCount: 4, onToggle: () => {} }} />)
    expect(screen.getByRole('button', { name: /Everything, show 4 more surfaces/ })).toBeInTheDocument()
    expect(animated(), 'the rail still hands framer a gesture or a shared layout').toEqual([])
  })

  it('clicking an item moves the active state at once, with no sliding pill', () => {
    const onSelect = vi.fn()
    const { rerender } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={onSelect} collapsed={false} />)
    rendered.length = 0
    const tasks = screen.getByRole('button', { name: 'Tasks' })
    fireEvent.pointerDown(tasks, { button: 0, isPrimary: true, pointerType: 'mouse' })
    fireEvent.click(tasks)
    expect(onSelect).toHaveBeenCalledWith('tasks')
    rerender(<NavRail items={ITEMS} activeId="tasks" onSelect={onSelect} collapsed={false} />)

    expect(rendered.filter((r) => r.props.layoutId !== undefined).map((r) => r.props.layoutId),
      'the active pill still slides between items').toEqual([])
    expect(animated(), 'the click started an animation on the rail').toEqual([])
    expect(screen.getByRole('button', { name: 'Tasks' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('button', { name: 'Home' })).not.toHaveAttribute('aria-current')
  })

  it('pressing an item writes no scale', async () => {
    render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} />)
    const tasks = screen.getByRole('button', { name: 'Tasks' })
    await act(async () => { fireEvent.pointerDown(tasks, { button: 0, isPrimary: true, pointerType: 'mouse' }) })
    await act(async () => { await new Promise((r) => setTimeout(r, 120)) })
    expect(tasks.getAttribute('style') ?? '', 'the rail item still shrinks under the pointer').not.toMatch(/scale\(/)
  })

  it('the recorder sees framer at all, so an empty result above means something', () => {
    // The phone drawer keeps its spring, so it is the positive control: the same wrapper that found
    // nothing on the desktop rail must find the drawer's `animate`.
    render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} overlay overlayOpen />)
    expect(rendered.some((r) => r.props.animate !== undefined)).toBe(true)
  })
})
