import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { BuddyPolicy } from '../../lib/api'

// ── Settings → Buddy: every buddy setting, in one place (#3901) ────────────────────────────────────
//
// Buddy status (`buddy.show`) is the opt-in, off on a fresh home; beside it, which surfaces show the
// buddy and how long you are away before it steps back. Each control writes its own `buddy.*` key.
// The notification posture the buddy's alerts follow is shown, not set here, with the way to
// Settings → Notifications; so is whether the system asks for reduced motion, with where to change
// it, and no switch of the buddy's own. Nothing for play, sound or the rest is offered. The panel
// reads the policy again when the owner's socket says it changed (`buddy_policy_changed`).

const { CFG, POLICY, patched, socket } = vi.hoisted(() => ({
  CFG: { buddy: {} as Record<string, unknown> },
  POLICY: { value: null as unknown as BuddyPolicy },
  patched: [] as [string, unknown][],
  socket: { onMessage: null as null | ((m: { type: string; data: Record<string, unknown> }) => void) },
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      personalclawConfig: () => Promise.resolve({ buddy: { ...CFG.buddy } }),
      patchConfig: (path: string, value: unknown) => { patched.push([path, value]); return Promise.resolve({}) },
      buddyPolicy: () => Promise.resolve(POLICY.value),
    },
  }
})
vi.mock('../../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: { type: string; data: Record<string, unknown> }) => void) => { socket.onMessage = onMessage },
}))

const { BuddyPanel } = await import('./BuddyPanel')

const policy = (over: Partial<BuddyPolicy> = {}, notes: Partial<BuddyPolicy['notifications']> = {}): BuddyPolicy => ({
  show: false,
  surfaces: { window: false, tray: false, dock: false, notifications: false },
  away_after_secs: 180,
  notifications: { muted: false, quiet_now: false, may_interrupt: true, changes_at: null, ...notes },
  ...over,
})

let reduced = false
const listeners = new Set<() => void>()
beforeEach(() => {
  invalidateKeys('settings:buddy', true)
  CFG.buddy = { show: false, window: true, tray: true, dock: true, notifications: true, away_after_secs: 180 }
  POLICY.value = policy()
  patched.length = 0
  reduced = false
  listeners.clear()
  vi.stubGlobal('matchMedia', (q: string) => ({
    get matches() { return q.includes('reduced-motion') && reduced },
    media: q,
    addEventListener: (_: string, l: () => void) => listeners.add(l),
    removeEventListener: (_: string, l: () => void) => listeners.delete(l),
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function mount() {
  render(<BuddyPanel />)
  return screen.findByRole('switch', { name: 'Buddy status' })
}

describe('the owner\'s knobs', () => {
  it('a fresh home has Buddy status off, and turning it on writes buddy.show', async () => {
    const status = await mount()
    expect(status).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(status)
    await waitFor(() => expect(patched).toContainEqual(['buddy.show', true]))
  })

  it.each([['Buddy window', 'window'], ['Tray face', 'tray'], ['Dock face', 'dock'], ['Alert notifications', 'notifications']])(
    '%s writes buddy.%s', async (label, key) => {
      await mount()
      fireEvent.click(screen.getByRole('switch', { name: label }))
      await waitFor(() => expect(patched).toContainEqual([`buddy.${key}`, false]))
    },
  )

  it('how long you are away before it steps back writes buddy.away_after_secs', async () => {
    await mount()
    expect(screen.getByRole('button', { name: 'When you’re away: 3 min' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'When you’re away: 10 min' }))
    await waitFor(() => expect(patched).toContainEqual(['buddy.away_after_secs', 600]))
  })

  it('offers nothing for play, drop, quick ask, sound, sprites or the bridge', async () => {
    await mount()
    const text = document.body.textContent ?? ''
    for (const later of [/\bplay\b/i, /\bdrop\b/i, /quick ask/i, /\bsound\b/i, /\bvolume\b/i, /sprite/i, /bridge/i]) {
      expect(text, String(later)).not.toMatch(later)
    }
    expect(patched.every(([p]) => /^buddy\.(show|window|tray|dock|notifications|away_after_secs)$/.test(p))).toBe(true)
  })
})

describe('what the buddy follows, shown and not set here', () => {
  it('the notification posture, with the way to Settings → Notifications', async () => {
    // 7:00 on this machine's clock, whatever its zone: the line says the time where the owner is.
    const sevenHere = new Date(2026, 9, 22, 7, 0).toISOString()
    POLICY.value = policy({}, { quiet_now: true, may_interrupt: false, changes_at: sevenHere })
    await mount()
    const notes = await screen.findByRole('group', { name: 'Notifications' })
    expect(await within(notes).findByText(/In quiet hours until 7:00/)).toBeInTheDocument()
    expect(within(notes).getByRole('link', { name: /Settings → Notifications/ }).getAttribute('href')).toBe('#/settings/notifications')
    expect(within(notes).queryByRole('switch')).toBeNull()
  })

  it('muted says so', async () => {
    POLICY.value = policy({}, { muted: true, may_interrupt: false })
    await mount()
    expect(await screen.findByText(/Notifications are muted/)).toBeInTheDocument()
  })

  it('reduced motion is the system\'s, shown with where to change it, and follows it live', async () => {
    reduced = true
    await mount()
    const motion = screen.getByRole('group', { name: 'Reduced motion' })
    expect(within(motion).getByText(/Your system asks for reduced motion/)).toBeInTheDocument()
    expect(within(motion).getByText(/Accessibility/)).toBeInTheDocument()
    expect(within(motion).queryByRole('switch')).toBeNull()
    reduced = false
    act(() => listeners.forEach((l) => l()))
    expect(within(motion).getByText(/Your system does not ask for reduced motion/)).toBeInTheDocument()
  })
})

describe('this computer\'s rows, in the desktop app only', () => {
  afterEach(() => { delete (window as { pclawDesktop?: unknown }).pclawDesktop })
  const shell = (place: 'docked' | 'floating', hidden: boolean | null) => {
    const bridge = {
      getPlace: vi.fn(() => Promise.resolve({ mode: place })),
      resetPlace: vi.fn(() => Promise.resolve({ mode: 'docked' as const })),
      getHideFromCapture: vi.fn(() => Promise.resolve(hidden)),
      setHideFromCapture: vi.fn((on: boolean) => Promise.resolve(hidden === null ? null : on)),
    }
    ;(window as { pclawDesktop?: unknown }).pclawDesktop = { buddy: bridge }
    return bridge
  }

  it('a browser tab has none of them', async () => {
    await mount()
    expect(screen.queryByText('This computer')).toBeNull()
    expect(screen.queryByRole('switch', { name: 'Hide from screen sharing and screenshots' })).toBeNull()
  })

  it('a floating buddy is docked from here, and the row says where it is now', async () => {
    const bridge = shell('floating', true)
    await mount()
    expect(await screen.findByText('Floating')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Dock it' }))
    expect(bridge.resetPlace).toHaveBeenCalled()
    expect(await screen.findByText('Docked')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Dock it' })).toBeNull()
  })

  it('screen capture is kept out by this computer\'s own switch', async () => {
    const bridge = shell('docked', true)
    await mount()
    const keep = await screen.findByRole('switch', { name: 'Hide from screen sharing and screenshots' })
    await waitFor(() => expect(keep).toHaveAttribute('aria-checked', 'true'))
    fireEvent.click(keep)
    expect(bridge.setHideFromCapture).toHaveBeenCalledWith(false)
    await waitFor(() => expect(keep).toHaveAttribute('aria-checked', 'false'))
    expect(patched.some(([p]) => p.includes('capture') || p.includes('place')), 'nothing of it goes to the gateway').toBe(false)
  })

  it('where this computer cannot keep a window out of a capture, it says so and offers no switch', async () => {
    const bridge = shell('docked', null)
    await mount()
    expect(await screen.findByText('Not available on this computer')).toBeInTheDocument()
    expect(screen.getByText(/This computer cannot keep a window out of a share or a screenshot/)).toBeInTheDocument()
    expect(screen.queryByRole('switch', { name: 'Hide from screen sharing and screenshots' })).toBeNull()
    expect(bridge.setHideFromCapture).not.toHaveBeenCalled()
  })

  it('where it can, the row names no platform that cannot', async () => {
    shell('docked', true)
    await mount()
    await screen.findByRole('switch', { name: 'Hide from screen sharing and screenshots' })
    expect(screen.queryByText(/Linux/)).toBeNull()
    expect(screen.queryByText('Not available on this computer')).toBeNull()
  })
})

describe('the panel follows a change made anywhere', () => {
  it('reads the policy again when the owner\'s socket says it changed', async () => {
    await mount()
    expect(await screen.findByText(/Alerts may interrupt you now/)).toBeInTheDocument()
    POLICY.value = policy({}, { muted: true, may_interrupt: false })
    act(() => socket.onMessage?.({ type: 'buddy_policy_changed', data: {} }))
    expect(await screen.findByText(/Notifications are muted/)).toBeInTheDocument()
  })
})
