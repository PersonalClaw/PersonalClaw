import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { storeToTrigger } from './triggerMeta'
import { StoreTriggerDetail } from './StoreTriggerDetail'
import type { Trigger as WireTrigger } from '../../lib/api'

// Foreign-trigger read-only rendering (TEAM-SHARED-ENTITIES §2.2 — TSE-4).
//
// A shared trigger store legitimately contains other people's automations. This harness never arms
// or fires them (proved server-side in tests/test_triggers_ownership.py), so the page must not offer
// a control that claims otherwise. The property under test is ABSENCE of the mutation affordances,
// not their `disabled` attribute: a greyed-out Delete still asserts that deleting is a thing this
// surface could do to the row.
//
// `read_only` is read from the wire, never re-derived from `author`: the backend computes it with the
// same predicate that decides what the scheduler arms, and a second opinion in the UI would drift.

vi.mock('../schedule/ScheduleDetail', () => ({
  RunHistory: () => null,
}))

const row = (over: Partial<WireTrigger> = {}): WireTrigger => ({
  kind: 'store', id: 'store:file:notes', raw_id: 'file:notes',
  name: 'Summarize notes', enabled: true, action: { provider: 'run-prompt', config: {} },
  store_kind: 'file', spec: { paths: ['~/notes/**'] }, broken: [],
  ...over,
})

describe('storeToTrigger attribution', () => {
  it('carries the author and the server read-only verdict onto the view-model', () => {
    const t = storeToTrigger(row({ author: 'alice', read_only: true }))
    expect(t.author).toBe('alice')
    expect(t.readOnly).toBe(true)
  })

  it('defaults to writable when the server sends no verdict — a single-user install is unchanged', () => {
    const t = storeToTrigger(row())
    expect(t.readOnly).toBe(false)
    expect(t.author).toBeUndefined()
  })

  it('does not infer read-only from an author string alone', () => {
    // An owner-authored row carries `author` too. Deriving the verdict from the presence of a name
    // would make every attributed row read-only the moment a username is configured.
    const t = storeToTrigger(row({ author: 'keyur', read_only: false }))
    expect(t.readOnly).toBe(false)
  })
})

describe('StoreTriggerDetail on a foreign automation', () => {
  it('offers no Run now, Dry run or Delete, and no enable toggle', () => {
    render(
      <StoreTriggerDetail
        trigger={row({ author: 'alice', read_only: true })}
        onChanged={() => {}}
        onDeleted={() => {}}
      />,
    )
    expect(screen.queryByRole('button', { name: /run now/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /dry run/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /delete/i })).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()
    // …and says WHOSE it is, so the missing controls read as an explanation rather than a bug.
    expect(screen.getByText(/alice/)).toBeTruthy()
    expect(screen.getByText(/never runs it/i)).toBeTruthy()
  })

  it('still shows the enabled STATE as text — the row is informational, not blank', () => {
    render(
      <StoreTriggerDetail
        trigger={row({ author: 'alice', read_only: true })}
        onChanged={() => {}}
        onDeleted={() => {}}
      />,
    )
    expect(screen.getByText('Enabled')).toBeTruthy()
  })

  it('keeps every control for the owner’s own automation (the vacuity floor)', () => {
    render(
      <StoreTriggerDetail trigger={row()} onChanged={() => {}} onDeleted={() => {}} />,
    )
    expect(screen.getByRole('button', { name: /run now/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /dry run/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /delete/i })).toBeTruthy()
    expect(screen.queryByText(/never runs it/i)).toBeNull()
  })
})
