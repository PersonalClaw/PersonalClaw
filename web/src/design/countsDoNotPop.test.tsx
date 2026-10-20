import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, within, act } from '@testing-library/react'
import type { TaskItem } from '../lib/api'

// ── A count that changes does not pop ───────────────────────────────────────────────────────────
//
// Three counts the owner watches tick all day used to re-mount on every change and pop back in on
// the most overshooting preset (`physics.playful`): Home's pulse counts, each Tasks board column's
// count, and the bell's unread badge. Each was a `motion.span` keyed on the number, so a new number
// was a new element with `scale(0.5…)`/`opacity: 0` written on it and a spring back to 1. A count
// changing many times an hour is not an event; the new number is the news. So the count is one
// element that keeps its place and swaps its text, with nothing written on it to animate.
//
// Read off the DOM: the node before and after the change must be the SAME node (no re-mount), and
// it must carry no inline scale or opacity (no entrance on the first frame either).

function expectNoPop(before: Element, after: Element, name: string) {
  expect(after, `${name} re-mounted on change, which is what replays its entrance`).toBe(before)
  expect(after.getAttribute('style') ?? '', `${name} still animates in`).not.toMatch(/scale|opacity/)
}

// ── Home's pulse ──────────────────────────────────────────────────────────────────────────────────

const live = vi.hoisted(() => ({
  value: {
    approvals: [] as unknown[], inbox: [] as unknown[], tasks: [] as unknown[],
    approvalsErr: null, inboxErr: null, tasksErr: null,
    read: { approvals: true, inbox: true, tasks: true },
    status: null,
  },
}))
vi.mock('../pages/dashboard/DashboardLive', () => ({ useDashboardLive: () => live.value }))

// ── The bell ──────────────────────────────────────────────────────────────────────────────────────

const bell = vi.hoisted(() => ({
  read: () => Promise.resolve({ notifications: [] as unknown[] }),
  onMessage: null as null | ((m: { type: string }) => void),
}))
vi.mock('../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../lib/api')>()
  return { ...mod, api: { ...mod.api, notifications: () => bell.read() } }
})
vi.mock('../lib/useChatSocket', () => ({
  useChatSocket: (handler: (m: { type: string }) => void) => { bell.onMessage = handler },
}))

const { HeroPulse } = await import('../pages/dashboard/widgets/HeroPulse')
const { TaskBoard } = await import('../pages/tasks/TaskBoard')
const { NotificationBell } = await import('../ui/NotificationBell')

const approval = (id: string) => ({ id, tool: 'shell', args: {} })
const task = (id: string, status: string): TaskItem => ({ id, title: `Task ${id}`, status, priority: 'medium' } as unknown as TaskItem)
const note = (ts: number) => ({ ts, kind: 'info', title: `Note ${ts}`, body: 'body', acked: false, mode: '', targets: [] })

afterEach(() => cleanup())

describe('Home’s pulse counts swap their number in place', () => {
  beforeEach(() => { live.value = { ...live.value, approvals: [approval('a1')] } })

  it('an approvals count going 1 → 2 keeps its element and writes no pop', () => {
    const props = { sub: '', navigate: () => {}, navEpoch: 0, setQuery: () => {}, query: {} }
    const { rerender } = render(<HeroPulse {...props} />)
    const before = within(screen.getByRole('button', { name: '1 approval waiting' })).getByText('1')
    expect(before.getAttribute('style') ?? '', 'the count animates in on its first frame').not.toMatch(/scale|opacity/)

    live.value = { ...live.value, approvals: [approval('a1'), approval('a2')] }
    rerender(<HeroPulse {...props} />)
    const after = within(screen.getByRole('button', { name: '2 approvals waiting' })).getByText('2')
    expectNoPop(before, after, 'the pulse count')
  })
})

describe('a Tasks board column’s count swaps its number in place', () => {
  it('a card arriving keeps the column count’s element and writes no pop', () => {
    const { rerender } = render(<TaskBoard tasks={[task('t1', 'open')]} onOpen={() => {}} onMove={() => {}} />)
    const header = () => screen.getByRole('group', { name: /^Not started — / }).parentElement!
    const before = within(header()).getByText('1')

    rerender(<TaskBoard tasks={[task('t1', 'open'), task('t2', 'open')]} onOpen={() => {}} onMove={() => {}} />)
    const after = within(header()).getByText('2')
    expectNoPop(before, after, 'the column count')
  })
})

describe('the bell’s unread badge swaps its number in place', () => {
  it('a notification arriving keeps the badge’s element and writes no pop', async () => {
    bell.read = () => Promise.resolve({ notifications: [note(1)] })
    render(<NotificationBell navigate={() => {}} />)
    const button = () => screen.getByRole('button', { name: /^Notifications/ })
    await waitFor(() => expect(button()).toHaveAttribute('aria-label', 'Notifications, 1 unread'))
    const before = within(button()).getByText('1')
    expect(before.getAttribute('style') ?? '', 'the badge pops in on its first frame').not.toMatch(/scale/)

    bell.read = () => Promise.resolve({ notifications: [note(2), note(1)] })
    await act(async () => { bell.onMessage?.({ type: 'notification' }) })
    await waitFor(() => expect(button()).toHaveAttribute('aria-label', 'Notifications, 2 unread'))
    const after = within(button()).getByText('2')
    expect(after, 'the badge re-mounted on change, which is what replays its pop').toBe(before)
    expect(after.getAttribute('style') ?? '', 'the badge still pops').not.toMatch(/scale/)
  })
})
