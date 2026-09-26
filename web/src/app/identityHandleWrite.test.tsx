// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'

// ── TSE-1: who may write the attribution handle, and the ONE slug rule ────────────────────
//
// `PUT /api/dashboard/config` is key-presence-gated (`if "username" in body`), which is what
// makes `setName`'s optional second argument a real contract rather than a convenience: a body
// that carries `username: ''` CLEARS the stored handle, and a body that omits the key leaves it
// untouched. Two callers rely on opposite halves of that —
//
//   • first-run onboarding ASKED, so it always sends one, including '' for a cleared field;
//   • Settings → Account's "Your name" Save did not ask, so a rename there must not be able to
//     clear (or invent) the handle — the Username field beside it is the only writer.
//
// A `setName` that always sent a handle would make renaming your display name silently
// re-derive your handle from it, overwriting a deliberate one and re-attributing nothing that
// was already written. That is close-condition clause 5, and it is invisible to a type checker:
// both shapes compile.
//
// The suggester's tests are here rather than beside the flow because this module is now its ONE
// home — `AccountPanel` imported a 13-line copy of it until this atom merged them.

const dashboardConfig = vi.fn()
const saveDashboardConfig = vi.fn()

vi.mock('../lib/api', () => ({
  api: {
    dashboardConfig: () => dashboardConfig(),
    saveDashboardConfig: (...a: unknown[]) => saveDashboardConfig(...a),
  },
}))

import { IdentityProvider, useIdentity, suggestHandle, USERNAME_MAX_LEN, DEFAULT_USER_NAME } from './identity'

/** Buttons for the three write shapes a caller can produce.
 *
 *  There is no `clearName` button, because there is no `clearName`. Re-entering first-run setup
 *  used to clear `user_name` to force the route guard's hand; it is now a request the guard honours
 *  (`app/onboarding/rerun.ts`), so identity is never wiped to reach a setup screen and the context
 *  exposes only the write that has a caller. The property the deleted case asserted — that a write
 *  must not disturb a handle it was not asked about — is exactly what the `rename-only` case above
 *  pins, on the path that still exists. */
function Harness() {
  const { setName, keepOrDefaultName, retry, status, name, username } = useIdentity()
  const [kept, setKept] = useState('')
  return (
    <div>
      <button type="button" onClick={() => setName('Ada King')}>rename-only</button>
      <button type="button" onClick={() => setName('Ada King', 'ada-king')}>rename-with-handle</button>
      <button type="button" onClick={() => setName('Ada King', '')}>rename-clearing-handle</button>
      <button type="button" onClick={() => keepOrDefaultName().then(
        () => setKept('resolved'), (e: Error) => setKept(`rejected: ${e.message}`))}>keep-or-default</button>
      <button type="button" onClick={retry}>retry</button>
      <output>{`${name}|${username}`}</output>
      <span data-testid="read">{status}</span>
      <span data-testid="kept">{kept}</span>
    </div>
  )
}
const readStatus = () => screen.getByTestId('read').textContent

async function renderHarness(stored: { user_name?: string; username?: string } = {}) {
  dashboardConfig.mockResolvedValue({ user_name: '', username: '', ...stored })
  render(<IdentityProvider><Harness /></IdentityProvider>)
  await waitFor(() => expect(dashboardConfig).toHaveBeenCalled())
}

const lastBody = () => saveDashboardConfig.mock.calls.at(-1)?.[0] as Record<string, unknown>

beforeEach(() => {
  vi.clearAllMocks()
  saveDashboardConfig.mockResolvedValue({ ok: true })
})

afterEach(cleanup)

describe('setName sends the handle only when its caller passes one', () => {
  it('omits the `username` KEY entirely for a caller that did not ask', async () => {
    await renderHarness({ username: 'lovelace' })
    fireEvent.click(screen.getByRole('button', { name: 'rename-only' }))
    await waitFor(() => expect(saveDashboardConfig).toHaveBeenCalled())
    // Key ABSENCE, not an empty string: the server's PUT writes only the keys present, so
    // `username: ''` here would wipe a handle the user never touched.
    expect('username' in lastBody()).toBe(false)
    expect(lastBody().user_name).toBe('Ada King')
    // …and the handle the provider reports is unchanged, so no surface reading it flickers.
    expect(screen.getByRole('status').textContent).toBe('Ada King|lovelace')
  })

  it('sends it when a caller does pass one', async () => {
    await renderHarness()
    fireEvent.click(screen.getByRole('button', { name: 'rename-with-handle' }))
    await waitFor(() => expect(saveDashboardConfig).toHaveBeenCalledWith({ user_name: 'Ada King', username: 'ada-king' }))
  })

  it('sends an EMPTY handle as a real value, because clearing one is a legitimate act', async () => {
    await renderHarness({ username: 'lovelace' })
    fireEvent.click(screen.getByRole('button', { name: 'rename-clearing-handle' }))
    await waitFor(() => expect(saveDashboardConfig).toHaveBeenCalled())
    // The distinction the whole contract rests on: '' is PRESENT (clear it), undefined is
    // ABSENT (leave it alone). Collapsing the two in either direction breaks one caller.
    expect(lastBody()).toEqual({ user_name: 'Ada King', username: '' })
  })

  it('reports the stored handle so a surface can offer it back', async () => {
    await renderHarness({ user_name: 'Ada Lovelace', username: 'lovelace' })
    expect(screen.getByRole('status').textContent).toBe('Ada Lovelace|lovelace')
  })
})

// ── a failed read is its own state, never an empty identity ──────────────────────────────
//
// This block REPLACES an assertion that pinned the defect. It read: an unreadable config "must read
// as 'no handle' rather than wedging the app", and it asserted the provider then reported `|` — no
// name, no handle, loaded. The handle half was harmless; the NAME half was not, because `onboarded`
// is derived from the name: an unreadable config therefore read as a fresh home, the shell opened
// first-run setup for an onboarded user, and that screen's "Skip setup for now" PUT
// `{"user_name":"Operator","username":""}` over the real name and handle. The bargain it cited
// (`identity.current_username()` treating an unreadable config as unattributed) is sound for a
// label decorating a write; it is unsound for the value that decides whether setup runs.
//
// "Don't wedge the app" is kept the honest way — a retry — and asserted here and, through the real
// shell, in `identityReadFailure.test.tsx`.
describe('a failed read is its own state, never an empty identity', () => {
  it('reports the failure as a failure, and writes nothing', async () => {
    dashboardConfig.mockRejectedValue(new Error('gateway down'))
    render(<IdentityProvider><Harness /></IdentityProvider>)
    await waitFor(() => expect(readStatus()).toBe('failed'))
    expect(saveDashboardConfig).not.toHaveBeenCalled()
  })

  it('reads again on retry, and then reports what is stored', async () => {
    dashboardConfig
      .mockRejectedValueOnce(new Error('gateway down'))
      .mockResolvedValue({ user_name: 'Ada Lovelace', username: 'lovelace' })
    render(<IdentityProvider><Harness /></IdentityProvider>)
    await waitFor(() => expect(readStatus()).toBe('failed'))
    fireEvent.click(screen.getByRole('button', { name: 'retry' }))
    await waitFor(() => expect(readStatus()).toBe('ready'))
    expect(screen.getByRole('status').textContent).toBe('Ada Lovelace|lovelace')
    expect(dashboardConfig).toHaveBeenCalledTimes(2)
    expect(saveDashboardConfig).not.toHaveBeenCalled()
  })
})

// ── keepOrDefaultName: a skipped first run's fallback lands only on a home with no name ──
//
// The fallback is the one identity write the user did not author, so it is decided from a read of
// what is stored at the moment of the skip — not from the read the tab took when it opened, which
// another tab or device may have overtaken.
describe('keepOrDefaultName reads before it writes', () => {
  it('keeps a name stored since the first read, and writes nothing', async () => {
    await renderHarness()
    dashboardConfig.mockResolvedValue({ user_name: 'Ada Lovelace', username: 'lovelace' })
    fireEvent.click(screen.getByRole('button', { name: 'keep-or-default' }))
    await waitFor(() => expect(screen.getByTestId('kept').textContent).toBe('resolved'))
    // Adopted, so `onboarded` flips and the route guard lets the user out — without a write.
    expect(screen.getByRole('status').textContent).toBe('Ada Lovelace|lovelace')
    expect(saveDashboardConfig).not.toHaveBeenCalled()
  })

  it('rejects and writes nothing when that read fails', async () => {
    await renderHarness()
    dashboardConfig.mockRejectedValue(new Error('gateway down'))
    fireEvent.click(screen.getByRole('button', { name: 'keep-or-default' }))
    await waitFor(() => expect(screen.getByTestId('kept').textContent).toBe('rejected: gateway down'))
    expect(saveDashboardConfig).not.toHaveBeenCalled()
  })

  it('writes the default name — and no handle key — onto a home with none', async () => {
    // A handle with no name is what the old "Restart onboarding" left behind. Key ABSENCE, as in
    // the first block: the skip asked for no handle, and `username: ''` would erase this one.
    await renderHarness({ username: 'lovelace' })
    fireEvent.click(screen.getByRole('button', { name: 'keep-or-default' }))
    await waitFor(() => expect(screen.getByTestId('kept').textContent).toBe('resolved'))
    expect(saveDashboardConfig.mock.calls).toEqual([[{ user_name: DEFAULT_USER_NAME }]])
    expect(screen.getByRole('status').textContent).toBe(`${DEFAULT_USER_NAME}|lovelace`)
  })

  it('a refused fallback write rejects, and claims no name the server never stored', async () => {
    // The fallback write itself can fail (a read-only config, a full disk); the rejection reaches
    // the caller, which keeps setup open, and `onboarded` stays false.
    await renderHarness()
    saveDashboardConfig.mockRejectedValue(new Error('read-only config'))
    fireEvent.click(screen.getByRole('button', { name: 'keep-or-default' }))
    await waitFor(() => expect(screen.getByTestId('kept').textContent).toBe('rejected: read-only config'))
    expect(screen.getByRole('status').textContent).toBe('|')
  })
})

// ── the ONE slug rule ────────────────────────────────────────────────────────────────────
//
// The rail that keeps it one rule lives in `tests/test_identity.py`: it counts the files under
// `web/src` that decompose Unicode the way this one does and requires the answer to be exactly
// one, and it checks this module's cap still equals the Python `USERNAME_MAX_LEN` it mirrors.
// (Which is why the form's name is spelled around throughout this file — a mention here would
// make the census read 2 and the rail would fail on its own test.)
// Asserted below is the behaviour that rule produces.
describe('suggestHandle mirrors the server slug rule', () => {
  it.each([
    ['Keyur Golani', 'keyur-golani'],
    ['Ada', 'ada'],
    ['  Trailing  Spaces  ', 'trailing-spaces'],
    ["Ann-Marie O'Neil", 'ann-marie-o-neil'],
    ['UPPER_CASE-ok', 'upper_case-ok'],
    ['multiple   ---   separators', 'multiple-separators'],
    ['jo@example.com', 'jo-example-com'],
    // Folding beats deleting, so José is jose and not jos — the same choice the Python
    // slugifier documents, and the reason both sides decompose before stripping marks.
    // (Spelled around the normalization form's NAME on purpose: `test_identity.py` counts
    // the files under web/src that contain it, and the count is how "one rule" is enforced.)
    ['José', 'jose'],
    ['Ünicode Café', 'unicode-cafe'],
    // Unusable input yields '' rather than a fabricated `user-1`: empty is a valid state.
    ['!!!', ''],
    ['   ', ''],
    ['', ''],
  ])('%s → %s', (raw, expected) => {
    expect(suggestHandle(raw)).toBe(expected)
  })

  it('caps at the shared length and never ends on a separator', () => {
    // The case a bare `.slice()` gets wrong: a cut landing on the separator leaves a trailing
    // `-`, which the server then trims — so the suggestion SHOWN and the handle STORED would
    // differ by a character, on the one field whose value lands in records forever.
    const cut = suggestHandle('a'.repeat(USERNAME_MAX_LEN) + ' tail')
    expect(cut).toHaveLength(USERNAME_MAX_LEN)
    expect(cut.endsWith('-')).toBe(false)
    expect(suggestHandle('a'.repeat(USERNAME_MAX_LEN - 1) + ' tail')).toBe('a'.repeat(USERNAME_MAX_LEN - 1))
  })

  it('is idempotent, so re-suggesting from a slug changes nothing', () => {
    const once = suggestHandle('Keyur Golani')
    expect(suggestHandle(once)).toBe(once)
  })
})
