import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { api, type NotificationRuleRow, type NotificationRulesDoc } from '../../lib/api'
import { NotificationRulesMatrix } from './NotificationRulesMatrix'

// ── the matrix says what notifies when you have set nothing ──────────────────────────────────────
//
// With no rule of yours, only a decision (an approval, a question) or an error notifies; every other
// kind is a badge (`notification_kinds`). Most rows therefore open on Badge, and the section says
// why where the rows are, before anyone wonders where a scheduled job's toast went.

const row = (over: Partial<NotificationRuleRow> = {}): NotificationRuleRow => ({
  key: 'cron/result', source: 'cron', kind: 'result', label: 'Scheduled job result', severity: 1,
  mode: 'badge', default_mode: 'badge', configured: false,
  targets: ['dashboard'], conditions: { keywords: [], name_mention: false }, sound: null,
  ...over,
})

const doc = (rows: NotificationRuleRow[]): NotificationRulesDoc => ({
  rules: rows, digest: { schedule: '0 8 * * *' }, targets: ['dashboard', 'channel_dm', 'push', 'native'],
})

afterEach(() => vi.restoreAllMocks())

function mount(rows: NotificationRuleRow[]) {
  vi.spyOn(api, 'saveNotificationRules').mockResolvedValue({ ok: true, ...doc(rows) })
  render(<NotificationRulesMatrix doc={doc(rows)} onSaved={vi.fn()} />)
}

describe('the Per-kind delivery hint', () => {
  it('says that only approvals, questions and errors notify by default', () => {
    mount([row(), row({ key: 'cron/failed', kind: 'failed', label: 'Scheduled job failed', severity: 3, mode: 'immediate', default_mode: 'immediate' })])
    expect(
      screen.getByText(/By default only approvals, questions and errors notify; everything else is a badge\./),
    ).toBeTruthy()
  })
})
