import { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { Plus, List, LayoutGrid, GitFork, Columns3, MessageSquare, FolderKanban, X, RotateCcw, ListChecks, Target, Code2, Check, CheckCircle2, Trash2, Users, UserRound } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { fvs } from '../../design/fontWeight'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../ui/HeaderActions'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { Button } from '../../ui/Button'
import { InlineError } from '../../ui/InlineError'
import { SearchField } from '../../ui/SearchField'
import { ResultAnnouncement } from '../../ui/ListControls'
import { TextLink } from '../../ui/TextLink'
import { confirmDelete } from '../../ui/dialog'
import { SidePanel } from '../../ui/SidePanel'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { spring, expr } from '../../design/motion'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { api, type TaskItem, type ProjectItem, type TaskListItem, type Loop } from '../../lib/api'
import { statusMeta, signalPriority, dueMeta, parseDueDate, TERMINAL, ListChecksLike, exitDoneCount } from './taskMeta'
import { TaskDetail } from './TaskDetail'
import { TaskGraph } from './TaskGraph'
import { TaskBoard } from './TaskBoard'
import { PageTitle } from '../../ui/PageTitle'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { accentChip } from '../../design/accent'

type ViewMode = 'list' | 'cards' | 'board' | 'dag'
// views that ignore the status filter (they present all statuses themselves)
const FULL_WIDTH: ViewMode[] = ['board', 'dag']
const VIEW_KEY = 'tasks-view'
// Status filter. The three real statuses take their label from `taskMeta`'s
// STATUSES — the single source of truth every other task surface renders (the
// board's columns, a row's status chip, the detail panel). Hardcoding them here
// meant the filter said "Open"/"Done" while the same keys read "Not started"/
// "Completed" three feet away on the board.
// `all` and `ready` are filter-only pseudo-states with no status equivalent
// (`ready` = unblocked and actionable, spanning open + in_progress), so they
// keep their own labels.
const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'ready', label: 'Ready' },
  ...['open', 'in_progress', 'blocked', 'done'].map((k) => ({ key: k, label: statusMeta(k).label })),
]
const SORT_KEY = 'tasks-sort'
const SORTS = [
  { key: 'recent', label: 'Recently updated' },
  { key: 'due', label: 'Due date' },
  { key: 'priority', label: 'Priority' },
]
// Scope filter — narrows the list to a slice of work, applied across every view
// (incl. board + DAG). Sentinels group tasks by origin; any other value is a
// specific Tasks Project name. Goal Loop tasks land under the "Goal Loops"
// project; Code-feature projects are the Tasks Projects backing a code project.
const SCOPE_KEY = 'tasks-scope'
const SCOPE_ALL = ''
const SCOPE_GOALS = '__goals__'
const SCOPE_CODING = '__coding__'
const GOAL_LOOPS_PROJECT = 'Goal Loops'
const ASSIGNED_EVERYONE = ''
const ASSIGNED_MINE = 'mine'

/** Whether a task is the owner's work — mirrors `Task.belongs_to` on the backend.
 *  Assignee decides when set; otherwise the author does, because an unassigned task
 *  I wrote is still mine. An unattributed task belongs to nobody in particular, so
 *  it counts as the owner's (that's how every pre-attribution task reads). */
const isMine = (t: TaskItem, owner: string) => {
  if (!owner) return true
  const assignee = (t.assignee ?? '').trim().toLowerCase()
  if (assignee) return assignee === owner.toLowerCase()
  const author = (t.author ?? '').trim().toLowerCase()
  return !author || author === owner.toLowerCase()
}
const PRIORITY_RANK: Record<string, number> = { critical: 5, high: 4, medium: 3, low: 2, trivial: 1 }
const _dueTs = (t: TaskItem) => { const v = t.due ? parseDueDate(t.due) : NaN; return Number.isNaN(v) ? Infinity : v }  // no due → last
const _updTs = (t: TaskItem) => Date.parse(t.updated_at || t.created_at || '') || 0

export function TasksListPage({ onCreate, view: viewProp, filter, openId, setView, setFilter, setOpenId,
  editing, setEditing,
  q: qProp, sort: sortProp, scope: scopeProp, list: listProp, setQ, setSort, setScope, setList }: {
  onCreate: () => void
  view: string; filter: string; openId: string | null
  setView: (v: string) => void; setFilter: (f: string) => void; setOpenId: (id: string | null) => void
  editing: boolean; setEditing: (v: boolean) => void
  q: string; sort: string; scope: string; list: string
  setQ: (v: string) => void; setSort: (v: string) => void; setScope: (v: string) => void; setList: (v: string) => void
}) {
  // The list is cache-backed for instant paint on revisit (persist:false — task
  // status is live, must not be stale across a reload), but a LOCAL mirror is kept
  // so the optimistic moveTask/patchLocal updates still apply (useCachedData has no
  // setter). The mirror hydrates from the cached data, and a post-mutation load()
  // invalidates + revalidates the cache.
  // NO `.catch(() => [])` here. It used to swallow the rejection, so `error` could never be read
  // even by a caller that tried, and a failed load arrived as an empty array — which the render
  // below then presented as "No tasks" with a create-a-task CTA. Measured with `/api/tasks` at 500
  // and a cold sessionStorage: "No tasks — Break a goal into tracked work…" plus the New-task
  // button, no alert, no retry, told to a user who may have a hundred tasks.
  const { data: cachedTasks, refresh, error: loadErr } = useCachedData('tasks', () => api.tasks().then((d) => d.tasks), { persist: false })
  const [tasks, setTasks] = useState<TaskItem[] | null>(null)
  // The configured username, so a row can say whether it's mine or someone else's.
  // Only meaningful once a shared provider actually returns other people's work, so
  // the "Assigned" filter below stays hidden until that happens.
  const [owner, setOwner] = useState('')
  // Mine-vs-everyone selection. Local rather than URL-backed: it's a viewing lens on
  // a shared board, not a shareable address (the scope/status filters that ARE worth
  // sharing live in the URL).
  const [assigned, setAssigned] = useState(ASSIGNED_EVERYONE)
  useEffect(() => {
    let alive = true
    api.tasks({ limit: 1 }).then((d) => { if (alive) setOwner(d.owner ?? '') }).catch(() => {})
    return () => { alive = false }
  }, [])
  // The "Ready" filter pulls startable tasks from the server (dependency-aware)
  // rather than filtering the loaded list, so it's kept in its own slice.
  const [ready, setReady] = useState<TaskItem[] | null>(null)
  // Server-backed search (/api/tasks/search): a non-empty query takes precedence
  // over the status filter. URL-backed (?q, replace) so it's shareable + survives
  // refresh; one Back exits search rather than rewinding keystrokes.
  const query = qProp
  const setQuery = setQ
  const [results, setResults] = useState<TaskItem[] | null>(null)
  // List sort order. URL-backed (?sort, replace); localStorage supplies the default
  // on a bare route so preference is remembered (the same URL⊃localStorage hybrid
  // `view` uses). Always keeps terminal tasks last; within that, by the chosen key.
  const sortBy = sortProp || localStorage.getItem(SORT_KEY) || 'recent'
  const setSortBy = setSort
  // Scope filter: a preset sentinel (Goals / Coding) or a specific project name.
  // URL-backed (?scope, replace) with the localStorage default on a bare route.
  // `setScope` is the prop itself (used directly below).
  const scope = scopeProp || localStorage.getItem(SCOPE_KEY) || SCOPE_ALL
  // The project + task-list catalog, loaded once so the scope dropdown can list
  // every project and the list sub-filter can resolve names.
  const [projects, setProjects] = useState<ProjectItem[]>([])
  const [allLists, setAllLists] = useState<TaskListItem[]>([])
  // Names of the Tasks Projects that back a Code-feature project — used to resolve
  // the "Coding projects" scope (there's no per-task origin flag).
  const [codingProjectNames, setCodingProjectNames] = useState<Set<string>>(new Set())
  // When a single project is scoped, an optional sub-filter by one of its lists —
  // URL-backed (?list, replace); resolve the name from the loaded catalog.
  const listFilter = listProp ? { id: listProp, name: allLists.find((l) => l.id === listProp)?.name || listProp } : null
  const setListFilter = (v: { id: string; name: string } | null) => setList(v?.id || '')
  // True when scope targets exactly one named project (vs a preset / All).
  const isProjectScope = scope !== SCOPE_ALL && scope !== SCOPE_GOALS && scope !== SCOPE_CODING
  // URL is the source of truth; fall back to the last-used view (localStorage)
  // when the URL doesn't pin one, so a bare #/tasks still respects preference.
  const view = (viewProp || localStorage.getItem(VIEW_KEY) || 'list') as ViewMode
  // Transient error surfaced when a board drag is rejected by the server (e.g. the
  // exit-criteria complete-gate), so the card snapping back isn't a silent mystery.
  const [moveError, setMoveError] = useState('')
  // Multi-select for bulk ops (list view). A non-empty set shows the bulk-action bar.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)

  // 🔴 `invalidateCache('tasks')` drops exactly that key. `TaskCreatePage` reads the SAME unfiltered
  // collection under `tasks-all` for its dependency picker, with `persist: true` — so a task created
  // or deleted here left that picker's FIRST PAINT showing the old list, and because the copy is
  // persisted, a hard reload replayed the same wrong list. (Measured, not assumed: the hook
  // revalidates on every mount, so the wrong list is replaced when the refetch lands — the cost is a
  // stale paint, not a durably wrong picker.) Prefix mode covers both keys, and any later reader of
  // the same collection that follows the `tasks*` naming.
  const load = () => { invalidateCache('tasks', true); refresh() }
  const toggleSelect = (id: string) => setSelected((prev) => {
    const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next
  })
  const clearSelection = () => setSelected(new Set())
  const runBulk = async (op: 'update' | 'delete', patch?: Record<string, unknown>) => {
    if (!selected.size || bulkBusy) return
    setBulkBusy(true)
    const items = [...selected].map((id) => (op === 'delete' ? { id } : { id, ...patch }))
    try { await api.tasksBulk(op, items) } catch { /* surfaced via reload showing unchanged */ }
    setBulkBusy(false); clearSelection(); load()
  }
  // Hydrate the local mirror whenever fresh cached data lands (initial fetch +
  // every revalidation), preserving the optimistic-update path below.
  useEffect(() => { if (cachedTasks !== undefined) setTasks(cachedTasks) }, [cachedTasks])
  useEffect(() => { const t = window.setInterval(refresh, 12000); return () => clearInterval(t) }, [refresh])
  useEffect(() => { if (viewProp) localStorage.setItem(VIEW_KEY, viewProp) }, [viewProp])
  useEffect(() => { localStorage.setItem(SORT_KEY, sortBy) }, [sortBy])
  useEffect(() => { localStorage.setItem(SCOPE_KEY, scope) }, [scope])
  // Clearing the scope (or moving off a single project) drops the list sub-filter.
  // Guard on listProp so it only writes when there's actually a ?list to clear
  // (else it would setQuery every render while unscoped — a no-op churn).
  useEffect(() => { if (!isProjectScope && listProp) setListFilter(null) }, [isProjectScope, listProp])
  // Refetch ready tasks whenever the Ready filter is active (and on tasks reload).
  useEffect(() => {
    if (filter !== 'ready') return
    api.readyTasks().then(setReady).catch(() => setReady([]))
  }, [filter, tasks])

  // Debounced server-side search; clears results when the query is emptied.
  const q = query.trim()
  useEffect(() => {
    if (!q) { setResults(null); return }
    let alive = true
    const h = window.setTimeout(() => {
      api.searchTasks({ query: q, limit: 100 })
        .then((d) => { if (alive) setResults(d.tasks) })
        .catch(() => { if (alive) setResults([]) })
    }, 250)
    return () => { alive = false; clearTimeout(h) }
  }, [q, tasks])

  // Load the project / task-list / code-project catalog once: powers the scope
  // dropdown (every project + which are code-backed) and the list sub-filter.
  useEffect(() => {
    let alive = true
    Promise.all([api.projects(), api.taskLists(), api.uLoops({ kind: 'code' }).catch(() => [] as Loop[])])
      .then(([ps, ls, cps]) => {
        if (!alive) return
        setProjects(ps)
        setAllLists(ls)
        const byId = new Map(ps.map((p) => [p.id, p.name]))
        const names = new Set<string>()
        for (const cp of cps) { const n = cp.tasks_project_id && byId.get(cp.tasks_project_id); if (n) names.add(n) }
        setCodingProjectNames(names)
      })
      .catch(() => { if (alive) { setProjects([]); setAllLists([]) } })
    return () => { alive = false }
  }, [])

  const repeatableProject = projects.find((p) => p.name === 'Repeatable')
  // The scoped project's task lists (for the list bar + Repeatable reset).
  const scopedProject = isProjectScope ? projects.find((p) => p.name === scope) : undefined
  const projectLists = useMemo(
    () => (scopedProject ? allLists.filter((l) => l.project_id === scopedProject.id) : []),
    [scopedProject, allLists],
  )

  // Does a task fall within the active scope? (preset by origin, or one project)
  const inScope = useMemo(() => {
    if (scope === SCOPE_ALL) return () => true
    if (scope === SCOPE_GOALS) return (t: TaskItem) => t.project === GOAL_LOOPS_PROJECT
    if (scope === SCOPE_CODING) return (t: TaskItem) => !!t.project && codingProjectNames.has(t.project)
    return (t: TaskItem) => t.project === scope
  }, [scope, codingProjectNames])

  // Tasks within the active scope, before status/search — feeds board + DAG
  // (which present their own statuses) so the scope applies to every view.
  const scopedTasks = useMemo(() => (tasks ?? []).filter(inScope), [tasks, inScope])

  // The unified Filter & sort menu's sections. Status hides while searching (the
  // query overrides it) and Sort hides on board/dag (they present + order their
  // own statuses) — so the menu only ever offers what the current view honors.
  const showStatus = !FULL_WIDTH.includes(view) && !q
  const showSort = view === 'list' || view === 'cards'
  const filterSections = useMemo<FilterSectionDef[]>(() => {
    const byProject = new Map<string, number>()
    let goals = 0, coding = 0
    for (const t of tasks ?? []) {
      if (t.project) {
        byProject.set(t.project, (byProject.get(t.project) ?? 0) + 1)
        if (t.project === GOAL_LOOPS_PROJECT) goals++
        if (codingProjectNames.has(t.project)) coding++
      }
    }
    const projOptions = projects.filter((p) => p.name !== GOAL_LOOPS_PROJECT).sort((a, b) => a.name.localeCompare(b.name))
    const statusCount = (key: string) => key === 'all' ? tasks?.length : key === 'ready' ? ready?.length : key === 'done' ? tasks?.filter((t) => TERMINAL.has(t.status)).length : tasks?.filter((t) => t.status === key).length

    const sections: FilterSectionDef[] = [{
      title: 'Scope', value: scope, defaultKey: SCOPE_ALL, onChange: setScope,
      options: [
        { key: SCOPE_ALL, label: 'All tasks', icon: ListChecks, count: tasks?.length },
        { key: SCOPE_GOALS, label: 'Goals', icon: Target, count: goals },
        ...(codingProjectNames.size > 0 ? [{ key: SCOPE_CODING, label: 'Coding projects', icon: Code2, count: coding }] : []),
        ...projOptions.map((p, i) => ({ key: p.name, label: p.name, icon: FolderKanban, count: byProject.get(p.name), groupLabel: i === 0 ? 'Projects' : undefined })),
      ],
    }]
    if (showStatus) sections.push({
      title: 'Status', value: filter, defaultKey: 'all', onChange: setFilter,
      options: FILTERS.map((f) => ({ key: f.key, label: f.label, count: statusCount(f.key) })),
    })
    // "Mine vs everyone" (TEAM-SHARED-ENTITIES §2.1). Hidden on a single-user
    // install: with no username, or no task belonging to anyone else, the filter
    // would be a control that can only ever be a no-op.
    const foreign = owner ? (tasks ?? []).filter((t) => !isMine(t, owner)).length : 0
    if (owner && foreign > 0) sections.push({
      title: 'Assigned', value: assigned, defaultKey: ASSIGNED_EVERYONE, onChange: setAssigned,
      options: [
        { key: ASSIGNED_EVERYONE, label: 'Everyone', icon: Users, count: tasks?.length },
        { key: ASSIGNED_MINE, label: 'Mine', icon: UserRound, count: (tasks?.length ?? 0) - foreign },
      ],
    })
    if (showSort) sections.push({
      title: 'Sort by', value: sortBy, defaultKey: 'recent', onChange: setSortBy,
      options: SORTS.map((s) => ({ key: s.key, label: s.label })),
    })
    return sections
  }, [tasks, ready, projects, codingProjectNames, scope, filter, sortBy, showStatus, showSort, owner, assigned])

  const filtered = useMemo(() => {
    let base: TaskItem[] | null
    if (q) base = results  // search overrides the status filter
    else if (filter === 'ready') base = ready
    else if (!tasks) base = null
    else base = filter === 'all' ? [...tasks] : filter === 'done' ? tasks.filter((t) => TERMINAL.has(t.status)) : tasks.filter((t) => t.status === filter)

    if (base) base = base.filter(inScope)
    if (base && listFilter) base = base.filter((t) => t.task_list_id === listFilter.id)
    if (base && owner && assigned === ASSIGNED_MINE) base = base.filter((t) => isMine(t, owner))

    // Sort: terminal tasks always sink to the bottom; within each group, by the
    // chosen key (recent updates / soonest due / highest priority first).
    if (base) {
      const cmp =
        sortBy === 'due' ? (a: TaskItem, b: TaskItem) => _dueTs(a) - _dueTs(b)
        : sortBy === 'priority' ? (a: TaskItem, b: TaskItem) => (PRIORITY_RANK[b.priority ?? ''] ?? 3) - (PRIORITY_RANK[a.priority ?? ''] ?? 3)
        : (a: TaskItem, b: TaskItem) => _updTs(b) - _updTs(a)
      base = [...base].sort((a, b) => (Number(TERMINAL.has(a.status)) - Number(TERMINAL.has(b.status))) || cmp(a, b))
    }
    return base
  }, [tasks, ready, filter, q, results, inScope, listFilter, sortBy])

  // Reset a Repeatable task list (server gates: all tasks must be done). Surfaces
  // the server message on the move-error banner on failure; reloads on success.
  async function resetList(list: TaskListItem) {
    setMoveError('')
    try { await api.resetTaskList(list.id); load() }
    catch (e) {
      const msg = e instanceof Error ? e.message : 'Could not reset the list.'
      setMoveError(`Reset “${list.name}”: ${msg}`)
      window.setTimeout(() => setMoveError(''), 6000)
    }
  }

  const open = tasks?.find((t) => t.id === openId) ?? null

  // Apply a PUT result: the edited task PLUS any tasks whose status cascaded
  // (auto-block/unblock'd dependents the server returns in `reconciled`).
  function patchLocal(updated: TaskItem) {
    const patches = new Map<string, TaskItem>()
    for (const t of updated.reconciled ?? [updated]) patches.set(t.id, t)
    if (!patches.has(updated.id)) patches.set(updated.id, updated)
    setTasks((ts) => ts?.map((t) => patches.get(t.id) ?? t) ?? null)
  }

  // Kanban drag-to-restatus: optimistic local update, then persist; revert on
  // failure AND surface why (e.g. the exit-criteria complete-gate's 400 message)
  // so the card snapping back isn't a silent, unexplained revert.
  async function moveTask(id: string, status: string) {
    const prev = tasks
    const cur = prev?.find((t) => t.id === id)
    if (!cur || cur.status === status || cur.provider === 'project') return
    setMoveError('')
    setTasks((ts) => ts?.map((t) => t.id === id ? { ...t, status } : t) ?? null)
    try { const updated = await api.updateTask(id, { status }); patchLocal(updated) }
    catch (e) {
      setTasks(prev ?? null)
      const msg = e instanceof Error ? e.message : 'Could not update the task.'
      setMoveError(`“${cur.title}” → ${status.replace('_', ' ')}: ${msg}`)
      window.setTimeout(() => setMoveError(''), 6000)
    }
  }

  return (
    <WorkbenchLayout
      scroll={false}
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle>Tasks</PageTitle>}
          right={
            // Header keeps only structural controls — the view switcher + the primary
            // action, in the 4-tier cluster so they degrade together (view icon+label →
            // icon-only; New task → icon-only → …) instead of clipping on narrow/mobile.
            // Search / filter / sort live on the page (below).
            <HeaderActions>
              <HeaderSegmented ariaLabel="View" value={view} onChange={(v) => setView(v as ViewMode)}
                options={[{ key: 'list', label: 'List view', icon: List }, { key: 'cards', label: 'Cards view', icon: LayoutGrid }, { key: 'board', label: 'Kanban board', icon: Columns3 }, { key: 'dag', label: 'Dependency graph', icon: GitFork }]} />
              <HeaderControl icon={Plus} label="New task" variant="primary" priority="primary" onClick={onCreate} />
            </HeaderActions>
          }
        />
      }
      controls={
        // On-page controls: search (hidden on board/DAG, which present all rows) +
        // the scope/status/sort filter popover. Centered to the content width.
        <div className="shrink-0 border-b border-outline-variant/30">
          <div className="mx-auto flex w-full items-center gap-s px-l py-m" style={{ maxWidth: 'var(--content-width)' }}>
            {!FULL_WIDTH.includes(view) && (
              <div className="min-w-[12rem] flex-1">
                <SearchField value={query} onChange={setQuery} placeholder="Search tasks" ariaLabel="Search tasks" />
              </div>
            )}
            <FilterMenu sections={filterSections} />
            {/* Typing here rewrites the list under the user, and nothing said so. Same idiom the
                `ListControls` adopters render — this page lays its own bar out, so it renders the
                extracted piece rather than a second copy of it. `active` is the SEARCH being
                non-empty, not `filter !== 'all'`: the status filter's own default is a preset, and a
                flag that is true at rest would announce a count to a user who has done nothing. */}
            <ResultAnnouncement count={filtered?.length ?? 0} noun="tasks" active={query.trim().length > 0} />
          </div>
        </div>
      }
      panel={open && (
        <SidePanel key={open.id} fillHeight storeKey="task-panel-w" icon={(() => { const I = statusMeta(open.status).icon; return <I size={18} style={{ color: statusMeta(open.status).tone }} /> })()} title={open.title} onClose={() => setOpenId(null)}>
          <TaskDetail task={open} editing={editing} onEditingChange={setEditing} allTasks={tasks ?? []} onOpenTask={(id) => setOpenId(id)} onSaved={(u) => { patchLocal(u); }} onDeleted={() => { setOpenId(null); load() }} />
        </SidePanel>
      )}
    >
      {/* Board is a fixed-height shell (manages its own column scroll); other
          views scroll the whole content column. */}
      {view === 'board' ? (
        // Board fills height as a shell; centered + bounded to the shell width
        // preset (the 'full' preset still fills the area — min(1600px,100%)).
        <div className="flex-1 min-h-0 px-l py-l flex flex-col gap-s">
          <div className="mx-auto w-full" style={{ maxWidth: 'var(--content-width)' }}>
            {moveError && <InlineError animated icon onDismiss={() => setMoveError('')}>{moveError}</InlineError>}
          </div>
          <div className="mx-auto h-full min-h-0 w-full" style={{ maxWidth: 'var(--content-width)' }}>
            {/* Error FIRST: `tasks === null` also satisfies the skeleton and the empty branch, so a
                later test would be unreachable. Removing the swallow above is only half the fix —
                without this branch a failed load would hang on the skeleton forever instead. */}
            {tasks === null && loadErr ? <LoadError what="tasks" error={loadErr} onRetry={() => { invalidateCache('tasks', true); refresh() }} />
              : filtered === null ? <ListSkeleton rows={6} what="tasks" /> : (tasks?.length ?? 0) === 0 ? (
              <EmptyState icon={ListChecksLike} title="No tasks" hint="Break a goal into tracked work. Create a task, or let an agent plan from a chat." action={{ label: 'New task', onClick: onCreate, icon: Plus }} />
            ) : scopedTasks.length === 0 ? (
              <EmptyState icon={ListChecksLike} title="Nothing here" hint="No tasks match this scope." />
            ) : (
              <TaskBoard tasks={scopedTasks} onOpen={(id) => setOpenId(id)} onMove={moveTask} />
            )}
          </div>
        </div>
      ) : (
        // The DAG renders an SVG canvas whose nodes are not focusable, so in that view this
        // scroll container holds NOTHING a keyboard can reach: 2785px of graph unreachable
        // (axe scrollable-region-focusable). List and Cards each expose 38 focusable rows, so
        // they need no tab stop and must not get a redundant one. Hence the per-view scope.
        <div className="flex-1 overflow-y-auto"
          tabIndex={view === 'dag' ? 0 : undefined}
          role={view === 'dag' ? 'group' : undefined}
          aria-label={view === 'dag' ? 'Dependency graph' : undefined}>
          {/* every view (incl. DAG) honors the shell content-width preset */}
          <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
            {moveError && <div className="mb-s"><InlineError animated icon onDismiss={() => setMoveError('')}>{moveError}</InlineError></div>}
            {isProjectScope && projectLists.length > 0 && (
              <TaskListBar lists={projectLists} repeatableId={repeatableProject?.id}
                active={listFilter?.id ?? ''}
                onPick={(l) => setListFilter(listFilter?.id === l.id ? null : { id: l.id, name: l.name })}
                onReset={resetList} />
            )}
            {tasks === null && loadErr ? <LoadError what="tasks" error={loadErr} onRetry={() => { invalidateCache('tasks', true); refresh() }} />
              : filtered === null ? <ListSkeleton rows={6} what="tasks" /> : (tasks?.length ?? 0) === 0 ? (
              <EmptyState icon={ListChecksLike} title="No tasks" hint="Break a goal into tracked work. Create a task, or let an agent plan from a chat." action={{ label: 'New task', onClick: onCreate, icon: Plus }} />
            ) : view === 'dag' ? (
              scopedTasks.length === 0
                ? <EmptyState icon={ListChecksLike} title="Nothing here" hint="No tasks match this scope." />
                : <TaskGraph tasks={scopedTasks} onOpen={(id) => setOpenId(id)} />
            ) : filtered.length === 0 ? (
              <EmptyState icon={ListChecksLike} title="Nothing here" hint="No tasks match this filter." />
            ) : view === 'list' ? (
              <div className="flex flex-col gap-s pb-16">
                {filtered.map((t, i) => (
                  <TaskRow key={t.id} t={t} index={i} onOpen={() => setOpenId(t.id)} onProject={setScope}
                    selected={selected.has(t.id)} selecting={selected.size > 0} onToggleSelect={() => toggleSelect(t.id)}
                    onComplete={() => moveTask(t.id, 'done')} />
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-s">
                {filtered.map((t, i) => <TaskCard key={t.id} t={t} index={i} onOpen={() => setOpenId(t.id)} onProject={setScope} />)}
              </div>
            )}
          </div>
        </div>
      )}
      {/* Bulk-action bar — floats above the list while tasks are selected. */}
      {selected.size > 0 && (
        <div className="pointer-events-none fixed inset-x-0 bottom-6 z-30 flex justify-center px-l">
          <div className="pointer-events-auto flex items-center gap-2 rounded-pill bg-surface-highest/95 px-3 py-2 shadow-sheet backdrop-blur">
            <span className="pl-1 text-on-surface text-[0.8125rem] tabular-nums" style={fvs(600)}>{selected.size} selected</span>
            <span className="h-4 w-px bg-outline-variant/50" aria-hidden />
            <Button size="sm" variant="ghost" disabled={bulkBusy} onClick={() => runBulk('update', { status: 'done' })}><CheckCircle2 size={14} /> Complete</Button>
            <Button size="sm" variant="ghost" disabled={bulkBusy} onClick={async () => { if (await confirmDelete('task', `${selected.size} tasks`)) runBulk('delete') }}><Trash2 size={14} /> Delete</Button>
            <button type="button" onClick={clearSelection} aria-label="Clear selection" className="ml-1 grid size-7 place-items-center rounded-full text-on-surface-low hover:bg-surface-container hover:text-on-surface"><X size={15} /></button>
          </div>
        </div>
      )}
    </WorkbenchLayout>
  )
}

/** Task-list bar shown when a project is filtered: chips to sub-filter by a list,
 *  plus a Reset action on lists under the Repeatable project. */
function TaskListBar({ lists, repeatableId, active, onPick, onReset }: {
  lists: TaskListItem[]; repeatableId?: string; active: string
  onPick: (l: TaskListItem) => void; onReset: (l: TaskListItem) => void
}) {
  return (
    <div className="mb-m flex flex-wrap items-center gap-s">
      <span className="inline-flex items-center gap-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide"><ListChecks size={12} /> Task lists</span>
      {lists.map((l) => {
        const isActive = active === l.id
        const repeatable = !!repeatableId && l.project_id === repeatableId
        return (
          <span key={l.id} className={`inline-flex items-center rounded-pill h-7 pl-3 ${repeatable ? 'pr-1' : 'pr-3'} text-[0.8125rem] transition-colors ${isActive ? 'bg-primary text-on-primary' : 'bg-surface-container text-on-surface-var hover:bg-surface-high'}`}>
            <button type="button" onClick={() => onPick(l)} className="inline-flex items-center gap-1">{l.name}</button>
            {repeatable && (
              <button type="button" onClick={(e) => { e.stopPropagation(); onReset(l) }} title="Reset this repeatable list (all tasks must be done)"
                className={`ml-1.5 inline-flex size-5 items-center justify-center rounded-full ${isActive ? 'hover:bg-on-primary/20' : 'hover:bg-surface-container'}`} aria-label={`Reset list ${l.name}`}>
                <RotateCcw size={12} />
              </button>
            )}
          </span>
        )
      })}
    </div>
  )
}

function MetaLine({ t, onProject }: { t: TaskItem; onProject?: (p: string) => void }) {
  // Only a priority the user actually CHOSE. `medium` is the default and is indistinguishable from
  // unset, so it rendered on 93% of rows in a semantic colour saying nothing — the same reasoning
  // the assignee below already gets. See `signalPriority`.
  const pm = signalPriority(t.priority)
  const due = dueMeta(t.due)
  const exit = t.exit_criteria ?? []
  // Whose work this is, on a shared board (TEAM-SHARED-ENTITIES §2.1). Shown only
  // when someone is named — on a single-user install every task is the owner's, and
  // "@you" on every row is noise. Rendered here because both the list row and the
  // card use this line, so one edit covers both views.
  const who = (t.assignee ?? '').trim() || (t.author ?? '').trim()

  // Two groups, exactly as before: an identity group separated by the flex gap alone, then a
  // schedule group whose members carry a leading `·`. That `·` used to be hard-coded, which was
  // safe only because priority ALWAYS rendered. Now that the group can be empty, the dot is
  // conditional on something actually preceding it — otherwise a row starts "· 0/2 criteria".
  const lead: React.ReactNode[] = []
  if (pm) lead.push(<span key="pri" style={{ color: pm.tone }}>{pm.label}</span>)
  if (who) {
    lead.push(
      <span key="who" className="inline-flex items-center gap-1"
        title={(t.assignee ?? '').trim() ? `Assigned to ${who}` : `Created by ${who}`}>
        <UserRound size={11} /> {who}
      </span>,
    )
  }
  if (t.project) {
    lead.push(
      <TextLink key="proj" onClick={(e) => { e.stopPropagation(); onProject?.(t.project!) }}
        icon={FolderKanban} iconSize={11} title={`Filter by project “${t.project}”`}>{t.project}</TextLink>,
    )
  }
  const tail: { key: string; node: React.ReactNode }[] = []
  if (due) tail.push({ key: 'due', node: <span style={{ color: due.tone }}>{due.label}</span> })
  if (exit.length > 0) tail.push({ key: 'exit', node: <span>{exitDoneCount(exit)}/{exit.length} criteria</span> })
  const comments = typeof t.comment_count === 'number' && t.comment_count > 0
    ? <span key="cmt" className="inline-flex items-center gap-1"><MessageSquare size={11} /> {t.comment_count}</span>
    : null

  // Nothing chosen, nobody named, no project, no date, no criteria, no comments ⇒ an EMPTY meta
  // line. Render nothing rather than a blank row that still spends its top margin.
  if (lead.length === 0 && tail.length === 0 && !comments) return null
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low text-[0.8125rem]">
      {lead}
      {tail.map((x, i) => (
        <span key={x.key}>{(lead.length > 0 || i > 0) ? '· ' : ''}{x.node}</span>
      ))}
      {comments}
    </div>
  )
}

function TaskRow({ t, index, onOpen, onProject, selected, selecting, onToggleSelect, onComplete }: {
  t: TaskItem; index: number; onOpen: () => void; onProject?: (p: string) => void
  selected?: boolean; selecting?: boolean; onToggleSelect?: () => void
  onComplete?: () => void
}) {
  const sm = statusMeta(t.status)
  const done = TERMINAL.has(t.status)
  // Right-click / long-press → scoped actions (open, complete, select) — the
  // shared ContextMenu primitive. Project-provider tasks can't be completed here.
  const menuItems: ContextMenuItem[] = [
    { icon: <MessageSquare size={15} />, label: 'Open', onSelect: onOpen },
    ...(!done && t.provider !== 'project' && onComplete ? [{ icon: <CheckCircle2 size={15} />, label: 'Complete', onSelect: onComplete }] : []),
    ...(onToggleSelect ? [{ icon: <Check size={15} />, label: selected ? 'Deselect' : 'Select', onSelect: onToggleSelect }] : []),
  ]
  return (
    <ContextMenu items={menuItems}>
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}
      onClick={onOpen}
      // 🔴 OPENING A TASK WAS POINTER-ONLY. This row handled `onClick` on a bare `div`, so the surface's
      // primary action had no keyboard equivalent (WCAG 2.1.1). Measured with the keyboard alone across
      // 30 rows: the only tab stop inside a row is its 24px select checkbox, Enter/Space there toggles
      // SELECTION, Shift+F10 opens nothing, and tabbing on reaches the project chip — which navigates
      // to the project, not the task. Four clean axe passes missed it: a div with an onclick and no
      // role is invisible to every rule.
      //
      // The fix is `ui/ListScaffold`'s de-nested idiom, verbatim: an EMPTY overlay button that owns the
      // single tab stop and the accessible name, tabIndex -1 on the wrapper so Framer's own tabindex
      // cannot add a second stop, no role on the wrapper (a wrapper role containing the checkbox is
      // nested-interactive), and the ring drawn on the ROW keyed off the overlay's focus.
      tabIndex={-1}
      className="group relative flex items-center gap-l rounded-lg bg-surface-container px-l py-m cursor-pointer transition-colors hover:bg-surface-high has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary/50"
      style={selected ? { outline: '1.5px solid var(--color-primary)', outlineOffset: -1.5 } : undefined}>
      {/* The row's tab stop and name, through the primitive that already owns this idiom.
          🪤 Hand-rolling a bespoke element here is what the primitive-adoption ratchet is for: it went
          red at 273/272, and the right answer was `ui/RowHitTarget`, which exists for exactly this
          shape. It then went red a SECOND time on this very comment, because the scanner counts the
          literal tag text wherever it appears — prose included.

          🔴 THE STATUS IS IN THE NAME because in this row it is in NOTHING ELSE. Measured on a seeded
          home, ten tasks spanning all five statuses: the row's only status carrier is the glyph below,
          a lucide icon that ships `aria-hidden` — so the row text held no status word, there was no
          sr-only copy, and this name was the bare title. Every task therefore read identically to
          assistive tech: done, blocked and cancelled indistinguishable from not-started.
          The other three surfaces in this family already say it — the detail chip renders
          `{sm.label}`, the card renders the same chip, and a board column names its group
          "<label> — N tasks". The compact row is the one that dropped it, so it says it here rather
          than growing a chip the dense list deliberately does without.
          🪤 NOT `rowSubject()`, the shared row-name helper: it caps at 55 characters, and 3 of those
          10 titles are already longer — it would have silently truncated away the very word being
          added. Its own docstring draws that line ("capping data is not the same as bounding a name
          you assembled"). Title first, status appended, uncapped — the `AppsSection` idiom. */}
      <RowHitTarget label={`${t.title} — ${sm.label}`} />
      {/* Selection checkbox — visible on hover, or always once a selection is active. */}
      {/* 20x20 painted, 24x24 CLICKED. Measured 30 of these on `#/tasks`, and SC 2.5.8's spacing
          exception cannot rescue them: each sits INSIDE this row's own 1212x47 clickable surface, so
          the 24px circle is inside another target by construction (cycle 72's trap, from the
          `sm` Toggle). The button is now a transparent 24px box with the painted 20px control inside
          it, and `-m-0.5` returns the 4px so no row reflows — the fix is the hit box, not the design. */}
      <button type="button" aria-label={`${selected ? 'Deselect' : 'Select'}: ${t.title}`}
        onClick={(e) => { e.stopPropagation(); onToggleSelect?.() }}
        className="shrink-0 grid size-6 -m-0.5 place-items-center">
        <span className={`grid size-5 place-items-center rounded-md border transition-all ${selected ? 'border-primary bg-primary text-on-primary' : `border-outline-variant text-transparent ${selecting ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`}`}>
          <Check size={13} />
        </span>
      </button>
      <sm.icon size={20} className="shrink-0" style={{ color: sm.tone }} />
      <div className="flex-1 min-w-0">
        {/* 🪤 THE TITLE IS THE ROW. Measured at 390px on ten real tasks: 254px of the 434px this one
            needs — 1.7x — with no `title`, so the second half of what the user wrote was unreachable.
            The row's accessible NAME already carries the whole title (cycle 598 put the status in it
            too), so assistive tech was the only reader getting all of it. Same shape as tag names,
            intent goals and conflict sources; a task title is simply the one that appears in four
            places, so all three task-owned ones move together. */}
        <span className={`block truncate text-[0.9375rem] ${done ? 'text-on-surface-low line-through' : 'text-on-surface'}`} style={fvs(500)} title={t.title}>{t.title}</span>
        <MetaLine t={t} onProject={onProject} />
      </div>
      {(t.labels?.length ?? 0) > 0 && <div className="hidden md:flex shrink-0 gap-1">{t.labels!.slice(0, 2).map((l) => <span key={l} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{l}</span>)}</div>}
    </motion.div>
    </ContextMenu>
  )
}

function TaskCard({ t, index, onOpen, onProject }: { t: TaskItem; index: number; onOpen: () => void; onProject?: (p: string) => void }) {
  const sm = statusMeta(t.status)
  const pm = signalPriority(t.priority)
  const due = dueMeta(t.due)
  const done = TERMINAL.has(t.status)
  const exit = t.exit_criteria ?? []
  const exitDone = exitDoneCount(exit)
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}
      // physical liftable card: rises toward the viewer + gains shadow on hover,
      // press-settles on tap (depth via expr) — consistent with ListRow/Surface.
      whileHover={{ y: -expr(4, 0.3), boxShadow: 'var(--shadow-lift)' }}
      whileTap={{ scale: 1 - expr(0.012, 0.3) }}
      onClick={onOpen}
      // Same defect, same fix, in the Cards view: 30 cards, each openable by pointer only.
      tabIndex={-1}
      className="group relative flex flex-col gap-m rounded-xl bg-surface-container p-l cursor-pointer transition-colors hover:bg-surface-high has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary/50">
      <RowHitTarget label={t.title} />
      <div className="flex items-start gap-s">
        <sm.icon size={18} className="shrink-0 mt-0.5" style={{ color: sm.tone }} />
        <span className={`flex-1 text-[0.9375rem] leading-snug ${done ? 'text-on-surface-low line-through' : 'text-on-surface'}`} style={fvs(500)}>{t.title}</span>
        {t.assignee && <span className="shrink-0 inline-flex items-center rounded-pill px-2 h-6 text-[0.75rem] bg-surface-high text-on-surface-var" title={`Assigned to ${t.assignee}`}>@{t.assignee}</span>}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="inline-flex items-center rounded-pill px-2 h-6 text-[0.75rem]" style={{ background: `color-mix(in srgb, ${sm.tone} 16%, transparent)`, color: sm.tone }}>{sm.label}</span>
        {pm && <span className="inline-flex items-center rounded-pill px-2 h-6 text-[0.75rem]" style={{ background: `color-mix(in srgb, ${pm.tone} 14%, transparent)`, color: pm.tone }}>{pm.label}</span>}
        {t.project && <button type="button" onClick={(e) => { e.stopPropagation(); onProject?.(t.project!) }} title={`Filter by project “${t.project}”`} className="inline-flex items-center gap-1 rounded-pill px-2 h-6 text-[0.75rem] hover:brightness-125" style={accentChip}><FolderKanban size={10} /> {t.project}</button>}
        {due && <span className="inline-flex items-center rounded-pill px-2 h-6 text-[0.75rem]" style={{ background: `color-mix(in srgb, ${due.tone} 14%, transparent)`, color: due.tone }}>{due.label}</span>}
        {(t.labels ?? []).slice(0, 2).map((l) => <span key={l} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{l}</span>)}
      </div>
      {exit.length > 0 && (
        <div className="flex items-center gap-s">
          {/* the exit-criteria progress fills with a spring on mount/change instead
              of snapping to width — a small "progress earned" moment */}
          <div className="flex-1 h-1 rounded-pill bg-surface-high overflow-hidden"><motion.div className="h-full rounded-pill" style={{ background: 'var(--color-ok)' }} initial={{ width: 0 }} animate={{ width: `${(exitDone / exit.length) * 100}%` }} transition={spring.spatialSlow} /></div>
          <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{exitDone}/{exit.length}</span>
        </div>
      )}
    </motion.div>
  )
}
