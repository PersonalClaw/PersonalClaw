import { useState } from 'react'
import { ArrowLeft, Plus, FileClock, Play, Trash2, AlertTriangle, Pencil } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { IconButton } from '../../ui/IconButton'
import { PageTitle } from '../../ui/PageTitle'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { TextLink } from '../../ui/TextLink'
import { ChipInput, Field, FieldError, TextInput } from '../../ui/forms'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { api, type ResearchReport, type ResearchReportInput } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { notify } from '../../app/appSdk'
import { relFuture, relPast } from '../schedule/scheduleMeta'
import { fvs } from '../../design/fontWeight'
import { BUSY_REASON } from '../../ui/unavailable'

const CACHE_KEY = 'knowledge:reports'

/** A blank definition. `cite-source-only` is the default because the narrower policy is the
 *  one a reader can trust without knowing the report's configuration: every marker points at
 *  something the report was actually asked to monitor. */
function blank(): ResearchReportInput {
  return {
    name: '',
    prompt: '',
    schedule: { kind: 'cron', cron_expr: '0 8 * * *' },
    tz: '',
    source: { tags: [], window_secs: 0 },
    context: null,
    citation_policy: 'cite-source-only',
    iteration_cap: 3,
    enabled: true,
  }
}

/** The editable part of a report, for its edit form. */
function editable(r: ResearchReport): ResearchReportInput {
  return {
    name: r.name,
    prompt: r.prompt,
    schedule: r.schedule,
    tz: r.tz,
    source: r.source,
    context: r.context,
    citation_policy: r.citation_policy,
    iteration_cap: r.iteration_cap,
    enabled: r.enabled,
  }
}

/** When the report runs, in words: its schedule as the Triggers page says it, the zone it runs
 *  in, and its next run in that zone. The server words it (`schedule_shown`), off the same trigger
 *  row that fires it, so this page and the Triggers page cannot tell two different times. */
function when(r: ResearchReport): string {
  const { words, timezone, next_run_at: next } = r.schedule_shown
  if (!words) return 'No schedule: it runs when you press Run now'
  const zone = timezone ? ` (${timezone})` : ''
  if (!r.enabled) return `${words}${zone} · paused, so it does not run`
  return next ? `${words}${zone} · next run ${nextRun(next, timezone)}` : `${words}${zone}`
}

/** A next run as a date and time in the zone the report runs in ("Mon, Oct 5, 8:00 AM EDT"),
 *  and how far off it is. A zone this browser does not know reads in the browser's own. */
function nextRun(iso: string, timezone: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  const style: Intl.DateTimeFormatOptions = {
    weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  }
  let said: string
  try { said = at.toLocaleString(undefined, { ...style, timeZone: timezone || undefined }) }
  catch { said = at.toLocaleString(undefined, style) }
  const off = relFuture(iso)
  return off ? `${said}, ${off}` : said
}

/** When the report last finished a run, and what that run found — in the run's own sentence
 *  ("Found no new material in your knowledge since its previous run."), so a run that read
 *  nothing new says so rather than passing as one that ran.
 *
 *  `last_status` is rendered separately (the "last run failed" chip) on purpose: a failed run
 *  deliberately does NOT advance the run stamp (so the next run reads the same material again),
 *  which means "last run 3h ago" and "it failed" are both true at once, and blending them would
 *  hide the retry. */
function lastRun(r: ResearchReport): string {
  if (!r.last_run_ts) return 'Never run yet.'
  const ran = `Last run ${relPast(r.last_run_ts)}`
  return r.last_result ? `${ran} · ${r.last_result}` : ran
}

export function ReportRow({ report, index, onChanged }: {
  report: ResearchReport
  index?: number
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true)
    try {
      await fn()
      notify(ok, 'success')
      onChanged()
    } catch (e) {
      // A 409 is not a failure: a scheduled fire already holds the lease, and the manual run
      // deliberately does not start a second one.
      notify(e instanceof Error ? e.message : 'That did not go through', 'error')
    } finally { setBusy(false) }
  }

  /** The run is over when its answer comes back, so the toast says what it found, in the run's
   *  own words: a finding written, nothing new, or why it did not run. "Started" said nothing. */
  async function runNow() {
    setBusy(true)
    try {
      const run = await api.runResearchReport(report.id)
      if (!run.ok) notify(`${report.name}: ${run.refused || run.result || 'the run did not go through'}`, 'error')
      else notify(`${report.name}: ${run.result || 'the run finished'}`, run.outcome === 'wrote' ? 'success' : 'info')
      onChanged()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'That did not go through', 'error')
    } finally { setBusy(false) }
  }

  return (
    <ListRow index={index}>
      {/* One column inside the row, so the edit form opens BELOW what it edits: the row lays
          its children out side by side. */}
      <div className="flex min-w-0 flex-1 flex-col items-start">
      <div className="flex min-w-0 items-start gap-m">
        <span className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-high">
          <FileClock size={16} className="text-on-surface-var" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-s">
            <span data-type="title-m" className="min-w-0 truncate text-on-surface" style={fvs(500)}>{report.name}</span>
            {report.last_status === 'error' && (
              <span data-type="caption" className="inline-flex items-center gap-xs rounded-pill bg-surface-high px-s h-6 text-on-surface-var"
                title={report.last_error || 'The last run failed'}>
                <AlertTriangle size={12} style={{ color: 'var(--color-warning)' }} aria-hidden />
                last run failed
              </span>
            )}
            <span data-type="caption" className="rounded-pill bg-surface-high px-s h-6 inline-flex items-center text-on-surface-var">
              {report.citation_policy === 'cite-source-only' ? 'cites new material only' : 'may cite context'}
            </span>
          </div>
          <p data-type="body-s" className="mt-0.5 text-on-surface-var">{when(report)}</p>
          <p data-type="body-s" className="text-on-surface-low">{report.sources_shown}</p>
          <p data-type="body-s" className="text-on-surface-low">{lastRun(report)}</p>
        </div>
        <div className="flex shrink-0 items-center gap-s">
          <Toggle on={report.enabled} disabled={busy}
            label={`${report.name} enabled`}
            onChange={(on) => void act(() => api.updateResearchReport(report.id, { enabled: on }),
              `${report.name} ${on ? 'resumed' : 'paused'}`)} />
          <Button size="xs" variant="secondary" disabled={busy} disabledReason={BUSY_REASON}
            ariaLabel={`Run ${report.name} now`}
            onClick={() => void runNow()}>
            <Play size={13} /> Run now
          </Button>
          <IconButton icon={Pencil} label={`Edit ${report.name}`} size={36} active={editing}
            onClick={() => setEditing(true)} />
          {/* `busy` is the row's own request in flight — `loading`, not `disabled`. The Toggle and
              the Run-now Button beside it keep `disabled`: those are native-disabled tiers whose
              in-flight treatment is their own (`Button` already has `loading`, and using it here
              would change what the row looks like mid-request — a separate call). */}
          <IconButton icon={Trash2} label={`Delete ${report.name}`} size={36} loading={busy} tone="danger"
            onClick={() => void act(() => api.deleteResearchReport(report.id), `${report.name} deleted`)} />
        </div>
      </div>
      {editing && (
        <div className="mt-m self-stretch">
          <ReportForm initial={editable(report)} reportId={report.id}
            onSaved={() => { setEditing(false); onChanged() }} onCancel={() => setEditing(false)} />
        </div>
      )}
      </div>
    </ListRow>
  )
}

/** What an edit changed: only the fields that differ from the report as it was opened, so a save
 *  that touched the prompt sends no schedule, and a schedule the form cannot show (an interval or
 *  a one-off set elsewhere) is never rewritten by a save that left it alone. */
function changed(draft: ResearchReportInput, initial: ResearchReportInput): Partial<ResearchReportInput> {
  const out: Partial<ResearchReportInput> = {}
  for (const key of Object.keys(draft) as (keyof ResearchReportInput)[]) {
    if (JSON.stringify(draft[key]) !== JSON.stringify(initial[key])) Object.assign(out, { [key]: draft[key] })
  }
  return out
}

/** Create or edit a report. Deliberately one screenful: the three scoping decisions (what counts
 *  as new material, what may be searched while writing, what may be cited) are the whole point
 *  of the feature, so they are visible together rather than behind an "advanced" disclosure. And
 *  it says first what a report can read, because a question it cannot answer from your knowledge
 *  ("what shipped this week, with links") reads like one it can. */
function ReportForm({ initial, reportId, onSaved, onCancel }: {
  initial: ResearchReportInput
  reportId?: string
  onSaved: () => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState<ResearchReportInput>(initial)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const set = (patch: Partial<ResearchReportInput>) => setDraft((d) => ({ ...d, ...patch }))

  async function save() {
    setSaving(true); setErr('')
    try {
      if (reportId) {
        await api.updateResearchReport(reportId, changed(draft, initial))
        notify(`${draft.name || 'Report'} saved`, 'success')
      } else {
        await api.createResearchReport(draft)
        notify(`${draft.name || 'Report'} created`, 'success')
      }
      onSaved()
    } catch (e) {
      // The server refuses a malformed cron expression rather than storing one that would
      // wedge the runner, so its message is the useful one — show it verbatim.
      setErr(e instanceof Error ? e.message : 'That did not save')
    } finally { setSaving(false) }
  }

  return (
    <div className="flex flex-col gap-m rounded-xl border border-outline-variant/60 bg-surface-container/50 p-l">
      <p data-type="body-s" className="text-on-surface-var">
        A report reads only your knowledge: what your sources, notes and imports have brought in. It does
        not search the web. To report on a website, <TextLink href="#/knowledge/sources" ink="emphasis">watch
        it as a source</TextLink> first, and the report reads what it brings in.
      </p>
      <Field label="Name">
        <TextInput value={draft.name} onChange={(v) => set({ name: v })} placeholder="Weekly contradiction scan" />
      </Field>
      <Field label="Research prompt" hint="What should it look for in what is new in your knowledge?">
        <TextInput value={draft.prompt} onChange={(v) => set({ prompt: v })}
          placeholder="Find claims that contradict what we already believe, and name both sides." />
      </Field>
      <Field label="Schedule (cron)" hint="Evaluated in the timezone below; a malformed expression is refused rather than stored.">
        <TextInput value={draft.schedule.cron_expr ?? ''}
          onChange={(v) => set({ schedule: { kind: 'cron', cron_expr: v } })} placeholder="0 8 * * *" />
      </Field>
      <Field label="Timezone" hint="Blank uses this machine's timezone.">
        <TextInput value={draft.tz} onChange={(v) => set({ tz: v })} placeholder="America/Los_Angeles" />
      </Field>
      <Field label="New material: tags" hint="Which items in your knowledge count as new material. Empty means anything new in your knowledge.">
        <ChipInput values={draft.source.tags} onChange={(tags) => set({ source: { ...draft.source, tags } })}
          placeholder="Add a tag…" />
      </Field>
      <Field label="Searchable context: tags" hint="What else in your knowledge it may look at while writing. Leave empty to look at nothing beyond the new material.">
        <ChipInput values={draft.context?.tags ?? []}
          onChange={(tags) => set({ context: tags.length ? { tags, window_secs: 0 } : null })}
          placeholder="Add a tag…" />
      </Field>
      <Field label="Citations" hint="Whether the writing may cite context as well as new material.">
        <div className="flex flex-wrap gap-s">
          {(['cite-source-only', 'allow-citing-context'] as const).map((p) => (
            <Button key={p} size="xs" variant={draft.citation_policy === p ? 'primary' : 'secondary'}
              onClick={() => set({ citation_policy: p })}>
              {p === 'cite-source-only' ? 'New material only' : 'Also allow context'}
            </Button>
          ))}
        </div>
      </Field>
      <Field label="Iteration cap" hint="How many model passes one run may take.">
        <TextInput value={String(draft.iteration_cap)}
          onChange={(v) => set({ iteration_cap: Math.max(1, Number.parseInt(v || '1', 10) || 1) })} />
      </Field>
      {err && <FieldError>{err}</FieldError>}
      <div className="flex items-center gap-s">
        <Button size="sm" disabled={saving || !draft.name.trim() || !draft.prompt.trim()}
          disabledReason={!draft.name.trim() ? 'Name it first' : !draft.prompt.trim() ? 'Give it a prompt' : BUSY_REASON}
          onClick={() => void save()}>{reportId ? 'Save report' : 'Create report'}</Button>
        <Button size="sm" variant="ghost" disabled={saving} disabledReason={BUSY_REASON} onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

/** The Reports destination inside the Knowledge section (`#/knowledge/reports`).
 *
 *  Scheduled research reports write their findings into the library as ordinary knowledge
 *  items, which is why they live here rather than in Settings: the thing they produce is
 *  knowledge, and the thing they consume is this library — never the web. */
export function ReportsPage({ onBack }: { onBack: () => void }) {
  const { data, loading, error, refresh } = useQuery(CACHE_KEY, () => api.researchReports())
  const [creating, setCreating] = useState(false)
  const reload = () => { invalidateKeys(CACHE_KEY); refresh() }
  const reports = data?.reports

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={<div className="flex items-center gap-s">
          <IconButton icon={ArrowLeft} label="Back to knowledge" size={40} onClick={onBack} />
          <PageTitle>Scheduled reports</PageTitle>
        </div>}
        right={
          <HeaderActions>
            <HeaderControl icon={Plus} label="New report" variant="primary" priority="primary"
              hint="Watch a corner of your knowledge on a schedule" onClick={() => setCreating(true)} />
          </HeaderActions>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {creating && (
            <div className="mb-l">
              <ReportForm initial={blank()} onSaved={() => { setCreating(false); reload() }} onCancel={() => setCreating(false)} />
            </div>
          )}
          {/* A failed fetch and an empty list are different facts; saying "no reports" when
              the truth is "we could not load them" is the worse of the two. */}
          {reports === undefined && error ? (
            <LoadError what="scheduled reports" error={error} onRetry={reload} />
          ) : reports === undefined || loading ? (
            <ListSkeleton rows={3} what="scheduled reports" />
          ) : reports.length === 0 ? (
            <EmptyState icon={FileClock} title="No scheduled reports"
              hint="A report reads what is new in your knowledge on a schedule and writes what it finds back in as a knowledge item. It does not search the web."
              action={{ label: 'New report', onClick: () => setCreating(true), icon: Plus }} />
          ) : (
            <div className="flex flex-col gap-m">
              {reports.map((r, i) => <ReportRow key={r.id} report={r} index={i} onChanged={reload} />)}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
