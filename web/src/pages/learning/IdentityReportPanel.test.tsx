import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { IdentityReportPanel } from './IdentityReportPanel'
import { api, type IdentityReportView } from '../../lib/api'

// ── LV-4's periodic identity report panel ─────────────────────────────────────────────
//
// Four properties, each of which has a way of quietly going wrong:
//
//  1. The heading count is the server's `count`, NEVER `items.length`. Every fixture below
//     ships a count LARGER than its sample so the two cannot be confused — an assertion
//     against a fixture where they agree would prove nothing.
//  2. A failed fetch must not render as "nothing has been learned". That is the one claim
//     this panel must never make by accident, so `error` is read.
//  3. `narrative_status === 'unavailable'` is STATED. A blank space reads as "nothing to
//     say", which is the opposite of "no model answered".
//  4. "Write it up" calls the POST — the delivery that persists the artifact and raises the
//     inbox item. If nothing called it, the whole delivery half would be an inert control.

function report(overrides: Partial<IdentityReportView> = {}): IdentityReportView {
  return {
    period: { window_days: 30, since: '2026-07-21T12:00:00+00:00', until: '2026-08-20T12:00:00+00:00' },
    window_days: 30,
    generated_at: '2026-08-20T12:00:00+00:00',
    total: 4,
    facets: {
      // count 9, ONE item: the sample is deliberately shorter than the count so a panel
      // rendering `items.length` reads 1 where the truth is 9.
      count: 9,
      items: [{ text: 'prefers terse replies', cls: 'style', stability: 0.82, state: 'Active', updated_at: '', pinned: false }],
    },
    lessons: { count: 2, items: [{ text: 'always run make lint', category: 'process', updated_at: '' }] },
    skills: {
      count: 3,
      items: [{ name: 'auto/triage-flow', uses: 1, last_used: '', used_in_window: true, aging_state: 'active', created_at: '' }],
    },
    proposals: { count: 1, items: [{ label: 'auto/triage-flow (refine)', kind: 'refine' }] },
    memory: { semantic_active: 12, episodic_active: 3, events_count: 40 },
    narrative: '',
    narrative_status: 'skipped',
    markdown: '# How I\'ve adapted to you\n',
    // The DELIVERY cadence, not a gathered fact (LV-4's schedule half). `monthly` is the
    // shipped default; `identityReportCadence.test.tsx` drives the control itself.
    cadence: 'monthly',
    ...overrides,
  }
}

describe('IdentityReportPanel', () => {
  it('renders the server count, not the length of the truncated sample', () => {
    const r = report()
    expect(r.facets.count).toBeGreaterThan(r.facets.items.length) // the fixture must differ

    render(<IdentityReportPanel report={r} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    // 9, not 1. The sample length is the wrong answer and it is present in the data, so a
    // panel that used it would render a plausible-looking number.
    expect(screen.getByText('9')).toBeTruthy()
    expect(screen.getByText('Showing 1 of 9.')).toBeTruthy()
    expect(screen.getByText('prefers terse replies — style, Active')).toBeTruthy()
  })

  it('omits the "showing N of M" note when nothing was dropped', () => {
    // The vacuity floor for the note above: it must be conditional, or it would appear on
    // every complete section and stop meaning "there is more".
    const r = report({ lessons: { count: 1, items: [{ text: 'always run make lint', category: '', updated_at: '' }] } })

    render(<IdentityReportPanel report={r} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    expect(screen.queryByText('Showing 1 of 1.')).toBeNull()
  })

  it('renders a failed fetch as an error, never as an empty report', () => {
    render(
      <IdentityReportPanel report={undefined} error={new Error('boom')} onRetry={() => {}} onDelivered={() => {}} />,
    )

    // The heading must be ABSENT: a panel that rendered its own chrome around zeros would
    // assert "nothing has been learned" over a failure to ask.
    expect(screen.queryByText("How I've adapted to you")).toBeNull()
    expect(screen.queryByText(/Nothing recorded yet/)).toBeNull()
    expect(document.body.textContent).toContain('identity report')
  })

  it('states a degraded narrative instead of leaving a blank space', () => {
    render(
      <IdentityReportPanel
        report={report({ narrative_status: 'unavailable' })}
        error={undefined} onRetry={() => {}} onDelivered={() => {}}
      />,
    )

    expect(screen.getByText(/No model was available to summarise this period/)).toBeTruthy()
  })

  it('does not claim a degraded narrative when none was attempted', () => {
    // The floor for the assertion above: `skipped` is "nobody asked", which is not a
    // degradation and must not be reported as one.
    render(<IdentityReportPanel report={report()} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    expect(screen.queryByText(/No model was available/)).toBeNull()
  })

  it('calls the delivery POST when asked to write it up, and links the artifact', async () => {
    // THE call-site assertion. Without this, `deliverIdentityReport` — and behind it the
    // artifact write and the inbox item — would be reachable from nothing.
    const spy = vi.spyOn(api, 'deliverIdentityReport').mockResolvedValue({
      artifact_slug: 'learning-identity-report',
      artifact_version: 2,
      inbox_item_id: 'report_abc_1.0',
      report: report(),
    })
    const onDelivered = vi.fn()

    render(<IdentityReportPanel report={report()} error={undefined} onRetry={() => {}} onDelivered={onDelivered} />)
    fireEvent.click(screen.getByRole('button', { name: /Write it up/ }))

    await waitFor(() => expect(spy).toHaveBeenCalledWith(30))
    await waitFor(() => expect(onDelivered).toHaveBeenCalled())
    const link = await screen.findByRole('link', { name: /Open the report/ })
    expect(link.getAttribute('href')).toBe('#/artifacts/learning-identity-report')
    spy.mockRestore()
  })

  it('refuses to write up an empty record rather than spending a model call', () => {
    const r = report({
      total: 0,
      facets: { count: 0, items: [] },
      lessons: { count: 0, items: [] },
      skills: { count: 0, items: [] },
      proposals: { count: 0, items: [] },
    })

    render(<IdentityReportPanel report={r} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)

    // `aria-disabled`, NOT the native attribute: the button must stay focusable so a keyboard
    // user can hear WHY it is unavailable. A native `disabled` here would tab straight past.
    const button = screen.getByRole('button', { name: /Write it up/ })
    expect(button.getAttribute('aria-disabled')).toBe('true')
    expect(button.hasAttribute('disabled')).toBe(false)
    expect(button.getAttribute('title')).toContain('Nothing has been learned yet')

    // The gate really holds: clicking a reachable-but-unavailable button must not deliver.
    fireEvent.click(button)
    expect(vi.isMockFunction(api.deliverIdentityReport)).toBe(false)
  })

  it('surfaces a delivery failure instead of looking like it worked', async () => {
    const spy = vi.spyOn(api, 'deliverIdentityReport').mockRejectedValue(new Error('artifact store unavailable'))

    render(<IdentityReportPanel report={report()} error={undefined} onRetry={() => {}} onDelivered={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /Write it up/ }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('artifact store unavailable')
    expect(screen.queryByRole('link', { name: /Open the report/ })).toBeNull()
    spy.mockRestore()
  })
})
