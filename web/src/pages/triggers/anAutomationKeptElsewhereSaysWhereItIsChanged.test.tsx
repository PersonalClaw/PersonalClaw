import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ScheduleJob, Trigger as WireTrigger, TriggerReviewCard } from '../../lib/api'
import { ScheduleDetail } from '../schedule/ScheduleDetail'
import { StoreTriggerDetail } from './StoreTriggerDetail'
import { reviewSentence } from './TriggerReview'
import { scheduleToTrigger, storeToTrigger } from './triggerMeta'

// ── An automation an app keeps, or one someone else wrote, says so on its panel ─────────────────
//
// The Triggers page lists an app's rows beside her own: a reminder an app keeps, her row in a shared
// automations file, a teammate's row in that file. The schedule inspector offered every action on
// every one of them: Run now, Dry run, Edit, Delete and the switch on a row someone else wrote, which
// this machine never runs and whose every route refuses it; and Edit on an app's reminder, which the
// app keeps as it is, so Save failed. Each panel now offers only what works, and says why the rest
// is not there, in the words the routes answer with.

const JOB: ScheduleJob = {
  id: 'dayplanner:reminder:bins', name: 'Put the bins out', message: '', enabled: true,
  schedule: 'once at 06:05', cron_expr: '', every_secs: null,
  action: { provider: 'notify', config: { title_template: 'Put the bins out' } },
  last_run_ts: null, last_run_status: '', last_status: 'ok', run_count: 0,
  next_run_ts: null, is_running: false, revision: 'r1',
}

const panel = (job: ScheduleJob, editing = false) =>
  render(<ScheduleDetail job={job} onSaved={vi.fn()} onDeleted={vi.fn()} onChanged={vi.fn()} editing={editing} onEditingChange={vi.fn()} />)

afterEach(() => vi.restoreAllMocks())

describe('a schedule someone else wrote', () => {
  const theirs: ScheduleJob = { ...JOB, id: 'sams-plants', name: 'Water the plants', author: 'sam', read_only: true }

  it('offers no Run now, Dry run, Edit, Delete or switch, and says whose it is', () => {
    panel(theirs)
    for (const name of [/run now/i, /dry run/i, /edit/i, /delete/i]) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.getByText('Enabled')).toBeTruthy()
    const note = screen.getAllByRole('note').find((n) => n.textContent?.includes('wrote this automation'))
    expect(note?.textContent).toContain('sam wrote this automation')
    expect(note?.textContent).toContain('does not run it on this computer')
  })

  it('shows no next run: it runs on its author\'s computer, never on this one', () => {
    panel({ ...theirs, next_run_ts: Date.now() / 1000 + 60 })
    expect(screen.queryByText(/^next /)).toBeNull()
    panel({ ...JOB, next_run_ts: Date.now() / 1000 + 60 })
    expect(screen.getByText(/^next /)).toBeTruthy()
  })

  it('opens the view, not the editor, from a link that asks for the editor', () => {
    panel(theirs, true)
    expect(screen.queryByRole('button', { name: /save/i })).toBeNull()
    expect(screen.getByText('Enabled')).toBeTruthy()
  })
})

describe('a schedule an app keeps', () => {
  const kept: ScheduleJob = { ...JOB, served_by: 'Day Planner' }

  it('offers Run now, Dry run, Delete and the switch, and no Edit, and says where it is changed', () => {
    panel(kept)
    expect(screen.getByRole('button', { name: /run now/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /dry run/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /delete/i })).toBeTruthy()
    expect(screen.getByRole('switch')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /edit/i })).toBeNull()
    expect(screen.getByText(/keeps this automation: it is changed in Day Planner/)).toBeTruthy()
  })

  it('opens the view from a link that asks for the editor', () => {
    panel(kept, true)
    expect(screen.queryByRole('button', { name: /save/i })).toBeNull()
    expect(screen.getByRole('button', { name: /run now/i })).toBeTruthy()
  })

  it('a schedule of her own keeps its Edit (the vacuity floor)', () => {
    panel(JOB)
    expect(screen.getByRole('button', { name: /edit/i })).toBeTruthy()
    expect(screen.queryByText(/keeps this automation/)).toBeNull()
    expect(screen.queryByText(/wrote this automation/)).toBeNull()
  })

  it('carries the app onto the list row, whose menu then offers no Edit', () => {
    expect(scheduleToTrigger(kept).servedBy).toBe('Day Planner')
    expect(scheduleToTrigger(JOB).servedBy).toBe('')
  })
})

describe('a watch an app keeps', () => {
  const watch: WireTrigger = {
    kind: 'store', id: 'store:dayplanner:watch:kitchen', raw_id: 'dayplanner:watch:kitchen',
    name: 'Kitchen PDFs', enabled: true, action: { provider: 'notify', config: {} },
    store_kind: 'file', spec: { paths: ['~/Documents/Home/Kitchen/*.pdf'] }, broken: [],
    served_by: 'Day Planner',
  }

  it('says which app keeps it, and keeps its Run now and Delete', () => {
    render(<StoreTriggerDetail trigger={watch} onChanged={() => {}} onDeleted={() => {}} />)
    expect(screen.getByText(/keeps this automation: it is changed in Day Planner/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /run now/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /delete/i })).toBeTruthy()
    expect(storeToTrigger(watch).servedBy).toBe('Day Planner')
  })
})

describe('a missed run of an automation that reached PersonalClaw late', () => {
  it('says so, rather than that the computer was asleep', () => {
    const card: TriggerReviewCard = {
      trigger_id: 'dayplanner:reminder:call', kind: 'missed', count: 1, latest: Date.now() / 1000 - 3600,
      oldest: Date.now() / 1000 - 3600, reason: '', count_is_floor: false, cause: 'unseen',
      name: 'Call the clinic', open_id: 'schedule:dayplanner:reminder:call',
    }
    const said = reviewSentence(card)
    expect(said).toContain('before this automation reached PersonalClaw')
    expect(said).not.toContain('asleep')
  })
})
