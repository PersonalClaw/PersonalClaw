import { useId, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { api, type ResearchReportInput } from '../../../lib/api'
import { notify } from '../../../app/appSdk'
import { Button } from '../../../ui/Button'
import { ChipInput, Field, Select, TextInput } from '../../../ui/forms'
import { ChoiceList } from '../../../ui/ChoiceList'
import { FormFooter } from '../../../ui/FormFooter'
import { TOUCH_TARGET } from '../../../ui/Toggle'
import { changed } from '../../knowledge/ReportsPage'
import { BUSY_REASON } from '../../../ui/unavailable'

// ── The report form, in the pane ────────────────────────────────────────────────
//
// Today's report form, in plain words: its name, what it should answer, when it runs and what it
// reads, with the technical fields (a custom schedule, the time zone, what else it may look at, what
// it may cite and how many passes a run takes) under More options. An edit sends only what changed,
// so a schedule this form cannot show is never rewritten by a save that left it alone.

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

/** When a report runs, as the form picks it; `custom` is a schedule it cannot show in words. */
export type When = { every: 'day' | 'weekday' | 'month' | `dow:${number}` | 'custom'; hour: number; cron: string }

/** The form's words for a cron expression: the four shapes it writes, and anything else as custom. */
export function whenOf(cron: string | null | undefined): When {
  const c = (cron ?? '').trim()
  let m = /^0 (\d{1,2}) \* \* \*$/.exec(c)
  if (m) return { every: 'day', hour: Number(m[1]), cron: c }
  m = /^0 (\d{1,2}) \* \* 1-5$/.exec(c)
  if (m) return { every: 'weekday', hour: Number(m[1]), cron: c }
  m = /^0 (\d{1,2}) 1 \* \*$/.exec(c)
  if (m) return { every: 'month', hour: Number(m[1]), cron: c }
  m = /^0 (\d{1,2}) \* \* ([0-6])$/.exec(c)
  if (m) return { every: `dow:${Number(m[2])}`, hour: Number(m[1]), cron: c }
  return { every: 'custom', hour: 8, cron: c }
}

/** The cron expression for a pick; a custom one is its own text. */
export function cronOfWhen(w: When): string {
  if (w.every === 'custom') return w.cron
  if (w.every === 'day') return `0 ${w.hour} * * *`
  if (w.every === 'weekday') return `0 ${w.hour} * * 1-5`
  if (w.every === 'month') return `0 ${w.hour} 1 * *`
  return `0 ${w.hour} * * ${w.every.slice(4)}`
}

export function LibraryReportForm({ initial, reportId, readsIntent, onSaved, onCancel }: {
  initial: ResearchReportInput
  /** The report being edited; none for a new one. */
  reportId?: string
  /** The goal of the intent whose findings it reads, when it reads one: its What it reads is that. */
  readsIntent?: string
  onSaved: () => void
  onCancel: () => void
}) {
  const readsId = useId()
  const [draft, setDraft] = useState<ResearchReportInput>(initial)
  const [when, setWhen] = useState<When>(() => whenOf(initial.schedule.cron_expr))
  const [onlyTagged, setOnlyTagged] = useState(initial.source.tags.length > 0)
  const [more, setMore] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const set = (patch: Partial<ResearchReportInput>) => setDraft((d) => ({ ...d, ...patch }))
  const pickWhen = (patch: Partial<When>) => {
    const next = { ...when, ...patch }
    setWhen(next)
    set({ schedule: { kind: 'cron', cron_expr: cronOfWhen(next) } })
  }

  async function save() {
    setSaving(true); setErr('')
    const out: ResearchReportInput = { ...draft, source: readsIntent ? draft.source: { ...draft.source, tags: onlyTagged ? draft.source.tags: [] } }
    try {
      if (reportId) await api.updateResearchReport(reportId, changed(out, initial))
      else await api.createResearchReport(out)
      notify(reportId ? `Saved “${out.name}”.` : `Scheduled “${out.name}”.`, 'success')
      onSaved()
    } catch (e) {
      // The server refuses a malformed schedule rather than storing one that would wedge the runner,
      // so its own sentence is the useful one.
      setErr(e instanceof Error ? e.message: "Couldn't save the report.")
    } finally { setSaving(false) }
  }

  const why = !draft.name.trim() ? 'Name it first.' : !draft.prompt.trim() ? 'Say what it should answer first.' : ''
  return (
    <div className="flex flex-col gap-m">
      <p data-type="body-s" className="text-on-surface-var">
        A report reads only your library: what your feeds, notes and files brought in. It doesn't search the web. To report on a site, follow it as a feed first.
      </p>
      <Field label="Name">
        <TextInput value={draft.name} onChange={(v) => set({ name: v })} placeholder="Weekly perf digest" required />
      </Field>
      <Field label="What should it answer?" hint="For example: what changed in my sources about Rust?">
        <TextInput value={draft.prompt} onChange={(v) => set({ prompt: v })} required />
      </Field>
      {when.every !== 'custom' && (
        <div className="flex flex-wrap items-center gap-s" data-type="body-s">
          <span className="text-on-surface">Every</span>
          <Select ariaLabel="How often" size="sm" value={when.every} onChange={(every) => pickWhen({ every: every as When['every'] })}
            options={[
              { value: 'day', label: 'day' }, { value: 'weekday', label: 'weekday' },
              ...DAYS.map((d, i) => ({ value: `dow:${i}`, label: d })), { value: 'month', label: 'month on the 1st' },
            ]} />
          <span className="text-on-surface">at</span>
          <Select ariaLabel="What time" size="sm" value={String(when.hour)} onChange={(h) => pickWhen({ hour: Number(h) })}
            options={Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: `${h}:00` }))} />
        </div>
      )}
      {when.every === 'custom' && (
        <p data-type="body-s" className="text-on-surface">Runs on a custom schedule. Change it under More options.</p>
      )}
      <div className="flex flex-col gap-xs">
        <p id={readsId} data-type="label-l" className="text-on-surface">What it reads</p>
        {readsIntent ? (
          <p data-type="body-s" className="text-on-surface">What “{readsIntent}” found since its last run.</p>
        ) : (
          <>
            <ChoiceList labelledBy={readsId} value={onlyTagged ? 'tagged' : 'all'} onChange={(k) => setOnlyTagged(k === 'tagged')}
              options={[{ key: 'all', label: 'Everything new' }, { key: 'tagged', label: 'Only items tagged' }]} />
            {onlyTagged && (
              <ChipInput values={draft.source.tags} onChange={(tags) => set({ source: { ...draft.source, tags } })} placeholder="Add a tag…" ariaLabel="Only items tagged" />
            )}
          </>
        )}
      </div>
      <Button variant="ghost" size="sm" onClick={() => setMore((m) => !m)} ariaExpanded={more} className={`self-start ${TOUCH_TARGET}`}>
        <ChevronDown size={14} aria-hidden className={more ? 'rotate-180' : ''} /> More options
      </Button>
      {more && (
        <div className="flex flex-col gap-m">
          <Field label="Custom schedule" hint="A cron expression, for a schedule the words above can't say. A malformed one is refused rather than kept.">
            <TextInput value={when.every === 'custom' ? when.cron: cronOfWhen(when)} mono
              onChange={(v) => { const w = whenOf(v); setWhen(w.every === 'custom' ? { ...w, cron: v } : w); set({ schedule: { kind: 'cron', cron_expr: v } }) }} />
          </Field>
          <Field label="Time zone" hint="Blank uses this computer's.">
            <TextInput value={draft.tz} onChange={(v) => set({ tz: v })} placeholder="America/Los_Angeles" />
          </Field>
          <Field label="Also look at items tagged" hint="What else in your library it may look at while writing. Empty looks at nothing more.">
            <ChipInput values={draft.context?.tags ?? []} onChange={(tags) => set({ context: tags.length ? { tags, window_secs: 0 } : null })}
              placeholder="Add a tag…" ariaLabel="Also look at items tagged" />
          </Field>
          <Field label="What it may cite">
            <Select ariaLabel="What it may cite" value={draft.citation_policy} onChange={(v) => set({ citation_policy: v as ResearchReportInput['citation_policy'] })}
              options={[{ value: 'cite-source-only', label: 'New items only' }, { value: 'allow-citing-context', label: 'Also what it looked at' }]} />
          </Field>
          <Field label="Most passes per run">
            <Select ariaLabel="Most passes per run" value={String(draft.iteration_cap)} onChange={(v) => set({ iteration_cap: Number(v) })}
              options={[1, 2, 3, 4, 5, 6, 8, 10].map((x) => ({ value: String(x), label: String(x) }))} />
          </Field>
        </div>
      )}
      <FormFooter error={err}>
        <Button variant="ghost" onClick={onCancel} disabled={saving} disabledReason={BUSY_REASON}>Cancel</Button>
        <Button onClick={() => void save()} loading={saving} loadingLabel="Saving…" disabled={!!why} disabledReason={why || undefined}>
          {reportId ? 'Save report' : 'Create report'}
        </Button>
      </FormFooter>
    </div>
  )
}
