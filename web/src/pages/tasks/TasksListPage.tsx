import { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { Plus, List, LayoutGrid, GitFork, Columns3, MessageSquare, FolderKanban, X, RotateCcw, ListChecks, Target, Code2, Check, CheckCircle2, Trash2, Users, UserRound, Search, Filter, Tag, Tags } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { fvs } from '../../design/fontWeight'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../ui/HeaderActions'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { PartialNotice } from '../../ui/PartialNotice'
import { Button } from '../../ui/Button'
import { InlineError } from '../../ui/InlineError'
import { Meter } from '../../ui/Meter'
import { SearchField } from '../../ui/SearchField'
import { ResultAnnouncement } from '../../ui/ListControls'
import { TextLink } from '../../ui/TextLink'
import { confirm, confirmDelete } from '../../ui/dialog'
import { SidePanel } from '../../ui/SidePanel'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { spring, expr } from '../../design/motion'
import { useQuery, invalidateKeys } from '../../lib/data'
import { api, type TaskItem, type ProjectItem, type TaskListItem, type Loop } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { reportActionFailure } from '../../app/reportingWrite'
import { statusMeta, signalPriority, dueMeta, parseDueDate, TERMINAL, ListChecksLike, exitDoneCount } from './taskMeta'
import { TaskDetail } from './TaskDetail'
import { TaskGraph } from './TaskGraph'
import { TaskBoard } from './TaskBoard'
import { PageTitle } from '../../ui/PageTitle'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { MetaChip } from '../../ui/MetaChip'
import { BUSY_REASON } from '../../ui/unavailable'

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
// Tag filter — one of the task's `labels`, applied across every view alongside scope.
// Deliberately URL-ONLY, with no localStorage twin: an empty-string "any" sentinel plus a
// remembered value is exactly the pair that made the scope filter unclearable (#476 below).
const TAG_ANY = ''

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
  q: qProp, sort: sortProp, scope: scopeProp, list: listProp, tag: tagProp, setQ, setSort, setScope, setList, setTag }: {
  onCreate: () => void
  view: string; filter: string; openId: string | null
  setView: (v: string) => void; setFilter: (f: string) => void; setOpenId: (id: string | null) => void
  editing: boolean; setEditing: (v: boolean) => void
  q: string; sort: string; scope: string; list: string; tag: string
  setQ: (v: string) => void; setSort: (v: string) => void; setScope: (v: string) => void; setList: (v: string) => void
  setTag: (v: string) => void
}) {
  // The list is cache-backed for instant paint on revisit (persist:false — task
  // status is live, must not be stale across a reload), but a LOCAL mirror is kept
  // so the optimistic moveTask/patchLocal updates still apply (useQuery has no
  // setter). The mirror hydrates from the cached data, and a post-mutation load()
  // invalidates + revalidates the cache.
  // NO `.catch(() => [])` here. It used to swallow the rejection, so `error` could never be read
  // even by a caller that tried, and a failed load arrived as an empty array — which the render
  // below then presented as "No tasks" with a create-a-task CTA. Measured with `/api/tasks` at 500
  // and a cold sessionStorage: "No tasks — Break a goal into tracked work…" plus the New-task
  // button, no alert, no retry, told to a user who may have a hundred tasks.
  //
  // 🔴 AND `api.allTasks`, NOT `api.tasks`. Sending no `limit` took the server default of 50, and
  // every view here derives structure from this one array: the DAG resolves each prerequisite id
  // against the ids it holds, so a task whose blocker was row 51 drew as a clean UNBLOCKED node
  // rather than a missing one, and the header analysis (`/api/tasks/graph`, unpaginated) stayed
  // whole-set while the drawing shrank. The collection pages to completeness and reports whether
  // it got everything; `PartialNotice` below states it when it did not (#485).
  const { data: collection, refresh, error: loadErr } = useQuery('tasks', () => api.allTasks(), { persist: false })
  const [tasks, setTasks] = useState<TaskItem[] | null>(null)
  // The configured username, so a row can say whether it's mine or someone else's.
  // Only meaningful once a shared provider actually returns other people's work, so
  // the "Assigned" filter below stays hidden until that happens. It rides on the SAME
  // response every row came from — the extra `limit: 1` probe request this used to make
  // existed only because the list call discarded everything but `tasks`.
  const owner = collection?.owner ?? ''
  // Mine-vs-everyone selection. Local rather than URL-backed: it's a viewing lens on
  // a shared board, not a shareable address (the scope/status filters that ARE worth
  // sharing live in the URL).
  const [assigned, setAssigned] = useState(ASSIGNED_EVERYONE)
  // The "Ready" filter pulls startable tasks from the server (dependency-aware)
  // rather than filtering the loaded list, so it's kept in its own slice.
  const [ready, setReady] = useState<TaskItem[] | null>(null)
  // Errors from the two server-backed slices are kept, not folded into `[]`: a failed read
  // rendered as "Nothing here — No tasks match this filter", the false-empty the main tasks
  // load already answers with LoadError. The nonces re-run each fetch from a Retry click.
  const [readyErr, setReadyErr] = useState<unknown>(null)
  const [readyNonce, setReadyNonce] = useState(0)
  const [searchErr, setSearchErr] = useState<unknown>(null)
  const [searchNonce, setSearchNonce] = useState(0)
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
  // URL-backed (?scope, replace) with the remembered scope as the default on a bare route.
  //
  // The remembered value is held in REACT STATE, seeded once from the store, rather than read from
  // localStorage during render. That distinction is load-bearing — see chooseScope below.
  const [storedScope, setStoredScope] = useState(() => localStorage.getItem(SCOPE_KEY) ?? SCOPE_ALL)
  const scope = scopeProp || storedScope
  // Tag filter: one of the task's `labels`, or TAG_ANY. URL-only (see TAG_ANY).
  const tagFilter = tagProp || TAG_ANY
  //
  // 🔴 CLEAR COULD NOT EXPRESS "CLEARED" (#476). Three correct-in-isolation behaviours composed
  // into a trap: "All tasks" is the EMPTY STRING (`SCOPE_ALL`), `setScope('')` DROPS `?scope`
  // from the URL (TasksSection maps a falsy value to `null`), and the read above then falls
  // through a missing param to the remembered value. So Clear wrote nothing anywhere, the stale
  // stored scope won on the very next render, and the effect below re-persisted it. A user who
  // scoped to a project with no tasks in it and clicked Clear got a permanently empty Tasks page
  // — "Nothing here" beside a filter menu simultaneously reporting 27 tasks — with no way out
  // through the Clear control itself. (Measured: `localStorage['tasks-scope']` still
  // `"__goals__"` with `location.hash === '#/tasks'` and zero rows rendered.)
  //
  // The URL cannot represent the cleared state, so the STORE has to: every scope choice writes
  // through, so clearing stores `''` and the read above resolves it to `SCOPE_ALL` instead of
  // resurrecting a project.
  //
  // 🪤 AND THE STORE WRITE ALONE IS NOT ENOUGH — the reason the remembered value is React state.
  // Clearing drops `?scope`, so on the reported repro (a bare `#/tasks`, param already absent) the
  // URL does not change either. With the fallback reading localStorage mid-render, NOTHING React
  // watches had changed: no re-render, and the stale scope stayed on screen until the next 12s
  // poll happened to repaint it. Clear still looked like a no-op. So the choice updates state too,
  // which is what actually makes the clearing visible.
  //
  // This is the only way the page may set a scope; calling the raw `setScope` prop reopens both
  // halves of the trap.
  const chooseScope = (v: string) => {
    localStorage.setItem(SCOPE_KEY, v)
    setStoredScope(v)
    setScope(v)
  }
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

  // 🔴 `invalidateKeys('tasks')` drops exactly that key. `TaskCreatePage` reads the SAME unfiltered
  // collection under `tasks-all` for its dependency picker, with `persist: true` — so a task created
  // or deleted here left that picker's FIRST PAINT showing the old list, and because the copy is
  // persisted, a hard reload replayed the same wrong list. (Measured, not assumed: the hook
  // revalidates on every mount, so the wrong list is replaced when the refetch lands — the cost is a
  // stale paint, not a durably wrong picker.) Prefix mode covers both keys, and any later reader of
  // the same collection that follows the `tasks*` naming.
  const load = () => { invalidateKeys('tasks', true); refresh() }
  const toggleSelect = (id: string) => setSelected((prev) => {
    const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next
  })
  const clearSelection = () => setSelected(new Set())
  const runBulk = async (op: 'update' | 'delete', patch?: Record<string, unknown>) => {
    if (!selected.size || bulkBusy) return
    setBulkBusy(true)
    const items = [...selected].map((id) => (op === 'delete' ? { id } : { id, ...patch }))
    try {
      // The endpoint returns 200 with per-item outcomes — a refused item (unmet exit
      // criteria) lands in `failed`/`errors[]`, not in a transport error. Awaiting
      // without reading the body reported success for refusals, and the reload then
      // showed the "completed" task still open with no explanation.
      const r = await api.tasksBulk(op, items)
      if (r.failed > 0) {
        const first = Array.isArray(r.errors) && r.errors.length ? `: ${String(r.errors[0])}` : ''
        notify(`${r.failed} of ${r.total} ${op === 'delete' ? 'deletions' : 'updates'} refused${first}`, 'error')
      }
    } catch (e) {
      reportActionFailure(`${op} ${items.length} task${items.length === 1 ? '' : 's'}`)(e)
    }
    setBulkBusy(false); clearSelection(); load()
  }
  // Hydrate the local mirror whenever fresh cached data lands (initial fetch +
  // every revalidation), preserving the optimistic-update path below.
  useEffect(() => { if (collection !== undefined) setTasks(collection.tasks) }, [collection])
  useEffect(() => { const t = window.setInterval(refresh, 12000); return () => clearInterval(t) }, [refresh])
  useEffect(() => { if (viewProp) localStorage.setItem(VIEW_KEY, viewProp) }, [viewProp])
  useEffect(() => { localStorage.setItem(SORT_KEY, sortBy) }, [sortBy])
  // Persist a scope that arrived from the URL (a deep link / a shared link), and keep the
  // remembered value in step with it so a later bare route and a later reload agree on what the
  // last-used scope was. Converges in one pass: once `storedScope === scope` this is a no-op.
  useEffect(() => { localStorage.setItem(SCOPE_KEY, scope); setStoredScope(scope) }, [scope])
  // Clearing the scope (or moving off a single project) drops the list sub-filter.
  // Guard on listProp so it only writes when there's actually a ?list to clear
  // (else it would setQuery every render while unscoped — a no-op churn).
  useEffect(() => { if (!isProjectScope && listProp) setListFilter(null) }, [isProjectScope, listProp])
  // Refetch ready tasks whenever the Ready filter is active (and on tasks reload).
  useEffect(() => {
    if (filter !== 'ready') return
    setReadyErr(null)
    api.readyTasks().then(setReady).catch((e) => { setReadyErr(e); setReady(null) })
  }, [filter, tasks, readyNonce])

  // Debounced server-side search; clears results when the query is emptied.
  const q = query.trim()
  useEffect(() => {
    if (!q) { setResults(null); setSearchErr(null); return }
    let alive = true
    setSearchErr(null)
    const h = window.setTimeout(() => {
      // `tags` is the search API's own filter axis — plumbed through
      // `tasks/handlers.py` to `registry.search_tasks` and working, but until now it
      // had ZERO callers anywhere in the frontend (#477). Sent only when a tag is
      // active so an unfiltered search keeps its existing request shape, and the
      // client-side `hasTag` pass below still applies for the non-search paths.
      api.searchTasks({ query: q, limit: 100, ...(tagFilter !== TAG_ANY ? { tags: [tagFilter] } : {}) })
        .then((d) => { if (alive) setResults(d.tasks) })
        .catch((e) => { if (alive) { setSearchErr(e); setResults(null) } })
    }, 250)
    return () => { alive = false; clearTimeout(h) }
  }, [q, tasks, tagFilter, searchNonce])

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

  // Does a task carry the active tag? (#477 — tags were a write-only decoration:
  // rendered as chips on every row and card, filterable by `POST /api/tasks/search`,
  // and reachable from nowhere in the UI.)
  const hasTag = useMemo(
    () => (tagFilter === TAG_ANY ? () => true : (t: TaskItem) => (t.labels ?? []).includes(tagFilter)),
    [tagFilter],
  )

  // Tasks within the active scope + tag, before status/search — feeds board + DAG
  // (which present their own statuses) so both narrowings apply to every view.
  const scopedTasks = useMemo(() => (tasks ?? []).filter(inScope).filter(hasTag), [tasks, inScope, hasTag])

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

    // Tag vocabulary, counted across the whole loaded set (the same basis the Scope
    // counts use, so the two sections' numbers are comparable). Hidden entirely when
    // nothing is tagged — a filter that can only ever be a no-op is noise.
    const tagCounts = new Map<string, number>()
    for (const t of tasks ?? []) for (const l of t.labels ?? []) tagCounts.set(l, (tagCounts.get(l) ?? 0) + 1)

    const sections: FilterSectionDef[] = [{
      title: 'Scope', value: scope, defaultKey: SCOPE_ALL, onChange: chooseScope,
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
    // Tag — the axis that existed end to end on the server and had no way in. Offered
    // in EVERY view (unlike Status): board and DAG present their own statuses, but a tag
    // narrows which work is on screen, which is exactly what scope does there too.
    // Busiest tag first, then alphabetical, so a real vocabulary stays navigable.
    if (tagCounts.size > 0) sections.push({
      title: 'Tag', value: tagFilter, defaultKey: TAG_ANY, onChange: setTag,
      options: [
        { key: TAG_ANY, label: 'Any tag', icon: Tags, count: tasks?.length },
        ...[...tagCounts.entries()]
          .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
          .map(([name, n]) => ({ key: name, label: name, icon: Tag, count: n })),
      ],
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
  }, [tasks, ready, projects, codingProjectNames, scope, filter, sortBy, showStatus, showSort, owner, assigned, tagFilter])

  const filtered = useMemo(() => {
    let base: TaskItem[] | null
    if (q) base = results  // search overrides the status filter
    else if (filter === 'ready') base = ready
    else if (!tasks) base = null
    else base = filter === 'all' ? [...tasks] : filter === 'done' ? tasks.filter((t) => TERMINAL.has(t.status)) : tasks.filter((t) => t.status === filter)

    if (base) base = base.filter(inScope)
    if (base) base = base.filter(hasTag)
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
  }, [tasks, ready, filter, q, results, inScope, hasTag, listFilter, sortBy])

  // Reset a Repeatable task list (server gates: all tasks must be done). Surfaces
  // the server message on the move-error banner on failure; reloads on success.
  //
  // 🔴 IT DESTROYED EVERY TASK'S EXECUTION NOTES ON ONE CLICK, AND SAID NOTHING. The server's own
  // docstring states what it does — *"all its tasks → open, exit criteria → incomplete, execution
  // notes cleared"* — and `tasks/hierarchy_handlers.py` loops the whole list passing
  // `execution_notes=[]`. There is no restore. The only copy this control carried was its tooltip,
  // *"Reset this repeatable list (all tasks must be done)"*, which names the PRECONDITION and never
  // the loss: a 20px `RotateCcw` that reads as "start the checklist again", not "discard the record
  // of the last run".
  //
  // 🪤 AND THE OBVIOUS IMPROVEMENT — naming HOW MANY tasks lose notes — STILL LIES, for a
  // narrower reason than it used to. It used to be flatly wrong: this array was one 50-row
  // window across the whole account, so a client-side count understated the loss precisely on
  // the large lists where it is biggest. The collection read (#485) removes that, but it does
  // NOT make the count safe: `collection.complete` can be false, and a warning that undercounts
  // is worse than one that does not count at all. The body states the consequence categorically
  // instead — true for every list at every size, and independent of completeness.
  async function resetList(list: TaskListItem) {
    if (!(await confirm({
      title: `Reset “${list.name}”?`,
      body: 'Every task goes back to open and its exit criteria are marked incomplete. '
        + 'Each task’s execution notes are cleared, and those cannot be recovered.',
      danger: true,
      confirmLabel: 'Reset list',
    }))) return
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

  // The nothing-on-screen state for the views that render `scopedTasks` (board + DAG). They
  // have no status/search branch to blame, so scope was the only narrower they could name —
  // and with a Tag filter now applying to them too, "No tasks match this scope." would blame
  // the wrong control. A tag is dismissable in place, so it gets its own escape; a scope is
  // chosen by navigation and keeps the neutral sentence.
  const nothingInView = tagFilter !== TAG_ANY ? (
    <EmptyState icon={Tag} title={`No tasks tagged “${tagFilter}”`}
      hint={`You have ${tasks?.length ?? 0} task${(tasks?.length ?? 0) === 1 ? '' : 's'} — just none carrying this tag here.`}
      action={{ label: 'Clear tag', onClick: () => setTag(TAG_ANY) }} />
  ) : (
    <EmptyState icon={ListChecksLike} title="Nothing here" hint="No tasks match this scope." />
  )

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
            <PartialNotice complete={collection?.complete ?? true} shown={tasks?.length ?? 0} total={collection?.total ?? 0}
              what="tasks" detail="dependencies on the ones outside this window are not shown, so a blocked task can read as unblocked" />
          </div>
          <div className="mx-auto h-full min-h-0 w-full" style={{ maxWidth: 'var(--content-width)' }}>
            {/* Error FIRST: `tasks === null` also satisfies the skeleton and the empty branch, so a
                later test would be unreachable. Removing the swallow above is only half the fix —
                without this branch a failed load would hang on the skeleton forever instead. */}
            {q && searchErr ? <LoadError what="search results" error={searchErr} onRetry={() => setSearchNonce((n) => n + 1)} />
              : filter === 'ready' && !q && readyErr ? <LoadError what="ready tasks" error={readyErr} onRetry={() => setReadyNonce((n) => n + 1)} />
              : tasks === null && loadErr ? <LoadError what="tasks" error={loadErr} onRetry={() => { invalidateKeys('tasks', true); refresh() }} />
              : filtered === null ? <ListSkeleton rows={6} what="tasks" /> : (tasks?.length ?? 0) === 0 ? (
              <EmptyState icon={ListChecksLike} title="No tasks" hint="Break a goal into tracked work. Create a task, or let an agent plan from a chat." action={{ label: 'New task', onClick: onCreate, icon: Plus }} />
            ) : scopedTasks.length === 0 ? nothingInView : (
              <TaskBoard tasks={scopedTasks} onOpen={(id) => setOpenId(id)} onMove={moveTask} />
            )}
          </div>
        </div>
      ) : (
        // No tab stop on this scroll container, in ANY view. The DAG used to need one as a
        // standin: its SVG nodes were not focusable, so the container held nothing a keyboard
        // could reach (2785px of graph unreachable, axe scrollable-region-focusable) and a
        // `tabIndex`/`role`/`aria-label` on the region at least made it scrollable. #474 fixed
        // the cause — every DAG node is now a real button and a tab stop — so by this page's own
        // rule ("List and Cards expose 38 focusable rows, so they need no tab stop and must not
        // get a redundant one") the standin is now the redundant stop it warned against. The
        // graph names itself too, via `DagView`'s own `role="group"` + label, so keeping one here
        // would announce the same region twice.
        <div className="flex-1 overflow-y-auto">
          {/* every view (incl. DAG) honors the shell content-width preset */}
          <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
            {moveError && <div className="mb-s"><InlineError animated icon onDismiss={() => setMoveError('')}>{moveError}</InlineError></div>}
            <PartialNotice className="mb-s" complete={collection?.complete ?? true} shown={tasks?.length ?? 0} total={collection?.total ?? 0}
              what="tasks" detail="dependencies on the ones outside this window are not shown, so a blocked task can read as unblocked" />
            {isProjectScope && projectLists.length > 0 && (
              <TaskListBar lists={projectLists} repeatableId={repeatableProject?.id}
                active={listFilter?.id ?? ''}
                onPick={(l) => setListFilter(listFilter?.id === l.id ? null : { id: l.id, name: l.name })}
                onReset={resetList} />
            )}
            {q && searchErr ? <LoadError what="search results" error={searchErr} onRetry={() => setSearchNonce((n) => n + 1)} />
              : filter === 'ready' && !q && readyErr ? <LoadError what="ready tasks" error={readyErr} onRetry={() => setReadyNonce((n) => n + 1)} />
              : tasks === null && loadErr ? <LoadError what="tasks" error={loadErr} onRetry={() => { invalidateKeys('tasks', true); refresh() }} />
              : filtered === null ? <ListSkeleton rows={6} what="tasks" /> : (tasks?.length ?? 0) === 0 ? (
              <EmptyState icon={ListChecksLike} title="No tasks" hint="Break a goal into tracked work. Create a task, or let an agent plan from a chat." action={{ label: 'New task', onClick: onCreate, icon: Plus }} />
            ) : view === 'dag' ? (
              scopedTasks.length === 0
                ? nothingInView
                : <TaskGraph tasks={scopedTasks} onOpen={(id) => setOpenId(id)} />
            ) : filtered.length === 0 ? (
              // This branch is reachable through FOUR narrowing controls (search, status filter,
              // list bar, Assigned), and it blamed "this filter" for all of them — a hint that
              // told a user who typed a search to check a dropdown they never touched. Same
              // split as the code list (emptyStateNoMatch): name the control that actually
              // narrowed, offer the escape that undoes it, and count what is really there so
              // the state cannot read as "you have no tasks". Scope-only narrowing keeps the
              // neutral scope sentence — scope is chosen by navigation, and a "View all" that
              // could not escape it would be an affordance that lies about what it does.
              q ? (
                <EmptyState icon={Search} title={`No tasks match “${q}”`}
                  hint={`You have ${tasks?.length ?? 0} task${(tasks?.length ?? 0) === 1 ? '' : 's'} — just none matching the search.`}
                  action={{ label: 'Clear search', onClick: () => setQ('') }} />
              ) : filter !== 'all' || listFilter || (owner && assigned === ASSIGNED_MINE) || tagFilter !== TAG_ANY ? (
                // The Tag filter joins the in-page narrowers here, which means it MUST also be
                // reset by the escape below — an affordance that left a tag applied would be the
                // same lie this branch was split up to stop.
                <EmptyState icon={Filter} title="No tasks in this view"
                  hint={`You have ${tasks?.length ?? 0} task${(tasks?.length ?? 0) === 1 ? '' : 's'} — just none in this view.`}
                  action={{ label: 'View all tasks', onClick: () => { setFilter('all'); setListFilter(null); setAssigned(ASSIGNED_EVERYONE); setTag(TAG_ANY) } }} />
              ) : (
                <EmptyState icon={ListChecksLike} title="Nothing here" hint="No tasks match this scope." />
              )
            ) : view === 'list' ? (
              <div className="flex flex-col gap-s pb-16">
                {filtered.map((t, i) => (
                  <TaskRow key={t.id} t={t} index={i} onOpen={() => setOpenId(t.id)} onProject={chooseScope} onTag={setTag}
                    selected={selected.has(t.id)} selecting={selected.size > 0} onToggleSelect={() => toggleSelect(t.id)}
                    onComplete={() => moveTask(t.id, 'done')} />
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-s">
                {filtered.map((t, i) => <TaskCard key={t.id} t={t} index={i} onOpen={() => setOpenId(t.id)} onProject={chooseScope} onTag={setTag} />)}
              </div>
            )}
          </div>
        </div>
      )}
      {/* Bulk-action bar — floats above the list while tasks are selected. */}
      {selected.size > 0 && (
        <div className="pointer-events-none fixed inset-x-0 bottom-6 z-30 flex justify-center px-l">
          <div className="pointer-events-auto flex items-center gap-2 rounded-pill bg-surface-highest/95 px-3 py-2 shadow-sheet backdrop-blur">
            <span data-type="label-s" className="pl-1 text-on-surface tabular-nums" style={fvs(600)}>{selected.size} selected</span>
            <span className="h-4 w-px bg-outline-variant/50" aria-hidden />
            <Button size="sm" variant="ghost" disabled={bulkBusy} disabledReason={BUSY_REASON} onClick={() => runBulk('update', { status: 'done' })}><CheckCircle2 size={14} /> Complete</Button>
            <Button size="sm" variant="ghost" disabled={bulkBusy} disabledReason={BUSY_REASON} onClick={async () => { if (await confirmDelete('task', `${selected.size} tasks`)) runBulk('delete') }}><Trash2 size={14} /> Delete</Button>
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
      <span data-type="caption" className="inline-flex items-center gap-1 text-on-surface-low uppercase tracking-wide"><ListChecks size={12} /> Task lists</span>
      {lists.map((l) => {
        const isActive = active === l.id
        const repeatable = !!repeatableId && l.project_id === repeatableId
        return (
          <span key={l.id} data-type="body-s" className={`inline-flex items-center rounded-pill h-7 pl-3 ${repeatable ? 'pr-1' : 'pr-3'} transition-colors ${isActive ? 'bg-primary text-on-primary' : 'bg-surface-container text-on-surface-var hover:bg-surface-high'}`}>
            {/* One-of-N: which task list the page is showing. `bg-primary text-on-primary` was the only
                cue, so this takes the app's recorded form — `<dimension>: <value>` plus `aria-pressed`.
                The name goes on the PICK button, not the pill wrapper, because the wrapper also holds the
                Reset control and a state on it would describe both. */}
            <button type="button" aria-label={`Task list: ${l.name}`} aria-pressed={isActive}
              onClick={() => onPick(l)} className="inline-flex items-center gap-1">{l.name}</button>
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

  // Two groups, both separated by the flex gap alone. The schedule group used to carry a leading
  // `·` guarded by `(lead.length > 0 || i > 0)`, which tests PRESENCE — "is anything in front of
  // me" — and cannot know LINE POSITION. Measured on the demo fixture, four meta lines carry a
  // schedule item:
  //
  //     1440 / 834 / 390px   0 stranded   (the row fits on one line, height 20px)
  //             360px        2 stranded   (height 41px — it has wrapped)
  //             320px        4 of 4       ← the width WCAG SC 1.4.10 (Reflow) mandates
  //
  // So the dot was correct at every tier the harness sweeps and wrong at the one AA requires,
  // which is why two earlier passes measured this row as clean at desktop and phone. A content
  // separator cannot survive wrapping; only a gap can. This row already separates its identity
  // group by `gap-x-m` with no glyph, so the schedule group now uses the same mechanism — the
  // ruling #2224 established for `#/prompts`, applied to the sibling it was compared against.
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
    <div data-type="body-s" className="mt-1 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low">
      {lead}
      {tail.map((x) => (
        <span key={x.key}>{x.node}</span>
      ))}
      {comments}
    </div>
  )
}

function TaskRow({ t, index, onOpen, onProject, onTag, selected, selecting, onToggleSelect, onComplete }: {
  t: TaskItem; index: number; onOpen: () => void; onProject?: (p: string) => void; onTag?: (tag: string) => void
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
      className="group relative flex items-center gap-l rounded-lg bg-surface-container px-l py-m cursor-pointer transition-colors hover:bg-surface-high has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary"
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
      {(t.labels?.length ?? 0) > 0 && <div className="hidden md:flex shrink-0 gap-1">{t.labels!.slice(0, 2).map((l) => <MetaChip key={l} label={l} title={`Filter by tag “${l}”`} onClick={onTag} />)}</div>}
    </motion.div>
    </ContextMenu>
  )
}

function TaskCard({ t, index, onOpen, onProject, onTag }: { t: TaskItem; index: number; onOpen: () => void; onProject?: (p: string) => void; onTag?: (tag: string) => void }) {
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
      className="group relative flex flex-col gap-m rounded-xl bg-surface-container p-l cursor-pointer transition-colors hover:bg-surface-high has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary">
      <RowHitTarget label={t.title} />
      <div className="flex items-start gap-s">
        <sm.icon size={18} className="shrink-0 mt-0.5" style={{ color: sm.tone }} />
        <span data-type="label-m" className={`flex-1 leading-snug ${done ? 'text-on-surface-low line-through' : 'text-on-surface'}`} style={fvs(500)}>{t.title}</span>
        {t.assignee && <span data-type="caption" className="shrink-0 inline-flex items-center rounded-pill px-2 h-6 bg-surface-high text-on-surface-var" title={`Assigned to ${t.assignee}`}>@{t.assignee}</span>}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span data-type="caption" className="inline-flex items-center rounded-pill px-2 h-6" style={{ background: `color-mix(in srgb, ${sm.tone} 16%, transparent)`, color: sm.tone }}>{sm.label}</span>
        {pm && <span data-type="caption" className="inline-flex items-center rounded-pill px-2 h-6" style={{ background: `color-mix(in srgb, ${pm.tone} 14%, transparent)`, color: pm.tone }}>{pm.label}</span>}
        {t.project && <MetaChip label={t.project} title={`Filter by project “${t.project}”`} icon={FolderKanban} tone="accent" onClick={onProject} />}
        {due && <span data-type="caption" className="inline-flex items-center rounded-pill px-2 h-6" style={{ background: `color-mix(in srgb, ${due.tone} 14%, transparent)`, color: due.tone }}>{due.label}</span>}
        {(t.labels ?? []).slice(0, 2).map((l) => <MetaChip key={l} label={l} title={`Filter by tag “${l}”`} onClick={onTag} />)}
      </div>
      {exit.length > 0 && (
        <div className="flex items-center gap-s">
          {/* Exit-criteria progress now goes through the Meter primitive, so a card
              announces "3 of 5 exit criteria met" instead of shipping a bar with no
              role at all. The bar formerly sprang in from width 0 on mount; that
              flourish fired once per card in a list of dozens, which is decoration
              rather than state, so it is gone with the hand-rolled track. */}
          <Meter size="thin" className="flex-1" tone="var(--color-ok)"
            label={`Exit criteria: ${exitDone} of ${exit.length} met`}
            pct={(exitDone / exit.length) * 100} />
          <span data-type="caption" className="shrink-0 text-on-surface-low tabular-nums">{exitDone}/{exit.length}</span>
        </div>
      )}
    </motion.div>
  )
}
