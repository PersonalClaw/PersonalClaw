import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ScheduleForm, MISSED_RUN_CHOICES, draftToPayload, emptyDraft, toDraft, type ScheduleDraft } from './ScheduleForm'
import type { ScheduleJob } from '../../lib/api'

// ── What a missed time does is a setting she can see and change ──────────────────────────────────
//
// `Trigger.catch_up` decides what happens to a time PersonalClaw could not run because it was stopped
// or the computer was asleep: off, it waits on the Triggers page's review; on, it runs once, late.
// It had no control: the form drew nothing for it and sent nothing, so every schedule made on the
// Triggers page was "review" with no way to say otherwise, or to know that was the rule.
//
// Asserted on the PAYLOAD as well as on the control, for the reason `failureRoutingReachesTheTrigger`
// gives: a switch that renders is not a switch that persists. And the hint is asserted to change with
// the choice, because the sentence under the control is the only place she reads what it does.

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  return { ...actual, api: { ...actual.api, channels: () => Promise.resolve([]) } }
})

vi.mock('../../lib/agents', () => ({
  useAgentCatalog: () => ({ options: [] }),
  useModelCatalog: () => ({ options: [] }),
}))

const job = (over: Partial<ScheduleJob> = {}): ScheduleJob =>
  ({ id: 'j', name: 'n', message: '', enabled: true, schedule: 'every 1h', ...over }) as ScheduleJob

beforeEach(async () => {
  const { invalidateKeys } = await import('../../lib/data')
  invalidateKeys('settings:channels', true)
})

describe('catch_up rides the wire', () => {
  it('defaults off, matching the entity, and is sent by presence', () => {
    expect(emptyDraft().catch_up).toBe(false)
    const body = draftToPayload(emptyDraft())
    expect('catch_up' in body).toBe(true)
    expect(body.catch_up).toBe(false)
  })

  it('sends true when chosen, whatever the action is', () => {
    for (const mode of ['agent', 'script', 'command', 'other'] as const) {
      expect(draftToPayload({ ...emptyDraft(), mode, catch_up: true }).catch_up, `mode=${mode}`).toBe(true)
    }
  })

  it('reads back what the server holds, and survives draft -> payload -> draft', () => {
    expect(toDraft(job({ catch_up: true })).catch_up).toBe(true)
    expect(toDraft(job({ catch_up: false })).catch_up).toBe(false)
    expect(toDraft(job()).catch_up).toBe(false)
    for (const value of [true, false]) {
      const body = draftToPayload({ ...emptyDraft(), catch_up: value })
      expect(toDraft(job({ catch_up: body.catch_up as boolean })).catch_up).toBe(value)
    }
  })
})

function mount(over: Partial<ScheduleDraft> = {}) {
  const onChange = vi.fn()
  const draft: ScheduleDraft = { ...emptyDraft(), ...over }
  const view = render(<ScheduleForm draft={draft} onChange={onChange} triggerOnly />)
  fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
  return { onChange, view, draft }
}

const select = () => screen.getByRole('combobox', { name: 'What a missed time does' }) as HTMLSelectElement
const described = (el: HTMLElement) => document.getElementById(el.getAttribute('aria-describedby') ?? '')?.textContent

describe('the "If a time is missed" control', () => {
  it('opens on "wait for me to decide" and says what that does', () => {
    mount()
    expect(select().value).toBe('review')
    expect(screen.getByRole('option', { name: 'Wait for me to decide' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Run it once, late' })).toBeTruthy()
    expect(described(select())).toBe(MISSED_RUN_CHOICES[0].hint)
    expect(MISSED_RUN_CHOICES[0].hint).toContain('waits on the Triggers page for you to run it or dismiss it')
  })

  it('choosing to run it late sets catch_up, and the hint then says what that does', () => {
    const { onChange, view, draft } = mount()
    fireEvent.change(select(), { target: { value: 'catch_up' } })
    const next = onChange.mock.calls.at(-1)?.[0] as ScheduleDraft
    expect(next.catch_up).toBe(true)
    view.rerender(<ScheduleForm draft={{ ...draft, catch_up: true }} onChange={onChange} triggerOnly />)
    expect(select().value).toBe('catch_up')
    expect(described(select())).toBe(MISSED_RUN_CHOICES[1].hint)
    expect(MISSED_RUN_CHOICES[1].hint).toContain('runs once by itself')
    expect(MISSED_RUN_CHOICES[1].hint).toContain('how late it ran')
  })

  it('a saved schedule that catches up opens on that choice, and switching back turns it off', () => {
    const { onChange } = mount(toDraft(job({ catch_up: true })))
    expect(select().value).toBe('catch_up')
    fireEvent.change(select(), { target: { value: 'review' } })
    expect((onChange.mock.calls.at(-1)?.[0] as ScheduleDraft).catch_up).toBe(false)
  })

  it('both hints name both ways a time is missed', () => {
    for (const choice of MISSED_RUN_CHOICES) {
      expect(choice.hint).toContain('PersonalClaw was stopped or the computer was asleep')
    }
  })
})
