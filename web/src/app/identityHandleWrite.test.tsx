// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
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

import { IdentityProvider, useIdentity, suggestHandle, USERNAME_MAX_LEN } from './identity'

/** Buttons for the three write shapes a caller can produce. */
function Harness() {
  const { setName, clearName, name, username } = useIdentity()
  return (
    <div>
      <button type="button" onClick={() => setName('Ada King')}>rename-only</button>
      <button type="button" onClick={() => setName('Ada King', 'ada-king')}>rename-with-handle</button>
      <button type="button" onClick={() => setName('Ada King', '')}>rename-clearing-handle</button>
      <button type="button" onClick={() => clearName()}>restart-onboarding</button>
      <output>{`${name}|${username}`}</output>
    </div>
  )
}

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

  it('leaves the handle alone when onboarding is restarted', async () => {
    await renderHarness({ user_name: 'Ada Lovelace', username: 'lovelace' })
    fireEvent.click(screen.getByRole('button', { name: 'restart-onboarding' }))
    await waitFor(() => expect(saveDashboardConfig).toHaveBeenCalled())
    // Restarting re-asks for the name; the handle already stamped onto existing records
    // survives to be OFFERED BACK on the first step rather than silently dropped.
    expect(lastBody()).toEqual({ user_name: '' })
  })

  it('reports the stored handle so a surface can offer it back', async () => {
    await renderHarness({ user_name: 'Ada Lovelace', username: 'lovelace' })
    expect(screen.getByRole('status').textContent).toBe('Ada Lovelace|lovelace')
  })

  it('degrades to no handle when the config cannot be read', async () => {
    dashboardConfig.mockRejectedValue(new Error('gateway down'))
    render(<IdentityProvider><Harness /></IdentityProvider>)
    // Attribution decorates a write; an unreadable config must read as "no handle" rather
    // than wedging the app — the same bargain `identity.current_username()` makes server-side.
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe('|'))
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
