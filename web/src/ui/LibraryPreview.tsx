import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ChevronDown, ExternalLink, MessageSquare, MoreHorizontal } from 'lucide-react'
import { api, type IntentOutcome, type LibraryItem } from '../lib/api'
import { useQuery } from '../lib/data'
import { Preview as KnowledgeBody } from '../pages/knowledge/KnowledgeDetail'
import { resolveType } from '../pages/knowledge/knowledgeMeta'
import { ageWords, dayWords, formatWord, refId, refKind, sizeWords, titleOf } from '../pages/library/libraryMeta'
import { moreRows, openPathOf, pinRow, useInChatGroups, type ActionGroup, type ActionInputs } from '../pages/library/libraryActions'
import { ContentSurface } from './content/ContentSurface'
import { PdfPreview } from '../pages/files/browse/FilePreviews'
import { resolveContentType } from './content/contentTypes'
import { epochSeconds } from '../lib/epoch'
import { Button } from './Button'
import { IconButton } from './IconButton'
import { MenuRow, Popover } from './Popover'
import { ListSkeleton, LoadError } from './ListScaffold'
import { TextLink } from './TextLink'
import { TOUCH_TARGET } from './Toggle'
import { useIsMobile } from '../app/useIsMobile'
import { notify } from '../app/appSdk'
import { StaleWriteNotice } from './StaleWriteNotice'
import { n } from '../pages/library/libraryWords'
import { PAUSED_RUN, runOnSaved, runReport, useIntentRun, useIntentSwitch } from '../pages/library/setup/SetupPanel'
import { nextRun } from '../pages/knowledge/ReportsPage'

/** One thing in the library, beside its list: its heading (an `h2`), its meta line,
 *  then **Use in chat ▾, Open, Pin, More**, the notice line for each problem it has with its fix,
 *  its body by kind (the text, the code, the link, the image or player, the artifact live and
 *  sandboxed, a report's write-up, a findings log's findings), and **About this item**. It never
 *  changes read state.
 *
 *  Hostable in the Projects page's detail column from 320 px wide. With a `project` context
 *  (`actions.project`) its New chat with this opens the chat in that project, its Open adds
 *  `back=project:<id>`, and for an item in that project About's Project row is the name alone and
 *  More has no Open project row. Ask about this stays an unbound investigate chat in both places.
 *  `phoneSheet` opens its Use in chat and More menus as phone sheets from 320 to 768 px. */
export function LibraryPreview({ item, actions, phoneSheet = false, foot = false }: {
  /** The thing, as `GET /api/library` answers it. */
  item: LibraryItem
  /** What its menus do, and where they go (`libraryActions.ActionInputs`): the host's navigation,
   *  pickers and project context. */
  actions: ActionInputs
  /** Its Use in chat and More menus open as phone sheets from 320 to 768 px. */
  phoneSheet?: boolean
  /** A phone's full-screen form: Use in chat, Open and Pin at its foot, 44 px each, and More the ⋯
   *  at its top. */
  foot?: boolean
}) {
  const phone = useIsMobile()
  const title = titleOf(item)
  const back = actions.project ? `project:${actions.project}` : ''
  const open = openPathOf(item, actions.writeUps, back)
  const pin = pinRow(item, actions.offReason)
  const groups = useInChatGroups(item, actions)
  const more = moreRows(item, actions)
  const meta = previewMeta(item)

  const useInChat = (
    <MenuButton label="Use in chat" icon={<MessageSquare size={16} />} groups={groups} phoneSheet={phoneSheet} tonal />
  )
  const bar = (
    <div className="flex flex-wrap items-center gap-s">
      {useInChat}
      {open && <Button variant="secondary" onClick={() => actions.navigate(open)} className={TOUCH_TARGET}><ExternalLink size={16} /> Open</Button>}
      <IconButton icon={pin.icon} label={pin.name ?? pin.label} onClick={pin.onSelect} disabled={pin.disabled} disabledReason={pin.disabledReason} className={TOUCH_TARGET} />
      {!foot && <MoreMenu title={title} rows={more} phoneSheet={phoneSheet} />}
    </div>
  )
  return (
    <section aria-labelledby={`lib-preview-${item.ref}`} className="flex min-h-0 flex-col gap-m p-l">
      <header className="flex flex-col gap-xs">
        <div className="flex items-start gap-s">
          <h2 id={`lib-preview-${item.ref}`} data-type="title-l" className="min-w-0 flex-1 text-on-surface">{title}</h2>
          {foot && <MoreMenu title={title} rows={more} phoneSheet={phoneSheet} />}
        </div>
        {meta && <p data-type="caption" className="text-on-surface-low">{meta}</p>}
        {!foot && bar}
      </header>
      <Notices item={item} actions={actions} />
      <div className="min-h-0">
        <Body item={item} actions={actions} />
      </div>
      <About item={item} actions={actions} />
      {foot && phone && <div className="sticky bottom-0 -mx-l border-t border-outline-variant/40 bg-surface px-l py-s">{bar}</div>}
    </section>
  )
}

/** The preview's meta line, as he saw it: "Page · Made in Lisbon weekend · 2 h ago · Version 3". */
function previewMeta(item: LibraryItem): string {
  const parts = [formatWord(item)]
  if (item.from?.title && refKind(item.from.ref) === 'chat') parts.push(`Made in ${item.from.title}`)
  else if (refKind(item.ref) === 'findings') parts.push('From your intent')
  else if (item.made_label && item.made !== 'not_recorded') parts.push(item.made_label)
  const age = ageWords(item.activity_at || item.created_at)
  if (age) parts.push(age)
  if (item.version && refKind(item.ref) === 'artifact') parts.push(`Version ${item.version}`)
  return parts.filter(Boolean).join(' · ')
}

/** The notice line under the heading, one per problem, each with its fix. */
function Notices({ item, actions }: { item: LibraryItem; actions: ActionInputs }) {
  const lines: { text: string; fix?: { label: string; run: () => void } }[] = []
  const kind = refKind(item.ref)
  if (item.conflicts.length) lines.push({ text: 'This says something different from another item in your library.', fix: { label: 'Compare them', run: () => actions.navigate(`library?item=${encodeURIComponent(item.ref)}&compare=${encodeURIComponent(item.conflicts[0])}`) } })
  if (item.state === 'unreadable' && kind === 'knowledge') {
    lines.push({ text: "Couldn't read this file. Its text isn't in search yet.", fix: { label: 'Try again', run: () => void api.generateKnowledgeIntelligence(refId(item.ref)) } })
  }
  if (item.state === 'being_read') lines.push({ text: "Still reading this file. You can open it now. Search finds its text when it's done." })
  // A report's running and failed lines are its bar's (`ReportBar`), with the run's own sentence.
  if (item.missing_tags.length && kind === 'report') {
    const tags = item.missing_tags.map((t) => `“${t}”`)
    lines.push({ text: tags.length > 1 ? `Reads nothing: the tags ${tags.slice(0, -1).join(', ')} and ${tags[tags.length - 1]} were deleted.` : `No longer reads ${tags[0]}: that tag was deleted.`,
      fix: { label: 'Choose a tag', run: () => actions.navigate(`library?setup=report:${encodeURIComponent(refId(item.ref))}`) } })
  }
  if (!lines.length) return null
  return (
    <div className="flex flex-col gap-xs">
      {lines.map((l) => (
        <p key={l.text} data-type="body-s" role="note" className="rounded-lg bg-surface-high px-m py-s text-on-surface">
          {l.text}{l.fix && <> <TextLink onClick={l.fix.run} className={TOUCH_TARGET}>{l.fix.label}</TextLink></>}
        </p>
      ))}
    </div>
  )
}

/** The body, by kind. */
function Body({ item, actions }: { item: LibraryItem; actions: ActionInputs }) {
  const kind = refKind(item.ref)
  const id = kind === 'report' ? actions.writeUps.get(refId(item.ref)) ?? '' : refId(item.ref)
  if (kind === 'artifact') return <ArtifactBody slug={id} title={titleOf(item)} />
  if (kind === 'findings') return <FindingsBody intentId={id} navigate={actions.navigate} />
  if (kind === 'report') {
    return (
      <div className="flex flex-col gap-m">
        <ReportBar reportId={refId(item.ref)} navigate={actions.navigate} />
        {id ? <KnowledgeItemBody id={id} /> : <p data-type="body-s" className="text-on-surface-low">Its first write-up appears after its first run.</p>}
      </div>
    )
  }
  // A PDF opens in the viewer Files and Artifacts use; other files keep the item's own body: its
  // player, or its download row and the text read from it.
  if (item.format === 'pdf') {
    return (
      <div className="h-[32rem] overflow-hidden rounded-lg border border-outline-variant/40">
        <PdfPreview src={api.knowledgeItemFileUrl(id)} />
      </div>
    )
  }
  return <KnowledgeItemBody id={id} />
}

/** A report's bar: when it runs, with Run now and Edit report in place; while it writes,
 *  that line, with Run now off; after a failed run, the run's own sentence with Run again. A report
 *  written from an intent's findings says so. */
function ReportBar({ reportId, navigate }: { reportId: string; navigate: (path: string) => void }) {
  const reports = useQuery('knowledge:reports', () => api.researchReports())
  const intents = useQuery('knowledge:intents', () => api.knowledgeIntents())
  const r = reports.data?.reports.find((x) => x.id === reportId)
  // Its schedule and its Run now are this read's: a failed one says so, with Retry.
  if (!r && reports.error) return <LoadError what="report's schedule" error={reports.error} onRetry={reports.refresh} />
  if (!r) return null
  const goal = r.source?.intent ? intents.data?.intents.find((i) => i.id === r.source.intent)?.goal ?? '' : ''
  const words = r.schedule_shown?.words ?? ''
  const next = r.enabled && r.schedule_shown?.next_run_at ? nextRun(r.schedule_shown.next_run_at, r.schedule_shown.timezone) : ''
  const line = r.running ? "Writing a new report. The last one stays until it's done."
    : r.last_status === 'error' ? `The last run failed${r.last_error ? `: ${r.last_error.replace(/\.$/, '')}` : ''}.`
    : goal && words ? `Rewritten ${words.charAt(0).toLowerCase()}${words.slice(1)} from what “${goal}” found.`
    : words && r.schedule_shown?.restore_hold ? `${words} · paused by the restore: resume it on the Triggers page`
    : words ? [words, next ? `Next ${next}` : ''].filter(Boolean).join(' · ')
    : 'No schedule: it runs when you press Run now.'
  const edit = () => navigate(`library?setup=${encodeURIComponent(`report:${reportId}`)}`)
  return (
    <div role="group" aria-label="This report" className="flex flex-col gap-s rounded-lg bg-surface-high px-m py-s">
      <p data-type="body-s" className="text-on-surface">{line}</p>
      {r.last_status === 'nothing_new' && !r.running && <p data-type="caption" className="text-on-surface-var">Last run found nothing new.</p>}
      <div className="flex flex-wrap gap-s">
        {/* Off while it writes one, with why: the run may be a scheduled fire, not this button's. */}
        <Button variant="secondary" size="sm" onClick={() => void runReport(r)} disabled={!!r.running} disabledReason="It's already writing one" className={TOUCH_TARGET}>
          {r.last_status === 'error' ? 'Run again' : 'Run now'}
        </Button>
        <Button variant="secondary" size="sm" onClick={edit} className={TOUCH_TARGET}>{goal ? 'Change schedule' : 'Edit report'}</Button>
      </div>
    </div>
  )
}

function KnowledgeItemBody({ id }: { id: string }) {
  const { data, loading, error, refresh } = useQuery(`knowledge:item:${id}`, () => api.knowledgeItem(id))
  if (loading && !data) return <ListSkeleton rows={3} what="item" />
  if (error && !data) return <LoadError what="item" error={error} onRetry={refresh} />
  if (!data) return null
  return <KnowledgeBody item={data} tm={resolveType(data)} />
}

function ArtifactBody({ slug, title }: { slug: string; title: string }) {
  const { data, loading, error, refresh } = useQuery(`artifacts:item:${slug}`, () => api.artifact(slug))
  const ctype = useMemo(() => (data ? resolveContentType({ kind: data.kind }) : null), [data])
  if (loading && !data) return <ListSkeleton rows={3} what="page" />
  if (error && !data) return <LoadError what="page" error={error} onRetry={refresh} />
  if (!data || !ctype) return null
  return (
    <div className="h-[28rem] overflow-hidden rounded-lg border border-outline-variant/40">
      <ContentSurface key={`${slug}:${data.version}`} type={ctype} content={data.content ?? ''} title={title} docId={slug} readOnly />
    </div>
  )
}

/** A findings log: its bar, which edits the intent in place (Pause or Resume, Change what it
 *  looks for, Run on saved items, and the running check with Stop), then its findings, newest first,
 *  20 at a time, each saying where it came from and opening that item. */
function FindingsBody({ intentId, navigate }: { intentId: string; navigate: (path: string) => void }) {
  const intents = useQuery('knowledge:intents', () => api.knowledgeIntents())
  const intent = intents.data?.intents.find((i) => i.id === intentId) ?? null
  const { guard, flip } = useIntentSwitch(intent)
  const { run, stop } = useIntentRun(intentId)
  const checking = run?.status === 'running'
  const paused = intent?.enabled === false
  const first = useQuery(`knowledge:intent-outcomes:${intentId}:page`, () => api.knowledgeIntentOutcomesPage(intentId, FINDINGS_PAGE))
  const [more, setMore] = useState<{ outcomes: IntentOutcome[]; before: string } | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  useEffect(() => { setMore(null) }, [first.data])
  const outcomes = [...(first.data?.outcomes ?? []), ...(more?.outcomes ?? [])]
  const next = more ? more.before: first.data?.before ?? ''
  const total = intent?.outcome_count ?? outcomes.length
  const showMore = async () => {
    setLoadingMore(true)
    try {
      const page = await api.knowledgeIntentOutcomesPage(intentId, FINDINGS_PAGE, next)
      setMore((m) => ({ outcomes: [...(m?.outcomes ?? []), ...page.outcomes], before: page.before }))
    } catch { notify("Couldn't load more findings.", 'error') } finally { setLoadingMore(false) }
  }
  const edit = () => navigate(`library?setup=${encodeURIComponent(`intent:${intentId}`)}`)
  return (
    <div className="flex flex-col gap-m">
      {intent && (
        <div role="group" aria-label="Your intent" className="flex flex-col gap-s rounded-lg bg-surface-high px-m py-s">
          <p data-type="body-s" className="text-on-surface">
            {checking ? `Checking saved items · ${n(run.done)} of ${n(run.total)}`
              : paused ? "Your intent is paused. It isn't checking new items." : 'Your intent checks each new item you add.'}
          </p>
          {checking && <p data-type="caption" className="text-on-surface-var">You can keep working. New findings show up here as they come.</p>}
          <div className="flex flex-wrap gap-s">
            {checking
              ? <Button variant="secondary" size="sm" onClick={() => void stop()} className={TOUCH_TARGET}>Stop</Button>
              : <Button variant="secondary" size="sm" onClick={() => flip(paused)} className={TOUCH_TARGET}>{paused ? 'Resume' : 'Pause'}</Button>}
            <Button variant="secondary" size="sm" onClick={edit} className={TOUCH_TARGET}>Change what it looks for</Button>
            {!checking && (
              <Button variant="secondary" size="sm" onClick={() => void runOnSaved(intent)} disabled={paused} disabledReason={PAUSED_RUN} className={TOUCH_TARGET}>
                Run on saved items
              </Button>
            )}
          </div>
          <StaleWriteNotice guard={guard} what="This intent" />
        </div>
      )}
      {first.loading && !first.data ? <ListSkeleton rows={3} what="findings" />
        : first.error && !first.data ? <LoadError what="findings" error={first.error} onRetry={first.refresh} />
        : !outcomes.length ? <p data-type="body-s" className="text-on-surface-low">Nothing found yet.</p>
        : (
          <ul aria-label="Findings" className="flex flex-col gap-xs">
            {outcomes.map((o) => (
              <li key={o.id} className="rounded-lg px-m py-s">
                <p data-type="body-s" className="text-on-surface">{o.takeaway || 'A finding with no words of its own.'}</p>
                {o.item_id
                  ? <TextLink size="sm" onClick={() => navigate(`library?item=${encodeURIComponent(`knowledge:${o.item_id}`)}`)} className={TOUCH_TARGET}>{o.item_title ? `From ${o.item_title}` : 'From an item in your library'}</TextLink>
                  : <p data-type="caption" className="text-on-surface-low">From an item you deleted. What it found is kept.</p>}
              </li>
            ))}
          </ul>
        )}
      {next && outcomes.length > 0 && (
        <Button variant="ghost" size="sm" onClick={() => void showMore()} loading={loadingMore} className={`self-start ${TOUCH_TARGET}`}>
          Show {n(Math.max(1, Math.min(FINDINGS_PAGE, total - outcomes.length)))} more
        </Button>
      )}
    </div>
  )
}

/** How many findings a findings log shows at a time. */
const FINDINGS_PAGE = 20

/** About this item: Made in, Project, Collections, Tags, Added, Size. */
function About({ item, actions }: { item: LibraryItem; actions: ActionInputs }) {
  const inThis = !!actions.project && item.project?.id === actions.project
  const rows: [string, ReactNode][] = []
  if (item.from?.title && refKind(item.from.ref) === 'chat') {
    rows.push(['Made in', <>{item.from.title} · <TextLink onClick={() => actions.navigate(`chat/${encodeURIComponent(refId(item.from!.ref))}`)} className={TOUCH_TARGET}>Show the chat</TextLink></>])
  } else if (item.contributor) rows.push(['Added by', item.contributor])
  if (refKind(item.ref) === 'knowledge' || refKind(item.ref) === 'artifact') {
    rows.push(['Project', item.project
      ? inThis ? item.project.name: <>{item.project.name} · <TextLink onClick={() => actions.navigate(`projects/${encodeURIComponent(item.project!.id)}`)} className={TOUCH_TARGET}>Open project</TextLink></>
      : <>No project · <TextLink onClick={() => actions.pickProject(item)} className={TOUCH_TARGET}>Add to project…</TextLink></>])
  }
  if (item.collections.length) rows.push([item.collections.length > 1 ? 'Collections' : 'Collection', item.collections.map((c) => c.name).join(', ')])
  if (item.tags.length) {
    rows.push(['Tags', (
      <span className="flex flex-wrap gap-xs">
        {item.tags.map((t) => <span key={t} data-type="caption" className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">{t}</span>)}
      </span>
    )])
  }
  const added = epochSeconds(item.created_at)
  if (added !== undefined) rows.push(['Added', dayWords(added * 1000)])
  const size = sizeWords(item.size)
  if (size) rows.push(['Size', size])
  if (!rows.length) return null
  return (
    <section aria-labelledby={`lib-about-${item.ref}`} className="flex flex-col gap-s border-t border-outline-variant/40 pt-m">
      <h3 id={`lib-about-${item.ref}`} data-type="label-l" className="text-on-surface">About this item</h3>
      <dl className="grid grid-cols-[8rem_1fr] gap-x-m gap-y-xs max-md:grid-cols-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt data-type="body-s" className="text-on-surface-low">{k}</dt>
            <dd data-type="body-s" className="min-w-0 text-on-surface">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

/** A button that opens a menu of groups (Use in chat ▾): each group under its caption, a break between
 *  groups, a phone sheet with `phoneSheet`. */
function MenuButton({ label, icon, groups, phoneSheet, tonal }: { label: string; icon: ReactNode; groups: ActionGroup[]; phoneSheet: boolean; tonal?: boolean }) {
  return (
    <Popover portal placement="bottom" align="left" width={340} phoneSheet={phoneSheet}
      trigger={(open, toggle) => (
        <Button variant={tonal ? 'tonal' : 'secondary'} onClick={toggle} ariaExpanded={open} className={TOUCH_TARGET}>
          {icon} {label} <ChevronDown size={14} aria-hidden />
        </Button>
      )}>
      {(close) => (
        <div className="flex flex-col gap-0.5">
          {groups.map((g, gi) => (
            <div key={g.group || gi} className="flex flex-col gap-0.5">
              {gi > 0 && <div role="separator" className="mx-m my-0.5 h-px bg-outline-variant" />}
              {g.group && <p data-type="caption" className="px-m pt-xs text-on-surface-low">{g.group}</p>}
              {g.rows.map((r) => (
                <MenuRow key={r.label} label={r.label} hint={r.disabled ? r.disabledReason: r.hint} disabled={r.disabled} icon={<r.icon size={15} />}
                  onClick={() => { if (r.disabled) return; r.onSelect(); close() }} />
              ))}
            </div>
          ))}
        </div>
      )}
    </Popover>
  )
}

/** More: the item's ⋯, the same rows as the row's menu. */
function MoreMenu({ title, rows, phoneSheet }: { title: string; rows: ReturnType<typeof moreRows>; phoneSheet: boolean }) {
  return (
    <Popover portal placement="bottom" align="right" width={280} phoneSheet={phoneSheet}
      trigger={(open, toggle) => (
        <IconButton icon={MoreHorizontal} label={`More for ${title}`} onClick={toggle} expanded={open} className={TOUCH_TARGET} />
      )}>
      {(close) => (
        <div className="flex flex-col gap-0.5">
          {rows.map((r) => (
            <div key={r.label} className={r.danger && !r.disabled ? '[&_span]:!text-danger' : undefined}>
              <MenuRow icon={r.icon} label={r.label} hint={r.hint} disabled={r.disabled}
                onClick={() => { if (r.disabled) return; r.onSelect(); close() }} />
            </div>
          ))}
        </div>
      )}
    </Popover>
  )
}
