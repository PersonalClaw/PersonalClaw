import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { UpdateCheck } from '../../lib/api'

// ── Check now and the automatic-check switch each say what they do, and do it ───────────────────
//
// The switch read "Check for updates … Off means ZERO outbound calls from the updater", and the
// button beside the version, labelled "Check", only re-read the status. So with the switch off the
// button could not check at all, and the Update it offered still reached GitHub. The switch is now
// "Automatic update checks" (what PersonalClaw does on its own), and the button is Check now (the
// owner's own single check, which runs whatever the switch says). These pin both halves: the words,
// and that pressing Check now sends the check request and shows the answer it got.

const checkForUpdatesNow = vi.fn()
const patchConfig = vi.fn()
const notify = vi.fn()
const invalidateKeys = vi.fn()
vi.mock('../../app/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))
vi.mock('../../ui/dialog', () => ({ confirm: vi.fn(() => Promise.resolve(true)) }))
const useQuery = vi.fn()
vi.mock('../../lib/data', () => ({
  useQuery: (...a: unknown[]) => useQuery(...a),
  invalidateKeys: (...a: unknown[]) => invalidateKeys(...a),
}))
vi.mock('../../lib/api', async (orig) => {
  const real = (await orig()) as { api: Record<string, unknown> }
  return {
    ...real,
    api: {
      ...real.api,
      patchConfig: (...a: unknown[]) => patchConfig(...a),
      checkForUpdatesNow: (...a: unknown[]) => checkForUpdatesNow(...a),
    },
  }
})

const { UpdatesPanel } = await import('./UpdatesPanel')

/** A pip install with automatic checks off, as its last check left it: an old answer, cached. */
const OFF: UpdateCheck = {
  available: false, changes: '', checked: true, auto: 'off', kind: 'pip', current: '0.2.0', latest: '0.2.0',
  channel: 'stable', pin: '', check_enabled: false, check_interval_hours: 12, last_version: '', release_notes: '',
}

function mountWith(over: Partial<UpdateCheck> = {}) {
  useQuery.mockReturnValue({ data: { info: { ...OFF, ...over }, changelog: '' }, loading: false, error: null, refresh: vi.fn() })
  return render(<UpdatesPanel />)
}
/** The live regions' text. The verdict line is one; each `SavedToast` holds another, empty until a save. */
const headline = () => screen.getAllByRole('status').map((e) => e.textContent ?? '').join(' | ')
const pressCheckNow = () => fireEvent.click(screen.getByRole('button', { name: /^Check now/ }))

beforeEach(() => {
  checkForUpdatesNow.mockReset()
  patchConfig.mockReset(); patchConfig.mockResolvedValue({})
  notify.mockReset(); invalidateKeys.mockReset(); useQuery.mockReset()
})

describe('Check now', () => {
  it('asks once with automatic checks off, and shows what it found rather than "off"', async () => {
    checkForUpdatesNow.mockResolvedValue({ ...OFF, checked_now: true })
    const { container } = mountWith()
    expect(headline()).toContain('Automatic update checks are off')

    pressCheckNow()

    await waitFor(() => expect(headline()).toContain('Up to date'))
    expect(checkForUpdatesNow).toHaveBeenCalledTimes(1)
    expect(headline()).not.toContain('Automatic update checks are off')
    expect(container.textContent).toContain('Checked once, just now. Automatic checks stay off.')
    expect(patchConfig, 'asking once must not turn automatic checks on').not.toHaveBeenCalled()
    expect(invalidateKeys, 'the hub tile reads the new answer').toHaveBeenCalledWith('settings:update-check')
  })

  it('offers the update it finds', async () => {
    checkForUpdatesNow.mockResolvedValue({ ...OFF, checked_now: true, available: true, update_available: true, latest: '0.3.0' })
    const { container } = mountWith()

    pressCheckNow()

    await waitFor(() => expect(container.textContent).toContain('Update available — 0.3.0'))
    expect(screen.getByRole('button', { name: 'Update' })).toBeTruthy()
  })

  it('says so when nothing answered, instead of showing the old answer as current', async () => {
    checkForUpdatesNow.mockResolvedValue({ ...OFF, check_enabled: true, checked_now: false })
    mountWith({ check_enabled: true })

    pressCheckNow()

    await waitFor(() => expect(headline()).toContain("Couldn't check for updates"))
    expect(headline()).not.toContain('Up to date')
  })

  it('a failed request is reported, not swallowed', async () => {
    checkForUpdatesNow.mockRejectedValue(new Error('{"error":"the gateway is restarting"}'))
    mountWith()

    pressCheckNow()

    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect(String(notify.mock.calls[0][0])).toContain("Couldn't check for updates: the gateway is restarting")
  })
})

describe('the automatic-check switch says what off means', () => {
  it('is named for automatic checks, and its hint names what still reaches GitHub', () => {
    const { container } = mountWith({ check_enabled: true, check_interval_hours: 6 })
    expect(screen.getByRole('switch', { name: 'Automatic update checks' })).toBeTruthy()
    const text = container.textContent ?? ''
    expect(text).toContain('when it starts, then every 6 hours')
    expect(text).toContain('Off: it never checks for updates on its own.')
    expect(text).toContain('Check now and Update still reach GitHub, each time you press one.')
    expect(text, 'the old promise was false: Update still reached GitHub').not.toMatch(/zero outbound calls/i)
  })

  it('with checks off, the headline points at Check now', () => {
    const { container } = mountWith()
    expect(container.textContent).toContain('Check now looks once, when you press it.')
    expect(container.textContent).toContain('press Check now to fetch the newest release’s notes')
  })
})
