import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DevicesPanel } from './DevicesPanel'
import { DialogHost } from '../../ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../ui/dialog/dialogStore'
import { invalidateKeys } from '../../lib/data'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api, type DeviceRec, type DevicePairStart, type IntegrationRec } from '../../lib/api'
import { encodeQr, qrPath } from '../../lib/qr'

// ── Settings → Devices ────────────────────────────────────────────────────────────────────────
//
// The backend shipped with FOUR routes and zero consumers, so every clause below was
// unreachable rather than wrong. Four ways this panel could look finished while lying:
//
//  • `last_seen` OF 0 MEANS "NEVER MADE AN AUTHORIZED REQUEST", and the field was held
//    back specifically because a value backfilled from the pairing time "would read as fresh
//    forever, which is worse than an absent column: the owner would use it to decide a device is
//    still in use". So "never" is asserted as its own state, and the pairing time is asserted
//    NOT to be standing in for it.
//  • A REVOKE IS A LOCKOUT. The bar is not "it asks first" — it is that the question NAMES the
//    device about to lose access, and that a dismissal writes nothing. Both are driven through
//    the real dialog, not a mocked `confirm`.
//  • A FAILED REVOKE IS INVISIBLE BY DEFAULT. The row would simply still be there, which is also
//    what a successful revoke of a re-paired device looks like. So the rejection path asserts the
//    user was TOLD.
//  • AN EMPTY LIST IS THE NORMAL ANSWER on a fresh install, so a swallowed read failure is
//    indistinguishable from the truth forever. The empty state and the failed read get different
//    copy, and the empty-state words are asserted ABSENT on the failure.

function device(over: Partial<DeviceRec> = {}): DeviceRec {
  return {
    id: 'dev-1',
    name: 'Kitchen tablet',
    kind: 'mobile',
    minted_at: 1_786_500_000,
    last_seen: 0,
    ip: '',
    issuer: 'pair',
    pool: 'device',
    expires_at: 1_790_000_000,
    current: false,
    ...over,
  }
}

// 🪤 NO `#` IN THE PATH. This fixture used to read `.../#/pair?code=…`, which the gateway never
// emits (`handlers/devices.py::_pair_base_url` composes `{base}/pair?code={formatted}`) and which
// would not WORK if it did: `/pair` is a standalone document whose script reads the code out of
// `location.search`, and a code parked behind a `#` lands in the fragment instead. A fixture that
// cannot happen is a QR payload nobody ever checked.
const START: DevicePairStart = {
  code: 'ABCD-EFGH',
  pairing_url: 'http://192.168.1.5:10000/pair?code=ABCD-EFGH',
  expires_at: Math.floor(Date.now() / 1000) + 300,
  expires_in: 300,
}

/** Toasts are how this panel reports a failed write; `notify` dispatches `ne:toast`. */
function captureToasts(): string[] {
  const seen: string[] = []
  window.addEventListener('ne:toast', ((e: Event) => {
    seen.push(String((e as CustomEvent).detail?.message ?? ''))
  }) as EventListener)
  return seen
}

const mount = () => render(<><DevicesPanel /><DialogHost /></>)

beforeEach(() => {
  // 🪤 The list rides a cached key, so a previous test's payload would seed the next mount and
  // every assertion below would measure the wrong fixture.
  invalidateKeys('settings:devices')
  invalidateKeys('settings:integrations')
  // The panel also lists integration tokens; none, unless a test says otherwise.
  vi.spyOn(api, 'deviceIntegrations').mockResolvedValue({ integrations: [], problem: '' })
})

afterEach(() => {
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list })()
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('the registry shows every column the owner needs', () => {
  it('renders name, kind, last-seen, issuer and the paired/expiry line', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device({ last_seen: 1_786_600_000 })])
    mount()

    // The vacuity floor: if the seeded device never renders, every assertion here is hollow.
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    // One line carries kind · last seen · issuer, so it is read as a whole rather than by
    // three separate substring queries — `/Paired /` alone matches both the issuer sentence
    // ("Paired with a code") and the paired-at line, which is a false ambiguity, not a defect.
    const meta = screen.getByText(/Last seen/).parentElement?.textContent ?? ''
    expect(meta, 'the kind, as a word not just a glyph').toMatch(/Phone/)
    expect(meta, 'the last-seen column').toMatch(/Last seen/)
    expect(meta, 'the issuer, in the owner’s words').toMatch(/Paired with a code/)
    expect(screen.getByText(/^Paired \d+[mhd] ago/), 'and when it paired').toBeTruthy()
    expect(screen.getByText(/session expires/), 'and when the session runs out').toBeTruthy()
  })

  it('a device that never came back reads "never" — NOT its pairing time', async () => {
    // THE distinction the whole field exists for. `minted_at` is a real, recent-ish timestamp
    // here, so a panel that coalesced the two would render a plausible relative time and look
    // completely correct.
    vi.spyOn(api, 'devices').mockResolvedValue([device({ last_seen: 0 })])
    mount()

    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    expect(screen.getByText(/Last seen never/i), 'an unstamped device is "never"').toBeTruthy()
    // And the failure mode is pinned directly: no relative time may appear in the last-seen slot.
    expect(screen.queryByText(/Last seen \d+[mhd] ago/i), 'a backfill from minted_at').toBeNull()
    expect(screen.queryByText(/Last seen just now/i)).toBeNull()
  })

  it('a stamped device reads as a relative time, so "never" is not the only branch', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([
      device({ last_seen: Math.floor(Date.now() / 1000) - 180 }),
    ])
    mount()
    await waitFor(() => expect(screen.getByText(/Last seen 3m ago/i)).toBeTruthy())
    expect(screen.queryByText(/Last seen never/i)).toBeNull()
  })

  it('an unnamed device still has something to call it', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device({ name: '' })])
    mount()
    await waitFor(() => expect(screen.getByText('Unnamed device')).toBeTruthy())
    // A revoke control with no name would be unusable by anyone not looking at the row.
    expect(screen.getByRole('button', { name: /Sign out Unnamed device/i })).toBeTruthy()
  })
})

describe('signing a device out is confirmed, named, and never silent', () => {
  it('the confirmation NAMES the device it is about to lock out', async () => {
    const revoke = vi.spyOn(api, 'deviceRevoke').mockResolvedValue({ ok: true, revoked: 1 })
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    mount()
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Sign out Kitchen tablet/i }))

    // `alertdialog` is the shell's DANGER role — finding it here is the proof this was raised as
    // destructive rather than as a neutral "are you sure?".
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent ?? '').toMatch(/Kitchen tablet/)
    expect(revoke, 'asking is not doing').not.toHaveBeenCalled()
  })

  it('a DISMISSED confirmation revokes nothing', async () => {
    const revoke = vi.spyOn(api, 'deviceRevoke').mockResolvedValue({ ok: true, revoked: 1 })
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    mount()
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Sign out Kitchen tablet/i }))
    const dialog = await screen.findByRole('alertdialog')
    const cancel = Array.from(dialog.querySelectorAll('button')).find((b) => /cancel/i.test(b.textContent ?? ''))
    expect(cancel).toBeTruthy()
    fireEvent.click(cancel!)

    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(revoke).not.toHaveBeenCalled()
    expect(screen.getByText('Kitchen tablet'), 'and the device is still listed').toBeTruthy()
  })

  it('a CONFIRMED revoke sends that device id and re-reads the list', async () => {
    const revoke = vi.spyOn(api, 'deviceRevoke').mockResolvedValue({ ok: true, revoked: 1 })
    const list = vi.spyOn(api, 'devices')
    list.mockResolvedValueOnce([device()]).mockResolvedValue([])
    mount()
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Sign out Kitchen tablet/i }))
    const dialog = await screen.findByRole('alertdialog')
    const go = Array.from(dialog.querySelectorAll('button')).find((b) => /^sign out$/i.test(b.textContent ?? ''))
    fireEvent.click(go!)

    await waitFor(() => expect(revoke).toHaveBeenCalledWith('dev-1'))
    // The list is the answer to "what can reach this gateway", so it must be re-read rather than
    // patched locally — a local splice would show a lockout that never happened.
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1))
  })

  it('a FAILED revoke is REPORTED, and the device stays listed', async () => {
    // The shape this repo has a whole family of bugs for: the write is refused, the UI says
    // nothing, and the owner stops watching a device that still holds a live session.
    const toasts = captureToasts()
    vi.spyOn(api, 'deviceRevoke').mockRejectedValue(new Error('device store is read-only'))
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    mount()
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Sign out Kitchen tablet/i }))
    const dialog = await screen.findByRole('alertdialog')
    const go = Array.from(dialog.querySelectorAll('button')).find((b) => /^sign out$/i.test(b.textContent ?? ''))
    fireEvent.click(go!)

    await waitFor(() => expect(toasts.some((t) => /Couldn't sign out Kitchen tablet/i.test(t))).toBe(true))
    // And it carries the server's reason, not a generic sentence.
    expect(toasts.join(' ')).toMatch(/read-only/)
    expect(screen.getByText('Kitchen tablet'), 'still there, because it still has access').toBeTruthy()
  })
})

describe('an empty registry and a failed read are different answers', () => {
  it('says nothing is paired, honestly, and offers the way to change that', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    mount()
    await waitFor(() => expect(screen.getByText(/No devices signed in/i)).toBeTruthy())
    // Two entrances to ONE pairing flow, with DISTINCT names: identical accessible names on one
    // screen make the action ambiguous to anyone navigating by name.
    expect(screen.getByRole('button', { name: /^Pair a device$/i }), 'the section control').toBeTruthy()
    expect(screen.getByRole('button', { name: /Pair your first device/i }), 'the empty-state on-ramp').toBeTruthy()
  })

  it('the empty state’s on-ramp actually opens pairing', async () => {
    // An empty state whose CTA does nothing is the defect the "on-ramp" verdict is about.
    vi.spyOn(api, 'devices').mockResolvedValue([])
    const start = vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices signed in/i)).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Pair your first device/i }))
    await waitFor(() => expect(start).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
  })

  it('a FAILED read renders the error, never the empty state', async () => {
    vi.spyOn(api, 'devices').mockRejectedValue(new Error('devices unreadable'))
    mount()
    await waitFor(() => expect(screen.getByText(/devices unreadable/)).toBeTruthy())
    // The whole point: a rejection must not borrow "nothing is paired", which reads as a fact.
    expect(screen.queryByText(/No devices signed in/i)).toBeNull()
  })
})

describe('pairing surfaces the code and the link', () => {
  it('shows the code and the actionable URL, with the expiry counting down', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices signed in/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
    // The URL already contains the code, which is what makes it the QR payload as well as a
    // copyable link — so it must be shown in full, not truncated to a hostname.
    expect(screen.getByText(START.pairing_url)).toBeTruthy()
    expect(screen.getByText(/Expires in \d+:\d\d/)).toBeTruthy()
    // Both values are individually copyable; a single "copy" would make one of them unreachable.
    expect(screen.getByRole('button', { name: /Copy pairing code/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Copy pairing link/i })).toBeTruthy()
  })

  it('renders a QR of the PAIRING URL — the payload, not the bare code', async () => {
    // The requirement is a QR of {pairing_url, one-time code}, and the two are one thing:
    // the URL already contains the code, which is what makes a single scan enough. So the
    // assertion is on the PAYLOAD, and its counter-assertion is the mistake that would look
    // identical on a screenshot — encoding the eight-character code, which a phone camera would
    // resolve to a string with nowhere to go.
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices signed in/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
    const svg = screen.getByRole('img', { name: /scan it with the camera/i })
    const drawn = svg.querySelector('path')?.getAttribute('d') ?? ''
    const symbol = encodeQr(START.pairing_url)!
    expect(drawn, 'the QR encodes the pairing URL').toBe(qrPath(symbol))
    expect(drawn, 'and NOT the bare code').not.toBe(qrPath(encodeQr(START.code)!))
    // Not an empty image dressed as one: a real symbol has hundreds of dark modules, and the
    // viewBox must leave room for the quiet zone or most cameras will not lock on.
    expect([...drawn.matchAll(/h1v1h-1z/g)].length).toBeGreaterThan(200)
    expect(svg.getAttribute('viewBox')).toBe(`0 0 ${symbol.size + 8} ${symbol.size + 8}`)
  })

  it('an EXPIRED code WITHDRAWS the payload instead of offering a dead one', async () => {
    // The security half. `redeem_code` refuses an expired code, so a dashboard that keeps
    // showing the QR and the link is handing the owner something guaranteed to fail at the far
    // end — where the failure reads as "pairing is broken", not "that code ran out".
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue({
      ...START, expires_at: Math.floor(Date.now() / 1000) - 5, expires_in: 0,
    })
    mount()
    await waitFor(() => expect(screen.getByText(/No devices signed in/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(screen.getByText(/This code has expired/i)).toBeTruthy())
    expect(screen.queryByText(/Expires in/i), 'not both at once').toBeNull()
    expect(screen.getByRole('button', { name: /Generate a new pairing code/i })).toBeTruthy()
    // Nothing redeemable is left on screen, and the withdrawal is EXPLAINED rather than silent.
    expect(screen.queryByRole('img', { name: /scan it with the camera/i })).toBeNull()
    expect(screen.queryByText('ABCD-EFGH'), 'the code is gone').toBeNull()
    expect(screen.queryByText(START.pairing_url), 'the link is gone').toBeNull()
    expect(screen.queryByRole('button', { name: /Copy pairing code/i })).toBeNull()
    expect(screen.getByText(/refuses an expired code/i)).toBeTruthy()
  })

  it('a gateway that cannot resolve its own address refuses the QR and keeps the code', async () => {
    // `pair/start` returns `pairing_url: ""` when the request host is unusable. A blank square
    // would read as "my camera is broken"; the sentence names the real problem, and the code —
    // which still works when typed — stays.
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue({ ...START, pairing_url: '' })
    mount()
    await waitFor(() => expect(screen.getByText(/No devices signed in/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
    expect(screen.queryByRole('img', { name: /scan it with the camera/i })).toBeNull()
    expect(screen.getByText(/could not work out its own address/i)).toBeTruthy()
    // Unlike an expired code, this one is still redeemable — so the code and its copy button stay.
    // (The VACUITY leg for both refusals is the test above: given a resolvable URL and time
    // on the clock, the same `PairingQr` draws a real symbol, so "no QR" cannot pass by the QR
    // never rendering at all.)
    expect(screen.getByRole('button', { name: /Copy pairing code/i })).toBeTruthy()
  })

  it('a FAILED pair/start is reported, not swallowed into a blank panel', async () => {
    const toasts = captureToasts()
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockRejectedValue(new Error('too many outstanding codes'))
    mount()
    await waitFor(() => expect(screen.getByText(/No devices signed in/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(toasts.some((t) => /Couldn't start pairing/i.test(t))).toBe(true))
    expect(toasts.join(' ')).toMatch(/too many outstanding codes/)
  })
})

// ── 2026-08-19: the code appeared, and the flow said nothing while dropping your place ─────────────
//
// `#/settings/devices` was the ONE shipped settings panel missing from the screenshot capture
// inventory (it is in the axe manifest, so `npm run e2e:a11y` covered it — the two lists had drifted
// while staying the same LENGTH, 32 each, which is how a set difference hides from a count). Adding
// it and driving the pairing flow with the keyboard found two things a scan cannot see:
//
//   focus on "Pair a device" → Enter →  focus = **<body>**
//     The button that had focus is REPLACED by the code view, so the user's place is simply gone.
//
//   the flow's only live region was the COUNTDOWN: measured **6 distinct texts in 6 seconds** inside
//     a `role="status"` — ~300 announcements for one 5-minute code — while the single fact worth
//     announcing, *a code is ready*, was never announced at all, because that region is mounted
//     together with its content ("a region created with its content is not reliably observed").
//
// After, measured the same way:
//
//   focus after Enter : DIV role="group" aria-label="Pairing code and link"
//   announced         : "Pairing code 2K2W-WDXF is ready. It expires in about 5 minutes."
//   countdown is live : false          stable region repeats: 1  (once per code, not per tick)
//
// 🔑 A TICKING VALUE IS NOT AN EVENT. The countdown keeps its words and its tone (an expiry carried
// by colour alone would fail 1.4.1) and stops being a live region; the two EVENTS — a code arrived,
// a code expired — go through one always-mounted `role="status" aria-live="polite" sr-only` region,
// the shape `ResultAnnouncement` and `Toaster` already use.
//
// 🪤 THE HOOKS MUST SIT ABOVE THE LOADING/ERROR EARLY RETURNS. Written after them they run on some
// renders and not others: React error #310, which took the panel down to its Retry state — and the
// build was green, so only driving it in a browser showed the crash.

describe('the pairing flow says what happened, and keeps your place', () => {
  it('announces the code once, through an always-mounted region', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    const { container } = mount()
    await waitFor(() => expect(screen.getByText(/No devices signed in/i)).toBeTruthy())

    // Mounted and EMPTY before anything happens — that is what makes the later update observable.
    const region = () => container.querySelector('[role="status"][aria-live="polite"].sr-only')
    expect(region(), 'the announcement region must exist before the event').toBeTruthy()
    expect(region()!.textContent, 'and say nothing while idle').toBe('')

    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])
    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
    await waitFor(() => expect(region()!.textContent).toMatch(/Pairing code ABCD-EFGH is ready/))
    expect(region()!.textContent, 'and it says how long it lasts, in words').toMatch(/expires in about 5 minutes/)
  })

  it('the countdown is NOT a live region — a ticking value is not an event', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    const { container } = mount()
    await waitFor(() => expect(screen.getByText(/No devices signed in/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])
    await waitFor(() => expect(screen.getByText(/Expires in/)).toBeTruthy())

    const ticking = screen.getByText(/Expires in/)
    expect(ticking.getAttribute('role'), 'six announcements in six seconds is not an announcement')
      .not.toBe('status')
    expect(ticking.closest('[aria-live]'), 'nor may an ancestor make it live').toBeNull()
    // The words and the tone stay: an expiry carried only by colour would fail 1.4.1.
    expect(ticking.textContent).toMatch(/Expires in \d+:\d\d/)
    // And exactly ONE live region exists in the card — the stable one.
    expect(container.querySelectorAll('[role="status"]').length).toBe(1)
  })

  it('focus moves to the code, on a named group that is not in the tab order', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices signed in/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])
    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())

    const group = screen.getByRole('group', { name: 'Pairing code and link' })
    await waitFor(() => expect(document.activeElement).toBe(group))
    expect(group.getAttribute('tabindex'), 'a programmatic target, not a new tab stop').toBe('-1')
    expect(group.contains(screen.getByText('ABCD-EFGH')), 'and it must actually contain the code').toBe(true)
  })

  it('the hooks that do this run before the panel can bail out', () => {
    // 🪤 React #310, and the build cannot see it. Written after the loading/error returns these effects
    // run on some renders only, and the panel renders its Retry state instead of itself.
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/DevicesPanel.tsx'), 'utf8')
    const firstEffectForCode = src.indexOf('if (announce.current === pairing.code) return')
    const firstEarlyReturn = src.indexOf('if (!data && loadErr) return')
    expect(firstEffectForCode, 'the announce/focus effect must exist').toBeGreaterThan(-1)
    expect(firstEarlyReturn, 'the loading guard must exist').toBeGreaterThan(-1)
    expect(firstEffectForCode, 'hooks before guards, always').toBeLessThan(firstEarlyReturn)
  })

  it('the announcement is keyed on the CODE, so a tick cannot re-announce or steal focus', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/DevicesPanel.tsx'), 'utf8')
    expect(src, 'guarded on the code it already announced').toContain('if (announce.current === pairing.code) return')
    expect(src, 'and the effect depends on the pairing object, not on the countdown').toMatch(/\}, \[pairing\]\)/)
    expect(src, 'the countdown state must NOT be a dependency of the focus effect').not.toMatch(/\}, \[pairing, left\]\)/)
  })
})

// ── The list is EVERY sign-in, and any of them can be signed out ──────────────────────────────
//
// The list used to be paired phones only, so the owner's own browsers, the desktop app and every
// script token — the sessions the old five-session limit silently signed out — could not even be
// seen. Each row now says what it is, when it signed in, when and where it was last seen, and
// how it got in; the one asking is marked; and "Sign out all other devices" is one click.
describe('the list names every sign-in, and signs any of them out', () => {
  const now = () => Math.floor(Date.now() / 1000)
  const rows = (): DeviceRec[] => [
    device({
      id: 'me', name: 'Chrome on Mac', kind: 'browser', issuer: 'startup', pool: 'browser',
      current: true, ip: '127.0.0.1', last_seen: now() - 60, minted_at: now() - 3 * 86400,
    }),
    device({ id: 'phone', name: 'iPhone', ip: '192.168.1.40', last_seen: now() - 3600 }),
    device({
      id: 'script', name: 'curl', kind: 'cli', issuer: 'token', pool: 'token', ip: '127.0.0.1',
      last_seen: now() - 120, minted_at: now() - 7200,
    }),
  ]

  it('shows what each one is, when it signed in, when and where it was last seen', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue(rows())
    mount()
    await waitFor(() => expect(screen.getByText('Chrome on Mac')).toBeTruthy())

    const meta = (name: string) =>
      screen.getByText(name).closest('div.flex.items-center.justify-between')?.textContent ?? ''
    expect(meta('Chrome on Mac'), 'the one asking is marked').toMatch(/This device/)
    expect(meta('Chrome on Mac')).toMatch(/Browser · Last seen 1m ago · from 127\.0\.0\.1/)
    expect(meta('Chrome on Mac'), 'how it got in').toMatch(/opened at startup/)
    expect(meta('Chrome on Mac'), 'when it signed in').toMatch(/Signed in 3d ago/)
    expect(meta('iPhone')).toMatch(/Phone · Last seen 1h ago · from 192\.168\.1\.40 · Paired with a code/)
    expect(meta('iPhone')).not.toMatch(/This device/)
    // A token nothing has opened is listed apart, as a token, with when it stops working.
    const tokens = screen.getByText(/^Tokens \(1\)$/).closest('section') ?? document.body
    expect(tokens.textContent).toMatch(/curl/)
    expect(meta('curl')).toMatch(/Token · Last seen 2m ago/)
    expect(meta('curl')).toMatch(/Issued 2h ago · stops working/)
  })

  it('a token already used by a client that did not name itself never reads as unused', async () => {
    // Found driving it: Node's `fetch` names itself only "node", so a script's token was listed
    // as "Token not used yet" directly above "Last seen just now · from 127.0.0.1".
    vi.spyOn(api, 'devices').mockResolvedValue([
      device({
        id: 'used', name: '', kind: 'unknown', issuer: 'token', pool: 'token', ip: '127.0.0.1',
        last_seen: now() - 30, minted_at: now() - 600,
      }),
      device({ id: 'fresh', name: '', kind: 'unknown', issuer: 'token', pool: 'token', minted_at: now() - 60 }),
    ])
    mount()
    await waitFor(() => expect(screen.getByText('Unrecognised client')).toBeTruthy())
    expect(screen.getAllByText('Token not used yet'), 'only the one with no use listed').toHaveLength(1)
  })

  it('signing ANOTHER device out names it, and sends its id', async () => {
    const revoke = vi.spyOn(api, 'deviceRevoke').mockResolvedValue({ ok: true, revoked: 1 })
    vi.spyOn(api, 'devices').mockResolvedValue(rows())
    mount()
    await waitFor(() => expect(screen.getByText('iPhone')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /^Sign out iPhone$/i }))
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent ?? '').toMatch(/iPhone/)
    expect(dialog.textContent ?? '', 'a paired device signs back in by pairing').toMatch(/pair it again/)
    // Its pushes end with its sign-in, and the owner is told before choosing.
    expect(dialog.textContent ?? '').toMatch(/gets no more push notifications from it/)
    fireEvent.click(Array.from(dialog.querySelectorAll('button')).find((b) => /^sign out$/i.test(b.textContent ?? ''))!)
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('phone'))
  })

  it('"Sign out all other devices" confirms how many, and keeps this one', async () => {
    const others = vi.spyOn(api, 'devicesRevokeOthers').mockResolvedValue({ ok: true, revoked: 2 })
    const one = vi.spyOn(api, 'deviceRevoke')
    vi.spyOn(api, 'devices').mockResolvedValue(rows())
    mount()
    await waitFor(() => expect(screen.getByText('Chrome on Mac')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /^Sign out all other devices$/i }))
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent ?? '').toMatch(/2 other devices and tokens/)
    expect(dialog.textContent ?? '').toMatch(/get no more push notifications/)
    expect(dialog.textContent ?? '').toMatch(/This device stays signed in/)
    expect(others, 'asking is not doing').not.toHaveBeenCalled()
    fireEvent.click(Array.from(dialog.querySelectorAll('button')).find((b) => /sign out all others/i.test(b.textContent ?? ''))!)
    await waitFor(() => expect(others).toHaveBeenCalledTimes(1))
    expect(one, 'one call, not a loop of single sign-outs').not.toHaveBeenCalled()
  })

  it('with only this device signed in there is no "sign out others" to offer', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([rows()[0]])
    mount()
    await waitFor(() => expect(screen.getByText('Chrome on Mac')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /Sign out all other devices/i })).toBeNull()
  })

  it('signing THIS device out uses the sign-out route, never a revoke of its own row', async () => {
    const logout = vi.spyOn(api, 'authLogout').mockResolvedValue({ ok: true, revoked: true })
    const revoke = vi.spyOn(api, 'deviceRevoke')
    const reload = vi.fn()
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, reload } as Location)
    vi.spyOn(api, 'devices').mockResolvedValue(rows())
    mount()
    await waitFor(() => expect(screen.getByText('Chrome on Mac')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /^Sign out of this device$/i }))
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(Array.from(dialog.querySelectorAll('button')).find((b) => /^sign out$/i.test(b.textContent ?? ''))!)
    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(reload).toHaveBeenCalled())
    expect(revoke).not.toHaveBeenCalled()
  })
})

// ── Integration tokens ─────────────────────────────────────────────────────────────────────────
//
// The tokens an external agent reaches an inbound surface with had no lifetime and no list: an
// MCP token pasted into an editor's config long ago still worked, and the owner could not see it,
// let alone revoke it. Each is now listed here, with when it stops working and a revoke.

const NOW = Math.floor(Date.now() / 1000)

function integration(over: Partial<IntegrationRec> = {}): IntegrationRec {
  return {
    id: 'surface-mcp',
    kind: 'surface',
    name: 'MCP token',
    surfaces: ['mcp'],
    surface_names: ['MCP'],
    issued_at: NOW - 3 * 86400,
    expires_at: NOW + 87 * 86400,
    last_seen: 0,
    found: false,
    state: 'live',
    renew: 'personalclaw inbound token create mcp --rotate',
    ...over,
  }
}

const IDE_CLIENT = integration({
  id: 'client-abc', kind: 'client', name: 'ide', surfaces: ['mcp', 'a2a'], surface_names: ['MCP', 'A2A'],
  renew: '', last_seen: NOW - 7200,
})

describe('integration tokens are listed, with when each stops working, and a revoke', () => {
  it('lists each surface token and each client, and what each reaches', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    vi.spyOn(api, 'deviceIntegrations').mockResolvedValue({ integrations: [integration(), IDE_CLIENT], problem: '' })
    mount()
    await waitFor(() => expect(screen.getByText('Integrations (2)')).toBeTruthy())
    expect(screen.getByText('MCP token')).toBeTruthy()
    const surfaceMeta = screen.getByText('Reaches MCP').parentElement?.textContent ?? ''
    expect(surfaceMeta).toMatch(/^Surface token · Reaches MCP · Last used never$/)
    const clientMeta = screen.getByText('Reaches MCP, A2A').parentElement?.textContent ?? ''
    expect(clientMeta).toMatch(/^Client · Reaches MCP, A2A · Last used 2h ago$/)
    expect(screen.getAllByText(/^Issued 3d ago · stops working /)).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Revoke MCP token' })).toBeTruthy()
    expect(screen.getByRole('button', { name: "Revoke the “ide” client's token" })).toBeTruthy()
  })

  it('says a token stopped working, and how to make a new one', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    vi.spyOn(api, 'deviceIntegrations').mockResolvedValue({
      integrations: [integration({ state: 'expired', issued_at: NOW - 91 * 86400, expires_at: NOW - 86400, found: true })],
      problem: '',
    })
    mount()
    await waitFor(() => expect(screen.getByText('Expired')).toBeTruthy())
    const line = screen.getByText(/^First seen /).textContent ?? ''
    expect(line).toMatch(/^First seen .* · stopped working .* · a new one: personalclaw inbound token create mcp --rotate$/)
  })

  it('offers no revoke for a token that is already revoked', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    vi.spyOn(api, 'deviceIntegrations').mockResolvedValue({ integrations: [integration({ state: 'revoked' })], problem: '' })
    mount()
    await waitFor(() => expect(screen.getByText('Revoked')).toBeTruthy())
    expect(screen.getByText(/^Issued 3d ago · revoked/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Revoke MCP token' })).toBeNull()
  })

  it('asks before revoking, naming what keeps working, and a dismissal revokes nothing', async () => {
    const revoke = vi.spyOn(api, 'deviceIntegrationRevoke').mockResolvedValue({ ok: true, revoked: 'surface-mcp' })
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    vi.spyOn(api, 'deviceIntegrations').mockResolvedValue({ integrations: [integration()], problem: '' })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke MCP token' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent).toMatch(/Revoke MCP token\?/)
    expect(dialog.textContent).toMatch(/Registered clients keep their own tokens\./)
    expect(dialog.textContent).toMatch(/personalclaw inbound token create mcp --rotate/)
    const cancel = Array.from(dialog.querySelectorAll('button')).find((b) => /cancel/i.test(b.textContent ?? ''))
    fireEvent.click(cancel!)
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(revoke).not.toHaveBeenCalled()
  })

  it('a confirmed revoke sends that token and re-reads the list', async () => {
    const revoke = vi.spyOn(api, 'deviceIntegrationRevoke').mockResolvedValue({ ok: true, revoked: 'client-abc' })
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    const list = vi.spyOn(api, 'deviceIntegrations')
    list.mockResolvedValueOnce({ integrations: [IDE_CLIENT], problem: '' }).mockResolvedValue({ integrations: [], problem: '' })
    const toasts = captureToasts()
    mount()
    fireEvent.click(await screen.findByRole('button', { name: "Revoke the “ide” client's token" }))
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent).toMatch(/Register it again to give it a new token\./)
    const go = Array.from(dialog.querySelectorAll('button')).find((b) => /^revoke$/i.test(b.textContent ?? ''))
    fireEvent.click(go!)
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('client-abc'))
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1))
    expect(toasts).toContain("The “ide” client's token is revoked.")
  })

  it('a failed revoke is reported with the reason, and the token stays listed', async () => {
    vi.spyOn(api, 'deviceIntegrationRevoke').mockRejectedValue(new Error('the token record is read-only'))
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    vi.spyOn(api, 'deviceIntegrations').mockResolvedValue({ integrations: [integration()], problem: '' })
    const toasts = captureToasts()
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke MCP token' }))
    const dialog = await screen.findByRole('alertdialog')
    const go = Array.from(dialog.querySelectorAll('button')).find((b) => /^revoke$/i.test(b.textContent ?? ''))
    fireEvent.click(go!)
    await waitFor(() => expect(toasts.some((t) => t === "Couldn't revoke MCP token: the token record is read-only")).toBe(true))
    expect(screen.getByText('MCP token')).toBeTruthy()
  })

  it('names a failed read of the list, and still shows the devices', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    vi.spyOn(api, 'deviceIntegrations').mockRejectedValue(new Error('gateway unreachable'))
    mount()
    await waitFor(() => expect(screen.getByText("Couldn't load your integration tokens")).toBeTruthy())
    expect(screen.getByText('Kitchen tablet')).toBeTruthy()
  })

  it('says when the record of token lifetimes cannot be read', async () => {
    const problem = "The record of when each integration token stops working can't be read, so every surface token is refused until it can: inbound_tokens.json is not valid JSON"
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    vi.spyOn(api, 'deviceIntegrations').mockResolvedValue({ integrations: [], problem })
    mount()
    await waitFor(() => expect(screen.getByText(problem)).toBeTruthy())
    expect(screen.getByText('Integrations')).toBeTruthy()
  })

  it('shows no Integrations section when there are none', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    mount()
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    expect(screen.queryByText(/^Integrations/)).toBeNull()
  })
})

describe('an integration row reads as a heading', () => {
  it('a surface token is named as a heading, and a client as its owner named it', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    vi.spyOn(api, 'deviceIntegrations').mockResolvedValue({
      integrations: [
        integration({ id: 'surface-capture', name: 'capture proxy token', surfaces: ['capture'], surface_names: ['capture proxy'] }),
        integration({ id: 'client-x', kind: 'client', name: 'my ide', renew: '' }),
      ],
      problem: '',
    })
    mount()
    await waitFor(() => expect(screen.getByText('Capture proxy token')).toBeTruthy())
    expect(screen.getByText('my ide')).toBeTruthy()
    // The sentence keeps it lowercase, where it sits mid-sentence.
    expect(screen.getByRole('button', { name: 'Revoke capture proxy token' })).toBeTruthy()
  })
})
