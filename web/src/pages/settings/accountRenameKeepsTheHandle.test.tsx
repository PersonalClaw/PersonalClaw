// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'

// ── TSE-1 clause 5: renaming yourself must not touch your handle ───────────────────────────
//
// Two fields on this panel write to the same config object through the same PUT, and the PUT is
// key-presence-gated (`if "username" in body`). So "Your name" → Save and "Username" → Save are
// distinguished ONLY by which keys their bodies carry, and the failure mode is silent in both
// directions:
//
//   • a rename that also sent `username` would overwrite a deliberate handle with one derived
//     from the new display name — and since a rename affects future writes only, everything
//     already written keeps the OLD handle. The user ends up with two handles across their own
//     records, neither of which they chose.
//   • a rename that sent `username: ''` would clear it outright.
//
// This is the clause that made `setName`'s second argument optional rather than derived. It is
// invisible to the type checker (both shapes compile) and invisible to a source scan (the call
// looks correct either way), so it is asserted here against the REAL IdentityProvider — the
// panel, the provider and the request body, end to end.
//
// Falsified (both mutations measured, both reds exactly as predicted):
//   • `setName(draft.trim() || DEFAULT_USER_NAME, suggestHandle(draft))` in this panel's `save`
//     → 2 failed / 2 passed here: `expected { user_name: 'Ada King', …(1) } to deeply equal
//     { user_name: 'Ada King' }` — a handle derived over the stored `lovelace`.
//   • `setName` in `app/identity.tsx` sending the key unconditionally → the same two red, plus
//     `identityHandleWrite.test.tsx`'s "omits the `username` KEY entirely" (3 failed / 21 passed).
//     Two surfaces, because the defect can live in either the caller or the provider.

const dashboardConfig = vi.fn()
const saveDashboardConfig = vi.fn()
const patchConfig = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    dashboardConfig: () => dashboardConfig(),
    saveDashboardConfig: (...a: unknown[]) => saveDashboardConfig(...a),
    patchConfig: (...a: unknown[]) => patchConfig(...a),
    // Kept PENDING: the sign-in section renders nothing until its session read lands, which is
    // the state this test wants — the identity fields are what is under test.
    personalclawConfig: () => new Promise(() => {}),
    authSession: () => new Promise(() => {}),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

import { AccountPanel } from './AccountPanel'
import { IdentityProvider } from '../../app/identity'

/** Every fixture here is named `Ada Lovelace`, so the suggestion the panel shows is always
 *  `ada-lovelace` — which is what `renderPanel` waits on below. */
const STORED = { user_name: 'Ada Lovelace', username: 'lovelace' }

async function renderPanel(stored: Record<string, string> = STORED) {
  dashboardConfig.mockResolvedValue(stored)
  render(<IdentityProvider><AccountPanel /></IdentityProvider>)
  // Two reads land on mount — the provider's and the panel's own — and the Username field's
  // PLACEHOLDER is derived from the provider's name while its VALUE comes from the panel's read,
  // so waiting for the placeholder waits for both. (Not the name field's value: this panel seeds
  // that draft once with `useState(name)`, which in the app is already loaded behind the route
  // guard but here would never catch up to the fetch.)
  await waitFor(() => expect(handleField().placeholder).toBe('ada-lovelace'))
}

const nameField = () => screen.getByLabelText('Your name') as HTMLInputElement
const handleField = () => screen.getByLabelText('Username') as HTMLInputElement
const lastBody = () => saveDashboardConfig.mock.calls.at(-1)?.[0] as Record<string, unknown>

beforeEach(() => {
  vi.clearAllMocks()
  saveDashboardConfig.mockResolvedValue({ ok: true })
})

afterEach(cleanup)

describe('the two identity fields write independently', () => {
  it('a rename sends no `username` key at all', async () => {
    await renderPanel()
    fireEvent.change(nameField(), { target: { value: 'Ada King' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save: Your name' }))
    await waitFor(() => expect(saveDashboardConfig).toHaveBeenCalled())
    expect(lastBody()).toEqual({ user_name: 'Ada King' })
  })

  it('a rename on an install that deliberately has NO handle does not acquire one', async () => {
    // The stricter half of the clause: '' is a chosen state ("keep records unattributed"), and a
    // derived handle here would start attributing writes the user opted out of attributing.
    await renderPanel({ user_name: 'Ada Lovelace', username: '' })
    fireEvent.change(nameField(), { target: { value: 'Ada King' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save: Your name' }))
    await waitFor(() => expect(saveDashboardConfig).toHaveBeenCalled())
    expect('username' in lastBody()).toBe(false)
  })

  it('the Username field is still the writer that CAN change it', async () => {
    // The control: the clause above must not be satisfied by making the handle unwritable here.
    await renderPanel()
    fireEvent.change(handleField(), { target: { value: 'ada-king' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save: Username' }))
    await waitFor(() => expect(saveDashboardConfig).toHaveBeenCalledWith({ username: 'ada-king' }))
  })

  it('shows the suggestion as a PLACEHOLDER, which writes nothing', async () => {
    // An install with no handle sees what it would get, derived from the name it already has —
    // the same rule onboarding shows, from the same module (`renderPanel` gates on that
    // placeholder). The point asserted here is that it is a placeholder and not a value: the
    // field stays empty, so nothing is dirty, no Save is armed, and nothing reaches the server
    // until the user types. A pre-filled VALUE here would turn merely opening Settings into a
    // handle the user never chose.
    await renderPanel({ user_name: 'Ada Lovelace', username: '' })
    expect(handleField().value).toBe('')
    expect(screen.getByRole('button', { name: 'Save: Username' }).getAttribute('aria-disabled')).toBe('true')
    expect(saveDashboardConfig).not.toHaveBeenCalled()
  })
})
