import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Archive, ArchiveRestore, Code2, FolderKanban, Download, FolderTree, ListChecks, MessageSquare, Palette, Pencil, Pin, Plus, Repeat, Star, Target, Telescope, Trash2 } from 'lucide-react'
import type { RouteProps } from '../../app/useQueryState'
import { useGatewayConnection } from '../../app/gatewayConnection'
import { reportingWrite } from '../../app/reportingWrite'
import { notify } from '../../app/appSdk'
import { useProjectPageActions } from '../../app/projectPageActions'
import { api, ApiError, type LibraryItem, type LoopKind, type ProjectItem, type WorkRow } from '../../lib/api'
import { invalidateKeys, useQuery } from '../../lib/data'
import { DEFAULT_PROJECT_KEY, useDefaultProject, useProjectPins } from '../../lib/defaultProject'
import { downloadFrom } from '../../lib/download'
import { clockTime } from '../../lib/epoch'
import { loopProjectId, PROJECT_LIST_KEY } from '../../lib/loopProject'
import { loopRoute } from '../../lib/loopKind'
import { useNeedsYou } from '../../lib/useNeedsYou'
import { useRunningList } from '../../lib/useRunningList'
import { useThreads } from '../../lib/useThreads'
import { useWorkspaceState } from '../../lib/useWorkspaceMissing'
import { useStaleAt } from '../../lib/waitingList'
import { Button } from '../../ui/Button'
import { HeaderActions, HeaderControl, HeaderMenu } from '../../ui/HeaderActions'
import { HeaderSearch } from '../../ui/HeaderSearch'
import { InlineError } from '../../ui/InlineError'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { ResultAnnouncement } from '../../ui/ListControls'
import { PageTitle } from '../../ui/PageTitle'
import { PaneHost, type PaneContent } from '../../ui/PaneHost'
import { ProjectMark } from '../../ui/ProjectMark'
import { StatusPill } from '../../ui/StatusPill'
import { TextLink } from '../../ui/TextLink'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { TopBar } from '../../ui/TopBar'
import { WorkspacePicker } from '../code/WorkspacePicker'
import { useNeedActions } from '../dashboard/useNeedActions'
import { NeedDetail, needName, rowKey } from '../dashboard/NeedCards'
import { TaskList } from '../tasks/TasksSection'
import { TaskCreateForm } from '../tasks/TaskCreatePage'
import { TaskDetail } from '../tasks/TaskDetail'
import { BriefPart, FolderPart, InstructionsPart, KnowledgePart, ProjectAbout, SecretsPart, ToldPart } from './ProjectAbout'
import { FolderTree as FolderTreeDetail, ToldDetail } from './ProjectDetails'
import { ProjectNeedsYou, ProjectRunning } from './ProjectLive'
import { RecentGroup, ResultsGroup, TasksGroup, UpNextGroup } from './ProjectWork'
import { useProjectLibrary } from './useProjectLibrary'
import { chatLaunchPath } from '../../app/linkState'
import { LibraryAddForm, type AddForm } from '../../ui/LibraryAddMenu'
import { LibraryPreview } from '../../ui/LibraryPreview'
import { ProjectPickerDialog } from '../../ui/ProjectPicker'
import { ChatPicker } from '../chat/ChatPicker'
import { chipRefOf, fileInto, LAST_PROJECT_KEY, readLast, type ActionInputs } from '../library/libraryActions'
import { useWriteUps } from '../library/useLibrary'
import { ProjectStartBox } from './ProjectStartBox'
import { ProjectTitle } from './ProjectTitle'
import { confirmArchiveProject, confirmDeleteProject, confirmForceDeleteProject } from './projectConfirms'
import { liveWork, listedLoops, projectLive, projectTaskOf, projectTasks, recentRows, upNextRows, upNextWork } from './projectPageModel'
import { openValue, projectDetail, retiredPanelPatch, type ProjectDetail } from './projectPageAddresses'

/** ── A project's page (`#/projects/<id>`): run and supervise the work in one project ──────────
 *
 *  Home's shape for one project, plus its plan, its history and its context. First on screen, under
 *  the header, the box to start a chat here; then what needs you, what is running, what comes next,
 *  the project's tasks, what its work made and the rest (`projectPageModel` places each piece of
 *  work in one group). About this project sits beside the work from 60rem of content, after it
 *  below that. One detail at a time opens beside the work (`?open=`, pushed, so Back closes it, and
 *  Esc closes it with focus back where it came from, `PaneHost`); while one is open it takes
 *  About's column and gives it back on close.
 *
 *  What its work made (Results) and what its agents know (About › Knowledge) are the library's,
 *  from one read (`useProjectLibrary`): its cards and compact rows, its preview beside the work,
 *  and its Add and add forms, filing into the project. From 320 to 768 px the header's New and
 *  "…", Add and the preview's menus open as phone sheets, the library's form for a menu there.
 *
 *  Its address (`projectPageAddresses`): `?q=` searches the page's rows, `?open=` the detail,
 *  `?rename=1` opens the title in edit mode (read once, dropped with a replace whether or not the
 *  project loads), `?tab=tasks` (with the Tasks view's keys) the project's Tasks view. The retired
 *  `?panel=` values are mapped as the page opens, before the project read answers.
 *
 *  Each source is read alone and fails alone: a failed one says so in its own words where it
 *  stands, "The rest of the page is still here.", and the rest draw. The project read failing is
 *  `LoadError`'s; a 404 says "This project no longer exists." (a failed read is not a deletion). */

/** The keys only the Tasks view reads, which the overview leaves behind. */
const TASK_VIEW_KEYS = ['view', 'filter', 'q', 'sort', 'list', 'tag', 'open', 'edit'] as const

/** What New starts, in the copy deck's order. */
const NEW_KINDS: { kind?: LoopKind; label: string; hint: (name: string, folder: boolean) => string; icon: typeof Repeat }[] = [
  { kind: 'goal', label: 'Goal loop', hint: () => 'Works toward a goal you can check', icon: Target },
  { kind: 'code', label: 'Code session', hint: (name, folder) => (folder ? `Changes the code in ${name}'s folder` : 'Starts a new codebase'), icon: Code2 },
  { kind: 'research', label: 'Research', hint: () => 'Reads up and writes a report', icon: Telescope },
  { kind: 'design', label: 'Design', hint: () => 'Shapes a design system and its tokens', icon: Palette },
  { kind: undefined, label: 'Loop', hint: () => "Works on a task until it's done", icon: Repeat },
]

const UNREACHABLE = "Can't reach PersonalClaw"

/** The one write a project's page makes to the project, from both its views, and the reads it
 *  refreshes: the project, the list, and the default (archiving the default makes it "none"). */
async function patchProject(id: string, body: Record<string, unknown>): Promise<void> {
  await api.updateProject(id, body)
  invalidateKeys(`projects:detail:${id}`); invalidateKeys(PROJECT_LIST_KEY); invalidateKeys(DEFAULT_PROJECT_KEY)
}

/** A name the owner typed: it also stops agents renaming the project (`name_locked`). */
const renameBody = (name: string) => ({ name, name_locked: true })

/** The address's retired keys, mapped once as the page opens, before any read answers. */
function useRetiredKeys(query: Record<string, string>, setQuery: RouteProps['setQuery']) {
  const panel = query.panel
  useEffect(() => {
    const patch = retiredPanelPatch(panel)
    if (patch) setQuery(patch, { replace: true })
  }, [panel, setQuery])
}

export function ProjectPage({ id, navigate, query, setQuery, navEpoch }: { id: string } & Pick<RouteProps, 'navigate' | 'query' | 'setQuery' | 'navEpoch'>) {
  useRetiredKeys(query, setQuery)
  const { data: project, error: projectError, refresh } = useQuery(`projects:detail:${id}`, () => api.project(id))
  // The title from the cached list at once, while the project read is out.
  const { data: cachedList } = useQuery<ProjectItem[]>(PROJECT_LIST_KEY, () => api.projects(), { persist: true })
  const cached = cachedList?.find((p) => p.id === id)
  const showsTasks = query.tab === 'tasks'

  // `?rename=1` is read once and dropped with a replace, whether or not the project loads.
  const [renameAsked, setRenameAsked] = useState(false)
  useEffect(() => {
    if (query.rename !== '1') return
    setRenameAsked(true)
    setQuery({ rename: null }, { replace: true })
  }, [query.rename, setQuery])

  const gone = projectError instanceof ApiError && projectError.status === 404
  if (!project && gone) {
    return (
      <PageFrame title="Project" navigate={navigate}>
        <div className="flex h-full flex-col items-center justify-center gap-s text-center">
          <p data-type="body-m" className="text-on-surface">This project no longer exists.</p>
          <Button onClick={() => navigate('projects')} className={TOUCH_TARGET}>Back to Projects</Button>
        </div>
      </PageFrame>
    )
  }
  if (!project && projectError) {
    return <PageFrame title={cached?.name ?? 'Project'} navigate={navigate}><LoadError what="project" error={projectError} onRetry={refresh} /></PageFrame>
  }
  if (showsTasks) {
    return <ProjectTasksView id={id} project={project ?? cached ?? null} navigate={navigate} query={query} setQuery={setQuery} navEpoch={navEpoch}
      renameAsked={renameAsked} onRenameTaken={() => setRenameAsked(false)} />
  }
  return <ProjectOverview id={id} project={project ?? null} cached={cached ?? null} projects={cachedList} navigate={navigate} query={query} setQuery={setQuery}
    renameAsked={renameAsked} onRenameTaken={() => setRenameAsked(false)} refresh={refresh} />
}

/** The page's frame for the states with no project to draw: its header's way back, and the title. */
function PageFrame({ title, navigate, children }: { title: string; navigate: (to: string) => void; children: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <TopBar keepCornerPadding left={<div className="flex min-w-0 items-center gap-s">
        <TextLink onClick={() => navigate('projects')} className="max-lg:hidden">Projects</TextLink>
        <Button variant="ghost" size="sm" ariaLabel="Back to Projects" onClick={() => navigate('projects')} className={`lg:hidden ${TOUCH_TARGET}`}><ArrowLeft size={16} /></Button>
        <PageTitle className="truncate">{title}</PageTitle>
      </div>} />
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </div>
  )
}

/** The project's Tasks view: the Tasks page's list and board, held to the project, with the
 *  project's name back to its page and Tasks as its crumb. */
function ProjectTasksView({ id, project, navigate, query, setQuery, navEpoch, renameAsked, onRenameTaken }: {
  id: string
  project: ProjectItem | null
  renameAsked: boolean
  onRenameTaken: () => void
} & Pick<RouteProps, 'navigate' | 'query' | 'setQuery' | 'navEpoch'>) {
  const toOverview = () => setQuery({ tab: null, ...Object.fromEntries(TASK_VIEW_KEYS.map((k) => [k, null])) }, { replace: true })
  const name = project?.name ?? ''
  const crumb = (
    <div className="flex min-w-0 items-center gap-2">
      <PageTitle className="flex min-w-0 items-baseline gap-xs">
        <TextLink onClick={toOverview} title={`${name}: its page`} className="min-w-0 shrink-[100] truncate">{name}</TextLink>{' '}
        <span data-title-keep className="flex shrink-0 items-baseline gap-xs whitespace-nowrap">
          <span aria-hidden className="text-on-surface-low">›</span>{' '}
          <span>Tasks</span>
        </span>
      </PageTitle>
    </div>
  )
  // Rename is the project's, so its Tasks view offers it too, editing the name in the header: low,
  // so it is the first into the "…" when the row is tight. Escape or Cancel gives the crumb back.
  const [renaming, setRenaming] = useState(false)
  useEffect(() => {
    if (!renameAsked || !project) return
    onRenameTaken()
    if (!project.is_builtin) setRenaming(true)
  }, [renameAsked, project, onRenameTaken])
  const rename = async (next: string) => {
    try {
      await patchProject(id, renameBody(next))
      return true
    } catch (e) {
      notify(`Couldn't rename ${name}: ${(e as Error).message || 'unknown error'}`, 'error')
      return false
    }
  }
  return (
    <TaskList navigate={navigate} query={query} setQuery={setQuery} navEpoch={navEpoch}
      inProject={{
        id, name,
        title: renaming ? <ProjectTitle name={name} editing onEditingChange={setRenaming} onSave={rename} /> : crumb,
        // Kept as the Tasks view was: its own Rename, then All projects (§5.7).
        actions: (
          <>
            {project && !project.is_builtin ? <HeaderControl icon={Pencil} label="Rename" priority="low" onClick={() => setRenaming(true)} /> : null}
            <HeaderControl icon={FolderKanban} label="All projects" onClick={() => navigate('projects')} />
          </>
        ),
      }} />
  )
}

function ProjectOverview({ id, project, cached, projects, navigate, query, setQuery, renameAsked, onRenameTaken, refresh }: {
  id: string
  project: ProjectItem | null
  cached: ProjectItem | null
  /** The page's projects read (`PROJECT_LIST_KEY`): the library preview's quick "Move to <the
   *  project you were last in>" row names one from it. Unread or failed, the row is left out and
   *  Add to project… stays, whose picker reads the list itself and says when it can't. */
  projects: ProjectItem[] | undefined
  renameAsked: boolean
  onRenameTaken: () => void
  refresh: () => void
} & Pick<RouteProps, 'navigate' | 'query' | 'setQuery'>) {
  const shown = project ?? cached
  const name = shown?.name ?? ''
  const aboutId = useId()
  const connection = useGatewayConnection()
  const offline = connection.state === 'disconnected'
  const offReason = offline ? UNREACHABLE : undefined
  const archived = shown?.status === 'archived'
  const archivedReason = archived ? `Restore ${name} to start new work in it.` : undefined
  const startReason = offReason ?? archivedReason
  const [err, setErr] = useState<string | null>(null)
  const [pickFolder, setPickFolder] = useState(false)

  // ── The reads, each on its own ──
  const needs = useNeedsYou()
  const runningRead = useRunningList()
  const threadsRead = useThreads()
  const { data: board, error: boardError, refresh: refreshBoard } = useQuery(`projects:work:${id}`, () => api.projectWork(id), { persist: true })
  const { data: lists, error: listsError, refresh: refreshLists } = useQuery(`projects:lists:${id}`, () => api.taskLists(id))
  const { data: tasksRead, error: tasksError, refresh: refreshTasks } = useQuery('tasks-all', () => api.allTasks(), { persist: true })
  const { data: told, error: toldError, refresh: refreshTold } = useQuery(`projects:told:${id}`, () => api.alwaysOn(id))
  const { data: proposals } = useQuery('learning:proposals:project_instruction', () => api.learningProposals({ kind: 'project_instruction' }))
  const { data: pcfg } = useQuery('config:personalclaw', () => api.personalclawConfig())
  // What its work made and what its agents know: one library read, split by store.
  const library = useProjectLibrary(id)
  const writeUps = useWriteUps()
  const [pickingChat, setPickingChat] = useState<LibraryItem | null>(null)
  const [pickingProject, setPickingProject] = useState<LibraryItem | null>(null)
  const adaptersEnabled = Boolean((pcfg as { legibility?: { context_adapters?: boolean } } | undefined)?.legibility?.context_adapters)
  const folderState = useWorkspaceState(project?.workspace_dir, { recheck: project })
  const { defaultProjectId, setDefaultProject, pending: defaultPending } = useDefaultProject()
  const { pins, changePins, settled: pinsSettled, error: pinsError } = useProjectPins()
  const isDefault = defaultProjectId === id
  const pinned = pins.includes(id)

  // ── The groups (`projectPageModel`) ──
  const waitingList = needs.data?.waiting
  const live = useMemo(() => projectLive(id, waitingList, runningRead.data), [id, waitingList, runningRead.data])
  const liveRefs = useMemo(() => liveWork(live), [live])
  const upNext = useMemo(() => upNextRows(board, liveRefs), [board, liveRefs])
  const threads = useMemo(() => threadsRead.groups.flatMap((g) => g.threads), [threadsRead.groups])
  const threadsFailed = !!threadsRead.error && threadsRead.groups.length === 0
  const loops = useMemo(() => listedLoops(threadsFailed ? null : threadsRead.loops ?? null, id,
    (l) => loopProjectId(l, null)), [threadsFailed, threadsRead.loops, id])
  // Read as unknown: a malformed answer is no list, never one that says the project has no tasks.
  const taskRows = Array.isArray(tasksRead?.tasks) ? tasksRead.tasks : null
  const listRows = Array.isArray(lists) ? lists : null
  const tasks = useMemo(() => (taskRows && listRows ? projectTasks(taskRows, listRows, loops) : null), [taskRows, listRows, loops])
  const recent = useMemo(() => recentRows(id, threads, board, new Set([...liveRefs, ...upNextWork(upNext)])), [id, threads, board, liveRefs, upNext])
  const section = (n: string) => board?.sections?.find((s) => s.name === n)
  const runsFailed = (!!boardError && !board) || section('runs')?.status === 'error'
  const loopsFailed = (!!boardError && !board) || section('loops')?.status === 'error'

  // ── Search: the page's rows by title ──
  const q = query.q ?? ''
  const needle = q.trim().toLowerCase()
  const matches = (title: string) => !needle || title.toLowerCase().includes(needle)

  // ── The detail beside the work ──
  const detail: ProjectDetail | null = projectDetail(query.open)
  const openDetail = (d: ProjectDetail) => setQuery({ open: openValue(d) })
  const closeDetail = () => setQuery({ open: null })
  /** Open a library item's preview beside the work, by its ref (`artifact:<slug>`, `knowledge:<id>`). */
  const openItem = (item: Pick<LibraryItem, 'ref'>) => {
    const at = item.ref.indexOf(':')
    const kind = item.ref.slice(0, at), rest = item.ref.slice(at + 1)
    if (kind === 'artifact') openDetail({ kind: 'artifact', slug: rest })
    else if (kind === 'knowledge') openDetail({ kind: 'knowledge', id: rest })
  }
  const openAdd = (form: AddForm) => openDetail({ kind: form === 'note' ? 'new-note' : form === 'link' ? 'new-link' : 'new-audio' })
  const waitingItems = useMemo(() => needs.rows.flatMap((r) => (r.origin === 'waiting' ? [r.item] : [])), [needs.rows])
  const now = useStaleAt(waitingItems)
  const { outcomes, actions } = useNeedActions(needs.refresh)
  const needRows = useMemo(() => needs.rows.filter((r) => r.origin === 'waiting' && r.item.project_id === id), [needs.rows, id])

  // `?rename=1`: the title opens in edit mode once the project arrives; a built-in ignores it.
  const [renaming, setRenaming] = useState(false)
  useEffect(() => {
    if (!renameAsked || !project) return
    onRenameTaken()
    if (!project.is_builtin) setRenaming(true)
  }, [renameAsked, project, onRenameTaken])

  async function patch(body: Record<string, unknown>): Promise<boolean> {
    setErr(null)
    try {
      await patchProject(id, body)
      refresh()
      return true
    } catch (e) {
      setErr((e as Error).message || "Couldn't update the project")
      return false
    }
  }
  const archive = async () => { if (await confirmArchiveProject(name)) await patch({ status: 'archived' }) }
  const restore = () => void patch({ status: 'active' })
  const del = async () => {
    if (!project || !(await confirmDeleteProject(name))) return
    const run = (force: boolean) => api.deleteProject(id, force).then(() => { invalidateKeys(DEFAULT_PROJECT_KEY); invalidateKeys(PROJECT_LIST_KEY); navigate('projects') })
    try { await run(false) } catch (e) {
      if ((e as { status?: number }).status === 409 && (await confirmForceDeleteProject(name))) {
        try { await run(true) } catch (e2) { setErr(`Couldn't delete ${name}: ${(e2 as Error).message || 'unknown error'}`) }
      } else if ((e as { status?: number }).status !== 409) setErr(`Couldn't delete ${name}: ${(e as Error).message || 'unknown error'}`)
    }
  }
  const exportIt = async () => {
    try { await downloadFrom(api.projectExportUrl(id)) } catch (e) { setErr(`Couldn't export ${name}: ${(e as Error).message || 'unknown error'}`) }
  }
  const togglePin = () => void changePins((now) => (now.includes(id) ? now.filter((p) => p !== id) : [...now, id]))
  const pinOff = offReason ?? (!pinsSettled || pinsError ? "Couldn't read your pins." : archived && !pinned ? "Restore it first. An archived project can't be pinned." : undefined)

  // ── Starting and resuming work ──
  const launch = (kind?: LoopKind) => navigate(`loop?project=${encodeURIComponent(id)}` + (kind ? `&kind=${kind}` : ''))
  const startBox = useRef<HTMLDivElement>(null)
  const focusStart = () => startBox.current?.querySelector<HTMLElement>('textarea, [contenteditable="true"]')?.focus()
  // A starter's prompt, put in the start box for the owner to send (each press a new one).
  const [startFill, setStartFill] = useState<{ text: string; at: number } | null>(null)
  const act = async (row: WorkRow, action: 'start' | 'resume') => {
    if (row.source === 'loop') {
      await reportingWrite(`${action} this loop`, () => api.uLoopAction(row.run_id, action))
      invalidateKeys(`projects:work:${id}`)
      navigate(loopRoute({ id: row.run_id, kind: row.kind as LoopKind }))
      return
    }
    await reportingWrite('resume this run', () => api.resumeWorkflowRun(row.run_id, {}))
    invalidateKeys(`projects:work:${id}`)
    navigate(`workflows/runs/${row.run_id}`)
  }

  // The palette's rows for this page, and ⌘N: published while the page is open (`projectPageActions`).
  const [briefAsk, setBriefAsk] = useState(0)
  const [instructionsAsk, setInstructionsAsk] = useState(0)
  const toAbout = () => document.getElementById(aboutId)?.scrollIntoView({ block: 'nearest' })
  useProjectPageActions(project ? {
    id, name: project.name,
    startChat: focusStart,
    newGoalLoop: () => launch('goal'),
    newTask: () => openDetail({ kind: 'new-task' }),
    editBrief: () => { closeDetail(); toAbout(); setBriefAsk((n) => n + 1) },
    editInstructions: () => { closeDetail(); setInstructionsAsk((n) => n + 1) },
    openFolder: () => (project.workspace_dir ? openDetail({ kind: 'folder' }) : setPickFolder(true)),
    told: () => openDetail({ kind: 'told' }),
  } : null)

  const needsAll = needs.data !== undefined && runningRead.data !== undefined
  const liveFailed = (!needs.data && !!needs.error) || (!runningRead.data && !!runningRead.error)
  const allClear = needsAll && !liveFailed && live.unreadable.length === 0 && live.needsYou.length === 0 && live.running.length === 0
  const firstRun = !!project && needsAll && !liveFailed && live.needsYou.length === 0 && live.running.length === 0 && upNext.length === 0
    && !!tasks && !tasks.any && recent.length === 0 && !threadsFailed && !runsFailed

  // What the library's preview does here: its New chat with this opens the chat in this project,
  // and its Open adds `back=project:<id>` (`libraryActions`).
  const libActions: ActionInputs = {
    navigate: (path) => navigate(path),
    writeUps,
    project: id,
    projects: (projects ?? []).map((p) => ({ id: p.id, name: p.name })),
    chatTitle: (key) => threads.find((t) => t.id === key || t.key === key)?.title ?? '',
    pickChat: setPickingChat,
    pickProject: setPickingProject,
    offReason,
  }

  const pane: PaneContent | null = !project || !detail ? null : (() => {
    switch (detail.kind) {
      case 'need': {
        const row = needRows.find((r) => rowKey(r) === `need:${detail.key}` || r.key === detail.key)
        return { id: `need:${detail.key}`, title: row ? needName(row) : 'Needs you', label: 'detail', onClose: closeDetail,
          body: row ? <NeedDetail row={row} outcome={outcomes[row.key]} actions={actions} now={now} />
            : <Gone text="This is no longer waiting on you." onClose={closeDetail} /> }
      }
      case 'task': {
        // Only one of this project's own tasks opens here: another project's would draw its Edit and
        // Delete under this project's header.
        const found = taskRows && listRows ? projectTaskOf(taskRows, listRows, detail.id) : null
        const t = found?.task ?? null
        return { id: `task:${detail.id}`, title: t?.title ?? 'Task', label: 'task', icon: ListChecks, onClose: closeDetail,
          body: !found ? <p data-type="body-s" className="px-m py-s text-on-surface-low">Loading…</p>
            : t ? <div className="px-m py-s"><TaskDetail task={t} onSaved={() => refreshTasks()} onDeleted={() => { refreshTasks(); closeDetail() }}
              editing={query.edit === '1'} onEditingChange={(v) => setQuery({ edit: v ? '1' : null }, { replace: true })}
              allTasks={taskRows ?? []} onOpenTask={(tid) => openDetail({ kind: 'task', id: tid })} /></div>
            : <Gone text={found.where === 'elsewhere' ? `This task isn't in ${name}.` : 'This task no longer exists.'} onClose={closeDetail} /> }
      }
      case 'new-task':
        return { id: 'task:new', title: 'New task', label: 'task', icon: ListChecks, onClose: closeDetail,
          body: <div className="flex h-full flex-col"><TaskCreateForm compact projectId={id} onCancel={closeDetail}
            onCreated={(t) => { refreshTasks(); refreshLists(); openDetail({ kind: 'task', id: t.id }) }} /></div> }
      case 'folder':
        return project.workspace_dir ? { id: 'folder', title: project.workspace_dir, label: 'folder', icon: FolderTree, onClose: closeDetail,
          body: <FolderTreeDetail path={project.workspace_dir} navigate={navigate} /> }
          : { id: 'folder', title: 'Folder', label: 'folder', onClose: closeDetail, body: <Gone text={`${name} has no folder.`} onClose={closeDetail} /> }
      case 'told':
        return { id: 'told', title: `What a new chat in ${name} is told`, label: 'detail', onClose: closeDetail,
          body: <ToldDetail projectId={id} projectName={name} read={told} failed={!told && !!toldError} onRetry={refreshTold} /> }
      case 'artifact':
      case 'knowledge': {
        const ref = detail.kind === 'artifact' ? `artifact:${detail.slug}` : `knowledge:${detail.id}`
        const item = [...library.results.rows, ...library.knowledge.rows].find((i) => i.ref === ref)
        // The pane names the group it came from: the preview's own heading is the item's title.
        return { id: ref, title: detail.kind === 'artifact' ? 'Result' : 'Knowledge item', label: 'item', onClose: closeDetail,
          body: item ? <LibraryPreview key={item.ref} item={item} actions={libActions} phoneSheet />
            : library.loading ? <p data-type="body-s" className="px-m py-s text-on-surface-low">Loading…</p>
              : <Gone text="Nothing in your library has this address." onClose={closeDetail} /> }
      }
      case 'new-note':
      case 'new-link':
      case 'new-audio': {
        const form: AddForm = detail.kind === 'new-note' ? 'note' : detail.kind === 'new-link' ? 'link' : 'audio'
        return { id: openValue(detail), title: form === 'note' ? 'New note' : form === 'link' ? 'Add a link' : 'Record audio', label: 'form', onClose: closeDetail,
          body: <LibraryAddForm key={form} form={form} project={{ id, name }} onClose={closeDetail}
            onAdded={(ref) => { library.refresh(); if (ref) openItem({ ref }); else closeDetail() }} /> }
      }
      default:
        return null
    }
  })()

  // ── The header ──
  const menuItems = [
    { label: 'Chat', icon: MessageSquare, hint: `Talk it through in ${name}`, onSelect: focusStart },
    ...NEW_KINDS.map((k) => ({ label: k.label, icon: k.icon, hint: k.hint(name, !!project?.workspace_dir), onSelect: () => launch(k.kind) })),
    { label: 'Task', icon: ListChecks, hint: `Adds a task to ${name}`, onSelect: () => openDetail({ kind: 'new-task' }) },
  ]
  const builtIn = !!shown?.is_builtin
  const left = (
    <div className="flex min-w-0 items-center gap-s">
      <TextLink onClick={() => navigate('projects')} className="shrink-0 max-lg:hidden">Projects</TextLink>
      <span aria-hidden className="text-on-surface-low max-lg:hidden">›</span>
      <Button variant="ghost" size="sm" ariaLabel="Back to Projects" onClick={() => navigate('projects')} className={`lg:hidden ${TOUCH_TARGET}`}><ArrowLeft size={16} /></Button>
      {name ? <ProjectMark name={name} /> : null}
      <ProjectTitle name={name} editing={renaming} onEditingChange={setRenaming} disabledReason={offReason}
        onSave={(next) => patch(renameBody(next))} />
    </div>
  )
  const right = (
    <HeaderActions overflowClassName={TOUCH_TARGET} overflowPhoneSheet>
      <HeaderSearch value={q} onChange={(v) => setQuery({ q: v || null }, { replace: true })} placeholder={`Search ${name || 'this project'}`} />
      <HeaderControl icon={Pin} label={pinned ? 'Unpin' : 'Pin to the top'} onClick={togglePin} inOverflow disabled={!!pinOff} disabledReason={pinOff} />
      <HeaderControl icon={Star} label={isDefault ? 'Default project' : 'Make default project'} inOverflow
        onClick={() => { if (!isDefault) void setDefaultProject(id) }}
        disabled={offline || defaultPending || isDefault || (archived && !isDefault)}
        disabledReason={offReason ?? (archived ? "Restore it first. An archived project can't be the default." : undefined)} />
      <HeaderControl icon={Pencil} label="Rename" inOverflow onClick={() => setRenaming(true)}
        disabled={builtIn || offline} disabledReason={offReason ?? (builtIn ? `${name} is built in. It can't be renamed.` : undefined)} />
      <HeaderControl icon={Download} label="Export" inOverflow onClick={() => void exportIt()} disabled={offline} disabledReason={offReason} />
      {archived
        ? <HeaderControl icon={ArchiveRestore} label="Restore" inOverflow onClick={restore} disabled={offline} disabledReason={offReason} />
        : <HeaderControl icon={Archive} label="Archive" inOverflow onClick={() => void archive()} disabled={offline} disabledReason={offReason} />}
      <HeaderControl icon={Trash2} label="Delete project…" inOverflow danger onClick={() => void del()}
        disabled={builtIn || offline} disabledReason={offReason ?? (builtIn ? `${name} is built in. It can't be deleted.` : undefined)} />
      <HeaderMenu icon={Plus} label="New" variant="primary" priority="primary" title={`Start work in ${name}`} items={menuItems}
        disabled={!!startReason} className={TOUCH_TARGET} phoneSheet />
    </HeaderActions>
  )
  const madeFor = project?.made_for
  const madeForLine = madeFor ? (madeFor.name ? `Made for the ${madeFor.kind === 'code' ? 'code session' : 'goal loop'} ${madeFor.name}` : 'Made for a loop that was deleted.') : ''
  const below = shown ? (
    <div className="flex min-w-0 flex-wrap items-center gap-s">
      {project?.workspace_dir ? (
        folderState === 'missing'
          ? <StatusPill tone="warn">Folder missing</StatusPill>
          : <Button size="xs" variant="ghost" onClick={() => openDetail({ kind: 'folder' })} title={project.workspace_dir} className={`font-mono ${TOUCH_TARGET}`}>{project.workspace_dir}</Button>
      ) : null}
      {isDefault ? <span data-type="label-s" className="text-on-surface-var">Default project</span> : null}
      {builtIn ? <span data-type="label-s" className="text-on-surface-var">Built-in</span> : null}
      {archived ? <span data-type="label-s" className="text-on-surface-var">Archived</span> : null}
      {madeForLine ? <span data-type="label-s" className="text-on-surface-var">{madeForLine}</span> : null}
      {/* An in-page jump, never an address: the app's routes are its hash, so `#<id>` would leave the page. */}
      <TextLink onClick={toAbout} className={`@[60rem]/page:hidden ${TOUCH_TARGET}`}>About this project</TextLink>
    </div>
  ) : null

  const retryLive = () => { needs.refresh(); runningRead.refresh() }
  const shownNeeds = needRows.filter((r) => matches(needName(r)))
  const shownRunning = live.running.filter((r) => matches(r.name))
  const shownUpNext = upNext.filter((r) => matches(r.title))
  const shownTasks = tasks ? { ...tasks, open: tasks.open.filter((t) => matches(t.title)) } : null
  const shownRecent = recent.filter((r) => matches(r.kind === 'thread' ? r.thread.title : r.row.title))
  const shownResults = { ...library.results, rows: library.results.rows.filter((i) => matches(i.title)) }
  const shownCount = shownNeeds.length + shownRunning.length + shownUpNext.length + (shownTasks?.open.length ?? 0) + shownResults.rows.length + shownRecent.length
  const nothingMatches = !!needle && shownCount === 0
  const work = (
    <div className="flex min-w-0 flex-col gap-l">
      {offline ? (
        <p role="status" data-type="body-s" className="text-on-surface">
          {`Can't reach PersonalClaw. Showing what was loaded${connection.lastReachedAt ? ` at ${clockTime(connection.lastReachedAt)}` : ''}.`}
        </p>
      ) : null}
      {archived ? (
        <div role="status" className="flex min-w-0 flex-wrap items-center gap-s rounded-md border border-outline-variant/40 p-s">
          <p data-type="body-s" className="text-on-surface">{`${name} is archived. It's off the project pickers, and nothing in it was deleted.`}</p>
          <Button size="xs" variant="ghost-accent" onClick={restore} disabled={offline} disabledReason={offReason} className={TOUCH_TARGET}>Restore</Button>
        </div>
      ) : null}
      {err ? <InlineError onDismiss={() => setErr(null)}>{err}</InlineError> : null}
      <div ref={startBox} data-start-box className="min-w-0">
        {/* The start box: the shared Composer, handing its draft to a new chat in this project. */}
        <ProjectStartBox projectId={id} name={name} folder={project?.workspace_dir || 'the workspace'} offReason={startReason}
          folderMissing={folderState === 'missing'} navigate={navigate} fill={startFill} focusOnMount={firstRun} />
      </div>
      {liveFailed ? (
        <p role="alert" data-type="body-s" className="flex min-w-0 flex-wrap items-center gap-xs text-on-surface">
          <span>{`Couldn't check what needs you or what's running in ${name}. The rest of the page is still here.`}</span>
          <Button size="xs" variant="ghost-accent" onClick={retryLive} className={TOUCH_TARGET}>Try again</Button>
        </p>
      ) : null}
      {!needsAll && !liveFailed ? <ListSkeleton rows={2} /> : null}
      {firstRun ? (
        <FirstRun name={name} hasFolder={!!project?.workspace_dir} onChooseFolder={() => setPickFolder(true)} onWriteBrief={() => document.getElementById(aboutId)?.scrollIntoView({ block: 'nearest' })}
          onPrompt={(text) => setStartFill((f) => ({ text, at: (f?.at ?? 0) + 1 }))} />
      ) : allClear && !needle ? (
        <p data-type="body-s" className="text-on-surface-low">{`Nothing needs you in ${name}, and nothing is running.`}</p>
      ) : null}
      <ProjectNeedsYou name={name} rows={shownNeeds} unreadable={live.unreadable}
        loading={!needs.data && !needs.error} outcomes={outcomes} actions={actions} now={now}
        open={detail?.kind === 'need' ? detail.key : undefined} onOpen={(key) => openDetail({ kind: 'need', key })} off={offReason} />
      <ProjectRunning rows={shownRunning} navigate={navigate} />
      <UpNextGroup rows={shownUpNext} failed={{ runs: runsFailed, loops: loopsFailed }} onRetry={refreshBoard}
        onOpen={navigate} onAct={(row, a) => void act(row, a)} offReason={startReason} />
      <TasksGroup tasks={shownTasks}
        failed={(!tasksRead && !!tasksError) || (!lists && !!listsError)} onRetry={() => { refreshTasks(); refreshLists() }}
        openTask={detail?.kind === 'task' ? detail.id : undefined}
        onOpenTask={(tid) => openDetail({ kind: 'task', id: tid })} onNewTask={() => openDetail({ kind: 'new-task' })}
        onSeeAll={() => setQuery({ tab: 'tasks', open: null, q: null }, { replace: true })} offReason={startReason} />
      <ResultsGroup projectId={id} part={shownResults} open={detail?.kind === 'artifact' ? `artifact:${detail.slug}` : undefined}
        onOpen={openItem} onRetry={library.refresh} />
      <RecentGroup rows={shownRecent}
        threadsFailed={threadsFailed} runsFailed={runsFailed} onRetry={() => { threadsRead.refresh(); refreshBoard() }} onOpen={navigate} />
      {/* What the search leaves, said to a screen reader as the rows narrow (`ListControls`). */}
      <ResultAnnouncement count={shownCount} noun="items" active={!!needle} empty={`Nothing in ${name} matches “${q.trim()}”.`} />
      {nothingMatches ? (
        <p data-type="body-s" className="flex flex-wrap items-center gap-xs text-on-surface-low">
          <span>{`Nothing in ${name} matches “${q.trim()}”.`}</span>
          <Button size="xs" variant="ghost-accent" onClick={() => setQuery({ q: null }, { replace: true })} className={TOUCH_TARGET}>Clear search</Button>
        </p>
      ) : null}
    </div>
  )
  const about = project ? (
    <ProjectAbout id={aboutId}>
      <BriefPart project={project} offReason={offReason} onSave={(b) => patch({ brief: b })} editAsk={briefAsk} />
      <InstructionsPart project={project} adapters={adaptersEnabled && !!project.workspace_dir} offReason={offReason} editAsk={instructionsAsk}
        proposals={(proposals && 'rows' in proposals ? proposals.rows : []).filter((p) => p.project_id === id).length} onReview={() => navigate('learning')} />
      <FolderPart project={project} state={folderState} offReason={offReason} onOpen={() => openDetail({ kind: 'folder' })}
        onChange={() => setPickFolder(true)} onRemove={() => void patch({ workspace_dir: '' })}
        onTrustChanged={() => { invalidateKeys(`projects:detail:${id}`); refresh() }} />
      <KnowledgePart project={project} part={library.knowledge} open={detail?.kind === 'knowledge' ? `knowledge:${detail.id}` : undefined}
        onOpen={openItem} onAdd={openAdd} onRetry={library.refresh} offReason={offReason} />
      <ToldPart read={told} failed={!told && !!toldError} onSee={() => openDetail({ kind: 'told' })} onRetry={refreshTold} />
      <SecretsPart project={project} />
    </ProjectAbout>
  ) : null

  return (
    // The page's container holds the header too, so the header's About this project link reads the
    // same 60rem the work and About do (it shows only while About sits after the work).
    <div className="@container/page flex h-full flex-col overflow-hidden">
      <TopBar keepCornerPadding left={left} right={right} below={below} />
      {/* A flex row, as ChatPage gives it: PaneHost grows into it (`flex-1`), so the work's scroller
          below has a height and scrolls. In a block PaneHost took its content's height, and the frame's
          overflow cut the page at the fold. */}
      <div className="flex min-h-0 min-w-0 flex-1">
        {/* The detail scrolls in its own scroller: PaneHost's body slot gives it the pane's height and no
            overflow, and none of the details here (a need, a task, the new task form, the folder, what
            a new chat is told) scrolls itself. Each scroller's scroll padding keeps a focused control's
            ring clear of its fold: focus scrolls the control in by it, and `s` is more than the 4 px ring
            in every density (`--space-scale`). */}
        <PaneHost surface="project" size={{ def: 336, min: 320, max: 640 }}
          pane={pane && { ...pane, body: <div className="h-full scroll-py-s overflow-y-auto">{pane.body}</div> }}>
          <div className="h-full scroll-py-s overflow-y-auto">
            <div className="mx-auto grid w-full min-w-0 grid-cols-1 gap-l px-l py-l @[60rem]/page:grid-cols-[minmax(0,1fr)_336px]">
              <div className="min-w-0">{work}</div>
              {pane ? null : <div className="min-w-0">{about}</div>}
            </div>
          </div>
        </PaneHost>
      </div>
      {/* The library preview's pickers: a chat to attach the item to, and a project to file it into. */}
      {pickingChat ? (
        <ChatPicker onClose={() => setPickingChat(null)}
          onPick={(key) => { const chip = chipRefOf(pickingChat, writeUps); if (chip) navigate(chatLaunchPath({ session: key, attach: [chip] })) }} />
      ) : null}
      {pickingProject ? (
        <ProjectPickerDialog itemTitle={pickingProject.title} current={pickingProject.project} lastOpened={readLast(LAST_PROJECT_KEY)}
          defaultProject={defaultProjectId} phoneSheet onClose={() => setPickingProject(null)}
          onPick={(to) => void fileInto(pickingProject, to, libActions)} />
      ) : null}
      {pickFolder ? (
        <WorkspacePicker mode="brownfield" allowCreate onClose={() => setPickFolder(false)}
          onPick={(dir) => { setPickFolder(false); void patch({ workspace_dir: dir }) }} />
      ) : null}
    </div>
  )
}

/** A detail whose subject is gone: it says so, with Close. */
function Gone({ text, onClose }: { text: string; onClose: () => void }) {
  return (
    <div className="flex flex-col gap-s px-m py-s">
      <p data-type="body-s" className="text-on-surface">{text}</p>
      <div><Button size="xs" variant="ghost-accent" onClick={onClose} className={TOUCH_TARGET}>Close</Button></div>
    </div>
  )
}

/** What "Explain this folder" and "Plan the first tasks" put in the start box, for the owner to send. */
export const STARTER_PROMPTS = {
  explain: (name: string) => `Explain what is in ${name}'s folder: what it holds, how it is laid out, and where to start.`,
  plan: (name: string) => `Help me plan the first tasks for ${name}.`,
}

/** A project nothing has happened in yet: one sentence and three starters that fit. With a folder:
 *  Explain this folder, Write the brief, Plan the first tasks; without, Choose a folder in the
 *  first's place. The two that ask the agent put a prompt in the start box for the owner to send. */
function FirstRun({ name, hasFolder, onChooseFolder, onWriteBrief, onPrompt }: {
  name: string; hasFolder: boolean; onChooseFolder: () => void; onWriteBrief: () => void; onPrompt: (text: string) => void
}) {
  return (
    <div className="flex min-w-0 flex-col gap-s">
      <p data-type="body-s" className="text-on-surface-var">{`Nothing has happened in ${name} yet. Chats, loops and tasks you start here show up on this page.`}</p>
      <div className="flex flex-wrap gap-xs">
        {hasFolder
          ? <Button size="sm" variant="secondary" onClick={() => onPrompt(STARTER_PROMPTS.explain(name))} className={TOUCH_TARGET}>Explain this folder</Button>
          : <Button size="sm" variant="secondary" onClick={onChooseFolder} className={TOUCH_TARGET}>Choose a folder</Button>}
        <Button size="sm" variant="secondary" onClick={onWriteBrief} className={TOUCH_TARGET}>Write the brief</Button>
        <Button size="sm" variant="secondary" onClick={() => onPrompt(STARTER_PROMPTS.plan(name))} className={TOUCH_TARGET}>Plan the first tasks</Button>
      </div>
    </div>
  )
}

