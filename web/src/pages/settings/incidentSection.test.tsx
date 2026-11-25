import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

// ── Settings › Guardrails › Incident kill switch: why it starts, and what it held back ──────────
//
// 🔴 The switch started incident mode with the literal "Activated from Settings" every time, and
// turned it off without a word about what it had stopped. A hook or event fire that came while it
// was on was gone for good, and nothing said so or offered to run it. Now turning it on asks why and
// needs an answer; turning it off first shows what was held back and takes a note; once resumed,
// each dropped hook or event fire can be run now; and every incident is listed under Past incidents.

const incident = vi.fn()
const incidentOn = vi.fn()
const incidentResume = vi.fn()
const incidents = vi.fn()
const incidentNote = vi.fn()
const runStoreTrigger = vi.fn()
const testHook = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    incident: () => incident(),
    incidentOn: (reason: string) => incidentOn(reason),
    incidentResume: () => incidentResume(),
    incidents: (cursor?: string) => incidents(cursor),
    incidentNote: (id: string, note: string) => incidentNote(id, note),
    runStoreTrigger: (id: string) => runStoreTrigger(id),
    testHook: (id: string, context?: string) => testHook(id, context),
  },
}))

const { IncidentSection } = await import('./GuardrailsPanel')
const { DialogHost } = await import('../../ui/dialog/DialogHost')
const { closeDialog, subscribeDialogs } = await import('../../ui/dialog/dialogStore')
const { resetDataStore } = await import('../../lib/data/store')

const OFF = { active: false, reason: '', started_at: '', activated_by: '', app: '', suppressed: {} }
const HELD = {
  runs_skipped: { count: 3, refs: ['trigger:clock:nightly'] },
  events_dropped: { count: 1, refs: ['trigger:event:new-mail'] },
  hooks_dropped: { count: 2, refs: ['trigger:lifecycle:on-end'] },
  loop_cycles_skipped: { count: 0, refs: [] },
  spawns_refused: { count: 1, refs: ['subagent:a1b2c3d4'] },
}
const ON = {
  active: true, reason: 'A run sent mail it should not have', started_at: '2026-11-25T08:00:00+00:00',
  activated_by: 'owner', app: '', suppressed: HELD,
}
const LINE = {
  id: 'inc_01jdv5x0000000000000000000', v: 1, type: 'incident',
  started_at: '2026-11-25T08:00:00+00:00', ended_at: '2026-11-25T08:42:10+00:00',
  reason: 'A run sent mail it should not have', activated_by: 'app', app: 'log-watch', resumed_by: 'owner',
  suppressed: HELD, note: '',
}

const flush = () => act(async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0)) })

function show() {
  render(<><IncidentSection /><DialogHost /></>)
}

beforeEach(() => {
  resetDataStore(); sessionStorage.clear()
  vi.clearAllMocks()
  incident.mockResolvedValue(OFF)
  incidents.mockResolvedValue({ items: [], next_cursor: null })
})

afterEach(() => {
  let open: { id: number }[] = []
  subscribeDialogs((list) => { open = list as typeof open })()
  act(() => { for (const d of open) closeDialog(d.id, null) })
  cleanup()
})

describe('Settings › Guardrails › Incident kill switch', () => {
  it('asks why and refuses an empty reason', async () => {
    incidentOn.mockResolvedValue({ ...ON, suppressed: {} })
    show()
    fireEvent.click(await screen.findByRole('switch', { name: 'Incident mode' }))
    const dialog = await screen.findByRole('dialog', { name: 'Why are you stopping unattended work?' })
    await flush()
    const stop = within(dialog).getByRole('button', { name: 'Stop unattended work' })
    expect(stop.getAttribute('aria-disabled')).toBe('true')
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: '    ' } })
    fireEvent.click(stop)
    await flush()
    expect(incidentOn, 'a reason of spaces started it').not.toHaveBeenCalled()

    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: '  A run sent mail it should not have ' } })
    expect(stop.getAttribute('aria-disabled')).not.toBe('true')
    fireEvent.click(stop)
    await flush()
    expect(incidentOn).toHaveBeenCalledWith('A run sent mail it should not have')
    expect(await screen.findByText('Incident mode is ACTIVE')).toBeTruthy()
    expect(screen.getByText(/A run sent mail it should not have\. Started by you\./)).toBeTruthy()
  })

  it('cancelling the ask leaves it off and sends nothing', async () => {
    show()
    fireEvent.click(await screen.findByRole('switch', { name: 'Incident mode' }))
    const dialog = await screen.findByRole('dialog', { name: 'Why are you stopping unattended work?' })
    await flush()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await flush()
    expect(incidentOn).not.toHaveBeenCalled()
    expect(screen.queryByText('Incident mode is ACTIVE')).toBeNull()
  })

  it('shows what was held back with Run now for each dropped fire', async () => {
    incident.mockResolvedValue(ON)
    incidentResume.mockResolvedValue({ active: false, incident: LINE, recorded: true, why: '' })
    runStoreTrigger.mockResolvedValue({ ok: true, status: 'launched' })
    testHook.mockResolvedValue({ ok: true, result: { stdout: '', stderr: '', exit_code: 0, error: '', duration_ms: 4 } })
    show()
    fireEvent.click(await screen.findByRole('switch', { name: 'Incident mode' }))
    const review = await screen.findByRole('region', { name: 'Before you resume' })
    expect(review.textContent).toContain(
      'During this incident: 3 runs skipped (a recurring one runs at its next time); 2 hook fires and 1 event dropped; 1 subagent start refused.',
    )
    expect(incidentResume, 'it showed the review before resuming').not.toHaveBeenCalled()

    fireEvent.click(within(review).getByRole('button', { name: 'Resume unattended work' }))
    await flush()
    expect(incidentResume).toHaveBeenCalledTimes(1)
    const done = await screen.findByRole('region', { name: 'Unattended work runs again' })
    expect(within(done).getByRole('status').textContent).toBe('Resumed. This incident is kept under Past incidents.')
    const fires = within(done).getByRole('list', { name: 'Dropped fires' })
    const hook = within(fires).getByRole('button', { name: 'Run Hook on-end now, without the event that was dropped' })
    const mail = within(fires).getByRole('button', { name: 'Run Automation event:new-mail now, without the event that was dropped' })
    expect(within(fires).getAllByRole('listitem')).toHaveLength(2)

    fireEvent.click(mail)
    await flush()
    expect(runStoreTrigger).toHaveBeenCalledWith('event:new-mail')
    expect(within(fires).getByText('Started: launched.')).toBeTruthy()
    fireEvent.click(hook)
    await flush()
    expect(testHook).toHaveBeenCalledWith('on-end', 'run again after an incident')
    expect(within(fires).getByText('Ran.')).toBeTruthy()
  })

  it('adds a note', async () => {
    incident.mockResolvedValue(ON)
    incidentResume.mockResolvedValue({ active: false, incident: LINE, recorded: true, why: '' })
    incidentNote.mockResolvedValue({ id: 'note_1', incident: LINE.id, note: 'x', at: '' })
    show()
    fireEvent.click(await screen.findByRole('switch', { name: 'Incident mode' }))
    const review = await screen.findByRole('region', { name: 'Before you resume' })
    fireEvent.change(within(review).getByRole('textbox', { name: 'What happened?' }), {
      target: { value: '  The digest filter was wrong; I mailed the two people.  ' },
    })
    fireEvent.click(within(review).getByRole('button', { name: 'Resume unattended work' }))
    await flush()
    expect(incidentNote).toHaveBeenCalledWith(LINE.id, 'The digest filter was wrong; I mailed the two people.')

    // And on a past incident, from its own row.
    incidents.mockResolvedValue({ items: [LINE], next_cursor: null })
    cleanup(); resetDataStore()
    incident.mockResolvedValue(OFF)
    show()
    const list = await screen.findByRole('list', { name: 'Past incidents' })
    fireEvent.click(within(list).getByRole('button', { name: /^Add a note to the incident of / }))
    const dialog = await screen.findByRole('dialog', { name: 'What happened?' })
    await flush()
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'Found the cause.' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add the note' }))
    await flush()
    expect(incidentNote).toHaveBeenLastCalledWith(LINE.id, 'Found the cause.')
    await waitFor(() => expect(incidents.mock.calls.length).toBeGreaterThan(1))
  })

  it('lists past incidents with their counts and notes', async () => {
    incidents.mockResolvedValueOnce({ items: [{ ...LINE, note: 'Fixed the filter.' }], next_cursor: LINE.id })
    incidents.mockResolvedValueOnce({
      items: [{ ...LINE, id: 'inc_older', reason: 'Testing the switch', activated_by: 'cli', resumed_by: 'cli', suppressed: {} }],
      next_cursor: null,
    })
    show()
    const list = await screen.findByRole('list', { name: 'Past incidents' })
    const [first] = within(list).getAllByRole('listitem')
    expect(first.textContent).toContain('A run sent mail it should not have')
    expect(first.textContent).toContain('42m 10s')
    expect(first.textContent).toContain('Started by the app log-watch · ended by you')
    expect(first.textContent).toContain('3 runs skipped (a recurring one runs at its next time); 2 hook fires and 1 event dropped; 1 subagent start refused.')
    expect(first.textContent).toContain('Note: Fixed the filter.')

    fireEvent.click(screen.getByRole('button', { name: 'Show older incidents' }))
    await flush()
    expect(incidents).toHaveBeenLastCalledWith(LINE.id)
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[1].textContent).toContain('Started by the command line · ended by the command line')
    expect(rows[1].textContent).toContain('Nothing was held back.')
    expect(screen.queryByRole('button', { name: 'Show older incidents' })).toBeNull()
  })

  it('says when the incident could not be recorded', async () => {
    incident.mockResolvedValue(ON)
    incidentResume.mockResolvedValue({
      active: false, incident: LINE, recorded: false,
      why: 'The incident history could not be written: No space left on device.',
    })
    show()
    fireEvent.click(await screen.findByRole('switch', { name: 'Incident mode' }))
    const review = await screen.findByRole('region', { name: 'Before you resume' })
    fireEvent.click(within(review).getByRole('button', { name: 'Resume unattended work' }))
    await flush()
    const done = await screen.findByRole('region', { name: 'Unattended work runs again' })
    expect(within(done).getByRole('status').textContent).toBe(
      'PersonalClaw resumed, but could not record this incident: The incident history could not be written: No space left on device.',
    )
    expect(screen.getByText('Off — automation runs normally.')).toBeTruthy()
  })

  it('keeping it on resumes nothing', async () => {
    incident.mockResolvedValue(ON)
    show()
    fireEvent.click(await screen.findByRole('switch', { name: 'Incident mode' }))
    const review = await screen.findByRole('region', { name: 'Before you resume' })
    fireEvent.click(within(review).getByRole('button', { name: 'Keep it on' }))
    await flush()
    expect(incidentResume).not.toHaveBeenCalled()
    expect(screen.queryByRole('region', { name: 'Before you resume' })).toBeNull()
    expect(screen.getByText('Incident mode is ACTIVE')).toBeTruthy()
  })
})
