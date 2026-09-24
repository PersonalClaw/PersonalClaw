import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { IdentityReportPanel } from './IdentityReportPanel'
import { api, type IdentityReportView } from '../../lib/api'

// ── LV-4's cadence control: the config round-trip's fifth point ────────────────────────
//
// The backend field, its `load()`/`to_dict()` mapping and its PATCH allowlist entry are railed in
// `tests/test_lv4_identity_report_schedule.py`. What only a mounted component can prove is the
// part the round-trip test provably cannot see: that a control EXISTS, that clicking it writes
// the field, and that a failure does not leave the strip showing a value nothing persisted.
//
// Four properties, each written against the way it goes quietly wrong:
//
//  1. The strip offers all three cadences. Missing `off` would leave the only switch the feature
//     has unreachable from the only surface that shows the report — a field that round-trips
//     perfectly and that no user can ever set.
//  2. A click WRITES `learning.identity_report_cadence`. Without this the whole schedule half is
//     configurable in theory and inert in practice.
//  3. A FAILED save reverts. A settings control that keeps an optimistic value is presenting
//     something it never persisted as saved state — GuardrailsPanel's 🔴 records that exact
//     defect, measured, with the form rendering in full and no error anywhere.
//  4. `cadence: ''` (the server could not read the config) renders NO control. Rendering the strip
//     at its first option would claim "Monthly" is what you saved, indistinguishable from the
//     truth and wrong.

function report(overrides: Partial<IdentityReportView> = {}): IdentityReportView {
  return {
    period: { window_days: 30, since: '2026-07-21T12:00:00+00:00', until: '2026-08-20T12:00:00+00:00' },
    window_days: 30,
    generated_at: '2026-08-20T12:00:00+00:00',
    total: 1,
    facets: { count: 1, items: [{ text: 'prefers terse replies', cls: 'style', stability: 0.8, state: 'Active', updated_at: '', pinned: false }] },
    lessons: { count: 0, items: [] },
    skills: { count: 0, items: [] },
    proposals: { count: 0, items: [] },
    memory: {},
    narrative: '',
    narrative_status: 'skipped',
    markdown: '# How I\'ve adapted to you\n',
    cadence: 'monthly',
    ...overrides,
  }
}

// Resolved from THIS file rather than from `process.cwd()`: the suite runs both from `web/` and
// from the repo root (single-root workspace), and a cwd-relative path silently ENOENTs in one of
// the two — measured, on the first run of this file.
const PANEL = join(dirname(fileURLToPath(import.meta.url)), 'IdentityReportPanel.tsx')

/** The strip, resolved by its accessible name. `Segmented` renders `role="tablist"` with the
 *  options as tabs, so this is also what asserts the group is NAMED — a bare tablist would make
 *  `getByRole` here fail rather than silently pass on an unnamed group. */
const strip = () => screen.getByRole('tablist', { name: 'Write one automatically' })

describe('the identity report cadence control', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('offers every cadence the backend accepts, including off', () => {
    render(<IdentityReportPanel report={report()} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    const labels = Array.from(strip().querySelectorAll('[role="tab"]')).map((t) => t.textContent?.trim())
    expect(labels).toEqual(['Monthly', 'Weekly', 'Off'])
    // The active one is the SERVER's value, not the first option — a strip that always
    // highlighted its head would read as "Monthly" for a weekly install.
    expect(screen.getByRole('tab', { name: 'Monthly' }).getAttribute('aria-selected')).toBe('true')
  })

  it('shows the server value as active, not the default', () => {
    // The floor for the assertion above: with a different saved value, a different tab is active.
    render(<IdentityReportPanel report={report({ cadence: 'weekly' })} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    expect(screen.getByRole('tab', { name: 'Weekly' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: 'Monthly' }).getAttribute('aria-selected')).toBe('false')
  })

  it('writes learning.identity_report_cadence and re-reads the report', async () => {
    // THE call-site assertion for round-trip point 5. The re-read matters on its own: the window
    // the header states is derived from the cadence server-side, so a saved change that left
    // "last 30 days" beside "Weekly" would be the panel disagreeing with its own document.
    const spy = vi.spyOn(api, 'patchConfig').mockResolvedValue({})
    const onRetry = vi.fn()

    render(<IdentityReportPanel report={report()} error={undefined} onRetry={onRetry} onDelivered={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Weekly' }))

    await waitFor(() => expect(spy).toHaveBeenCalledWith('learning.identity_report_cadence', 'weekly'))
    await waitFor(() => expect(onRetry).toHaveBeenCalled())
    expect(screen.getByRole('tab', { name: 'Weekly' }).getAttribute('aria-selected')).toBe('true')
  })

  it('says what off means instead of leaving the panel looking broken', () => {
    render(<IdentityReportPanel report={report({ cadence: 'off' })} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    expect(screen.getByText(/Nothing is scheduled/)).toBeTruthy()
    // And the hand-run stays available — `off` stops the JOB, not the feature.
    expect(screen.getByRole('button', { name: /Write it up/ }).getAttribute('aria-disabled')).toBeNull()
  })

  it('REVERTS the strip when the save fails, and names the control in the error', async () => {
    const spy = vi.spyOn(api, 'patchConfig').mockRejectedValue(new Error('gateway said no'))

    render(<IdentityReportPanel report={report()} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)
    fireEvent.click(screen.getByRole('tab', { name: 'Off' }))

    await waitFor(() => expect(spy).toHaveBeenCalled())
    const alert = await screen.findByRole('alert')
    // Named with the control's OWN visible words, not with the config path.
    expect(alert.textContent).toContain('Write one automatically')
    expect(alert.textContent).toContain('gateway said no')
    // Reverted. Leaving "Off" selected would claim a state the gateway rejected.
    expect(screen.getByRole('tab', { name: 'Monthly' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: 'Off' }).getAttribute('aria-selected')).toBe('false')
  })

  it('renders NO control when the server could not read the config', () => {
    render(<IdentityReportPanel report={report({ cadence: '' })} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    expect(screen.queryByRole('tablist', { name: 'Write one automatically' })).toBeNull()
    expect(screen.getByText(/Your settings could not be read/)).toBeTruthy()
    // The rest of the panel still renders — an unreadable SETTING is not an unreadable report.
    expect(screen.getByText("How I've adapted to you")).toBeTruthy()
  })

  it('uses one wording for the visible label and the accessible name', () => {
    // Label-in-name: a spoken name that differs from the words on screen is the failure a bare
    // `Segmented` already cost this app seven times (`ui/segmentedNamed.test.tsx`). Asserted
    // through the DOM rather than by reading the source, so a renamed constant cannot pass it.
    render(<IdentityReportPanel report={report()} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    const visible = screen.getByTestId('cadence-label').textContent?.trim()
    expect(visible).toBe(strip().getAttribute('aria-label'))
  })

  it('does not also ship a second on/off switch', () => {
    // `off` is a MEMBER of the cadence. A sibling toggle would let `enabled=false, cadence=weekly`
    // exist, and whichever of the two lost would be a control that silently does nothing.
    const src = readFileSync(PANEL, 'utf8')
    expect(src).not.toContain('identity_report_enabled')
  })
})
