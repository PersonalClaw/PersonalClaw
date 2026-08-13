/** Settings → Security → Desktop capabilities (DC-2 T2.3).
 *
 *  The panel's whole job is to render TRUTH, so the cases below are the ways it could
 *  lie: naming capabilities in a browser tab where none can be granted, offering a
 *  grant button for something macOS will not let the app prompt for, or dressing a
 *  "not requested yet" state up as a failure.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SecurityPanel } from './SecurityPanel'
import type { DesktopStateWire } from '../../lib/api'

const STATS = { denied_commands: 1, suspicious_patterns: 2, tool_schemas: 3, redaction_paths: 4 }

const desktopState = vi.fn<() => Promise<DesktopStateWire | null>>()

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      securityStats: () => Promise.resolve(STATS),
      deniedCommands: () => Promise.resolve({ builtin: [], user: [] }),
      // The panel catches this; the egress editor is not under test here.
      securityEgress: () => Promise.reject(new Error('not under test')),
      desktopState: () => desktopState(),
    },
  }
})

const CONNECTED = (caps: DesktopStateWire['capabilities']): DesktopStateWire => ({
  connected: true,
  shell: { version: '0.1.0', platform: 'darwin' },
  capabilities: caps,
  registered_at: '2026-08-13T00:00:00+00:00',
  last_seen: '2026-08-13T00:00:00+00:00',
})

const cap = (over: Partial<DesktopStateWire['capabilities'][string]> = {}) => ({
  available: true,
  granted: 'not-determined' as const,
  requestable: true,
  reason: '',
  ...over,
})

beforeEach(() => {
  sessionStorage.clear()
  desktopState.mockReset()
})

afterEach(() => {
  delete window.pclawDesktop
})

describe('DesktopCapabilitiesPanel — a browser tab', () => {
  it('says the desktop app is not connected instead of listing capabilities', async () => {
    desktopState.mockResolvedValue({
      connected: false, shell: null, capabilities: {}, registered_at: '', last_seen: '',
    })
    render(<SecurityPanel />)
    expect(await screen.findByText(/Desktop app not connected/)).toBeTruthy()
    // Nothing may advertise a capability the gateway cannot deliver.
    expect(screen.queryByText(/Microphone/)).toBeNull()
    expect(screen.queryByRole('button', { name: /allow/i })).toBeNull()
  })

  it('renders the not-connected state even if a stale payload carries capabilities', async () => {
    // Defence in depth: `connected: false` wins over a non-empty map, so a caching bug
    // upstream cannot resurrect grant buttons in a tab.
    desktopState.mockResolvedValue({
      connected: false, shell: null,
      capabilities: { audio_capture: cap() },
      registered_at: '', last_seen: '',
    })
    render(<SecurityPanel />)
    expect(await screen.findByText(/Desktop app not connected/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /allow/i })).toBeNull()
  })
})

describe('DesktopCapabilitiesPanel — connected to the shell', () => {
  it('renders each capability with its state, and one named grant button per row', async () => {
    desktopState.mockResolvedValue(CONNECTED({
      audio_capture: cap(),
      tray: cap({ granted: 'granted', requestable: false }),
    }))
    render(<SecurityPanel />)
    expect(await screen.findByText('Microphone')).toBeTruthy()
    expect(screen.getByText('Not requested yet')).toBeTruthy()
    expect(screen.getByText('Menu-bar item')).toBeTruthy()
    expect(screen.getByText('Granted')).toBeTruthy()
    // The capability is IN the accessible name, so rows are distinguishable.
    expect(screen.getByRole('button', { name: 'Allow microphone' })).toBeTruthy()
    // An already-granted, non-requestable capability offers no button.
    expect(screen.queryByRole('button', { name: /menu-bar/i })).toBeNull()
  })

  it('offers NO button for a disclosure-only capability, and shows where to grant it', async () => {
    desktopState.mockResolvedValue(CONNECTED({
      screen_capture: cap({
        granted: 'denied',
        requestable: false,
        reason: 'Grant Screen Recording in System Settings › Privacy & Security.',
      }),
    }))
    render(<SecurityPanel />)
    expect(await screen.findByText('Screen recording')).toBeTruthy()
    // The row carries its own reason (the section hint mentions System Settings too).
    expect(screen.getByText(/Grant Screen Recording in System Settings/)).toBeTruthy()
    // macOS exposes no prompt for this, so a button here would do nothing.
    expect(screen.queryByRole('button', { name: /allow screen/i })).toBeNull()
  })

  it('never claims a notification grant it cannot observe', async () => {
    desktopState.mockResolvedValue(CONNECTED({
      native_notifications: cap({
        granted: 'not-determined',
        requestable: false,
        reason: 'macOS does not report notification authorization to the app.',
      }),
    }))
    render(<SecurityPanel />)
    expect(await screen.findByText('Native notifications')).toBeTruthy()
    expect(screen.getByText('Not requested yet')).toBeTruthy()
    expect(screen.queryByText('Granted')).toBeNull()
  })

  it('renders an unmapped capability name rather than dropping it', async () => {
    // A capability the shell knows and this panel does not must still be disclosed —
    // a default branch that swallowed it would make the UI less honest than the API.
    desktopState.mockResolvedValue(CONNECTED({ future_thing: cap({ requestable: false }) }))
    render(<SecurityPanel />)
    expect(await screen.findByText('Future thing')).toBeTruthy()
  })

  it('routes a grant through the bridge and re-reads the gateway afterwards', async () => {
    desktopState
      .mockResolvedValueOnce(CONNECTED({ audio_capture: cap() }))
      .mockResolvedValue(CONNECTED({
        audio_capture: cap({ granted: 'granted', requestable: false }),
      }))
    const request = vi.fn().mockResolvedValue({
      granted: true, state: 'granted', prompted: true, reason: '',
    })
    window.pclawDesktop = {
      capabilities: {
        names: () => ['audio_capture'],
        probe: vi.fn(),
        snapshot: vi.fn(),
        request,
        on: () => () => {},
      },
    } as unknown as Window['pclawDesktop']

    render(<SecurityPanel />)
    await userEvent.click(await screen.findByRole('button', { name: 'Allow microphone' }))
    expect(request).toHaveBeenCalledWith('audio_capture')
    // The panel re-reads the gateway rather than trusting its own optimistic guess.
    await waitFor(() => expect(screen.getByText('Granted')).toBeTruthy())
  })

  it('surfaces the reason when the OS refuses, without claiming a grant', async () => {
    desktopState.mockResolvedValue(CONNECTED({ audio_capture: cap() }))
    window.pclawDesktop = {
      capabilities: {
        names: () => ['audio_capture'],
        probe: vi.fn(),
        snapshot: vi.fn(),
        request: vi.fn().mockResolvedValue({
          granted: false, state: 'denied', prompted: true,
          reason: 'Microphone was already denied for PersonalClaw.',
        }),
        on: () => () => {},
      },
    } as unknown as Window['pclawDesktop']

    render(<SecurityPanel />)
    await userEvent.click(await screen.findByRole('button', { name: 'Allow microphone' }))
    expect(await screen.findByText(/already denied/)).toBeTruthy()
  })

  it('says the shell is gone when the bridge is absent mid-session', async () => {
    // The panel painted from a cached connected state, then the shell quit — clicking
    // must report that, not fail silently.
    desktopState.mockResolvedValue(CONNECTED({ audio_capture: cap() }))
    render(<SecurityPanel />)
    await userEvent.click(await screen.findByRole('button', { name: 'Allow microphone' }))
    expect(await screen.findByText(/no longer connected/)).toBeTruthy()
  })
})
