import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { api, type NotificationRuleRow, type NotificationRulesDoc } from '../../lib/api'
import { NotificationRulesMatrix } from './NotificationRulesMatrix'

// ── "reset" has to CLEAR the rule, not save the default (issue #285) ──────────────────────────────
//
// It was `save(r.key, { mode: r.default_mode })` — a write of the registry default as an explicit
// rule. So "back to default" produced "explicitly set to the same value as default": the row stayed
// `configured: true` forever and stopped tracking `default_mode`, so a later change to a kind's
// default would silently miss every user who had ever pressed reset, indistinguishably from one who
// never touched the row.
//
// 🪤 AND IT MASKED ITSELF. The chip rendered only while `r.configured && r.mode !== r.default_mode`,
// so the buggy reset made mode === default_mode, which turned that condition false and HID the chip
// — leaving no UI path back to a genuinely unconfigured state. Both halves are pinned here: the
// request body, and that a row pinned AT its default still offers the control.
//
// Driven through the real component and the real button, because the whole defect was a click
// producing the wrong request — a source-text assertion would have passed against the old code too
// (it also contained the word "reset").

const row = (over: Partial<NotificationRuleRow> = {}): NotificationRuleRow => ({
  key: 'skills/proposal', source: 'skills', kind: 'proposal', label: 'Skill proposal', severity: 1,
  mode: 'badge', default_mode: 'immediate', configured: true,
  targets: ['dashboard'], conditions: { keywords: [], name_mention: false }, sound: null,
  ...over,
})

const doc = (rows: NotificationRuleRow[]): NotificationRulesDoc => ({
  rules: rows, digest: { schedule: '0 8 * * *' }, targets: ['dashboard', 'channel_dm', 'push', 'native'],
})

afterEach(() => vi.restoreAllMocks())

// The chip's accessible name is its own text ("reset"); `title` carries the explanation, which is
// asserted separately below rather than used as the query — content wins over `title` for a name.
const resetChip = () => screen.getByRole('button', { name: /^reset$/i })
const queryResetChip = () => screen.queryByRole('button', { name: /^reset$/i })

function mount(rows: NotificationRuleRow[]) {
  const save = vi.spyOn(api, 'saveNotificationRules').mockResolvedValue({
    ok: true, ...doc(rows),
  })
  const onSaved = vi.fn()
  render(<NotificationRulesMatrix doc={doc(rows)} onSaved={onSaved} />)
  return { save, onSaved }
}

describe('the reset chip', () => {
  it('sends null — a CLEAR — rather than a write of the default value', async () => {
    const { save, onSaved } = mount([row()])
    fireEvent.click(resetChip())
    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save).toHaveBeenCalledWith({ rules: { 'skills/proposal': null } })
    // 🪤 The exact shape the bug shipped. Without this the assertion above could coexist with it.
    expect(save).not.toHaveBeenCalledWith({ rules: { 'skills/proposal': { mode: 'immediate' } } })
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })

  it('is still offered on a row pinned AT its default', () => {
    // The self-masking half. This row is exactly what the buggy reset used to leave behind, and the
    // old condition rendered no control for it at all.
    mount([row({ mode: 'immediate', default_mode: 'immediate', configured: true })])
    expect(resetChip()).toBeTruthy()
  })

  it('🪤 is absent on an untouched row', () => {
    // The vacuity leg: a chip rendered unconditionally would pass both tests above while putting a
    // "reset" on all 32 rows.
    mount([row({ configured: false, mode: 'immediate' })])
    expect(queryResetChip()).toBeNull()
  })

  it('names the default by the word the pills use, not the wire value', () => {
    // `immediate` is called "Notify" on every pill in this matrix, so the old tooltip named a mode
    // the user cannot see anywhere on the surface.
    mount([row()])
    const chip = resetChip()
    expect(chip.getAttribute('title')).toContain('Notify')
    expect(chip.getAttribute('title')).not.toContain('immediate')
  })
})

describe('delivery targets tell the truth about themselves (#343)', () => {
  it('marks Channel DM as not-yet-delivered', async () => {
    // It was the one target with nothing behind it — `notification_rules.TARGETS`' own comment calls
    // it "accepted and persisted but inert" — and the only one presented as a plain, live choice, so
    // ticking it silently did nothing.
    mount([row()])
    fireEvent.click(screen.getByRole('button', { name: /delivery detail for Skill proposal/i }))
    const label = await screen.findByText(/Channel DM/)
    expect(label.textContent).toMatch(/not delivered yet/i)
    // Dimmed like `push`, which is the established way this surface says "saved, not live".
    expect(label.className).toContain('text-on-surface-low')
  })
})
