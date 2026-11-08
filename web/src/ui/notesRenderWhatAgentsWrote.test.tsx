import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MD_LINE, rendersLine } from '../test/markdownSinks'

// ── A notification shows what its agent or run wrote, rendered ─────────────────────────────────
//
// 🔴 A note's title and its first line, its toast and the bell's row showed their marks; the OS
// banner sent them out to the system. Each now renders in the form its sink holds (a line, an
// excerpt), every name read off it is plain text, and the OS gets plain text only. The app's own
// toasts (`notify()`) are our sentences, not markdown, and stay the text they are.

const SEEN = 'Skip model_calls in export_digest() in published.py'
const NOTE = { ts: 1785890207, kind: 'error', title: MD_LINE, body: '```sh\n**Key point**: run `make test`\n```', acked: false, mode: '', targets: [] }

vi.mock('../lib/api', async (orig) => {
  const mod = await orig<typeof import('../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      notifications: vi.fn(async () => ({ notifications: [NOTE] })),
      deleteNotification: vi.fn(async () => ({})),
      ackNotification: vi.fn(async () => ({})),
      ackAllNotifications: vi.fn(async () => ({})),
    },
  }
})
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const { NotificationBell } = await import('./NotificationBell')
const { Toaster } = await import('./Toaster')
const { firstLine } = await import('../pages/notifications/notificationMeta')
const { toastMessageForNote } = await import('../lib/notificationToasts')

afterEach(cleanup)

describe("the bell's row", () => {
  it('renders the title as a line and the first line as an excerpt, and names the row in plain text', async () => {
    render(<NotificationBell navigate={() => {}} />)
    fireEvent.click(await waitFor(() => screen.getByRole('button', { name: /Notifications/ })))
    const title = await screen.findByTitle(SEEN)
    expect(rendersLine(title)).toEqual([])
    // The body's first line that says something: the fence line is skipped, the marks render.
    expect((await screen.findByText('Key point')).tagName).toBe('STRONG')
    for (const el of document.querySelectorAll('[aria-label]')) {
      expect(el.getAttribute('aria-label'), 'a name carries a mark').not.toMatch(/`|\*\*/)
    }
  })
})

describe('the first line', () => {
  it('skips a bare fence line, and comes uncut for CSS to cut', () => {
    expect(firstLine('```ts\nconst x = 1\n```')).toBe('const x = 1')
    expect(firstLine('a'.repeat(300))).toHaveLength(300)
    expect(firstLine('a'.repeat(300), 120)).toHaveLength(121)
  })
})

describe('a note toast renders its line; the app’s own toast stays text', () => {
  const toast = (detail: Record<string, unknown>) =>
    act(() => { window.dispatchEvent(new CustomEvent('ne:toast', { detail })) })

  it('a note toast renders MD_LINE, and its announcement is the plain line', () => {
    render(<Toaster />)
    toast({ level: 'info', message: MD_LINE, markdown: true })
    // The toast's visible line (its announcement is the live regions', so the line is aria-hidden).
    const shown = document.querySelector('span[aria-hidden][data-type="body-m"]')!
    expect(shown.textContent).toBe(SEEN)
    expect(rendersLine(shown)).toEqual([])
    const regions = Array.from(document.querySelectorAll('[role="status"], [role="alert"]')).map((r) => r.textContent).join(' ')
    expect(regions).toContain(SEEN)
    expect(regions).not.toMatch(/`|\*\*/)
  })

  it("a notify() toast's underscores stay literal", () => {
    render(<Toaster />)
    toast({ level: 'success', message: 'Saved _draft_ as model_calls_v2' })
    expect(screen.getAllByText('Saved _draft_ as model_calls_v2').length).toBeGreaterThan(0)
    expect(document.querySelector('em')).toBeNull()
  })

  it("a note's toast text cuts its first line with no mark left open", () => {
    const message = toastMessageForNote({ title: 'Loop failed', body: `**${'word '.repeat(30).trim()}**` })
    expect(message.startsWith('Loop failed — **')).toBe(true)
    expect(message.endsWith('**…')).toBe(true)
  })
})
