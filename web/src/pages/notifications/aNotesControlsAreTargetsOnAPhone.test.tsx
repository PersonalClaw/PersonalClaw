// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

// ── The notifications view's controls are 44 px targets on a phone ────────────────────────────────
//
// The UI quality matrix measured every control the Inbox's notifications view draws under 44 px at
// 320, 390, 412 and 768 px (#/inbox?filter=notifications and #/notifications, both themes):
// - each note's Investigate in chat, Mark read or Mark unread, and Delete, drawn at 34 px;
// - the header's Mark all read and Clear all, at 40;
// - Filter & sort, at 40.
// Each now carries the compact widths' 44 px floor (`TOUCH_TARGET`) from its call site, and keeps
// its own size above them.
//
// jsdom has no layout, so the classes are the honest thing to pin: they are what set the box. The
// matrix (`e2e/surfaces.spec.ts`) measures the box itself.

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: { ...mod.api, notifications: vi.fn(async () => ({ notifications: [UNREAD, READ] })) },
  }
})
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/rungs', () => ({ useAutonomyLadder: () => ({ ladder: null, refresh: () => {} }) }))

const { NotificationsPage } = await import('./NotificationsPage')
const { EmptyState } = await import('../../ui/ListScaffold')
const { TOUCH_TARGET } = await import('../../ui/Toggle')

const UNREAD = { ts: 1785890207, kind: 'error', title: 'Weekly backup failed', body: 'disk full', acked: false, mode: '', targets: [] }
const READ = { ts: 1785890100, kind: 'info', title: 'Inbox digest sent', body: 'three items', acked: true, mode: '', targets: [] }

afterEach(() => cleanup())

/** Whether `el`'s classes give it a 44 px box below the large breakpoint, in both dimensions. */
const compactFloor = (el: Element) => {
  const cls = el.className.toString().split(/\s+/)
  return cls.includes('max-lg:min-h-11') && cls.includes('max-lg:min-w-11')
}

describe('the notifications view on a phone', () => {
  it("each note's actions carry the floor", async () => {
    render(<NotificationsPage query={{}} setQuery={() => {}} navigate={() => {}} />)
    await screen.findByText('Weekly backup failed')
    const names = [
      'Investigate in chat: Weekly backup failed', 'Mark read: Weekly backup failed', 'Delete: Weekly backup failed',
      'Investigate in chat: Inbox digest sent', 'Mark unread: Inbox digest sent', 'Delete: Inbox digest sent',
    ]
    for (const name of names) {
      const button = screen.getByRole('button', { name: new RegExp(`^${name}`) })
      expect(compactFloor(button), `${name}: ${button.className}`).toBe(true)
    }
  })

  it("so do the header's actions and Filter & sort", async () => {
    render(<NotificationsPage query={{}} setQuery={() => {}} navigate={() => {}} />)
    await screen.findByText('Weekly backup failed')
    for (const name of ['Mark all read', 'Clear all', 'Filter & sort']) {
      const button = screen.getByRole('button', { name })
      expect(compactFloor(button), `${name}: ${button.className}`).toBe(true)
    }
  })

  it('an empty state gives its action the class it is handed', () => {
    render(<EmptyState title="Nothing has arrived yet" action={{ label: 'Add a message source', onClick: () => {}, className: TOUCH_TARGET }} />)
    const button = screen.getByRole('button', { name: /Add a message source/ })
    expect(compactFloor(button), button.className).toBe(true)
  })
})
