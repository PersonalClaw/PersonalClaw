import { describe, expect, it, vi, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { api, type NotificationRuleRow, type NotificationRulesDoc } from '../../lib/api'
import { NotificationRulesMatrix } from './NotificationRulesMatrix'

// ── A rule's lists change one entry per save, never this tab's copy of them ──────────────────────
//
// Every list edit in the matrix is one name in or out: a target ticked or unticked, a keyword chip
// added or removed. They were saved as the row's whole list, built from the copy this page painted
// — so a tab opened before the phone turned push on for a kind switched it off again with the next
// tick, and a keyword added in one tab was deleted by the next chip edit in another. The gateway
// now takes the ONE entry and applies it to the rule as stored, and the page sends only that.
//
// Driven through the real controls, because the defect was a click producing the wrong request.

const row = (over: Partial<NotificationRuleRow> = {}): NotificationRuleRow => ({
  key: 'skills/proposal', source: 'skills', kind: 'proposal', label: 'Skill proposal', severity: 1,
  mode: 'immediate', default_mode: 'immediate', configured: true,
  targets: ['dashboard'], conditions: { keywords: ['deploy'], name_mention: false }, sound: null,
  ...over,
})

const doc = (rows: NotificationRuleRow[]): NotificationRulesDoc => ({
  rules: rows, digest: { schedule: '0 8 * * *' }, targets: ['dashboard', 'channel_dm', 'push', 'native'],
})

afterEach(() => vi.restoreAllMocks())

function open(r: NotificationRuleRow = row()) {
  const save = vi.spyOn(api, 'saveNotificationRules').mockResolvedValue({ ok: true, ...doc([r]) })
  render(<NotificationRulesMatrix doc={doc([r])} onSaved={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: `Show delivery detail for ${r.label}` }))
  return save
}

describe('a notification rule list is edited one entry at a time', () => {
  it('ticking a target adds that target — not this row’s list with it appended', async () => {
    const save = open()
    fireEvent.click(screen.getByRole('checkbox', { name: /Deliver Skill proposal to Push/ }))
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save).toHaveBeenCalledWith({ rules: { 'skills/proposal': { targets: { add: 'push' } } } })
  })

  it('unticking a target removes that target, including the last one', async () => {
    // The gateway leaves Dashboard on a rule whose last target goes, as a rule with none reads back.
    const save = open()
    fireEvent.click(screen.getByRole('checkbox', { name: /Deliver Skill proposal to Dashboard/ }))
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save).toHaveBeenCalledWith({ rules: { 'skills/proposal': { targets: { remove: 'dashboard' } } } })
  })

  it('a keyword chip sends the keyword it added or removed, and nothing else of the conditions', async () => {
    const save = open()
    // Named by its field's label (`aria-labelledby`), which is what a screen reader announces.
    const input = screen.getByRole('textbox', { name: /Escalate on keywords/ })
    fireEvent.change(input, { target: { value: 'outage' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    // No `name_mention` riding along from this row's copy: another tab may have turned it on.
    expect(save).toHaveBeenLastCalledWith({ rules: { 'skills/proposal': { conditions: { keywords: { add: 'outage' } } } } })

    fireEvent.click(screen.getByRole('button', { name: 'Remove deploy' }))
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
    expect(save).toHaveBeenLastCalledWith({ rules: { 'skills/proposal': { conditions: { keywords: { remove: 'deploy' } } } } })
  })

  it('the name-mention switch sends only itself — not the keyword list it sits beside', async () => {
    const save = open()
    fireEvent.click(screen.getByRole('switch', { name: 'Escalate on name mention' }))
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save).toHaveBeenCalledWith({ rules: { 'skills/proposal': { conditions: { name_mention: true } } } })
  })
})
