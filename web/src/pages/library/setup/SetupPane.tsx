import { useId, useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { api, type ResearchReport, type ResearchReportInput, type WatchedSource } from '../../../lib/api'
import { invalidateKeys, useQuery } from '../../../lib/data'
import { notify } from '../../../app/appSdk'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import { ChoiceList } from '../../../ui/ChoiceList'
import { Field, Select, TextInput } from '../../../ui/forms'
import { FormFooter } from '../../../ui/FormFooter'
import { ListSkeleton, LoadError } from '../../../ui/ListScaffold'
import { TOUCH_TARGET } from '../../../ui/Toggle'
import { blank, editable } from './reportDraft'
import { LibraryReportForm } from './LibraryReportForm'
import { SourceCreatePage } from '../../knowledge/SourceCreatePage'
import { INTERVAL_CHOICES, RAW_ENRICHMENT, fmtInterval } from '../../knowledge/sourceMeta'
import { IntentForm } from './IntentForm'
import { SetupPanel, type SetupGroup, type SetupNav } from './SetupPanel'
import { BUSY_REASON } from '../../../ui/unavailable'

// ── `?setup=`: the set-up panel and its forms, in the pane ──────────────────────────
//
//   all | intents | reports | feeds   the panel (the last three scroll that group to the top)
//   intent:new | intent:<id>          the intent form
//   report:new | report:new:<intent> | report:<id>   the report form (a new one from an intent reads its findings)
//   source:new | source:<id>          following a feed or folder, or changing one

/** What a `?setup=` value opens. */
export type SetupView =
  | { view: 'panel'; focus?: SetupGroup }
  | { view: 'intent'; id: string }
  | { view: 'report'; id: string; intent?: string }
  | { view: 'source'; id: string }

/** The view a `?setup=` value names; `null` for none, and the panel for one it doesn't know. */
export function setupViewOf(raw: string | undefined): SetupView | null {
  if (!raw) return null
  const [kind, id = '', more = ''] = raw.split(':')
  if (kind === 'intents' || kind === 'reports' || kind === 'feeds') return { view: 'panel', focus: kind }
  if (kind === 'intent' && id) return { view: 'intent', id }
  if (kind === 'report' && id) return { view: 'report', id, ...(id === 'new' && more ? { intent: more } : {}) }
  if (kind === 'source' && id) return { view: 'source', id }
  return { view: 'panel' }
}

export function SetupPane({ setup, nav, onClose, detail }: {
  /** The address's `setup` value. */
  setup: string
  nav: SetupNav
  /** Close the panel (and every form in it). */
  onClose: () => void
  /** The Power user view's detail lines on the panel's rows. */
  detail?: boolean
}) {
  const v = setupViewOf(setup)
  const back = () => nav.form('all')
  if (!v) return null
  if (v.view === 'panel') return <SetupPanel focus={v.focus} nav={nav} onClose={onClose} detail={detail} />
  if (v.view === 'intent') return <IntentPane id={v.id} back={back} />
  if (v.view === 'report') return <ReportPane id={v.id} intent={v.intent} back={() => nav.form('reports')} />
  if (v.id === 'new') {
    return (
      <div className="p-l">
        <SourceCreatePage onBack={() => nav.form('feeds')} onCreated={() => { invalidateKeys('knowledge', true); nav.form('feeds') }} />
      </div>
    )
  }
  return <SourcePane id={v.id} back={() => nav.form('feeds')} />
}

function IntentPane({ id, back }: { id: string; back: () => void }) {
  const intents = useQuery('knowledge:intents', () => api.knowledgeIntents())
  const reports = useQuery('knowledge:reports', () => api.researchReports())
  if (id === 'new') return <IntentForm onClose={back} onSaved={back} />
  if ((intents.loading && !intents.data) || (reports.loading && !reports.data)) return <div className="p-l"><ListSkeleton rows={4} what="intent" /></div>
  if (intents.error && !intents.data) return <div className="p-l"><LoadError what="intent" error={intents.error} onRetry={intents.refresh} /></div>
  const intent = intents.data?.intents.find((i) => i.id === id)
  if (!intent) return <Gone what="intent" back={back} />
  const report = (reports.data?.reports ?? []).find((r) => r.source?.intent === id) ?? null
  return <IntentForm key={`${intent.id}:${intent.revision ?? ''}`} intent={intent} report={report} onClose={back} onSaved={back} />
}

function ReportPane({ id, intent, back }: { id: string; intent?: string; back: () => void }) {
  const headingId = useId()
  const reports = useQuery('knowledge:reports', () => api.researchReports())
  const intents = useQuery('knowledge:intents', () => api.knowledgeIntents())
  const isNew = id === 'new'
  if (!isNew && reports.loading && !reports.data) return <div className="p-l"><ListSkeleton rows={4} what="report" /></div>
  if (!isNew && reports.error && !reports.data) return <div className="p-l"><LoadError what="report" error={reports.error} onRetry={reports.refresh} /></div>
  const report: ResearchReport | undefined = isNew ? undefined: reports.data?.reports.find((r) => r.id === id)
  if (!isNew && !report) return <Gone what="report" back={back} />
  const goal = intent ? intents.data?.intents.find((i) => i.id === intent)?.goal ?? '' : ''
  const initial: ResearchReportInput = report ? editable(report) : intent
    ? { ...blank(), name: `Report: ${goal || 'what it finds'}`, prompt: `Write a short update from what the intent “${goal || intent}” found since the last report.`, source: { tags: [], window_secs: 0, intent } }
    : blank()
  const done = () => { invalidateKeys('knowledge', true); back() }
  return (
    <section aria-labelledby={headingId} className="flex min-h-0 flex-col gap-m p-l">
      <header className="flex items-start gap-s">
        <IconButton icon={ArrowLeft} label="Back to Feeds, intents and reports" onClick={back} className={TOUCH_TARGET} />
        <h2 id={headingId} data-type="title-l" className="min-w-0 flex-1 text-on-surface">{report ? 'Edit report' : 'New report'}</h2>
      </header>
      <LibraryReportForm key={report?.id ?? `new:${intent ?? ''}:${goal}`} initial={initial} reportId={report?.id}
        readsIntent={(report?.source?.intent || intent) ? (intents.data?.intents.find((i) => i.id === (report?.source?.intent || intent))?.goal || 'your intent') : undefined}
        onSaved={done} onCancel={back} />
    </section>
  )
}

/** Changing a feed or folder: its name, what to do with new items, and how often to check. */
function SourcePane({ id, back }: { id: string; back: () => void }) {
  const sources = useQuery('knowledge:sources', () => api.knowledgeSources())
  if (sources.loading && !sources.data) return <div className="p-l"><ListSkeleton rows={4} what="source" /></div>
  if (sources.error && !sources.data) return <div className="p-l"><LoadError what="source" error={sources.error} onRetry={sources.refresh} /></div>
  const source = sources.data?.sources.find((s) => s.id === id)
  if (!source) return <Gone what="feed or folder" back={back} />
  return <SourceForm key={source.id} source={source} back={back} />
}

function SourceForm({ source, back }: { source: WatchedSource; back: () => void }) {
  const headingId = useId()
  const doId = useId()
  const [name, setName] = useState(source.name)
  const [enrichment, setEnrichment] = useState(source.enrichment === RAW_ENRICHMENT ? RAW_ENRICHMENT: 'full')
  const [every, setEvery] = useState(String(source.poll_interval_secs || INTERVAL_CHOICES[1]))
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const choices = INTERVAL_CHOICES.includes(Number(every)) ? INTERVAL_CHOICES: [...INTERVAL_CHOICES, Number(every)].sort((a, b) => a - b)
  async function save() {
    setErr(''); setSaving(true)
    try {
      await api.updateKnowledgeSource(source.id, {
        name: name.trim() || source.name, enrichment,
        ...(source.event_driven ? {} : { poll_interval_secs: Number(every) }),
      })
      invalidateKeys('knowledge:sources')
      notify(`Saved “${name.trim() || source.name}”.`, 'success')
      back()
    } catch (e) { setErr(e instanceof Error ? e.message: "Couldn't save it.") } finally { setSaving(false) }
  }
  return (
    <section aria-labelledby={headingId} className="flex min-h-0 flex-col gap-m p-l">
      <header className="flex items-start gap-s">
        <IconButton icon={ArrowLeft} label="Back to Feeds, intents and reports" onClick={back} className={TOUCH_TARGET} />
        <h2 id={headingId} data-type="title-l" className="min-w-0 flex-1 text-on-surface">Edit “{source.name}”</h2>
      </header>
      <Field label="Name">
        <TextInput value={name} onChange={setName} required />
      </Field>
      <div className="flex flex-col gap-xs">
        <p id={doId} data-type="label-l" className="text-on-surface">What to do with new items</p>
        <ChoiceList labelledBy={doId} value={enrichment} onChange={setEnrichment}
          options={[
            { key: 'full', label: 'Read them', hint: 'Tags, topics and search by meaning. This uses your model.' },
            { key: RAW_ENRICHMENT, label: 'Keep them as they are', hint: 'No AI: they are kept and found by their words.' },
          ]} />
      </div>
      {!source.event_driven && (
        <Field label="Check for new items" hint="A source that fetches over the network is checked at most as often as Settings allows.">
          <Select ariaLabel="Check for new items" value={every} onChange={setEvery}
            options={choices.map((s) => ({ value: String(s), label: `Every ${fmtInterval(s)}` }))} />
        </Field>
      )}
      <FormFooter error={err}>
        <Button variant="ghost" onClick={back} disabled={saving} disabledReason={BUSY_REASON}>Cancel</Button>
        <Button onClick={() => void save()} loading={saving} loadingLabel="Saving…">Save</Button>
      </FormFooter>
    </section>
  )
}

function Gone({ what, back }: { what: string; back: () => void }) {
  return (
    <div className="flex flex-col items-start gap-s p-l">
      <p data-type="body-m" className="text-on-surface">This {what} isn't here any more. It may have been deleted.</p>
      <Button variant="secondary" onClick={back} className={TOUCH_TARGET}>Back to Feeds, intents and reports</Button>
    </div>
  )
}
