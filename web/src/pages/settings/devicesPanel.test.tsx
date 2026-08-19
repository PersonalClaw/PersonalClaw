import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DevicesPanel } from './DevicesPanel'
import { DialogHost } from '../../ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../ui/dialog/dialogStore'
import { invalidateCache } from '../../lib/useCachedData'
import { api, type DeviceRec, type DevicePairStart } from '../../lib/api'

// ── Settings → Devices (COMPANION-APPS C2 / CA-2) ─────────────────────────────────────────────
//
// The backend shipped in CA-1 with FOUR routes and zero consumers, so every clause below was
// unreachable rather than wrong. Four ways this panel could look finished while lying:
//
//  • `last_seen` OF 0 MEANS "NEVER MADE AN AUTHORIZED REQUEST", and the C1 author held the field
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
    issuer: 'pair',
    expires_at: 1_790_000_000,
    ...over,
  }
}

const START: DevicePairStart = {
  code: 'ABCD-EFGH',
  pairing_url: 'http://192.168.1.5:10000/#/pair?code=ABCD-EFGH',
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
  invalidateCache('settings:devices')
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
    expect(screen.getByRole('button', { name: /Revoke Unnamed device/i })).toBeTruthy()
  })
})

describe('revoking is confirmed, named, and never silent', () => {
  it('the confirmation NAMES the device it is about to lock out', async () => {
    const revoke = vi.spyOn(api, 'deviceRevoke').mockResolvedValue({ ok: true, revoked: 1 })
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    mount()
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Revoke Kitchen tablet/i }))

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
    fireEvent.click(screen.getByRole('button', { name: /Revoke Kitchen tablet/i }))
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
    fireEvent.click(screen.getByRole('button', { name: /Revoke Kitchen tablet/i }))
    const dialog = await screen.findByRole('alertdialog')
    const go = Array.from(dialog.querySelectorAll('button')).find((b) => /revoke access/i.test(b.textContent ?? ''))
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
    fireEvent.click(screen.getByRole('button', { name: /Revoke Kitchen tablet/i }))
    const dialog = await screen.findByRole('alertdialog')
    const go = Array.from(dialog.querySelectorAll('button')).find((b) => /revoke access/i.test(b.textContent ?? ''))
    fireEvent.click(go!)

    await waitFor(() => expect(toasts.some((t) => /Couldn't revoke Kitchen tablet/i.test(t))).toBe(true))
    // And it carries the server's reason, not a generic sentence.
    expect(toasts.join(' ')).toMatch(/read-only/)
    expect(screen.getByText('Kitchen tablet'), 'still there, because it still has access').toBeTruthy()
  })
})

describe('an empty registry and a failed read are different answers', () => {
  it('says nothing is paired, honestly, and offers the way to change that', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    // Two entrances to ONE pairing flow, with DISTINCT names: identical accessible names on one
    // screen make the action ambiguous to anyone navigating by name.
    expect(screen.getByRole('button', { name: /^Pair a device$/i }), 'the section control').toBeTruthy()
    expect(screen.getByRole('button', { name: /Pair your first device/i }), 'the empty-state on-ramp').toBeTruthy()
  })

  it('the empty state’s on-ramp actually opens pairing', async () => {
    // An empty state whose CTA does nothing is the defect PEP-2's "on-ramp" verdict is about.
    vi.spyOn(api, 'devices').mockResolvedValue([])
    const start = vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Pair your first device/i }))
    await waitFor(() => expect(start).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
  })

  it('a FAILED read renders the error, never the empty state', async () => {
    vi.spyOn(api, 'devices').mockRejectedValue(new Error('devices unreadable'))
    mount()
    await waitFor(() => expect(screen.getByText(/devices unreadable/)).toBeTruthy())
    // The whole point: a rejection must not borrow "nothing is paired", which reads as a fact.
    expect(screen.queryByText(/No devices paired/i)).toBeNull()
  })
})

describe('pairing surfaces the code and the link', () => {
  it('shows the code and the actionable URL, with the expiry counting down', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
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

  it('marks where the scannable code belongs instead of silently omitting it', async () => {
    // CA-2 ships the URL + code rather than a QR image (no encoder in either ecosystem, and the
    // repo's own TOTP enrollment ships no QR either). The honest form of that is a LABELLED gap:
    // the owner can see the scannable form is absent and still finish pairing.
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
    const placeholder = screen.getByRole('img', { name: /QR code not available/i })
    expect(placeholder.getAttribute('aria-label')).toMatch(/link and code/i)
  })

  it('an EXPIRED code says so rather than counting into nonsense', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue({
      ...START, expires_at: Math.floor(Date.now() / 1000) - 5, expires_in: 0,
    })
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(screen.getByText(/This code has expired/i)).toBeTruthy())
    expect(screen.queryByText(/Expires in/i), 'not both at once').toBeNull()
    expect(screen.getByRole('button', { name: /Generate a new pairing code/i })).toBeTruthy()
  })

  it('a FAILED pair/start is reported, not swallowed into a blank panel', async () => {
    const toasts = captureToasts()
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockRejectedValue(new Error('too many outstanding codes'))
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(toasts.some((t) => /Couldn't start pairing/i.test(t))).toBe(true))
    expect(toasts.join(' ')).toMatch(/too many outstanding codes/)
  })
})
