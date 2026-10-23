import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PendingApproval, RunningList, WaitingList } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import type { BuddyLayout } from '../../lib/desktopBridge'
import type { WsMessage } from '../../lib/useChatSocket'
import { waitingApproval, waitingList } from '../../test/waiting'
import { CompanionPage } from '../companion/CompanionPage'
import { BuddyPage } from './BuddyPage'

// ── `#/buddy`, the buddy's live presence (#3832), driven as the owner drives it ──────────────────
//
// The host's own consent test (`lib/buddy/host.consent.test.ts`) drives every input; this one drives
// the page: what arrives shows and sends nothing, an answer goes only from the card's controls to
// the approval's own route, a broad-reach Allow asks its confirmation first, Escape decides nothing,
// a frame reads both lists again, and the approval reads word for word as the phone's.

let onFrame: ((m: WsMessage) => void) | null = null
let sockets = 0
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<typeof import('../../lib/useChatSocket')>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { sockets++; onFrame = onMessage },
}))

const lists: { running: RunningList; waiting: WaitingList } = {
  running: { running: [], finished: [] },
  waiting: { waiting: [], unreadable: [] } as WaitingList,
}
const reads = { running: 0, waiting: 0 }
const policy = { show: true }
const resolveApproval = vi.fn()
const answerChatQuestion = vi.fn()
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      running: async () => { reads.running++; return lists.running },
      waiting: async () => { reads.waiting++; return lists.waiting },
      listening: async () => ({ on: false, sources: [], since: null }),
      buddyPolicy: async () => ({
        show: policy.show,
        surfaces: { window: policy.show, tray: policy.show, dock: policy.show, notifications: policy.show },
        away_after_secs: 180,
        notifications: { muted: false, quiet_now: false, may_interrupt: true, changes_at: null },
      }),
      resolveApproval: (id: string, action: string) => resolveApproval(id, action),
      answerChatQuestion: (...a: unknown[]) => answerChatQuestion(...a),
      approvals: async () => [],
      status: async () => ({}),
      uLoops: async () => [],
      tasks: async () => ({ tasks: [], total: 0 }),
      inboxOpen: async () => [],
      notifications: async () => ({ notifications: [], unread: 0 }),
    },
  }
})

const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }

const AP: PendingApproval = {
  id: 'ap-1', request_id: 'ap-1', source: 'cron', tool: 'Bash',
  tool_input: 'rm -rf /tmp/scratch', tool_purpose: 'Clear the scratch directory',
  session: 'cron:nightly', ts: Math.round(Date.now() / 1000) - 90, confirms: [],
  session_title: '', agent: '', risk: 'safe', grant_agent: '', source_label: 'trigger “Nightly sweep”',
  deny_effect: 'The run stops here.',
}

const RUNNING: RunningList = {
  running: [{
    work: 'chat:s1', kind: 'chat', session: 's1', phase: 'approval', line: 'Needs your OK', since: null,
    name: 'Nightly sweep', agent: '', started_at: new Date().toISOString(), route: '#/chat/s1', blocked: false, waiting: ['ap-1'],
  }],
  finished: [],
}

function waitingFor(ap: PendingApproval): WaitingList {
  return waitingList(waitingApproval(ap, { work: 'chat:s1', consequence: 'changes' }))
}

const prompt = () => screen.findByRole('group', { name: /^Permission needed to run Bash/ })

beforeEach(() => {
  lists.running = RUNNING
  lists.waiting = waitingFor(AP)
  reads.running = 0
  reads.waiting = 0
  policy.show = true
  resolveApproval.mockReset().mockResolvedValue({ ok: true })
  answerChatQuestion.mockReset()
  onFrame = null
  sockets = 0
  invalidateKeys('companion:', true)
})
afterEach(() => cleanup())

describe('the buddy presence', () => {
  it('shows the approval that arrives, expanded with its brief, and sends nothing for its arrival', async () => {
    render(<BuddyPage {...route} />)
    const group = await prompt()
    expect(within(group).getByRole('button', { name: 'Allow Bash' })).toBeTruthy()
    expect(within(group).getByRole('button', { name: 'Deny Bash. The run stops here.' })).toBeTruthy()
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('Deny sends one request, to the approval its own route names', async () => {
    render(<BuddyPage {...route} />)
    const group = await prompt()
    await userEvent.click(within(group).getByRole('button', { name: 'Deny Bash. The run stops here.' }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledTimes(1))
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'reject')
  })

  it('a broad-reach Allow asks its confirmation first: the Allow sends nothing, the confirm sends one', async () => {
    lists.waiting = waitingFor({ ...AP, confirms: ['deletes'] })
    render(<BuddyPage {...route} />)
    const group = await prompt()
    await userEvent.click(within(group).getByRole('button', { name: 'Allow Bash' }))
    expect(resolveApproval).not.toHaveBeenCalled()
    await userEvent.click(within(group).getByRole('button', { name: /^Yes, run it/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledTimes(1))
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'approve')
  })

  it('Escape on the focused alert decides nothing, and nor does Enter', async () => {
    render(<BuddyPage {...route} />)
    const group = await prompt()
    group.focus()
    await userEvent.keyboard('{Escape}{Enter}')
    expect(resolveApproval).not.toHaveBeenCalled()
    expect(await prompt()).toBeTruthy()
  })

  it('a frame that says a list moved reads both lists again, and an answer elsewhere takes the alert away', async () => {
    render(<BuddyPage {...route} />)
    await prompt()
    const before = { ...reads }
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    await act(async () => { onFrame?.({ type: 'waiting_changed', data: {} }) })
    await waitFor(() => expect(reads.waiting).toBe(before.waiting + 1))
    expect(reads.running).toBe(before.running + 1)
    await waitFor(() => expect(screen.queryByRole('group', { name: /^Permission needed/ })).toBeNull())
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('while work runs it shows the compact line; a click on it opens the card and answers nothing', async () => {
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    const line = await waitFor(() => {
      const b = container.querySelector<HTMLButtonElement>('[data-buddy-card-surface] button')
      expect(b).not.toBeNull()
      return b!
    }, { timeout: 2000 })
    await userEvent.click(line)
    await waitFor(() => expect(container.querySelector('[data-buddy-presence][data-room="expanded"]')).not.toBeNull())
    expect(resolveApproval).not.toHaveBeenCalled()
    expect(answerChatQuestion).not.toHaveBeenCalled()
  })

  it('with Buddy status off, as the policy says it, shows nothing; turned on, the alert is there', async () => {
    policy.show = false
    const { container } = render(<BuddyPage {...route} />)
    await waitFor(() => expect(reads.waiting).toBe(1))
    await act(async () => { await Promise.resolve() })
    expect(container.querySelector('[data-buddy-presence]')).toBeNull()
    expect(screen.queryByRole('group', { name: /^Permission needed/ })).toBeNull()
    policy.show = true
    await act(async () => { onFrame?.({ type: 'buddy_policy_changed', data: {} }) })
    expect(await prompt()).toBeTruthy()
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('shows nothing when nothing runs and nothing waits', async () => {
    lists.running = { running: [], finished: [] }
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    await waitFor(() => expect(reads.waiting).toBe(1))
    expect(container.querySelector('[data-buddy-presence]')).toBeNull()
  })
})

describe("in the buddy's own window", () => {
  let ring: (() => void) | null = null
  let connection: ((s: 'up' | 'lost' | 'restarting') => void) | null = null
  let placement: ((p: 'moving' | 'docked' | 'floating') => void) | null = null
  let fullScreen: ((on: boolean) => void) | null = null
  let away: ((on: boolean) => void) | null = null
  let hidden: ((on: boolean) => void) | null = null
  let layout: ((l: BuddyLayout) => void) | null = null
  const bridge = {
    pointerOver: vi.fn(), setRegions: vi.fn(), dragStart: vi.fn(), dragBy: vi.fn(), dragEnd: vi.fn(), setPresentation: vi.fn(), openInApp: vi.fn(),
    onRing: vi.fn((cb: () => void) => { ring = cb; return () => { ring = null } }),
    onConnection: vi.fn((cb: (s: 'up' | 'lost' | 'restarting') => void) => { connection = cb; return () => { connection = null } }),
    onPlacement: vi.fn((cb: (p: 'moving' | 'docked' | 'floating') => void) => { placement = cb; return () => { placement = null } }),
    onFullScreen: vi.fn((cb: (on: boolean) => void) => { fullScreen = cb; return () => { fullScreen = null } }),
    onAway: vi.fn((cb: (on: boolean) => void) => { away = cb; return () => { away = null } }),
    onHidden: vi.fn((cb: (on: boolean) => void) => { hidden = cb; return () => { hidden = null } }),
    openMenu: vi.fn(),
    onLayout: vi.fn((cb: (l: BuddyLayout) => void) => { layout = cb; return () => { layout = null } }),
  }
  beforeEach(() => {
    for (const f of Object.values(bridge)) f.mockClear()
    ring = null
    connection = null
    placement = null
    fullScreen = null
    away = null
    hidden = null
    layout = null
    window.pclawBuddy = bridge
  })
  afterEach(() => { delete window.pclawBuddy })

  it('holds no socket of its own: the shell\'s doorbell rings it, and a ring reads the lists again', async () => {
    render(<BuddyPage {...route} />)
    await prompt()
    expect(sockets, 'the window opens no socket').toBe(0)
    expect(ring).not.toBeNull()
    const before = { ...reads }
    await act(async () => { ring?.() })
    await waitFor(() => expect(reads.waiting).toBe(before.waiting + 1))
    expect(reads.running).toBe(before.running + 1)
  })

  it('sizes the window to the room once it has read, and marks the open card as the shape', async () => {
    render(<BuddyPage {...route} />)
    const group = await prompt()
    await waitFor(() => expect(bridge.setPresentation).toHaveBeenLastCalledWith('card'))
    expect(bridge.setPresentation).not.toHaveBeenCalledWith('hidden')
    expect(group.closest('[data-buddy-shape]')).not.toBeNull()
  })

  it('docked with nothing to show it keeps the top-edge zone, and only after it has read the lists', async () => {
    lists.running = { running: [], finished: [] }
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    expect(bridge.setPresentation, 'nothing before the first read').not.toHaveBeenCalled()
    await waitFor(() => expect(bridge.setPresentation).toHaveBeenCalledWith('orb'))
    expect(bridge.setPresentation).not.toHaveBeenCalledWith('hidden')
    expect(container.querySelector('[data-buddy-shape]'), 'the zone takes no click').toBeNull()
  })

  it('the pointer resting on the zone peeks, and leaving it lets the peek go; neither answers', async () => {
    lists.running = { running: [], finished: [] }
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    await waitFor(() => expect(bridge.setPresentation).toHaveBeenCalledWith('orb'))
    await act(async () => { document.dispatchEvent(new MouseEvent('mousemove')) })
    await waitFor(() => expect(container.querySelector('[data-buddy-presence][data-room="peek"]')).not.toBeNull())
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('a move outside the orb\'s box is no rest on the zone: only the pointer on the orb\'s box peeks (#5216)', async () => {
    lists.running = { running: [], finished: [] }
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    await waitFor(() => expect(bridge.setPresentation).toHaveBeenCalledWith('orb'))
    // In the card's area, where the window is shown but takes nothing: the shell's own move as the
    // window shows lands there, and so does every pointer the window forwards on macOS.
    await act(async () => { document.dispatchEvent(new MouseEvent('mousemove', { clientX: 200, clientY: 300 })) })
    expect(container.querySelector('[data-buddy-presence]'), 'no peek').toBeNull()
    await act(async () => { document.dispatchEvent(new MouseEvent('mousemove', { clientX: 20, clientY: 20 })) })
    await waitFor(() => expect(container.querySelector('[data-buddy-presence][data-room="peek"]')).not.toBeNull())
  })

  it('floating, its orb stays on the screen with nothing running, and the window keeps its size', async () => {
    lists.running = { running: [], finished: [] }
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    render(<BuddyPage {...route} />)
    await waitFor(() => expect(placement).not.toBeNull())
    await act(async () => { placement?.('floating') })
    await waitFor(() => expect(bridge.setPresentation).toHaveBeenLastCalledWith('orb'))
  })

  it('where a drag the shell moved settles docks or floats the buddy, and the alert goes with it', async () => {
    const { container } = render(<BuddyPage {...route} />)
    await prompt()
    await act(async () => { placement?.('moving') })
    await act(async () => { placement?.('floating') })
    await waitFor(() => expect(container.querySelector('[data-buddy-presence][data-room="floating"]')).not.toBeNull())
    await act(async () => { placement?.('moving') })
    await act(async () => { placement?.('docked') })
    await waitFor(() => expect(container.querySelector('[data-buddy-presence]:not([data-room="floating"])')).not.toBeNull())
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('over a full-screen app window an alert arrives as a quiet peek, and answers nothing', async () => {
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    await waitFor(() => expect(fullScreen).not.toBeNull())
    await waitFor(() => expect(reads.waiting).toBe(1))
    await act(async () => { fullScreen?.(true) })
    lists.waiting = waitingFor(AP)
    await act(async () => { ring?.() })
    await waitFor(() => expect(container.querySelector('[data-buddy-presence][data-room="peek"]')).not.toBeNull())
    expect(screen.queryByRole('group', { name: /^Permission needed/ }), 'not expanded over the full screen').toBeNull()
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('a restart the shell says keeps the alert, and sends nothing', async () => {
    const { container } = render(<BuddyPage {...route} />)
    await prompt()
    await act(async () => { connection?.('restarting') })
    expect(container.querySelector('[data-buddy-presence]:not([data-room="floating"])')).not.toBeNull()
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('hidden by its owner it shows nothing and asks to be hidden; the hide over, the alert is back', async () => {
    const { container } = render(<BuddyPage {...route} />)
    await prompt()
    await act(async () => { hidden?.(true) })
    await waitFor(() => expect(bridge.setPresentation).toHaveBeenLastCalledWith('hidden'))
    expect(container.querySelector('[data-buddy-presence]')).toBeNull()
    await act(async () => { hidden?.(false) })
    await waitFor(() => expect(bridge.setPresentation).toHaveBeenLastCalledWith('card'))
    expect(await prompt()).toBeTruthy()
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('away, a docked buddy with work running hides it, keeping the top-edge zone; back, it shows again', async () => {
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    await waitFor(() => expect(container.querySelector('[data-buddy-presence]:not([data-room="floating"])')).not.toBeNull(), { timeout: 2000 })
    await act(async () => { away?.(true) })
    await waitFor(() => expect(container.querySelector('[data-buddy-presence]:not([data-room="floating"])')).toBeNull())
    expect(bridge.setPresentation).toHaveBeenLastCalledWith('orb')
    await act(async () => { away?.(false) })
    await waitFor(() => expect(container.querySelector('[data-buddy-presence]:not([data-room="floating"])')).not.toBeNull())
  })

  // ── The shell's layout (#5216): one window of a fixed size, the orb at a fixed box in it and the
  //    card's area beside it, on the side the shell chose. The page draws there, never elsewhere.
  const box = (el: Element | null) => {
    const st = (el as HTMLElement | null)?.style
    return st ? [st.left, st.top, st.width, st.height].join(' ') : null
  }

  it('before the shell says where, the orb is at the window\'s corner and the card opens right of it', async () => {
    const { container } = render(<BuddyPage {...route} />)
    await prompt()
    expect(box(container.querySelector('[data-buddy-orb-slot]'))).toBe('0px 0px 56px 56px')
    const area = container.querySelector('[data-buddy-card-area]')
    expect(area?.getAttribute('data-side')).toBe('right')
    expect(box(area)).toBe('64px 0px 420px 560px')
  })

  it('draws the orb at the layout the shell pushes, and the card in its area, anchored to the orb', async () => {
    const { container } = render(<BuddyPage {...route} />)
    await prompt()
    await act(async () => {
      layout?.({ side: 'left', opens: 'up', orb: { x: 428, y: 504, width: 56, height: 56 }, card: { x: 0, y: 0, width: 420, height: 560 } })
    })
    expect(box(container.querySelector('[data-buddy-orb-slot]'))).toBe('428px 504px 56px 56px')
    const area = container.querySelector<HTMLElement>('[data-buddy-card-area]')!
    expect(box(area)).toBe('0px 0px 420px 560px')
    expect(area.dataset.side).toBe('left')
    expect(area.dataset.opens).toBe('up')
    // Up from the orb's bottom, on the orb's side of the area: the card's bottom-right corner.
    expect(area.className).toContain('justify-end')
    expect(area.className).toContain('items-end')
    // The area takes no click; the card in it does, and so does the orb.
    expect(area.hasAttribute('data-buddy-shape')).toBe(false)
    expect(area.querySelector('[data-buddy-card-surface][data-buddy-shape]')).not.toBeNull()
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('the orb\'s box is the layout\'s whatever the buddy shows; only a new layout moves it', async () => {
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    await act(async () => {
      layout?.({ side: 'right', opens: 'down', orb: { x: 0, y: 0, width: 56, height: 56 }, card: { x: 64, y: 0, width: 420, height: 560 } })
    })
    const slot = await waitFor(() => { const el = container.querySelector('[data-buddy-orb-slot]'); expect(el).not.toBeNull(); return el! }, { timeout: 2000 })
    const at = box(slot)
    lists.waiting = waitingFor(AP)
    await act(async () => { ring?.() })
    await prompt()
    expect(box(container.querySelector('[data-buddy-orb-slot]'))).toBe(at)
    expect(container.querySelector('[data-buddy-orb-slot]')).toBe(slot)
    await act(async () => { placement?.('floating') })
    await act(async () => { fullScreen?.(true) })
    expect(box(container.querySelector('[data-buddy-orb-slot]'))).toBe(at)
  })

  // The whole-UI zoom Appearance sets on the page's root (`app/appearance.tsx`, 80 to 150 %), and
  // twice the size: the shell's boxes are its points, so the zoom is undone on them, and done again
  // on what the card says inside its area.
  it.each([0.8, 1, 1.5, 2])('under a whole-UI zoom of %s, the orb\'s box and the card\'s area stay the shell\'s points', async (zoom) => {
    document.documentElement.style.zoom = String(zoom)
    try {
      const { container } = render(<BuddyPage {...route} />)
      await prompt()
      await act(async () => {
        layout?.({ side: 'left', opens: 'up', orb: { x: 428, y: 504, width: 56, height: 56 }, card: { x: 0, y: 0, width: 420, height: 560 } })
      })
      const undone = zoom === 1 ? '' : String(1 / zoom)
      const slot = container.querySelector<HTMLElement>('[data-buddy-orb-slot]')!
      expect([box(slot), slot.style.zoom], 'the orb at its box, in points').toEqual(['428px 504px 56px 56px', undone])
      const area = container.querySelector<HTMLElement>('[data-buddy-card-area]')!
      expect([box(area), area.style.zoom], 'the card\'s area, in points').toEqual(['0px 0px 420px 560px', undone])
      const surface = area.querySelector<HTMLElement>('[data-buddy-card-surface]')!
      expect(surface.style.zoom, 'what the card says, at the owner\'s zoom').toBe(zoom === 1 ? '' : String(zoom))
    } finally {
      document.documentElement.style.zoom = ''
    }
  })

  it('under a whole-UI zoom, the line card is never wider than its area', async () => {
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    document.documentElement.style.zoom = '2'
    try {
      const { container } = render(<BuddyPage {...route} />)
      await act(async () => {
        layout?.({ side: 'right', opens: 'down', orb: { x: 0, y: 0, width: 56, height: 56 }, card: { x: 64, y: 0, width: 420, height: 560 } })
      })
      const surface = await waitFor(() => {
        const el = container.querySelector<HTMLElement>('[data-buddy-card-area] [data-buddy-card-surface]')
        expect(el).not.toBeNull()
        return el!
      }, { timeout: 2000 })
      expect(surface.style.maxWidth, 'zoomed twice, half the area in its own points').toBe('210px')
    } finally {
      document.documentElement.style.zoom = ''
    }
  })

  it('a right-click on the face asks the shell for the context menu, and answers nothing', async () => {
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    const face = await waitFor(() => {
      const f = container.querySelector('[data-buddy-shape]')
      expect(f).not.toBeNull()
      return f!
    }, { timeout: 2000 })
    await act(async () => { face.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true })) })
    expect(bridge.openMenu).toHaveBeenCalledTimes(1)
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('the face is the shape and the drag handle; a link to the app opens in the main window', async () => {
    lists.waiting = { waiting: [], unreadable: [] } as WaitingList
    const { container } = render(<BuddyPage {...route} />)
    const face = await waitFor(() => {
      const f = container.querySelector('[data-buddy-orb-slot][data-buddy-shape][data-buddy-drag]')
      expect(f).not.toBeNull()
      return f!
    }, { timeout: 2000 })
    await userEvent.click(face.querySelector('button')!)
    const open = await screen.findByRole('link', { name: /Open in PersonalClaw/ })
    await userEvent.click(open)
    expect(bridge.openInApp).toHaveBeenCalledWith('#/mission-control')
    expect(resolveApproval).not.toHaveBeenCalled()
  })
})

describe('in the window of a shell built before its layout push', () => {
  afterEach(() => { delete window.pclawBuddy })

  it('the page lays itself out by the window\'s rule: the orb at its top centre, the card beside it', async () => {
    window.pclawBuddy = { pointerOver: vi.fn(), setRegions: vi.fn(), dragStart: vi.fn(), dragBy: vi.fn(), dragEnd: vi.fn(), setPresentation: vi.fn(), openInApp: vi.fn(), onRing: vi.fn(() => () => {}) }
    const { container } = render(<BuddyPage {...route} />)
    await prompt()
    const slot = container.querySelector<HTMLElement>('[data-buddy-orb-slot]')!
    expect(slot.style.left).toBe(`${Math.round(window.innerWidth / 2 - 28)}px`)
    expect(slot.style.top).toBe('8px')
    const area = container.querySelector<HTMLElement>('[data-buddy-card-area]')!
    expect(area.dataset.side).toBe(window.innerWidth >= 2 * (28 + 8 + 420) + 16 ? 'right' : 'below')
  })

  it('under a whole-UI zoom, lays itself out in the zoomed page: the orb still at its top centre', async () => {
    window.pclawBuddy = { pointerOver: vi.fn(), setRegions: vi.fn(), dragStart: vi.fn(), dragBy: vi.fn(), dragEnd: vi.fn(), setPresentation: vi.fn(), openInApp: vi.fn(), onRing: vi.fn(() => () => {}) }
    document.documentElement.style.zoom = '1.5'
    try {
      const { container } = render(<BuddyPage {...route} />)
      await prompt()
      const slot = container.querySelector<HTMLElement>('[data-buddy-orb-slot]')!
      expect(slot.style.left, 'in the page\'s zoomed points, which the zoom makes the window\'s').toBe(`${Math.round(window.innerWidth / 1.5 / 2 - 28)}px`)
      expect(slot.style.zoom, 'a tab zooms all of it').toBe('')
    } finally {
      document.documentElement.style.zoom = ''
    }
  })
})

describe('in the window of a shell built before its doorbell', () => {
  afterEach(() => { delete window.pclawBuddy })

  it('the page keeps its own socket, and draws as before', async () => {
    window.pclawBuddy = { pointerOver: vi.fn(), setRegions: vi.fn(), dragStart: vi.fn(), dragBy: vi.fn(), dragEnd: vi.fn(), setPresentation: vi.fn(), openInApp: vi.fn() }
    render(<BuddyPage {...route} />)
    expect(await prompt()).toBeTruthy()
    expect(sockets, 'no ring to hear, so its own socket rings it').toBeGreaterThan(0)
  })
})

describe("the buddy's approval is the app's own, word for word", () => {
  it('reads as the phone reads the same row: its text, its choices\' names and what declining does', async () => {
    const buddy = render(<BuddyPage {...route} />)
    const mine = await prompt()
    const read = (g: HTMLElement) => ({
      text: g.textContent?.replace(/\s+/g, ' ').trim(),
      names: within(g).getAllByRole('button').map((b) => b.getAttribute('aria-label') || b.textContent),
    })
    const words = read(mine)
    buddy.unmount()
    render(<CompanionPage {...route} />)
    const phone = await prompt()
    expect(words).toEqual(read(phone))
  })
})
