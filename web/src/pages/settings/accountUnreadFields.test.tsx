// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'

// ── Settings → Account edits only what it has read ────────────────────────────────────────────
//
// The Username and Assistant-name fields each seed from their own read, and both reads swallowed a
// failure (`.catch(() => {})`) into `''`. So an unread handle showed as NO handle — beside a hint
// saying an empty one keeps records unattributed — and an unread assistant name showed as the
// default ("Empty uses the default, PersonalClaw"). A user who took the screen at its word and typed
// one in overwrote a stored value they could not see.
//
// Same family as the onboarding defect (`app/identityReadFailure.test.tsx`): a failed read became
// an empty state, and the empty state licensed a write. `accountRenameKeepsTheHandle.test.tsx` and
// `accountAssistantNameShowsWhatWasStored.test.tsx` own the read-succeeded behaviour.

const dashboardConfig = vi.fn()
const saveDashboardConfig = vi.fn()
const personalclawConfig = vi.fn()
const patchConfig = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    dashboardConfig: () => dashboardConfig(),
    saveDashboardConfig: (...a: unknown[]) => saveDashboardConfig(...a),
    personalclawConfig: () => personalclawConfig(),
    patchConfig: (...a: unknown[]) => patchConfig(...a),
    // Kept PENDING: the sign-in section renders nothing until its session read lands.
    authSession: () => new Promise(() => {}),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))
// The shell renders this panel only once identity has been READ (`app/App.tsx`), so the context is
// a settled one here; the panel's OWN two reads are what is under test.
vi.mock('../../app/identity', async (orig) => ({
  ...(await orig<typeof import('../../app/identity')>()),
  useIdentity: () => ({ status: 'ready', name: 'Ada Lovelace', username: 'lovelace', onboarded: true, setName: vi.fn() }),
}))

import { AccountPanel } from './AccountPanel'

const handleField = () => screen.getByLabelText('Username') as HTMLInputElement
const botField = () => screen.getByLabelText('Assistant name') as HTMLInputElement
const settle = () => act(() => new Promise((r) => setTimeout(r, 30)))

beforeEach(() => {
  vi.clearAllMocks()
  dashboardConfig.mockResolvedValue({ user_name: 'Ada Lovelace', username: 'lovelace' })
  personalclawConfig.mockResolvedValue({ agent: { bot_name: 'Astra' } })
  saveDashboardConfig.mockResolvedValue({ ok: true })
  patchConfig.mockResolvedValue({ agent: { bot_name: 'Astra' } })
})
afterEach(cleanup)

describe('Username', () => {
  it('a failed read disables the field and its Save, says so, and a typed handle is never written', async () => {
    dashboardConfig.mockRejectedValue(new Error('config unreadable'))
    render(<AccountPanel />)
    expect(await screen.findByText(/Couldn't read your saved username: config unreadable/)).toBeInTheDocument()
    expect(handleField()).toBeDisabled()
    expect(handleField().title).toMatch(/Couldn't read what is saved/)
    // What the old screen invited: take the empty field at its word and type a handle in.
    fireEvent.change(handleField(), { target: { value: 'ada' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save: Username' }))
    await settle()
    expect(saveDashboardConfig, 'a handle was written over one the field never showed').not.toHaveBeenCalled()
  })

  it('a retry that reads it enables the field with what is stored', async () => {
    dashboardConfig.mockRejectedValueOnce(new Error('config unreadable'))
    render(<AccountPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /Retry/ }))
    await waitFor(() => expect(handleField().value).toBe('lovelace'))
    expect(handleField()).not.toBeDisabled()
  })
})

describe('Assistant name', () => {
  it('a failed read disables the field and its Save, says so, and a typed name is never written', async () => {
    personalclawConfig.mockRejectedValue(new Error('config unreadable'))
    render(<AccountPanel />)
    expect(await screen.findByText(/Couldn't read the saved assistant name: config unreadable/)).toBeInTheDocument()
    expect(botField()).toBeDisabled()
    fireEvent.change(botField(), { target: { value: 'Jarvis' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save: Assistant name' }))
    await settle()
    expect(patchConfig, 'a name was written over one the field never showed').not.toHaveBeenCalled()
  })

  it('a successful read leaves the field editable, and a change is saved', async () => {
    // The control.
    render(<AccountPanel />)
    await waitFor(() => expect(botField().value).toBe('Astra'))
    fireEvent.change(botField(), { target: { value: 'Jarvis' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save: Assistant name' }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('agent.bot_name', 'Jarvis'))
  })
})
