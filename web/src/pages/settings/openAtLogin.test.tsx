/** Settings → Security → "Open at login" — the login-item bridge's renderer half (DC-4 T4.3).
 *
 *  THE DEFECT THIS CLOSES is an inert control, and it had two halves. `preload.js` exposed
 *  `pclawDesktop.loginItem.get/set` and `main.js` registered the IPC, but
 *  `web/src/lib/desktopBridge.ts` never declared the namespace and no surface called it —
 *  measured: `git grep loginItem -- web/` returned nothing. Meanwhile the row a user DOES
 *  see was worse than missing: `login_item` is a `kind: "shell"` capability, so `probe()`
 *  answers `granted` the moment the shell runs, and the panel rendered a green **"Open at
 *  login — Granted"** on a machine with no registration, with no control to change it.
 *
 *  So the assertions below are shaped around "does it REACH the bridge", not "does it
 *  render". A toggle that flips and writes nothing is the whole bug, which is why the
 *  positive legs read the recorded bridge calls rather than the switch's own position.
 *
 *  REAL here (jsdom): the panel, the row, the state read-back, the desktop-only gate.
 *  SIMULATED: `window.pclawDesktop` is a recording double — it exists only inside Electron.
 *  UNRUNNABLE here: whether macOS actually launches the app at the next reboot. That needs
 *  a signed bundle, a real login, and a machine restart; no test in this repo can observe it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SecurityPanel } from './SecurityPanel'
import type { DesktopStateWire } from '../../lib/api'
import type { LoginItemResult, LoginItemState } from '../../lib/desktopBridge'

const STATS = { denied_commands: 0, suspicious_patterns: 0, tool_schemas: 0, redaction_paths: 0 }
const desktopState = vi.fn<() => Promise<DesktopStateWire | null>>()

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      securityStats: () => Promise.resolve(STATS),
      deniedCommands: () => Promise.resolve({
        builtin: [], user: [],
        baseline: { version: 1, sha256: '0'.repeat(64), count: 0, verified: true, detail: '' },
        user_additions: 0,
      }),
      securityEgress: () => Promise.reject(new Error('not under test')),
      desktopState: () => desktopState(),
    },
  }
})

/** `describe()`'s real macOS sentence, verbatim from `desktop/loginItem.js`. */
const DESCRIBES = 'macOS Login Items (System Settings → General → Login Items) for this app bundle only'
/** And its real answer where Electron implements no login item. */
const UNSUPPORTED = 'login items are not implemented on linux'

/** A shell connected with the login-item capability present — the state in which the old
 *  code drew "Open at login — Granted" and offered nothing. */
const CONNECTED: DesktopStateWire = {
  connected: true,
  shell: { version: '0.1.0', platform: 'darwin' },
  capabilities: {
    login_item: { available: true, granted: 'granted', requestable: false, reason: '' },
  },
  registered_at: '2026-08-27T00:00:00+00:00',
  last_seen: '2026-08-27T00:00:00+00:00',
}

/** The recording double for `pclawDesktop.loginItem`. `sets` is the observable that
 *  separates a live control from an inert one. */
function fakeLoginItemBridge({
  enabled = false,
  supported = true,
  /** Simulate the OS dropping the write — `set()` reads back and reports the truth. */
  refuse = false,
  /** Simulate a shell built before the namespace existed. */
  absent = false,
} = {}) {
  let current = enabled
  const sets: boolean[] = []
  const bridge: Record<string, unknown> = {
    capabilities: {
      names: () => [], probe: vi.fn(), snapshot: vi.fn(), request: vi.fn(), on: () => () => {},
    },
  }
  if (!absent) {
    bridge.loginItem = {
      get: (): Promise<LoginItemState> =>
        // `describes` follows support, exactly as `desktop/loginItem.js` does: on an
        // unsupported platform it names the refusal, not a macOS list that does not apply.
        Promise.resolve({ enabled: current, supported, describes: supported ? DESCRIBES : UNSUPPORTED }),
      set: (next: boolean): Promise<LoginItemResult> => {
        sets.push(next)
        if (!supported) {
          return Promise.resolve({ ok: false, enabled: false, changed: false, supported: false, reason: 'login items are not implemented on linux' })
        }
        if (refuse) {
          return Promise.resolve({ ok: false, enabled: current, changed: false, supported: true, reason: 'the OS did not apply the change' })
        }
        const changed = current !== next
        current = next
        return Promise.resolve({ ok: true, enabled: current, changed, supported: true })
      },
    }
  }
  ;(window as unknown as { pclawDesktop: unknown }).pclawDesktop = bridge
  return { sets, current: () => current }
}

const toggle = () => screen.getByRole('switch', { name: /open at login/i })

beforeEach(() => {
  sessionStorage.clear()
  desktopState.mockReset()
  desktopState.mockResolvedValue(CONNECTED)
})

afterEach(() => {
  delete window.pclawDesktop
})

describe('Open at login — the desktop-only gate', () => {
  it('renders NO toggle in a browser tab', async () => {
    // No bridge at all, and the gateway says nothing is connected: the section already
    // states that, and a switch here would be the dead control this row used to be.
    desktopState.mockResolvedValue({
      connected: false, shell: null, capabilities: {}, registered_at: '', last_seen: '',
    })
    render(<SecurityPanel />)
    expect(await screen.findByText(/Desktop app not connected/)).toBeTruthy()
    expect(screen.queryByRole('switch', { name: /open at login/i })).toBeNull()
  })

  it('renders no toggle when the gateway reports a shell but this page has no bridge', async () => {
    // The case a `connected`-only gate would get wrong: a BROWSER tab open against the
    // same gateway while the desktop app runs. `connected` is true, but there is no IPC
    // here, so the control must not appear.
    render(<SecurityPanel />)
    expect(await screen.findByText('Open at login')).toBeTruthy()
    expect(screen.queryByRole('switch', { name: /open at login/i })).toBeNull()
  })

  it('falls back to the plain capability row on a shell with no loginItem namespace', async () => {
    fakeLoginItemBridge({ absent: true })
    render(<SecurityPanel />)
    expect(await screen.findByText('Open at login')).toBeTruthy()
    expect(screen.queryByRole('switch', { name: /open at login/i })).toBeNull()
  })
})

describe('Open at login — inside the desktop shell', () => {
  it('reports the OS registration, not the capability probe', async () => {
    // The regression that matters: `capabilities.login_item.granted` is `granted` in
    // CONNECTED, yet nothing is registered. The row must say OFF.
    fakeLoginItemBridge({ enabled: false })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle()).toBeTruthy())
    expect(toggle().getAttribute('aria-checked')).toBe('false')
    expect(screen.getByText(/starts only when you open it/)).toBeTruthy()
    expect(screen.queryByText('Granted')).toBeNull()
  })

  it('names what it touches before the user flips it', async () => {
    fakeLoginItemBridge()
    render(<SecurityPanel />)
    expect(await screen.findByText(DESCRIBES)).toBeTruthy()
  })

  it('reflects an already-registered login item', async () => {
    fakeLoginItemBridge({ enabled: true })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('true'))
    expect(screen.getByText(/starts when you log in/)).toBeTruthy()
  })

  it('WRITES THROUGH THE BRIDGE when flipped, and shows what came back', async () => {
    // The anti-inert leg. `sets` is read, not the switch's position: a control that
    // moves and reaches nothing is exactly what this atom found.
    const b = fakeLoginItemBridge({ enabled: false })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle()).toBeTruthy())

    await userEvent.click(toggle())

    await waitFor(() => expect(b.sets).toEqual([true]))
    expect(b.current()).toBe(true)
    await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('true'))
  })

  it('un-registers through the same call', async () => {
    const b = fakeLoginItemBridge({ enabled: true })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('true'))

    await userEvent.click(toggle())

    await waitFor(() => expect(b.sets).toEqual([false]))
    expect(b.current()).toBe(false)
  })

  it('a refused write leaves the switch showing the OS, not the request', async () => {
    const b = fakeLoginItemBridge({ enabled: false, refuse: true })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle()).toBeTruthy())

    await userEvent.click(toggle())

    await waitFor(() => expect(screen.getByText(/did not apply the change/)).toBeTruthy())
    // The write must be ATTEMPTED, not skipped — otherwise this case would pass for a
    // control that never reached the bridge at all, which is the defect next door.
    expect(b.sets).toEqual([true])
    // The switch snaps back: a toggle sitting ON while nothing is registered is the
    // same lie in the opposite direction.
    expect(toggle().getAttribute('aria-checked')).toBe('false')
  })

  it('an unsupported platform softens the switch and NAMES why, keeping it reachable', async () => {
    // A PRECONDITION, not an in-flight write: nothing the user does on this page will ever
    // enable it, so the switch carries its reason and stays in the tab order rather than
    // going dark and unexplained. `aria-disabled`, deliberately not native `disabled` — a
    // natively disabled switch leaves the tab order and a keyboard user tabs straight past
    // the one control that would have told them why.
    fakeLoginItemBridge({ supported: false })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle()).toBeTruthy())
    expect(toggle().getAttribute('aria-disabled')).toBe('true')
    expect(toggle()).not.toBeDisabled()
    expect(toggle().getAttribute('title')).toBe(UNSUPPORTED)
  })

  it('an unsupported platform cannot write a registration the OS has no API for', async () => {
    const b = fakeLoginItemBridge({ supported: false })
    render(<SecurityPanel />)
    await waitFor(() => expect(toggle()).toBeTruthy())
    await userEvent.click(toggle())
    // `aria-disabled` keeps the control focusable, so the refusal has to be real rather
    // than relying on the browser to swallow the click.
    expect(b.sets).toEqual([])
    expect(toggle().getAttribute('aria-checked')).toBe('false')
  })
})
