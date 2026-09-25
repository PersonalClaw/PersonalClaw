import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScheduleForm, emptyDraft, type ScheduleDraft } from './ScheduleForm'
import { MIN_INTERVAL_SECS } from './scheduleMeta'

// 🔴 The author-time half of issue 531. The interval control was `min={1}` and a unit picker with no
// floor mentioned anywhere, so the only thing standing between a typo and a per-fifteen-minutes LLM
// invocation was the backend's 900s warning — which the backend computed and every surface then
// dropped. The floor now renders beside the control, at author time, before the save.
//
// An ADVISORY, never a gate: the backend deliberately warns rather than refusing (R1 makes the floor
// overridable — a fast local-model poll is a legitimate choice), so a form that blocked Save would
// refuse a cadence the API accepts. The assertions below pin BOTH halves of that: the hint appears,
// and nothing about the control becomes invalid or disabled when it does.
//
// The cadences here are expressed in the units the form actually offers (`INTERVAL_UNITS` is
// minutes/hours/days and `intervalToSecs` floors at 60s). That is deliberate and not a gap: a
// sub-minute cadence stored by the API is preserved rather than displayed, because `draftToPayload`
// sends the ORIGINAL `every_secs` for an untouched interval — railed next door in
// `renameKeepsItsCadence.test.ts`, which also asserts that the display pair is lossy on purpose.

vi.mock('../../lib/agents', () => ({
  useAgentCatalog: () => ({ options: [] }),
  useModelCatalog: () => ({ options: [] }),
}))

function mount(over: Partial<ScheduleDraft> = {}, invokesModel?: boolean) {
  const draft: ScheduleDraft = { ...emptyDraft(), kind: 'every', ...over }
  return render(<ScheduleForm draft={draft} onChange={() => {}} invokesModel={invokesModel} />)
}

// 🔴 B10 (2026-09-25): the floor is about MODEL calls. Measured on the create form — "Every 60s is
// below the 900s floor for an LLM-invoking trigger" under a Dashboard Notification, an action that
// makes no model call at all. The caller says what the action is (the catalog's `invokes_model`);
// a caller that cannot say (undefined) keeps the floor, the backend's own direction.
describe('the floor speaks only for an action that can call a model', () => {
  it('is silent at 60s for a zero-token action', () => {
    mount({ intervalValue: 1, intervalUnit: 'm' }, false)
    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByLabelText('Run every — interval count')).not.toHaveAttribute('aria-describedby')
  })

  it('still warns at 60s for a model-invoking action', () => {
    mount({ intervalValue: 1, intervalUnit: 'm' }, true)
    expect(screen.getByRole('status')).toHaveTextContent('Every 60s')
  })

  it('keeps the floor when the caller cannot say (the default)', () => {
    mount({ intervalValue: 1, intervalUnit: 'm' })
    expect(screen.getByRole('status')).toHaveTextContent('Every 60s')
  })
})

describe('the interval control says the cadence floor out loud', () => {
  it('warns when the chosen cadence is below the floor', () => {
    mount({ intervalValue: 5, intervalUnit: 'm' })
    const hint = screen.getByRole('status')
    expect(hint).toHaveTextContent(`below the ${MIN_INTERVAL_SECS}s floor`)
    expect(hint).toHaveTextContent('Every 300s')
  })

  it('warns at the smallest cadence the control can express (1 minute)', () => {
    // `intervalToSecs(1, 'm') = 60` is the fastest thing a user can select here, and it is the row
    // the live measurement was taken on: a 60s LLM-invoking trigger the doctor called healthy.
    mount({ intervalValue: 1, intervalUnit: 'm' })
    expect(screen.getByRole('status')).toHaveTextContent('Every 60s')
  })

  it('warns one step below the floor (14 minutes), so the boundary is not the only case', () => {
    mount({ intervalValue: 14, intervalUnit: 'm' })
    expect(screen.getByRole('status')).toHaveTextContent('Every 840s')
  })

  it('stays quiet at and above the floor (vacuity leg)', () => {
    // 15 minutes IS the floor, so it must not warn — a hint that fires on the boundary is a hint
    // that fires on the recommended value, which is how a real advisory becomes noise.
    mount({ intervalValue: 15, intervalUnit: 'm' })
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('stays quiet on the form default (1 hour)', () => {
    mount({ intervalValue: 1, intervalUnit: 'h' })
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('describes the count input by the hint, so a screen reader reaches the reason', () => {
    mount({ intervalValue: 5, intervalUnit: 'm' })
    expect(screen.getByLabelText('Run every — interval count')).toHaveAttribute(
      'aria-describedby',
      'interval-floor-hint',
    )
  })

  it('drops the description again once the cadence is at the floor', () => {
    // The pair to the leg above: a stale `aria-describedby` pointing at a removed node is worse
    // than none, because assistive tech announces a reference it cannot resolve.
    mount({ intervalValue: 15, intervalUnit: 'm' })
    expect(screen.getByLabelText('Run every — interval count')).not.toHaveAttribute('aria-describedby')
  })

  it('does not gate anything — the advisory is the whole guard', () => {
    // The number input must stay editable and unmarked-invalid: `aria-invalid` would tell assistive
    // tech the value is refused, and the API accepts it.
    mount({ intervalValue: 5, intervalUnit: 'm' })
    const input = screen.getByLabelText('Run every — interval count')
    expect(input).not.toHaveAttribute('aria-invalid')
    expect(input).not.toBeDisabled()
  })
})
