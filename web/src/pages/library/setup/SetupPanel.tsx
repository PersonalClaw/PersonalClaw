import { useEffect, useRef, type ReactNode } from 'react'
import { BookOpen, CalendarClock, FileText, MoreHorizontal, Pencil, Play, Plus, RefreshCw, Sparkles, Square, Trash2, X } from 'lucide-react'
import { ApiError, api, type IntentRun, type KnowledgeIntent, type KnowledgeIntentRecord, type ResearchReport, type WatchedSource } from '../../../lib/api'
import { invalidateKeys, useQuery } from '../../../lib/data'
import { notify } from '../../../app/appSdk'
import { reportActionFailure, reportingWrite } from '../../../app/reportingWrite'
import { useStaleWriteGuard } from '../../../lib/useStaleWriteGuard'
import { StaleWriteNotice } from '../../../ui/StaleWriteNotice'
import { intentRecord, readIntent, writeIntent } from '../../knowledge/intentRecord'
import { useIsMobile } from '../../../app/useIsMobile'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import { MenuRow, Popover } from '../../../ui/Popover'
import { Toggle, TOUCH_TARGET } from '../../../ui/Toggle'
import { TextLink } from '../../../ui/TextLink'
import { ListSkeleton, LoadError } from '../../../ui/ListScaffold'
import { confirmDestructive } from '../../../ui/dialog'
import { Remediation } from './Remediation'
import { RAW_ENRICHMENT, cadenceNote, eventDrivenMetaLine, firstScanLine, fmtInterval, healthMeta, linksOutsideLine } from '../../knowledge/sourceMeta'
import { usePowerUserView } from '../powerUserView'
import { relFuture } from '../../schedule/scheduleMeta'
import { INTENT_KINDS } from './intentKinds'
import { ageWords } from '../libraryMeta'
import { n } from '../libraryWords'
import { rowSubject } from '../../../lib/rowSubject'

// ── Feeds, intents and reports ───────────────────────────────────────────────────────
//
// What adds to the library on its own, in the regular view, in the pane beside the list (full screen
// on a phone). Every row and form round-trips through the address (`?setup=`). Pausing one keeps
// everything it added.

/** One row's menu entry. */
interface RowAction { label: string; icon: ReactNode; onSelect: () => void; danger?: boolean; disabled?: boolean; reason?: string }

/** An inline link on a row's sub line (a desktop's); on a phone it moves to the top of the row's ⋯,
 *  since each would be a 17 px target there. */
interface RowLink { label: string; run: () => void; icon: ReactNode; disabled?: boolean; reason?: string }

/** The group `?setup=feeds` and `?setup=reports` scroll to the top of the pane. */
export type SetupGroup = 'intents' | 'reports' | 'feeds'

/** What a row opens: its form at `?setup=<kind>:<id|new>`, or an item at `?item=<ref>`. */
export interface SetupNav {
  form: (setup: string) => void
  item: (ref: string) => void
}

export function SetupPanel({ focus, nav, onClose, detail }: {
  /** The group the address names, scrolled to the top of the pane; focus stays where it was. */
  focus?: SetupGroup
  nav: SetupNav
  onClose: () => void
  /** The Power user view's detail lines on each row; the stored setting when not given. */
  detail?: boolean
}) {
  const phone = useIsMobile()
  const power = usePowerUserView()
  const more = detail ?? power.stored
  const intents = useQuery('knowledge:intents', () => api.knowledgeIntents())
  const reports = useQuery('knowledge:reports', () => api.researchReports())
  const sources = useQuery('knowledge:sources', () => api.knowledgeSources())
  const intentsAt = useRef<HTMLElement>(null)
  const reportsAt = useRef<HTMLElement>(null)
  const feedsAt = useRef<HTMLElement>(null)
  const loaded = !!(intents.data && reports.data && sources.data)
  useEffect(() => {
    const at = focus === 'feeds' ? feedsAt: focus === 'reports' ? reportsAt: focus === 'intents' ? intentsAt: null
    if (at && loaded) at.current?.scrollIntoView({ block: 'start' })
  }, [focus, loaded])

  const intentList = intents.data?.intents ?? []
  const reportList = reports.data?.reports ?? []
  // The artifacts' own mirror is a row only in the Power user view: it is set in Settings.
  const sourceList = (sources.data?.sources ?? []).filter((s) => more || !s.event_driven)
  const kindName = (provider: string) => sources.data?.kinds?.find((k) => k.provider === provider)?.display_name ?? ''
  // An intent's report is the one that reads its findings.
  const reportOf = (id: string) => reportList.find((r) => r.source?.intent === id) ?? null
  // The mirror is no row in the regular view, but while it is on it adds on its own, so the panel
  // never says nothing does.
  const mirrorOn = (sources.data?.sources ?? []).some((s) => s.event_driven && s.enabled)
  const nothing = loaded && !intentList.length && !reportList.length && !sourceList.length && !mirrorOn
  return (
    <section aria-labelledby="lib-setup-title" className="flex min-h-0 flex-col gap-l p-l">
      <header className="flex items-start gap-s">
        <div className="min-w-0 flex-1">
          <h2 id="lib-setup-title" data-type="title-l" className="text-on-surface">Feeds, intents and reports</h2>
          <p data-type="body-s" className="mt-xs text-on-surface-var">These add to your library on their own. Pausing one keeps everything it added.</p>
        </div>
        <IconButton icon={X} label="Close Feeds, intents and reports" onClick={onClose} className={TOUCH_TARGET} />
      </header>
      {nothing && <p data-type="body-m" className="text-on-surface">Nothing adds on its own yet.</p>}

      <Group at={intentsAt} title="Intents" line="things the agent looks out for in everything you add"
        newLabel="New intent" newName="New intent" onNew={() => nav.form('intent:new')}
        foot={intentList.length ? 'Each intent uses your model once for every item you add.' : undefined}>
        {intents.loading && !intents.data ? <ListSkeleton rows={2} what="intents" />
          : intents.error && !intents.data ? <LoadError what="intents" error={intents.error} onRetry={intents.refresh} />
          : intentList.map((it) => <IntentRow key={it.id} intent={it} report={reportOf(it.id)} phone={phone} nav={nav} detail={more} />)}
      </Group>

      <Group at={reportsAt} title="Reports" line="questions answered on a schedule"
        newLabel={phone ? 'Schedule' : 'Schedule a report'} newName="Schedule a report" onNew={() => nav.form('report:new')}>
        {reports.loading && !reports.data ? <ListSkeleton rows={2} what="reports" />
          : reports.error && !reports.data ? <LoadError what="reports" error={reports.error} onRetry={reports.refresh} />
          : reportList.map((r) => <ReportRow key={r.id} report={r} phone={phone} nav={nav} detail={more} />)}
      </Group>

      <Group at={feedsAt} title="Feeds, folders and apps" line="they bring new things in"
        newLabel={phone ? 'Follow' : 'Follow a feed or folder'} newName="Follow a feed or folder" onNew={() => nav.form('source:new')}>
        {sources.loading && !sources.data ? <ListSkeleton rows={2} what="sources" />
          : sources.error && !sources.data ? <LoadError what="sources" error={sources.error} onRetry={sources.refresh} />
          : sourceList.map((s) => <SourceRow key={s.id} source={s} phone={phone} nav={nav} detail={more ? kindName(s.provider) || s.provider : ''} />)}
      </Group>
    </section>
  )
}

/** A group: its heading and what it holds, its New (secondary: Add is the page's primary), its
 *  rows. On a phone the New reads a shorter word, its name the full words, visible word first. */
function Group({ at, title, line, newLabel, newName, onNew, foot, children }: {
  at: React.RefObject<HTMLElement | null>; title: string; line: string; newLabel: string; newName: string
  onNew: () => void; foot?: string; children: ReactNode
}) {
  return (
    <section ref={at} role="region" aria-label={title} className="flex scroll-mt-l flex-col gap-s">
      <div className="flex flex-wrap items-center gap-s">
        <h3 data-type="label-l" className="min-w-0 flex-1 text-on-surface">
          {title} <span className="text-on-surface-low" style={{ fontWeight: 470 }}>· {line}</span>
        </h3>
        <Button variant="secondary" onClick={onNew} ariaLabel={newName === newLabel ? undefined: newName} className={TOUCH_TARGET}>
          <Plus size={16} aria-hidden /> {newLabel}
        </Button>
      </div>
      <div className="flex flex-col gap-xs">{children}</div>
      {foot && <p data-type="caption" className="text-on-surface-low">{foot}</p>}
    </section>
  )
}

/** One set-up row: its name, its sub line, its switch (named by its words and its row), Edit and
 *  ⋯. On a phone the sub line starts with the switch's words, so each switch has a visible label, and
 *  the inline links move to the top of the ⋯. A row with no switch (a source fed by a listener, which
 *  pausing would not stop) has none. */
function SetupRow({ name, parts, details = [], links = [], warn, toggle, onEdit, actions, phone, below }: {
  name: string
  parts: string[]
  /** The Power user view's lines under the row's own. */
  details?: string[]
  links?: RowLink[]
  warn?: string
  toggle?: { words: string; on: boolean; onChange: (on: boolean) => void }
  onEdit?: () => void
  actions: RowAction[]
  phone: boolean
  below?: ReactNode
}) {
  const lead = phone && toggle ? [toggle.words, ...parts] : parts
  const menu: RowAction[] = phone
    ? [...links.map((l) => ({ label: l.label, icon: l.icon, onSelect: l.run, disabled: l.disabled, reason: l.reason })),
      ...(onEdit ? [{ label: 'Edit', icon: <Pencil size={15} />, onSelect: onEdit }] : []), ...actions]
    : actions
  const shown = phone ? [] : links.filter((l) => !l.disabled)
  return (
    <div className="flex flex-col gap-xs rounded-lg px-m py-s">
      <div className="flex items-start gap-s">
        <div className="min-w-0 flex-1">
          <p data-type="body-m" className="text-on-surface">{name}</p>
          <p data-type="caption" className="text-on-surface-low">
            {lead.join(' · ')}
            {shown.map((l, i) => (
              <span key={l.label}>{lead.length || i ? ' · ' : ''}<TextLink size="xs" onClick={l.run}>{l.label}</TextLink></span>
            ))}
          </p>
          {details.filter(Boolean).map((d) => <p key={d} data-type="caption" className="text-on-surface-low">{d}</p>)}
          {warn && <p data-type="caption" className="text-warn">{warn}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-s">
          {toggle && !phone && <span data-type="caption" className="text-on-surface-var">{toggle.words}</span>}
          {toggle && <Toggle on={toggle.on} onChange={toggle.onChange} label={`${toggle.words}: ${name}`} />}
          {onEdit && !phone && (
            <Button variant="secondary" size="sm" onClick={onEdit} ariaLabel={`Edit ${name}`}><Pencil size={14} aria-hidden /> Edit</Button>
          )}
          <RowMenu name={name} actions={menu} />
        </div>
      </div>
      {below}
    </div>
  )
}

function RowMenu({ name, actions }: { name: string; actions: RowAction[] }) {
  if (!actions.length) return null
  return (
    <Popover portal placement="bottom" align="right" width={280} phoneSheet
      trigger={(open, toggle) => <IconButton icon={MoreHorizontal} label={`More for ${name}`} onClick={toggle} expanded={open} className={TOUCH_TARGET} />}>
      {(close) => (
        <div className="flex flex-col gap-0.5">
          {actions.map((a) => (
            <div key={a.label} className={a.danger && !a.disabled ? '[&_span]:!text-danger' : undefined}>
              <MenuRow icon={a.icon} label={a.label} hint={a.disabled ? a.reason: undefined} disabled={a.disabled}
                onClick={() => { if (a.disabled) return; a.onSelect(); close() }} />
            </div>
          ))}
        </div>
      )}
    </Popover>
  )
}

/** Why a paused intent's Run on saved items is off: a paused intent checks nothing (the gateway refuses the run). */
export const PAUSED_RUN = 'Resume it to check saved items.'

const codeOf = (e: unknown) => (e instanceof ApiError ? e.code: '')

/** Pause or resume an intent: one field, re-applied onto what is stored when another tab changed
 *  the intent since this copy was read (`useStaleWriteGuard`), through the intent's one writer. Its
 *  `guard` is for the host's `StaleWriteNotice`. */
export function useIntentSwitch(intent: KnowledgeIntent | null | undefined) {
  const guard = useStaleWriteGuard<KnowledgeIntentRecord>({
    read: () => readIntent(intent?.id ?? ''),
    write: (next, base) => writeIntent(next, base),
    onSaved: () => { invalidateKeys('knowledge:intents'); invalidateKeys('knowledge:library', true) },
    onDiscard: () => invalidateKeys('knowledge:intents'),
  })
  const flip = (on: boolean) => {
    if (!intent) return
    void reportingWrite(on ? 'resume this intent' : 'pause this intent',
      () => guard.apply({ value: intentRecord(intent), revision: intent.revision ?? '' }, (theirs) => ({ ...theirs, enabled: on })))
  }
  return { guard, flip }
}

/** How often a running check of saved items is read again. */
const RUN_POLL_MS = 2000

/** An intent's background check of what you already saved, read again while it runs; `null`
 *  when it has none (404 `no_run`) or the read failed. */
export function useIntentRun(id: string): { run: IntentRun | null; stop: () => Promise<void> } {
  const key = `knowledge:intent-run:${id}`
  // No run (404 `no_run`) and a read that failed both mean there is no check to show.
  const { data, error, refresh } = useQuery<IntentRun | null>(key, () => (id ? api.intentRun(id) : Promise.resolve(null)))
  const running = data?.status === 'running'
  useEffect(() => {
    if (!running) return
    const t = window.setInterval(refresh, RUN_POLL_MS)
    return () => window.clearInterval(t)
  }, [running, refresh])
  // When it ends, what it found is in the counts and the library.
  const was = useRef(running)
  useEffect(() => {
    if (was.current && !running) { invalidateKeys('knowledge:intents'); invalidateKeys('knowledge:library', true) }
    was.current = running
  }, [running])
  const stop = async () => {
    await reportingWrite('stop checking saved items', () => api.stopIntentRun(id))
    invalidateKeys(key)
  }
  return { run: error && data === undefined ? null : data ?? null, stop }
}

/** Start checking the saved items for an intent, saying why when it can't. */
export async function runOnSaved(intent: KnowledgeIntent): Promise<void> {
  const goal = intent.goal || 'this intent'
  try {
    const { total } = await api.startIntentRun(intent.id)
    invalidateKeys('knowledge:intent-run', true)
    notify(`Checking ${n(total)} saved items for “${goal}”. You can keep working.`, 'success')
  } catch (e) {
    const code = codeOf(e)
    notify(code === 'already_running' ? `“${goal}” is already checking your saved items.`
      : code === 'paused' ? PAUSED_RUN: `Couldn't start checking saved items for “${goal}”.`, 'error')
  }
}

function IntentRow({ intent, report, phone, nav, detail }: { intent: KnowledgeIntent; report: ResearchReport | null; phone: boolean; nav: SetupNav; detail: boolean }) {
  const goal = intent.goal || 'An intent with no goal'
  const paused = intent.enabled === false
  const found = intent.outcome_count ?? 0
  const last = intent.last_found_at ? ageWords(intent.last_found_at) : ''
  const parts = found
    ? [`${n(found)} found`, ...(last && !paused ? [`last ${last}`] : []), ...(paused ? ['paused'] : []), ...(intent.propose_skill ? ['Proposes a skill'] : [])]
    : ['Nothing found yet']
  const { run: check, stop } = useIntentRun(intent.id)
  const checking = check?.status === 'running'
  const run: RowLink = {
    label: 'Run on saved items', icon: <Play size={15} />, run: () => void runOnSaved(intent),
    disabled: paused || checking, reason: paused ? PAUSED_RUN: checking ? 'It is checking your saved items now.' : undefined,
  }
  const halt: RowLink = { label: 'Stop', icon: <Square size={15} />, run: () => void stop() }
  const links: RowLink[] = [
    ...(found ? [{ label: 'See findings', icon: <BookOpen size={15} />, run: () => nav.item(`findings:${intent.id}`) }] : []),
    ...(found && report ? [{ label: 'See the report', icon: <FileText size={15} />, run: () => nav.item(`report:${report.id}`) }] : []),
    ...(checking ? [halt] : !found || phone ? [run] : []),
  ]
  const { guard, flip } = useIntentSwitch(intent)
  const shown = checking ? [`Checking saved items · ${n(check.done)} of ${n(check.total)}`] : parts
  const actions: RowAction[] = [
    // On a phone Run on saved items is already at the menu's top, with the row's other links.
    ...(phone ? [] : [{ label: run.label, icon: run.icon, onSelect: run.run, disabled: run.disabled, reason: run.reason }]),
    { label: report ? 'Change its report…' : 'Write a report…', icon: <CalendarClock size={15} />, onSelect: () => nav.form(report ? `report:${report.id}` : `report:new:${intent.id}`) },
    ...(intent.propose_skill && found ? [{ label: 'Generate skill', icon: <Sparkles size={15} />, onSelect: () => void generateSkill(intent) }] : []),
    { label: 'Delete intent…', icon: <Trash2 size={15} />, danger: true, onSelect: () => void deleteIntent(intent, report) },
  ]
  return (
    <SetupRow name={goal} parts={shown} links={links} details={detail ? [intentUse(intent)] : []}
      toggle={{ words: 'Checking new items', on: !paused, onChange: flip }} onEdit={() => nav.form(`intent:${intent.id}`)}
      actions={actions} phone={phone}
      below={<>
        {checking && <p data-type="caption" className="text-on-surface-var">You can keep working. New findings show up here as they come.</p>}
        <StaleWriteNotice guard={guard} what="This intent" />
      </>} />
  )
}

async function generateSkill(intent: KnowledgeIntent) {
  try {
    const r = await api.generateSkillFromIntent(intent.id)
    notify(`Made the skill “${r.skill}” from ${n(intent.outcome_count ?? 0)} findings. Find it under Skills.`, 'success')
  } catch { notify(`Couldn't make a skill from “${intent.goal || 'this intent'}”.`, 'error') }
}

/** "weekly" or "daily" for a schedule the intent form wrote (`0 <h> * * <d|*>`), '' for any other. */
export function cadenceOf(cron: string | null | undefined): string {
  const m = /^0 \d{1,2} \* \* (\*|[0-6])$/.exec((cron ?? '').trim())
  return !m ? '' : m[1] === '*' ? 'daily' : 'weekly'
}

/** Delete an intent and the findings it kept, saying what its report does then. */
export async function deleteIntent(intent: KnowledgeIntent, report?: ResearchReport | null): Promise<boolean> {
  const goal = intent.goal || 'this intent'
  const count = intent.outcome_count ?? 0
  // Counted, so it never overstates the loss: an intent that found nothing loses only itself.
  const what = count === 0 ? "This deletes the intent. It hasn't found anything yet."
    : `This deletes the intent and the ${n(count)} ${count === 1 ? 'finding' : 'findings'} it kept.`
  const how = report ? cadenceOf(report.schedule.cron_expr) : ''
  const body = report
    ? `${what} Its ${how ? `${how} ` : ''}report stops. The last one stays in your library. You can't undo this.`
    : `${what} You can't undo this.`
  // A goal is a sentence; the title takes it whole up to a line's length.
  if (!(await confirmDestructive(`Delete “${rowSubject([goal], 60)}”?`, body, { confirmLabel: 'Delete intent' }))) return false
  if (!(await reportingWrite(`delete “${goal}”`, () => api.deleteKnowledgeIntent(intent.id)))) return false
  invalidateKeys('knowledge', true)
  return true
}

/** Run a report now, saying why when it can't. */
export async function runReport(report: ResearchReport): Promise<void> {
  notify(`Writing “${report.name}”. It stays in your library when it's done.`, 'success')
  // A refusal is said in the gateway's own words; a run that ended says what it found, in the run's.
  const r = await api.runResearchReport(report.id).catch(reportActionFailure(`run “${report.name}”`))
  invalidateKeys('knowledge', true)
  if (!r) return
  if (r.outcome === 'already_running') notify(`“${report.name}” is already writing one.`, 'error')
  else if (!r.ok || r.outcome === 'failed') notify(`${report.name}: ${r.refused || r.result || "it couldn't finish"}`, 'error')
  else notify(`${report.name}: ${r.result || 'the run finished'}`, r.outcome === 'wrote' ? 'success' : 'info')
}

function ReportRow({ report, phone, nav, detail }: { report: ResearchReport; phone: boolean; nav: SetupNav; detail: boolean }) {
  const failed = report.last_status === 'error'
  const last = report.last_run_ts ? ageWords(report.last_run_ts) : ''
  const missing = report.missing_tags ?? []
  // A restore holds the automation that runs it: it waits for a Resume on the Triggers page.
  const held = !!report.schedule_shown?.restore_hold
  const parts = [
    report.running ? 'Writing now' : failed ? 'Last run failed' : report.schedule_shown?.words || 'No schedule',
    ...(held && !report.running ? ['paused by the restore: resume it on the Triggers page'] : []),
    ...(!report.running && !failed && last ? [`last run ${last}`] : []),
  ]
  const warn = missing.length ? (missing.length > 1 ? 'Reads nothing: its tags were deleted.' : `No longer reads “${missing[0]}”: that tag was deleted.`)
    : phone && failed ? 'Last run failed' : undefined
  const again: RowLink = { label: 'Run again', icon: <Play size={15} />, run: () => void runReport(report) }
  const flip = async (on: boolean) => {
    try { await api.updateResearchReport(report.id, { enabled: on }); invalidateKeys('knowledge:reports'); invalidateKeys('knowledge:library', true) }
    catch { notify(`Couldn't ${on ? 'resume' : 'pause'} “${report.name}”.`, 'error') }
  }
  const actions: RowAction[] = [
    ...(failed && phone ? [] : [{ label: 'Run now', icon: <Play size={15} />, onSelect: () => void runReport(report), disabled: !!report.running, reason: report.running ? "It's writing one now." : undefined }]),
    {
      label: 'Delete report…', icon: <Trash2 size={15} />, danger: true,
      onSelect: async () => {
        if (!(await confirmDestructive(`Delete “${report.name}”?`, 'It stops running. Its last write-up stays in your library.', { confirmLabel: 'Delete report' }))) return
        try { await api.deleteResearchReport(report.id); invalidateKeys('knowledge', true) } catch { notify(`Couldn't delete “${report.name}”.`, 'error') }
      },
    },
  ]
  return (
    <SetupRow name={report.name} parts={parts} links={failed && !report.running ? [again] : []} warn={warn}
      details={detail ? [report.sources_shown, report.citation_policy === 'cite-source-only' ? 'Cites only what is new since its last run' : 'May also cite what it was given to read'] : []}
      toggle={{ words: 'On schedule', on: report.enabled, onChange: flip }} onEdit={() => nav.form(`report:${report.id}`)}
      actions={actions} phone={phone} />
  )
}

/** Why the artifacts' mirror has no Remove. */
const MIRROR_REMOVE = 'This one is set in Settings › Automations › Sources.'

function SourceRow({ source, phone, nav, detail }: { source: WatchedSource; phone: boolean; nav: SetupNav
  /** The kind's name for the Power user view's detail line; '' in the regular view. */
  detail: string }) {
  const parts = sourceParts(source)
  const refresh = () => { invalidateKeys('knowledge:sources'); invalidateKeys('knowledge:library', true) }
  // Nothing registered to check its kind: it will never bring anything in, whatever the view.
  const warn = !source.event_driven && !source.enrolled ? 'Nothing is set up to check this kind, so it brings nothing in.' : undefined
  const details = detail ? sourceDetails(source, detail) : []
  const flip = async (on: boolean) => {
    try { await api.updateKnowledgeSource(source.id, { enabled: on }); refresh() }
    catch { notify(`Couldn't ${on ? 'resume' : 'pause'} “${source.name}”.`, 'error') }
  }
  const actions: RowAction[] = [
    ...(source.event_driven ? [] : [{
      label: 'Check now', icon: <RefreshCw size={15} />,
      onSelect: async () => {
        try { await api.checkKnowledgeSource(source.id); notify(`Checking “${source.name}” now.`, 'success'); refresh() }
        catch (e) { notify(codeOf(e) === 'already_running' ? `“${source.name}” is being checked now.` : `Couldn't check “${source.name}”.`, 'error') }
      },
    }]),
    { label: 'Edit', icon: <Pencil size={15} />, onSelect: () => nav.form(`source:${source.id}`) },
    {
      label: 'Remove…', icon: <Trash2 size={15} />, danger: true,
      // The artifacts' own mirror follows the Settings switch, and can't be removed here.
      disabled: !!source.event_driven, reason: source.event_driven ? MIRROR_REMOVE: undefined,
      onSelect: async () => {
        if (!(await confirmDestructive(`Remove “${source.name}”?`, 'It stops bringing new items in. What it already added stays in your library.', { confirmLabel: 'Remove' }))) return
        try { await api.deleteKnowledgeSource(source.id); refresh() } catch { notify(`Couldn't remove “${source.name}”.`, 'error') }
      },
    },
  ]
  return (
    <SetupRow name={source.name} parts={parts} details={details} warn={warn}
      // A source fed by a listener has no switch: pausing its row would stop nothing.
      toggle={source.event_driven ? undefined: { words: 'Watching', on: source.enabled, onChange: flip }}
      actions={actions} phone={phone} below={<>
        {[firstScanLine(source.first_scan), linksOutsideLine(source.links_outside)].filter(Boolean).map((l) => (
          <p key={l} data-type="caption" className="text-on-surface-low">{l}</p>
        ))}
        <Remediation source={source} onChanged={refresh} />
      </>} />
  )
}

/** A feed's own line: a folder's path, when it was last checked, and what that brought in. */
export function sourceParts(source: WatchedSource, now = Date.now()): string[] {
  if (source.event_driven) return [eventDrivenMetaLine()]
  // A watched folder's spec names its path (the create form's `dir` form).
  const path = typeof source.spec?.path === 'string' ? source.spec.path: ''
  const files = source.first_scan?.found
  return [
    path ? 'Folder' : 'Feed', ...(path ? [path] : []),
    source.last_poll_at ? `checked ${ageWords(source.last_poll_at, now)}` : 'not checked yet',
    ...(path && files ? [`${n(files)} files`] : source.last_new_count ? [`${n(source.last_new_count)} new`] : []),
  ]
}

/** A feed's Power user view lines: its kind, how often it is really checked, how its last check
 *  went, that its items never reach a model, and the costlier ways its last check had to read. */
export function sourceDetails(source: WatchedSource, kind: string): string[] {
  // A mirror's own line already says how it is fed; it has no kind, cadence or health of its own.
  if (source.event_driven) return []
  const health = healthMeta(source.health_status)
  const next = source.enabled && source.next_poll_at ? ` · next ${relFuture(source.next_poll_at)}` : ''
  return [
    [kind, `checked every ${fmtInterval(source.poll_every_secs)}${next}`, source.last_poll_at ? health.label : 'not checked yet'].join(' · '),
    // What a health that is not Healthy means; one the source has not earned (never checked) says nothing.
    source.last_poll_at && health.tone !== 'ok' ? `${health.label}: ${health.hint}` : '',
    cadenceNote(source),
    source.enrichment === RAW_ENRICHMENT ? 'No AI: its items are indexed on this computer and never reach a model' : '',
    (source.last_escalations ?? []).join(' · '),
  ]
}

/** What an intent asks of your model, for the Power user view. */
function intentUse(intent: KnowledgeIntent): string {
  const kinds = INTENT_KINDS.filter((k) => k.types.some((t) => (intent.enabled_for ?? []).includes(t))).map((k) => k.label.toLowerCase())
  const what = kinds.length && kinds.length < INTENT_KINDS.length ? kinds.join(', ').replace(/, ([^,]*)$/, ' and $1') : 'item you add'
  return `Uses your model once for each new ${what === 'item you add' ? what : `${what} item`}`
}
