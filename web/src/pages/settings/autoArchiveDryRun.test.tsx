import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// The row talks to exactly one endpoint — the auto-archive dry-run — so the mock is that thin.
const autoArchiveSessions = vi.fn()
vi.mock('../../lib/api', () => ({
  api: { autoArchiveSessions: (...a: unknown[]) => autoArchiveSessions(...a) },
}))

import { AutoArchiveRow } from './ChatPanel'

// cleanup runs from the global test setup (src/test/setup.ts) — no local afterEach needed.
beforeEach(() => {
  autoArchiveSessions.mockReset()
  autoArchiveSessions.mockResolvedValue({ ok: true, enabled: true, days: 30, keys: ['a', 'b', 'c'], count: 3 })
})

// ── SM-6: the retention row shows EXACTLY what would move, and only archives ──────────────────────
//
// The auto-archive rule ran silently on the heartbeat since S2; a retention rule the user cannot see
// is indistinguishable from data loss. This pins the two properties that make it safe: the number is
// the dry-run's real count (not an estimate computed from the threshold), and the surface promises an
// archive that restores — never a delete. ARCC's retention principle (accurate preview + reversible,
// no destructive purge) is exactly these.
describe('AutoArchiveRow — live dry-run preview', () => {
  it('shows the dry-run count verbatim (the number that WOULD move, not an estimate)', async () => {
    render(<AutoArchiveRow days={30} onCommit={vi.fn()} saved={false} />)
    // It asks the same dry-run the sweep runs — reads, moves nothing.
    await waitFor(() => expect(autoArchiveSessions).toHaveBeenCalledWith({ dry_run: true }))
    // The rendered "3" is the mock's count, so the surface reflects the API, not a computed guess.
    expect(await screen.findByText('3 stale now')).toBeTruthy()
  })

  it('says "none stale now" when the dry-run finds nothing (never a bare 0)', async () => {
    autoArchiveSessions.mockResolvedValue({ ok: true, enabled: true, days: 30, keys: [], count: 0 })
    render(<AutoArchiveRow days={30} onCommit={vi.fn()} saved={false} />)
    expect(await screen.findByText('none stale now')).toBeTruthy()
  })

  it('is OFF at 0 days: no dry-run call, no preview, reads "off"', async () => {
    render(<AutoArchiveRow days={0} onCommit={vi.fn()} saved={false} />)
    expect(screen.getByText('off')).toBeTruthy()
    // Give any stray effect a tick to (not) fire, then assert the network was never touched.
    await Promise.resolve()
    expect(autoArchiveSessions).not.toHaveBeenCalled()
    expect(screen.queryByText(/stale now/)).toBeNull()
  })

  it('commits an edited threshold with its own label (so a rejected save names this control)', async () => {
    const onCommit = vi.fn()
    render(<AutoArchiveRow days={30} onCommit={onCommit} saved={false} />)
    const field = screen.getByLabelText('Auto-archive after (days)')
    await userEvent.clear(field)
    await userEvent.type(field, '60')
    await userEvent.tab() // NumberField commits on blur
    expect(onCommit).toHaveBeenCalledWith(60, 'Auto-archive after (days)')
  })

  it('promises a reversible archive — nothing is deleted (purge deliberately not built)', () => {
    // days=0 so the (off) row fires no dry-run: the hint is static and independent of the preview,
    // so this stays a synchronous, fetch-free assertion.
    render(<AutoArchiveRow days={0} onCommit={vi.fn()} saved={false} />)
    expect(screen.getByText(/nothing is deleted/i)).toBeTruthy()
    expect(screen.getByText(/restore in one click/i)).toBeTruthy()
  })

  it('hides the count when the server reports the rule disabled, even with days > 0', async () => {
    // The rule can be off server-side while the local threshold is non-zero; the count must not
    // claim sessions would move when nothing would. Guarded by `preview?.enabled` (ChatPanel.tsx).
    autoArchiveSessions.mockResolvedValue({ ok: true, enabled: false, days: 30, keys: [], count: 5 })
    render(<AutoArchiveRow days={30} onCommit={vi.fn()} saved={false} />)
    await waitFor(() => expect(autoArchiveSessions).toHaveBeenCalledWith({ dry_run: true }))
    await act(async () => {}) // flush the resolved dry-run into state so we read the POST-resolve DOM
    expect(screen.queryByText(/stale now/)).toBeNull()
  })
})
