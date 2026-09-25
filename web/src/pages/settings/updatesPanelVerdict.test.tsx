import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { UpdateCheck } from '../../lib/api'

// ── Settings › Updates says what the check actually found, on every install kind ──────────────────
//
// On a pip install `Check` never produced a result: the server only ever set `checked` from the git
// half, so the headline read "No update check yet" forever. And a pin that names no release — the
// hint's own example, `0.2.1`, was one — had its notice only on container installs and only while an
// update was available, which a pin-miss can never be; so the one message about that state was the
// one nobody could see. The server now sends `checked` and `pin_miss` for every kind; this pins what
// the panel does with them.

const patchConfig = vi.fn()
const notify = vi.fn()
const refresh = vi.fn()
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
  return { ...real, api: { ...real.api, patchConfig: (...a: unknown[]) => patchConfig(...a) } }
})

const { UpdatesPanel } = await import('./UpdatesPanel')

/** A pip install on stable — the kind the defect was measured on. */
const PIP: UpdateCheck = {
  available: false, changes: '', checked: true, auto: 'off', kind: 'pip', current: '0.1.3',
  channel: 'stable', pin: '', check_enabled: true, check_interval_hours: 12, last_version: '', release_notes: '',
}

function mountWith(over: Partial<UpdateCheck> = {}) {
  useQuery.mockReturnValue({ data: { info: { ...PIP, ...over }, changelog: '' }, loading: false, error: null, refresh })
  return render(<UpdatesPanel />)
}
/** The live regions' text. The verdict line is one; each `SavedToast` holds another, empty until a save. */
const headline = () => screen.getAllByRole('status').map((e) => e.textContent ?? '').join(' | ')

beforeEach(() => {
  patchConfig.mockReset(); patchConfig.mockResolvedValue({})
  notify.mockReset(); refresh.mockReset(); invalidateKeys.mockReset(); useQuery.mockReset()
})

describe('the Version headline is the check’s real answer', () => {
  it('a pin that names no release says so on a pip install — not only a container, not only when "available"', () => {
    const { container } = mountWith({ pin: '0.2.1', pin_miss: true, latest: '' })
    expect(headline()).toContain('No release matches pin 0.2.1')
    expect(container.textContent).toContain('Nothing is offered or installed while this pin stands')
    expect(container.textContent).not.toContain('Up to date')
  })

  it('a check that produced no answer says it could not check, not that none was run', () => {
    mountWith({ checked: false })
    expect(headline()).toContain("Couldn't check for updates")
  })

  it('checking switched off is said as such, not reported as "Up to date" from an old answer', () => {
    mountWith({ checked: true, check_enabled: false })
    expect(headline()).toContain('Update checks are off')
  })

  it('a pip install the check compared reads "Up to date" (vacuity floor)', () => {
    mountWith({ checked: true, latest: '0.1.3' })
    expect(headline()).toContain('Up to date')
  })

  it('release notes do not send a pinned user to a Check that cannot help', () => {
    const { container } = mountWith({ pin: '0.2.1', pin_miss: true })
    expect(container.textContent).toContain('No published release is version 0.2.1')
    expect(container.textContent).not.toContain('run a check')
  })
})

describe('the version pin', () => {
  it('its example is a release that exists — a released heading in CHANGELOG.md, both in the hint and the box', () => {
    const released = new Set([...readFileSync(join(process.cwd(), '..', 'CHANGELOG.md'), 'utf8')
      .matchAll(/^## \[(\d+\.\d+\.\d+)\]/gm)].map((m) => m[1]))
    expect(released.size, 'the CHANGELOG must still list releases for this to mean anything').toBeGreaterThan(3)
    const { container } = mountWith()
    const hint = container.textContent?.match(/Stay on an exact release \(e\.g\. ([^)]+)\)/)?.[1]
    const placeholder = (screen.getByLabelText('Version pin') as HTMLInputElement).placeholder
    expect(released, `the hint's example ${hint} must be a real release`).toContain(hint)
    expect(released, `the placeholder ${placeholder} must be a real release`).toContain(placeholder)
  })

  it('a pin the server refuses is not displayed as the stored pin', async () => {
    patchConfig.mockRejectedValue(new Error("'not-a-version!!' is not a release version"))
    const { container } = mountWith()
    fireEvent.change(screen.getByLabelText('Version pin'), { target: { value: 'not-a-version!!' } })
    fireEvent.click(screen.getByRole('button', { name: /Save pin/ }))
    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect(String(notify.mock.calls[0][0])).toContain('is not a release version')
    expect(container.textContent, 'the row must still say what is actually stored').not.toContain('pinned to not-a-version!!')
    expect((screen.getByLabelText('Version pin') as HTMLInputElement).value, 'what was typed is kept to fix').toBe('not-a-version!!')
  })

  it('saving a pin re-runs the check, so a pin that names no release says so right away', async () => {
    mountWith()
    fireEvent.change(screen.getByLabelText('Version pin'), { target: { value: '0.2.1' } })
    fireEvent.click(screen.getByRole('button', { name: /Save pin/ }))
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('updates.pin', '0.2.1'))
    await waitFor(() => expect(invalidateKeys).toHaveBeenCalledWith('settings:updates'))
    expect(refresh).toHaveBeenCalled()
  })
})
