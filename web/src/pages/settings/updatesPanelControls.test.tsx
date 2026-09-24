import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { UpdateCheck } from '../../lib/api'

// ── RUM-10: Settings > Updates edits every `updates.*` field, not two of six ────────────────
//
// Measured before this: `UpdatesPanel.tsx` rendered exactly TWO boolean switches over a block of
// six config fields — "Auto-update" (off ⇄ staged) and "Developer update mode" (stable ⇄ nightly).
// So `beta` was unreachable from the UI, a version pin could only be set by hand-editing
// `config.json`, the check interval had no control, and the update check's kill switch — the one
// setting the README's privacy section tells users about — had no home in the app at all.
//
// The backend was already complete: all six fields sit in `_EDITABLE_CONFIG` and round-trip
// through `PATCH /api/config/personalclaw`. So what this locks is the FRONTEND half: every
// control reaches the right config path with the right value, a rejected write is reported
// rather than swallowed, and the rollback control pins before it applies.
//
// 🔑 THE ASSERTION IS THE CONFIG PATH, NOT "a control exists". A panel could render six pretty
// controls wired to nothing, or to the wrong field — which is exactly the failure a
// render-only test cannot see.

const patchConfig = vi.fn()
const applyUpdate = vi.fn()
const notify = vi.fn()
const confirmDialog = vi.fn()
vi.mock('../../app/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))
vi.mock('../../ui/dialog', () => ({ confirm: (...a: unknown[]) => confirmDialog(...a) }))

// Drive the data layer directly: `useQuery` returns the query result the panel destructures.
const useQuery = vi.fn()
vi.mock('../../lib/data', () => ({
  useQuery: (...a: unknown[]) => useQuery(...a),
  invalidateKeys: vi.fn(),
}))
vi.mock('../../lib/api', async (orig) => {
  const real = (await orig()) as { api: Record<string, unknown> }
  return {
    ...real,
    api: {
      ...real.api,
      patchConfig: (...a: unknown[]) => patchConfig(...a),
      applyUpdate: (...a: unknown[]) => applyUpdate(...a),
    },
  }
})

const { UpdatesPanel } = await import('./UpdatesPanel')

/** A git install on stable with checking on — the shape that renders every control. */
const BASE: UpdateCheck = {
  available: false,
  changes: '',
  checked: true,
  auto: 'off',
  kind: 'git',
  current: '0.1.3',
  channel: 'stable',
  pin: '',
  check_enabled: true,
  check_interval_hours: 12,
  last_version: '',
  release_notes: '',
}

function mountWith(over: Partial<UpdateCheck> = {}) {
  const info = { ...BASE, ...over }
  useQuery.mockReturnValue({ data: { info, changelog: '' }, loading: false, error: null, refresh: vi.fn() })
  return render(<UpdatesPanel />)
}

beforeEach(() => {
  patchConfig.mockReset(); patchConfig.mockResolvedValue({})
  applyUpdate.mockReset(); applyUpdate.mockResolvedValue({ ok: true })
  notify.mockReset()
  confirmDialog.mockReset(); confirmDialog.mockResolvedValue(true)
  useQuery.mockReset()
})

describe('every Updates control round-trips to its own config field (RUM-10)', () => {
  it('the Channel selector writes updates.channel', async () => {
    mountWith()
    fireEvent.click(screen.getByRole('button', { name: 'Update channel: Beta' }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('updates.channel', 'beta'))
  })

  it('offers the git-only Developer channel on a checkout and NOT on a wheel install', () => {
    // Vacuity floor for the clause above: if this panel offered every channel on every kind, the
    // Channel test would pass while the UI invited a wheel user into a lane with no published
    // artifact (the resolver silently rides `stable` there).
    const git = mountWith({ kind: 'git' })
    expect(git.container.textContent).toContain('Developer')
    git.unmount()
    const pip = mountWith({ kind: 'pip' })
    expect(pip.queryByRole('button', { name: 'Update channel: Developer' })).toBeNull()
    expect(pip.getByRole('button', { name: 'Update channel: Beta' })).not.toBeNull()
  })

  it('the Version pin input writes updates.pin on Save, and clears it', async () => {
    mountWith({ pin: '0.1.2' })
    fireEvent.change(screen.getByLabelText('Version pin'), { target: { value: '0.2.1' } })
    fireEvent.click(screen.getByRole('button', { name: /Save pin/ }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('updates.pin', '0.2.1'))

    // Clearing is how a user goes back to following the channel — a pin with no way out is a trap.
    fireEvent.click(screen.getByRole('button', { name: /Clear/ }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('updates.pin', ''))
  })

  it('offers no Clear when there is no pin to clear', () => {
    const { queryByRole } = mountWith({ pin: '' })
    expect(queryByRole('button', { name: /Clear/ })).toBeNull()
  })

  it('the pin box re-syncs after a Clear instead of still showing the removed version', async () => {
    // 🔴 FOUND BY DRIVING THE PANEL, not by reading it. The draft was seeded from the QUERY
    // snapshot, whose `pin` does not move when this panel writes one — so after clearing a pin
    // the box still showed the version it had just removed, while the row beside it correctly
    // read "Stable channel". Two surfaces on the same screen disagreeing about one field.
    mountWith({ pin: '0.1.2' })
    expect((screen.getByLabelText('Version pin') as HTMLInputElement).value).toBe('0.1.2')
    fireEvent.click(screen.getByRole('button', { name: /Clear/ }))
    await waitFor(() =>
      expect((screen.getByLabelText('Version pin') as HTMLInputElement).value).toBe(''),
    )
    expect(screen.queryByRole('button', { name: /Clear/ })).toBeNull()
  })

  it('but typing is never clobbered — the draft only follows a committed pin', async () => {
    // The other half of the same contract: re-syncing on every render would fight the user
    // mid-keystroke, which is the hazard `NumberField`'s own docstring records.
    mountWith({ pin: '' })
    const box = screen.getByLabelText('Version pin')
    fireEvent.change(box, { target: { value: '0.2.' } })
    await waitFor(() => expect((box as HTMLInputElement).value).toBe('0.2.'))
    expect(patchConfig).not.toHaveBeenCalled()  // and nothing is written per keystroke
  })

  it('the Automatic-check toggle writes updates.check_enabled', async () => {
    mountWith()
    fireEvent.click(screen.getByRole('switch', { name: 'Check for updates' }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('updates.check_enabled', false))
  })

  it('the interval writes updates.check_interval_hours, and is absent while the check is off', async () => {
    const on = mountWith({ check_enabled: true })
    const field = screen.getByLabelText('Check every')
    fireEvent.change(field, { target: { value: '24' } })
    fireEvent.blur(field)
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('updates.check_interval_hours', 24))
    on.unmount()

    // With checking off the interval is ignored entirely by the backend, so showing an inert
    // number would invite the reader to tune something with no effect.
    const off = mountWith({ check_enabled: false })
    expect(off.queryByLabelText('Check every')).toBeNull()
  })

  it('the Auto-update mode writes updates.auto (Off/Staged, not a bool)', async () => {
    mountWith()
    fireEvent.click(screen.getByRole('button', { name: 'Apply updates: Staged' }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('updates.auto', 'staged'))
  })

  it('a refused write is REPORTED, not swallowed', async () => {
    // The whole reason these writes are optimistic-with-a-report: the control is left showing a
    // value the server refused, so silence makes the UI lie until the next reload.
    patchConfig.mockRejectedValue(new Error('{"error":"field not editable"}'))
    mountWith()
    fireEvent.click(screen.getByRole('button', { name: 'Update channel: Beta' }))
    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect(String(notify.mock.calls[0][0])).toContain("Couldn't")
    expect(String(notify.mock.calls[0][0])).toContain('field not editable')
    expect(notify.mock.calls[0][1]).toBe('error')
  })
})

describe('the rollback control (RUM-9 surfaced)', () => {
  it('offers the recorded previous version, pins it, THEN applies', async () => {
    mountWith({ last_version: '0.1.2' })
    const btn = screen.getByRole('button', { name: /Roll back to v0\.1\.2/ })
    fireEvent.click(btn)

    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('updates.pin', '0.1.2'))
    await waitFor(() => expect(applyUpdate).toHaveBeenCalled())
    // The ORDER is the contract: applying before the pin lands would install the channel's
    // newest release — an upgrade for a user who asked to roll back.
    expect(patchConfig.mock.invocationCallOrder[0]).toBeLessThan(applyUpdate.mock.invocationCallOrder[0])
    // and the confirm names the version + the snapshot advice
    const body = String((confirmDialog.mock.calls[0][0] as { body: string }).body)
    expect(body).toContain('0.1.2')
    expect(body).toContain('personalclaw snapshot')
  })

  it('does not apply when the pin write fails', async () => {
    // An unpinned "rollback" is an upgrade wearing the wrong label.
    patchConfig.mockRejectedValue(new Error('nope'))
    mountWith({ last_version: '0.1.2' })
    fireEvent.click(screen.getByRole('button', { name: /Roll back to v0\.1\.2/ }))
    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect(applyUpdate).not.toHaveBeenCalled()
  })

  it('is hidden when nothing was recorded, or when it would offer the running version', () => {
    // Vacuity floor: until `record_running_version` sees a version change, `last_version` is ""
    // and a "Roll back to v" naming nothing is worse than no offer at all.
    expect(mountWith({ last_version: '' }).queryByRole('button', { name: /Roll back/ })).toBeNull()
    expect(mountWith({ last_version: '0.1.3', current: '0.1.3' }).queryByRole('button', { name: /Roll back/ })).toBeNull()
  })

  it('never applies without the confirm', async () => {
    confirmDialog.mockResolvedValue(false)
    mountWith({ last_version: '0.1.2' })
    fireEvent.click(screen.getByRole('button', { name: /Roll back to v0\.1\.2/ }))
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled())
    expect(patchConfig).not.toHaveBeenCalled()
    expect(applyUpdate).not.toHaveBeenCalled()
  })
})

describe('the panel renders release notes for the resolved channel', () => {
  it('renders the notes the backend resolved, headed by the resolved version', async () => {
    const { container } = mountWith({ channel: 'beta', latest: '0.3.0-rc.1', release_notes: '### Added\n- a beta thing' })
    await waitFor(() => expect(container.textContent).toContain('a beta thing'))
    expect(container.textContent).toContain('v0.3.0-rc.1')
    // The section is scoped to the resolved line, so a Beta reader is not shown Stable's notes.
    expect(container.textContent).toContain('newest Beta release')
  })

  it('names the PIN rather than a channel when one is set', async () => {
    const { container } = mountWith({ pin: '0.2.0', latest: '0.2.0', release_notes: 'pinned notes' })
    await waitFor(() => expect(container.textContent).toContain('pinned notes'))
    expect(container.textContent).toContain('pinned release 0.2.0')
  })

  it('says why there are none rather than rendering an empty card', () => {
    expect(mountWith({ release_notes: '' }).container.textContent).toContain('No release notes yet')
    expect(mountWith({ release_notes: '', check_enabled: false }).container.textContent).toContain('Update checks are off')
  })
})
