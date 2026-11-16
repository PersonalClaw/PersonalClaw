import { useId, useState, type ReactNode } from 'react'
import { Code2, Repeat, Workflow } from 'lucide-react'
import type { LibraryItem, TaskItem, WorkRow } from '../../lib/api'
import { previewLine } from '../../lib/previewText'
import { Button } from '../../ui/Button'
import { LibraryCard } from '../../ui/LibraryCard'
import { MarkdownLine } from '../../ui/MarkdownLine'
import { TextLink } from '../../ui/TextLink'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { WINDOWING_THRESHOLD, WindowedList } from '../../ui/WindowedList'
import { ListSkeleton } from '../../ui/ListScaffold'
import { statusMeta } from '../tasks/taskMeta'
import { runLook } from '../workflows/workflowMeta'
import { ageWords, WORK_OUTCOME_LOOK, workRoute } from './ProjectWorkRow'
import { ProjectThreadRow } from './ProjectThreadRow'
import { upNextWords, type ProjectTasks, type RecentRow } from './projectPageModel'
import type { ProjectLibraryPart } from './useProjectLibrary'

/** ── A project's page: its work groups ────────────────────────────────────────────────────────
 *
 *  Up next, Tasks and Recent, each an `h2` with its count and rows drawn only when it holds
 *  something (Tasks also when every task is done, so the Tasks view stays a click away). Needs you
 *  and Running now are Home's own cards (`ProjectLive.tsx`). Each row is one tab stop
 *  (`RowHitTarget`) with its action after it, named "<kind>: <title>, <state>". */

export function GroupHeader({ id, title, count, dot, children }: {
  id: string
  title: string
  /** The count beside the title: a number, or words ("8 open", "No open tasks."). */
  count?: ReactNode
  dot?: string
  children?: ReactNode
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-s gap-y-xs">
      <h2 id={id} data-type="title-m" className="flex items-center gap-xs text-on-surface">
        {dot ? <span aria-hidden className="size-2 shrink-0 rounded-pill" style={{ background: dot }} /> : null}
        {title}
      </h2>
      {count !== undefined && count !== null ? <span data-type="label-s" className="text-on-surface-low">{count}</span> : null}
      {children ? <span className="ml-auto flex flex-wrap items-center gap-xs">{children}</span> : null}
    </div>
  )
}

/** A source of the page that could not be read, in its own words, with Try again. */
export function SourceFailed({ text, onRetry }: { text: string; onRetry: () => void }) {
  return (
    <p role="status" data-type="body-s" className="flex min-w-0 flex-wrap items-center gap-xs text-on-surface">
      <span>{`${text} The rest of the page is still here.`}</span>
      <Button size="xs" variant="ghost-accent" onClick={onRetry} className={TOUCH_TARGET}>Try again</Button>
    </p>
  )
}

/** One row of a group: its state word, its title and its meta, with its action after it. */
function PageRow({ name, state, title, meta, selected, onOpen, action, glyph: Glyph }: {
  name: string
  state: string
  title: string
  meta?: string
  selected?: boolean
  onOpen: () => void
  action?: ReactNode
  glyph?: typeof Repeat
}) {
  return (
    <li tabIndex={-1} onClick={onOpen}
      className={`group relative grid min-w-0 cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-s rounded-md px-s py-xs text-on-surface-var hover:bg-surface-high max-lg:min-h-11 lg:min-h-9 has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary ${selected ? 'bg-surface-high' : ''}`}>
      <RowHitTarget label={name} selected={selected} />
      <span data-type="label-s" className="inline-flex min-w-0 items-center gap-xs text-on-surface">
        {Glyph ? <Glyph aria-hidden size={14} className="shrink-0 text-on-surface-var" /> : null}
        {state}
      </span>
      <span className="flex min-w-0 items-baseline gap-s">
        <span data-type="body-s" title={previewLine(title)} className="min-w-0 truncate text-on-surface"><MarkdownLine text={title} /></span>
        {meta ? <span data-type="caption" className="shrink-0 text-on-surface-low max-lg:hidden">{meta}</span> : null}
      </span>
      {action ? <span className="flex shrink-0 items-center" onClick={(e) => e.stopPropagation()}>{action}</span> : <span />}
    </li>
  )
}

const KIND_WORD: Record<string, string> = { code: 'Code session', goal: 'Goal loop', research: 'Research', design: 'Design', general: 'Loop' }

/** A board row's kind, in words: a loop by its kind, a run as Workflow run. */
function boardKind(row: Pick<WorkRow, 'source' | 'kind'>): string {
  if (row.source === 'run') return 'Workflow run'
  return KIND_WORD[row.kind] ?? 'Loop'
}

export function UpNextGroup({ rows, failed, onRetry, onOpen, onAct, offReason }: {
  rows: WorkRow[]
  /** The board's loops or runs could not be read: their own lines, and the rest draw. */
  failed: { runs: boolean; loops: boolean }
  onRetry: () => void
  onOpen: (route: string) => void
  onAct: (row: WorkRow, action: 'start' | 'resume') => void
  offReason?: string
}) {
  const id = useId()
  if (rows.length === 0 && !failed.runs && !failed.loops) return null
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-xs">
      <GroupHeader id={id} title="Up next" count={rows.length || undefined} />
      {failed.loops ? <SourceFailed text="Couldn't load its loops." onRetry={onRetry} /> : null}
      {failed.runs ? <SourceFailed text="Couldn't load its runs." onRetry={onRetry} /> : null}
      {rows.length > 0 ? (
        <ul className="flex min-w-0 flex-col gap-0.5">
          {rows.map((row) => {
            const words = upNextWords(row)
            const label = words.action === 'start' ? 'Start' : 'Resume'
            return (
              <PageRow key={`${row.source}:${row.run_id}`} name={`${boardKind(row)}: ${previewLine(row.title)}, ${words.state.toLowerCase()}`}
                state={words.state} title={row.title} meta={boardKind(row)} onOpen={() => onOpen(workRoute(row))}
                glyph={row.source === 'run' ? Workflow : row.kind === 'code' ? Code2 : Repeat}
                action={words.action ? (
                  <Button size="xs" variant="ghost-accent" ariaLabel={`${label}: ${previewLine(row.title)}`}
                    onClick={() => onAct(row, words.action!)} disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>
                    {label}
                  </Button>
                ) : undefined} />
            )
          })}
        </ul>
      ) : null}
    </section>
  )
}

/** The first open tasks the group lists. */
export const TASKS_SHOWN = 5

export function TasksGroup({ tasks, failed, onRetry, openTask, onOpenTask, onNewTask, onSeeAll, offReason }: {
  /** `null` while the tasks have not been read. */
  tasks: ProjectTasks | null
  failed: boolean
  onRetry: () => void
  /** The task whose detail is open beside the work. */
  openTask?: string
  onOpenTask: (id: string) => void
  onNewTask: () => void
  onSeeAll: () => void
  offReason?: string
}) {
  const id = useId()
  if (failed) {
    return (
      <section aria-labelledby={id} className="flex min-w-0 flex-col gap-xs">
        <GroupHeader id={id} title="Tasks" />
        <SourceFailed text="Couldn't load its tasks." onRetry={onRetry} />
      </section>
    )
  }
  if (!tasks) {
    return (
      <section aria-labelledby={id} className="flex min-w-0 flex-col gap-xs">
        <GroupHeader id={id} title="Tasks" />
        <ListSkeleton rows={3} />
      </section>
    )
  }
  if (!tasks.any) return null
  const open = tasks.open
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-xs">
      <GroupHeader id={id} title="Tasks" count={open.length ? `${open.length} open` : 'No open tasks.'}>
        <Button size="xs" variant="ghost-accent" onClick={onNewTask} disabled={!!offReason} disabledReason={offReason} className={TOUCH_TARGET}>New task</Button>
        <Button size="xs" variant="ghost-accent" onClick={onSeeAll} className={TOUCH_TARGET}>See all tasks</Button>
      </GroupHeader>
      {open.length > 0 ? (
        <ul className="flex min-w-0 flex-col gap-0.5">
          {open.slice(0, TASKS_SHOWN).map((t: TaskItem) => {
            const meta = statusMeta(t.status)
            const extra = [t.priority ? `${t.priority} priority` : '', t.due ? `due ${t.due}` : ''].filter(Boolean).join(' · ')
            return (
              <PageRow key={t.id} name={`Task: ${previewLine(t.title)}, ${meta.label.toLowerCase()}`} state={meta.label}
                title={t.title} meta={extra} selected={openTask === t.id} onOpen={() => onOpenTask(t.id)} />
            )
          })}
        </ul>
      ) : null}
    </section>
  )
}

/** How many Recent rows show before Show more. */
export const RECENT_SHOWN = 6

export function RecentGroup({ rows, threadsFailed, runsFailed, onRetry, onOpen }: {
  rows: RecentRow[]
  threadsFailed: boolean
  runsFailed: boolean
  onRetry: () => void
  onOpen: (route: string) => void
}) {
  const id = useId()
  const [more, setMore] = useState(false)
  if (rows.length === 0 && !threadsFailed && !runsFailed) return null
  const shown = more ? rows : rows.slice(0, RECENT_SHOWN)
  const row = (r: RecentRow) => r.kind === 'thread'
    ? <ProjectThreadRow key={r.key} thread={r.thread} onOpen={onOpen} />
    : <RunRow key={r.key} row={r.row} onOpen={onOpen} />
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-xs">
      <GroupHeader id={id} title="Recent" />
      {threadsFailed ? <SourceFailed text="Couldn't load its chats and loops." onRetry={onRetry} /> : null}
      {runsFailed ? <SourceFailed text="Couldn't load its runs." onRetry={onRetry} /> : null}
      {more && rows.length > WINDOWING_THRESHOLD ? (
        <WindowedList items={rows} rowKey={(r) => r.key} rowHeights="uniform" estimateRowHeight={44} noun="rows"
          findHint="use the header's search to find one." ariaLabel="Recent" className="flex flex-col gap-0.5">
          {(r) => row(r)}
        </WindowedList>
      ) : (
        <ul className="flex min-w-0 flex-col gap-0.5">{shown.map(row)}</ul>
      )}
      {rows.length > RECENT_SHOWN && !more ? (
        <div>
          <Button size="xs" variant="ghost-accent" ariaExpanded={false} onClick={() => setMore(true)} className={TOUCH_TARGET}>Show more</Button>
        </div>
      ) : null}
    </section>
  )
}

/** A finished run, in Recent: how it ended, its title and when. */
function RunRow({ row, onOpen }: { row: WorkRow; onOpen: (route: string) => void }) {
  const look = row.outcome ? WORK_OUTCOME_LOOK[row.outcome] : null
  const state = look?.label ?? runLook('completed').label
  const when = row.updated_at ? ageWords((Date.now() - (Date.parse(row.updated_at) || Date.now())) / 1000) : ''
  return (
    <PageRow name={`Workflow run: ${previewLine(row.title)}, ${state.toLowerCase()}`} state={state} title={row.title}
      meta={['Workflow run', when].filter(Boolean).join(' · ')} onOpen={() => onOpen(workRoute(row))} glyph={Workflow} />
  )
}


/** The tiles Results draws: four across a wide page, two by two on a phone. */
export const RESULTS_SHOWN = 4

/** The library's address for one of the project's stores. */
export const libraryOf = (projectId: string, store: 'artifacts' | 'knowledge') =>
  `#/library?project=${encodeURIComponent(projectId)}&store=${store}`

/** Results: the latest things the project's work made, each the library's card in its project form
 *  ("From Plan the spring beds · yesterday"), opening the library's preview beside the work. "See
 *  all 12 in Library" counts the store, as the read's facet says; a store that failed draws its line
 *  and no count. Not drawn while there is nothing, or while the read has not answered. */
export function ResultsGroup({ projectId, part, open, onOpen, onRetry }: {
  projectId: string
  part: ProjectLibraryPart
  /** The item whose preview is open beside the work, by its ref. */
  open?: string
  onOpen: (item: LibraryItem) => void
  onRetry: () => void
}) {
  const id = useId()
  if (part.failed) {
    return (
      <section aria-labelledby={id} className="flex min-w-0 flex-col gap-xs">
        <GroupHeader id={id} title="Results">
          <TextLink href={libraryOf(projectId, 'artifacts')} size="sm" className={TOUCH_TARGET}>See all in Library</TextLink>
        </GroupHeader>
        <SourceFailed text="Couldn't load what its work made." onRetry={onRetry} />
      </section>
    )
  }
  if (part.rows.length === 0) return null
  const shown = part.rows.slice(0, RESULTS_SHOWN)
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-xs">
      <GroupHeader id={id} title="Results">
        <TextLink href={libraryOf(projectId, 'artifacts')} size="sm" className={TOUCH_TARGET}>
          {part.count !== null ? `See all ${part.count} in Library` : 'See all in Library'}
        </TextLink>
      </GroupHeader>
      <ul className="grid min-w-0 grid-cols-2 gap-s @[40rem]/page:grid-cols-4">
        {shown.map((item) => (
          <li key={item.ref} className="min-w-0">
            <LibraryCard item={item} form="project" selected={open === item.ref} onOpen={() => onOpen(item)} />
          </li>
        ))}
      </ul>
    </section>
  )
}
