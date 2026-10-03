import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { AutonomyLadder, AutonomyReversal, AutonomyType } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { UndoList } from './GuardrailsPanel'

// ── The undo list says only what an undo changes ──────────────────────────────────────────────────
//
// Undoing an automatic action takes it back and demotes its type to its floor
// (`guardrails.ladder.reverse_action`). The list said every undo "also stops <type> from doing this
// on its own", and its toast that the type "will ask again from now on". The core actions that keep
// an undo sit AT their floor already (a task an automation files runs on its own by declaration, and
// keeps its undo only because nobody watches its runs), so for every undo they offer, both
// sentences were false: the demotion changed nothing. A rung change is said where there is one.
//
// And an empty list says nothing is waiting, never that nothing has run "with undo": it holds
// PENDING records only, so an undo already taken back empties it while the security log still
// records the run.

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      autonomyUndo: vi.fn(() => Promise.resolve({ ok: true, code: '', action_type: '', demoted: true })),
    },
  }
})
vi.mock('../../app/appSdk', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../app/appSdk')>()
  return { ...real, notify: vi.fn() }
})

const RUNGS = ['draft_only', 'one_tap', 'auto_with_undo', 'autonomous']

function type(key: string, floor: string, granted: string, resolved: string): AutonomyType {
  return {
    key, floor, ceiling: 'autonomous', leaves_machine: false, providers: [],
    resolved_rung: resolved, granted_rung: granted, held_by_incident: false, authority: '',
    granted_at: '', evidence_window: '', demotions: [], eligible: false, next_rung: '', record: '',
    clean_approvals: 0, rejections: 0, observed_days: 0, cooldown_until: '',
  }
}

function record(id: string, action_type: string, reversed_at = ''): AutonomyReversal {
  return { id, action_type, rung: 'auto_with_undo', label: action_type, created_at: '2026-10-02T09:15:00+00:00', reversed_at }
}

function ladder(reversals: AutonomyReversal[]): AutonomyLadder {
  return {
    rungs: RUNGS,
    rung_meta: [
      { key: 'draft_only', label: 'drafts only', hint: '' },
      { key: 'one_tap', label: 'asks first', hint: '' },
      { key: 'auto_with_undo', label: 'runs with undo', hint: '' },
      { key: 'autonomous', label: 'runs on its own', hint: '' },
    ],
    incident_active: false,
    types: [
      // Declared to run on its own, never promoted: it is at its floor.
      type('action.create_task', 'autonomous', 'autonomous', 'auto_with_undo'),
      // Promoted from "asks first": an undo's demotion takes that promotion back.
      type('app:acme.file-task', 'one_tap', 'auto_with_undo', 'auto_with_undo'),
    ],
    reversals,
  }
}

beforeEach(() => vi.mocked(notify).mockClear())

describe('the undo list', () => {
  it('says nothing is waiting when nothing is, and claims nothing about what ran', () => {
    render(<UndoList ladder={ladder([record('rev_0000000000000001', 'action.create_task', '2026-10-02T09:20:00+00:00')])} onChange={() => {}} />)
    expect(screen.getByText('Nothing is waiting to be undone.')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/has run/)
  })

  it('promises no rung change for an action that already runs at its floor', async () => {
    render(<UndoList ladder={ladder([record('rev_0000000000000002', 'action.create_task')])} onChange={() => {}} />)
    const text = document.body.textContent ?? ''
    expect(text).toContain('Ran 2026-10-02 09:15.')
    expect(text).not.toMatch(/on its own|ask again|back so it/)

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect(vi.mocked(notify).mock.calls[0]).toEqual(['Undone.', 'success'])
  })

  it('says where the undo puts an action it demotes', async () => {
    render(<UndoList ladder={ladder([record('rev_0000000000000003', 'app:acme.file-task')])} onChange={() => {}} />)
    expect(document.body.textContent).toContain('Undoing it also puts app:acme.file-task back so it asks first.')

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(notify).toHaveBeenCalled())
    expect(vi.mocked(notify).mock.calls[0]).toEqual(['Undone. app:acme.file-task is back at asks first.', 'success'])
  })
})
