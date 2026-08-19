import { useState } from 'react'
import { ArrowLeft, Plus, FileClock, Play, Trash2, AlertTriangle } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { IconButton } from '../../ui/IconButton'
import { PageTitle } from '../../ui/PageTitle'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { ChipInput, Field, FieldError, TextInput } from '../../ui/forms'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { api, type ResearchReport, type ResearchReportInput } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { notify } from '../../app/appSdk'
import { relPast } from '../schedule/scheduleMeta'
import { fvs } from '../../design/fontWeight'

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

/** One line naming what this report watches and when it last spoke.
 *
 *  `last_status` is rendered separately from `last_run_ts` on purpose: a failed run
 *  deliberately does NOT advance the run stamp (so the next window retries instead of
 *  skipping), which means "ran 3 hours ago" and "errored" are both true at once and
 *  blending them would hide the retry. */
function meta(r: ResearchReport): string {
  const when = r.schedule.cron_expr
    ? `cron ${r.schedule.cron_expr}`
    : r.schedule.every_secs
      ? `every ${Math.round(r.schedule.every_secs / 60)} min`
      : r.schedule.at_ts
        ? 'once'
        : 'no schedule'
  const watches = r.source.tags.length ? `tagged ${r.source.tags.join(', ')}` : 'anything new'
  const ran = r.last_run_ts ? `ran ${relPast(r.last_run_ts)}` : 'never run'
  return `${when} · ${watches} · ${ran}`
}

export function ReportRow({ report, index, onChanged }: {
  report: ResearchReport
  index?: number
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)

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

  return (
    <ListRow index={index}>
      <div className="flex min-w-0 items-start gap-m">
        <span className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-high">
          <FileClock size={16} className="text-on-surface-var" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{report.name}</span>
            {report.last_status === 'error' && (
              <span className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-6 text-[0.75rem] text-on-surface-var"
                title={report.last_error || 'The last run failed'}>
                <AlertTriangle size={12} style={{ color: 'var(--color-warning)' }} aria-hidden />
                last run failed
              </span>
            )}
            <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-[0.75rem] text-on-surface-var">
              {report.citation_policy === 'cite-source-only' ? 'cites new material only' : 'may cite context'}
            </span>
          </div>
          <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]">{meta(report)}</p>
        </div>
        <div className="flex shrink-0 items-center gap-s">
          <Toggle on={report.enabled} disabled={busy}
            label={`${report.name} enabled`}
            onChange={(on) => void act(() => api.updateResearchReport(report.id, { enabled: on }),
              `${report.name} ${on ? 'resumed' : 'paused'}`)} />
          <Button size="xs" variant="secondary" disabled={busy}
            ariaLabel={`Run ${report.name} now`}
            onClick={() => void act(() => api.runResearchReport(report.id), `${report.name} started`)}>
            <Play size={13} /> Run now
          </Button>
          <IconButton icon={Trash2} label={`Delete ${report.name}`} size={36} disabled={busy}
            onClick={() => void act(() => api.deleteResearchReport(report.id), `${report.name} deleted`)} />
        </div>
      </div>
    </ListRow>
  )
}

/** Create a report. Deliberately one screenful: the three scoping decisions (what counts as
 *  new material, what may be searched while writing, what may be cited) are the whole point
 *  of the feature, so they are visible together rather than behind an "advanced" disclosure. */
function CreateForm({ onCreated, onCancel }: { onCreated: () => void; onCancel: () => void }) {
  const [draft, setDraft] = useState<ResearchReportInput>(blank())
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const set = (patch: Partial<ResearchReportInput>) => setDraft((d) => ({ ...d, ...patch }))

  async function save() {
    setSaving(true); setErr('')
    try {
      await api.createResearchReport(draft)
      notify(`${draft.name || 'Report'} created`, 'success')
      onCreated()
    } catch (e) {
      // The server refuses a malformed cron expression rather than storing one that would
      // wedge the runner, so its message is the useful one — show it verbatim.
      setErr(e instanceof Error ? e.message : 'That did not save')
    } finally { setSaving(false) }
  }

  return (
    <div className="flex flex-col gap-m rounded-xl border border-outline-variant/60 bg-surface-container/50 p-l">
      <Field label="Name">
        <TextInput value={draft.name} onChange={(v) => set({ name: v })} placeholder="Weekly contradiction scan" />
      </Field>
      <Field label="Research prompt" hint="What should it look for in the new material?">
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
      <Field label="New material: tags" hint="Which items count as new material. Empty means anything new.">
        <ChipInput values={draft.source.tags} onChange={(tags) => set({ source: { ...draft.source, tags } })}
          placeholder="Add a tag…" />
      </Field>
      <Field label="Searchable context: tags" hint="What may be searched while writing. Leave empty to search nothing beyond the new material.">
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
          disabledReason={!draft.name.trim() ? 'Name it first' : !draft.prompt.trim() ? 'Give it a prompt' : undefined}
          onClick={() => void save()}>Create report</Button>
        <Button size="sm" variant="ghost" disabled={saving} onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

/** The Reports destination inside the Knowledge section (`#/knowledge/reports`).
 *
 *  Scheduled research reports write their findings into the library as ordinary knowledge
 *  items, which is why they live here rather than in Settings: the thing they produce is
 *  knowledge, and the thing they consume is this library's tags. */
export function ReportsPage({ onBack }: { onBack: () => void }) {
  const { data, loading, error, refresh } = useCachedData(CACHE_KEY, () => api.researchReports())
  const [creating, setCreating] = useState(false)
  const reload = () => { invalidateCache(CACHE_KEY); refresh() }
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
              hint="Watch a corner of the library on a schedule" onClick={() => setCreating(true)} />
          </HeaderActions>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {creating && (
            <div className="mb-l">
              <CreateForm onCreated={() => { setCreating(false); reload() }} onCancel={() => setCreating(false)} />
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
              hint="A report watches part of your library on a schedule and writes what it finds back in as a knowledge item."
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
