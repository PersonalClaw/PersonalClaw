import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Archive, ArchiveRestore, Download, FolderKanban, FolderOpen, KeyRound, ListChecks, MessageSquare, MoreHorizontal, Pencil, Pin, Plus, Repeat, Star, Trash2, TriangleAlert, Upload } from 'lucide-react'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { notify } from '../../app/appSdk'
import { useGatewayConnection } from '../../app/gatewayConnection'
import { api, type ProjectImportResult, type ProjectItem, type WaitingItem } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { DEFAULT_PROJECT_KEY, useDefaultProject, useProjectPins } from '../../lib/defaultProject'
import { downloadFrom } from '../../lib/download'
import { clockTime } from '../../lib/epoch'
import { previewLine } from '../../lib/previewText'
import { projectOrder, projectPulse, pulseWords, type LiveRow, type ProjectPulse } from '../../lib/projectPulse'
import { useNeedsYou } from '../../lib/useNeedsYou'
import { useRunningList } from '../../lib/useRunningList'
import { NO_PROJECT, useThreads, type Thread } from '../../lib/useThreads'
import { Button } from '../../ui/Button'
import { cx } from '../../ui/cx'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { HeaderFilter } from '../../ui/HeaderFilter'
import { HeaderSearch } from '../../ui/HeaderSearch'
import { InlineError } from '../../ui/InlineError'
import { ListStatus } from '../../ui/ListControls'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { useSyncedDraft } from '../../ui/forms'
import { WINDOWING_THRESHOLD, WindowedList } from '../../ui/WindowedList'
import { FoldRow, type FoldLive } from '../../ui/FoldRow'
import { Modal } from '../../ui/Modal'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { PageTitle } from '../../ui/PageTitle'
import { MenuRow, Popover } from '../../ui/Popover'
import { ProjectMark } from '../../ui/ProjectMark'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { TextLink } from '../../ui/TextLink'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { TopBar } from '../../ui/TopBar'
import { SOURCE_WORDS } from '../dashboard/NeedCards'
import { NewProjectModal } from './NewProjectDialog'
import { confirmArchiveProject, confirmDeleteProject, confirmForceDeleteProject } from './projectConfirms'
import { ProjectWorkRow } from './ProjectWorkRow'

/** ── The Projects page (`#/projects`): one list ──────────────────────────────────────────────
 *
 *  See what is happening in each project, and open the one to work in. One card per project, each
 *  with what needs you and what is running in it (`projectPulse`, from Home's own reads) and those
 *  rows under it; the projects a loop made for itself and the chats in no project fold at the end.
 *  The order is taken when the page opens (`projectOrder`: the pins, then the newest activity) and
 *  kept while it is open. Cards go into two columns once the list is wide enough for each to be
 *  about 600 px (a container query on the list's own width), one column below. Past the shared
 *  windowing threshold (`WindowedList`) the list windows, one card to a row.
 *
 *  Its address: `?q=` the search (projects and their chats), `?show=archived|all` the filter.
 *  `?new=1` opens the New project dialog and `?import=1` the import's file picker, each read once
 *  and dropped with a replace (the palette's New project and Import a project reach the list this
 *  way); the picker opens only within the palette's press. The retired `?view=chats` lands on the
 *  list with its search, and `?peek=<id>` on the project's own page. */

/** Show: which projects the list holds (`?show=`). Active by default. */
const SHOW_OPTIONS = [
  { key: '', label: 'Active' },
  { key: 'archived', label: 'Archived' },
  { key: 'all', label: 'All' },
]

/** What each built-in project is for, its row's second line. */
const BUILTIN_PURPOSE: Record<string, string> = {
  Personal: 'Tasks with no other project go here.',
  Repeatable: 'Task lists you reset and run again.',
}

/** How many live rows a project's card shows before "N more in Garden". */
const LIVE_ROWS = 3
/** How many search matches show under a project before "N more matches in Garden". */
const MATCH_ROWS = 5

/** Both folds following the search, open while it matches inside: no choice of the owner's. */
const FOLDS_AT_REST: { madeFor: boolean | null; loose: boolean | null } = { madeFor: null, loose: null }

/** The state families' tones, as every row and fold marks them (`tokens.css`). */
const STATE_TONE = { waiting: 'var(--color-state-waiting)', active: 'var(--color-state-active)' } as const

/** A text link's box at the compact widths: the 44 px target, its words centred in it. */
const LINK_TARGET = cx(TOUCH_TARGET, 'max-lg:inline-flex max-lg:items-center')

/** The reason on every control that writes while the gateway cannot be reached. */
const UNREACHABLE = "Can't reach PersonalClaw"

/** A folder's last two segments, the way a person knows it. */
function folderTail(path: string): string {
  return path.replace(/\/+$/, '').split('/').slice(-2).join('/')
}

/** A brief's first sentence. */
function firstSentence(text: string): string {
  const line = previewLine(text)
  const end = line.search(/[.!?](\s|$)/)
  return end >= 0 ? line.slice(0, end + 1) : line
}

/** "Active 3 min ago", "Active yesterday", "Active Nov 4": when a project last did something. */
export function activeWords(ms: number, now: number = Date.now()): string {
  if (!ms) return ''
  const mins = Math.max(0, Math.round((now - ms) / 60000))
  if (mins < 1) return 'Active just now'
  if (mins < 60) return `Active ${mins} min ago`
  const hours = Math.round(mins / 60)
  const at = new Date(ms), today = new Date(now)
  const yesterday = new Date(now); yesterday.setDate(today.getDate() - 1)
  if (at.toDateString() === today.toDateString()) return `Active ${hours} h ago`
  if (at.toDateString() === yesterday.toDateString()) return 'Active yesterday'
  return `Active ${at.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
}

/** The same, as a heading says it: "active 3 minutes ago", "active yesterday". */
export function activeSpoken(ms: number, now: number = Date.now()): string {
  if (!ms) return ''
  const mins = Math.max(0, Math.round((now - ms) / 60000))
  if (mins < 1) return 'active just now'
  if (mins < 60) return `active ${mins} ${mins === 1 ? 'minute' : 'minutes'} ago`
  const hours = Math.round(mins / 60)
  const at = new Date(ms), today = new Date(now)
  const yesterday = new Date(now); yesterday.setDate(today.getDate() - 1)
  if (at.toDateString() === today.toDateString()) return `active ${hours} ${hours === 1 ? 'hour' : 'hours'} ago`
  if (at.toDateString() === yesterday.toDateString()) return 'active yesterday'
  return `active ${at.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
}

/** The line a made-for project's card says: "Made for the code session Fix the gate". A loop
 *  that is gone says so; one the gateway could not read (`kind: null`) is only "a loop". */
export function madeForLine(made: NonNullable<ProjectItem['made_for']>): string {
  if (made.kind === null) return 'Made for a loop.'
  if (!made.kind) return 'Made for a loop that was deleted.'
  const word = made.kind === 'code' ? 'code session' : made.kind === 'goal' ? 'goal loop' : made.kind === 'general' ? 'loop' : made.kind
  return made.name ? `Made for the ${word} ${previewLine(made.name)}` : `Made for a ${word}`
}

/** Marks the words of *needle* in *text*. */
function marked(text: string, needle: string): React.ReactNode {
  if (!needle) return text
  const at = text.toLowerCase().indexOf(needle)
  if (at < 0) return text
  return <>{text.slice(0, at)}<mark className="rounded-xs bg-primary/20 text-on-surface">{text.slice(at, at + needle.length)}</mark>{text.slice(at + needle.length)}</>
}

export function ProjectListPage({ navigate, onOpenTasks, query, setQuery }: {
  navigate: RouteProps['navigate']
  onOpenTasks: () => void
} & Pick<RouteProps, 'query' | 'setQuery'>) {
  const onOpen = (id: string) => navigate(`projects/${id}`)
  const { data: projects, loading, error: loadErr, refresh, stale } = useQuery('projects:list', () => api.projects(), { persist: true })
  const needs = useNeedsYou()
  const runningRead = useRunningList()
  const threadsRead = useThreads()
  const { defaultProjectId, setDefaultProject } = useDefaultProject()
  const { pins, changePins, settled: pinsSettled, error: pinsError } = useProjectPins()
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [show, setShow] = useQueryParam(query, setQuery, 'show', '', { replace: true })
  const [creating, setCreating] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const importInput = useRef<HTMLInputElement>(null)
  const [importing, setImporting] = useState<{ file: File; plan: ProjectImportResult } | null>(null)
  const [importBusy, setImportBusy] = useState(false)

  // Offline: the shell's one reading of the gateway connection. The list shows its last good read,
  // says when that was, and every control that writes is off with the reason.
  const connection = useGatewayConnection()
  const offline = connection.state === 'disconnected'
  const offReason = offline ? UNREACHABLE : undefined

  // The addresses this page used to answer, kept landing: the Chats by project view is the list
  // now (its search stays), and a peek is the project's own page. Each replaces its entry.
  useEffect(() => {
    if (query.peek) { navigate(`projects/${query.peek}`, { replace: true }); return }
    if (query.view) setQuery({ view: null }, { replace: true })
  }, [query.peek, query.view, navigate, setQuery])
  // The palette's New project and Import a project reach this page through its address. Each key
  // is read once and dropped with a replace, so Back and a reload never reopen the dialog or the
  // picker. A file picker opens only from a person's press, so Import clicks the input only while
  // the palette's press still counts; a copied link carries none, and only drops the key. Offline,
  // New project is off, so the key is dropped and the dialog not opened.
  useEffect(() => {
    if (query.new !== '1') return
    if (!offline) setCreating(true)
    setQuery({ new: null }, { replace: true })
  }, [query.new, setQuery, offline])
  useEffect(() => {
    if (query.import !== '1') return
    const press = (navigator as Navigator & { userActivation?: { isActive: boolean } }).userActivation
    if (press?.isActive) importInput.current?.click()
    setQuery({ import: null }, { replace: true })
  }, [query.import, setQuery])

  async function previewImport(file: File) {
    setErr(null)
    try {
      setImporting({ file, plan: await api.projectImport(file, { preview: true }) })
    } catch (e) { setErr(`Couldn't read that archive: ${(e as Error).message || 'unknown error'}`) }
  }
  async function commitImport() {
    if (!importing || importBusy) return
    setImportBusy(true)
    try {
      const done = await api.projectImport(importing.file)
      setImporting(null)
      invalidateKeys('projects:list'); refresh()
      if (done.summary) notify(done.summary, 'success')
      if (done.project_id) onOpen(done.project_id)
    } catch (e) {
      setImporting(null)
      setErr(`Couldn't import that archive: ${(e as Error).message || 'unknown error'}`)
    } finally { setImportBusy(false) }
  }
  async function del(proj: ProjectItem) {
    setErr(null)
    if (!(await confirmDeleteProject(proj.name))) return
    const run = async (force: boolean) => {
      await api.deleteProject(proj.id, force)
      // A deleted default resolves to "none" server-side; drop the cached copy so no reader
      // paints the dead id in the meantime.
      invalidateKeys(DEFAULT_PROJECT_KEY)
    }
    try {
      await run(false)
    } catch (e) {
      // 409 = the project still has bound Goal Loops / Code projects. Re-confirm and
      // delete with force (the backend then tears down its worktrees/context too).
      if ((e as { status?: number }).status === 409) {
        const force = await confirmForceDeleteProject(proj.name)
        if (!force) return
        try { await run(true) }
        catch (e2) { setErr(`Couldn't delete that project: ${(e2 as Error).message || 'unknown error'}`) }
      } else {
        setErr(`Couldn't delete that project: ${(e as Error).message || 'unknown error'}`)
      }
    }
    invalidateKeys('projects:list'); refresh()
  }
  async function setArchived(proj: ProjectItem, archived: boolean) {
    setErr(null)
    if (archived && !(await confirmArchiveProject(proj.name))) return
    try {
      await api.updateProject(proj.id, { status: archived ? 'archived' : 'active' })
      invalidateKeys('projects:list'); invalidateKeys(DEFAULT_PROJECT_KEY); refresh()
    } catch (e) { setErr(`Couldn't ${archived ? 'archive' : 'restore'} that project: ${(e as Error).message || 'unknown error'}`) }
  }
  async function exportProject(proj: ProjectItem) {
    setErr(null)
    try { await downloadFrom(`/api/projects/${encodeURIComponent(proj.id)}/export`) }
    catch (e) { setErr(`Couldn't export that project: ${(e as Error).message || 'unknown error'}`) }
  }
  // From the latest pins, after any change still being written, so two quick toggles compose.
  const togglePin = (id: string) => void changePins((now) => (now.includes(id) ? now.filter((p) => p !== id) : [...now, id]))
  const pinsUnread = !pinsSettled || !!pinsError

  // The pulse, from Home's own reads. Failed reads draw no pulse; the note says so.
  const waitingList = needs.data?.waiting
  const runningList = runningRead.data
  const pulseFailed = (!waitingList && !!needs.error) || (!runningList && !!runningRead.error)
  const threads = useMemo(() => threadsRead.groups.flatMap((g) => g.threads), [threadsRead.groups])
  const pulseOf = useMemo(() => {
    const cache = new Map<string, ProjectPulse>()
    return (p: ProjectItem): ProjectPulse => {
      let hit = cache.get(p.id)
      if (!hit) { hit = projectPulse(p.id, waitingList, runningList, threads, p); cache.set(p.id, hit) }
      return hit
    }
  // `projects` too: a re-read brings each project's own `updated_at`, which its pulse reads.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waitingList, runningList, threads, projects])

  // The order is taken when the page opens and kept while it is open: a project never moves while
  // the owner reads. A project that arrives later goes after the ones already placed; Try again
  // takes the order afresh.
  // It is taken once what places a project has answered: the pins, the threads' activity and the
  // running list (each answered or failed). Until then the list is drawn in that order as it
  // stands, which is before anyone can have read it.
  const [orderEpoch, setOrderEpoch] = useState(0)
  const placed = useRef<{ epoch: number; ids: string[] } | null>(null)
  const placeable = pinsSettled && !threadsRead.loading && (!!runningRead.data || !!runningRead.error)
  const ordered = useMemo(() => {
    const list = projects ?? []
    if (!list.length) return list
    const fresh = () => projectOrder(list, pins, (id) => {
      const p = list.find((x) => x.id === id)
      return p ? pulseOf(p).lastActiveAt : 0
    }).map((p) => p.id)
    if (!placeable) return projectOrder(list, pins, (id) => pulseOf(list.find((x) => x.id === id)!).lastActiveAt)
    if (!placed.current || placed.current.epoch !== orderEpoch) placed.current = { epoch: orderEpoch, ids: fresh() }
    const at = new Map(placed.current.ids.map((id, i) => [id, i]))
    return [...list].sort((a, b) => (at.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (at.get(b.id) ?? Number.MAX_SAFE_INTEGER))
  // Once placed, the pins and the pulse change nothing until Try again takes the order afresh.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, orderEpoch, placeable])
  // Try again takes the order afresh once the reads it asked again have each answered (a value or
  // an error that was not there at the press), so the order comes from the new pulse, not the old.
  const answers = [projects, loadErr, needs.data, needs.error, runningRead.data, runningRead.error]
  const retakeFrom = useRef<unknown[] | null>(null)
  useEffect(() => {
    const from = retakeFrom.current
    if (!from) return
    if (![0, 2, 4].every((i) => answers[i] !== from[i] || answers[i + 1] !== from[i + 1])) return
    retakeFrom.current = null
    setOrderEpoch((n) => n + 1)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, answers)
  const tryAgain = () => { retakeFrom.current = answers; refresh(); needs.refresh(); runningRead.refresh() }

  const needle = q.trim().toLowerCase()
  // Each fold's open state: the owner's choice, or null to follow the search (open while it matches
  // inside). A new search hands both back to it, and only a new one: never the first render.
  const [open, setOpen] = useSyncedDraft(FOLDS_AT_REST, needle)
  const inShow = (p: ProjectItem) => show === 'all' ? true : show === 'archived' ? p.status === 'archived' : p.status !== 'archived'
  const shown = ordered.filter(inShow)
  // Search: a project's name, and the titles of its chats, goal loops and code sessions.
  const matchesOf = (pid: string) => (needle ? threads.filter((t) => t.projectId === pid && t.title.toLowerCase().includes(needle)) : [])
  const listed = needle ? shown.filter((p) => p.name.toLowerCase().includes(needle) || matchesOf(p.id).length > 0) : shown
  const mine = listed.filter((p) => !p.made_for)
  const madeFor = listed.filter((p) => !!p.made_for)
  const loose = threads.filter((t) => t.projectId === NO_PROJECT && (!needle || t.title.toLowerCase().includes(needle)))
  const looseLive: LiveRow[] = useMemo(() => {
    const items = (waitingList?.waiting ?? []).filter((w) => !w.project_id && /^(chat|subagent|loop):/.test(w.work))
    const asked = new Set(items.map((w) => w.work))
    const rows = (runningList?.running ?? []).filter((r) => !r.project_id && ['chat', 'subagent', 'loop'].includes(r.kind) && r.waiting.length === 0 && !asked.has(r.work))
    return [...items.map((item) => ({ kind: 'waiting' as const, item })), ...rows.map((row) => ({ kind: 'running' as const, row }))]
  }, [waitingList, runningList])
  const matchCount = needle ? listed.reduce((n, p) => n + matchesOf(p.id).length, 0) + loose.length : 0
  // While searching, a project counts as a match by its own name; one found only by its chats is
  // counted by those chats. At rest the count is the list's own cards, the folds aside.
  const named = needle ? listed.filter((p) => p.name.toLowerCase().includes(needle)).length : 0
  const projectsWord = (n: number) => `${n} project${n === 1 ? '' : 's'}`
  const chatsWord = (n: number) => `${n} chat${n === 1 ? '' : 's'}`
  const line = !projects ? undefined
    : needle
      ? (named && matchCount ? `${projectsWord(named)} and ${chatsWord(matchCount)} match`
        : named ? `${projectsWord(named)} match` : matchCount ? `${chatsWord(matchCount)} match` : undefined)
      : mine.length ? projectsWord(mine.length) : undefined
  // The chats could not be read: the list says so, and never reads that as nothing there.
  const threadsFailed = !!threadsRead.error && threadsRead.groups.length === 0
  const firstRun = !!projects && projects.length > 0 && projects.every((p) => p.is_builtin) && threads.length === 0 && !threadsFailed
  const unreadable = needs.unreadable.map((k) => SOURCE_WORDS[k] ?? k)
  const madeForLive = madeFor.reduce((n, p) => n + pulseOf(p).running.length, 0)
  const madeForNeeds = madeFor.reduce((n, p) => n + pulseOf(p).needsYou.length, 0)
  const looseNeeds = looseLive.filter((r) => r.kind === 'waiting').length
  const looseRunning = looseLive.length - looseNeeds
  const liveOf = (needsN: number, runningN: number): FoldLive[] => [
    ...(needsN > 0 ? [{ text: `${needsN} ${needsN === 1 ? 'needs' : 'need'} you`, tone: STATE_TONE.waiting }] : []),
    ...(runningN > 0 ? [{ text: `${runningN} running`, tone: STATE_TONE.active }] : []),
  ]
  // A search that matches inside a fold opens it, until the owner closes it.
  const madeForOpen = open.madeFor ?? (!!needle && madeFor.length > 0)
  const looseOpen = open.loose ?? (!!needle && loose.length > 0)
  // During a search the loose fold is drawn only when a chat in no project matches.
  const looseShown = needle ? loose.length > 0 : loose.length > 0 || looseLive.length > 0
  const lastOf = (pid: string): Thread | undefined => threads.reduce<Thread | undefined>((best, t) => (t.projectId === pid && (!best || t.activeAt > best.activeAt) ? t : best), undefined)

  const rowMenu = (p: ProjectItem): RowMenuItem[] => {
    const pinned = pins.includes(p.id)
    const isDefault = p.id === defaultProjectId
    const archived = p.status === 'archived'
    const pinOff = offReason ?? (pinsUnread ? "Couldn't read your pins." : archived && !pinned ? "Restore it first. An archived project can't be pinned." : undefined)
    return [
      { icon: <FolderOpen size={15} />, label: 'Open', onSelect: () => onOpen(p.id) },
      { icon: <MessageSquare size={15} />, label: 'Start a chat here', onSelect: () => navigate(`chat/new?project=${encodeURIComponent(p.id)}`), disabled: archived || offline, disabledReason: offReason ?? (archived ? 'Restore it first.' : undefined), breakAfter: true },
      { icon: <Pin size={15} />, label: pinned ? 'Unpin' : 'Pin to the top', onSelect: () => togglePin(p.id), disabled: !!pinOff, disabledReason: pinOff },
      { icon: <Star size={15} />, label: isDefault ? 'Default project' : 'Make default project', checkMark: isDefault,
        onSelect: () => { if (!isDefault) void setDefaultProject(p.id) },
        disabled: offline || (archived && !isDefault), disabledReason: offReason ?? (archived ? 'Restore it first. An archived project can\'t be the default.' : undefined) },
      { icon: <Pencil size={15} />, label: 'Rename…', onSelect: () => navigate(`projects/${p.id}?rename=1`), disabled: p.is_builtin || offline, disabledReason: offReason ?? (p.is_builtin ? `${p.name} is built in. It can't be renamed.` : undefined) },
      { icon: <Download size={15} />, label: 'Export', onSelect: () => void exportProject(p), disabled: offline, disabledReason: offReason, breakAfter: true },
      archived
        ? { icon: <ArchiveRestore size={15} />, label: 'Restore', onSelect: () => void setArchived(p, false), disabled: offline, disabledReason: offReason }
        : { icon: <Archive size={15} />, label: 'Archive', onSelect: () => void setArchived(p, true), disabled: offline, disabledReason: offReason },
      { icon: <Trash2 size={15} />, label: 'Delete project…', name: `Delete project: ${p.name}`, onSelect: () => void del(p), danger: true, disabled: p.is_builtin || offline, disabledReason: offReason ?? (p.is_builtin ? `${p.name} is built in. It can't be deleted.` : undefined) },
    ]
  }

  const card = (p: ProjectItem, as: 'li' | 'div' = 'li') => (
    <ProjectCard key={p.id} as={as} project={p} pulse={pulseFailed ? null : pulseOf(p)} activeAt={pulseOf(p).lastActiveAt} pinned={pins.includes(p.id)} last={lastOf(p.id)}
      isDefault={p.id === defaultProjectId} menu={rowMenu(p)} onOpen={() => onOpen(p.id)} onOpenRoute={navigate}
      matches={needle ? matchesOf(p.id) : null} needle={needle} waiting={waitingList?.waiting ?? []}
      onMoreMatches={() => navigate(`projects/${p.id}?q=${encodeURIComponent(q.trim())}`)} />
  )

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        keepCornerPadding
        left={<PageTitle>Projects</PageTitle>}
        // The ordering tenet: search, the filter, the "…", then the primary. All tasks and All loops
        // live in the "…": a phone's way to every project's tasks and to every loop. Its trigger is
        // a 44 px target at the compact widths.
        right={<HeaderActions overflowClassName={TOUCH_TARGET}>
          <HeaderSearch value={q} onChange={setQ} placeholder="Search projects and chats" />
          <HeaderFilter sections={[{ title: 'Show', value: show, defaultKey: '', options: SHOW_OPTIONS, onChange: setShow }]} />
          <HeaderControl icon={Upload} label="Import a project…" onClick={() => importInput.current?.click()} inOverflow
            disabled={offline} disabledReason={offReason} hint="A project archive (.zip) exported from PersonalClaw" />
          <HeaderControl icon={ListChecks} label="All tasks" onClick={onOpenTasks} inOverflow />
          <HeaderControl icon={Repeat} label="All loops" onClick={() => navigate('loops/history')} inOverflow />
          <HeaderControl icon={Plus} label="New project" onClick={() => setCreating(true)} variant="primary" priority="primary"
            className={TOUCH_TARGET} disabled={offline} disabledReason={offReason} />
        </HeaderActions>} />
      {/* Raw: no primitive draws a file picker. Forwarded to by the header's Import a project…, which is the keyboard-reachable control. */}
      <input ref={importInput} type="file" accept=".zip,application/zip" className="hidden" data-project-import
        onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void previewImport(file) }} />

      {creating && (
        <NewProjectModal onClose={() => setCreating(false)} taken={(projects ?? []).map((p) => p.name)}
          onCreated={(p) => { setCreating(false); refresh(); onOpen(p.id) }} />
      )}
      {importing && (
        <ImportProjectModal plan={importing.plan} fileName={importing.file.name} busy={importBusy}
          onClose={() => setImporting(null)} onImport={commitImport} />
      )}

      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-l py-l">
        {/* The list spans the pane, and its own width decides its columns. */}
        <div className="@container flex w-full flex-col gap-m">
          {!!projects?.length && (
            <ListStatus results={{ count: needle ? named + matchCount : mine.length, noun: 'projects', active: !!needle || show !== '' }} stale={stale} line={line} />
          )}
          {err && <InlineError onDismiss={() => setErr(null)}>{err}</InlineError>}
          {offline && (
            <p data-type="body-s" className="text-on-surface-var">
              {`${UNREACHABLE}. Showing what was loaded${connection.lastReachedAt ? ` at ${clockTime(connection.lastReachedAt / 1000)}` : ''}.`}
            </p>
          )}
          {projects && pulseFailed && (
            <ReadNote onRetry={tryAgain}>Couldn't check what's running. Projects are listed without it.</ReadNote>
          )}
          {projects && threadsFailed && (
            <ReadNote onRetry={() => threadsRead.refresh()}>Couldn't read the chats. Projects are listed without them.</ReadNote>
          )}
          {projects && !pulseFailed && unreadable.length > 0 && (
            <ReadNote onRetry={tryAgain} announce>
              {`Could not read ${unreadable.join(', ')}: what they hold may be waiting on you and is not counted here.`}
            </ReadNote>
          )}

          {/* A failed first load is NOT an empty list: the error branch comes first, because
              `projects === undefined` also satisfies the skeleton and empty conditions. */}
          {projects === undefined && loadErr ? <LoadError what="projects" error={loadErr} onRetry={() => { invalidateKeys('projects:list'); refresh() }} />
            : loading && !projects ? <ListSkeleton rows={5} what="projects" />
            : !projects?.length ? (
              <EmptyState icon={FolderKanban} title="No projects yet"
                hint="Projects keep related work together. Each one shows what needs you, what is running and what its work made."
                action={{ label: 'New project', onClick: () => setCreating(true), icon: Plus, className: TOUCH_TARGET, disabledReason: offReason }} />
            ) : needle && !listed.length && !loose.length && !threadsFailed ? (
              <OutlinedNote>
                <p data-type="body-m" className="min-w-0 flex-1">{`No project or chat matches “${q.trim()}”.`}</p>
                <TextLink onClick={() => setQ('')} ink="emphasis" className={LINK_TARGET}>Clear search</TextLink>
              </OutlinedNote>
            ) : !shown.length && show === 'archived' ? (
              <OutlinedNote>
                <p data-type="body-m" className="min-w-0 flex-1">Nothing is archived.</p>
                <TextLink onClick={() => setShow('')} ink="emphasis" className={LINK_TARGET}>Show active projects</TextLink>
              </OutlinedNote>
            ) : !shown.length ? (
              <OutlinedNote>
                <p data-type="body-m" className="min-w-0 flex-1">No project is active.</p>
                <div className="flex flex-wrap gap-s">
                  <Button variant="secondary" onClick={() => setShow('archived')} className={TOUCH_TARGET}>Show archived</Button>
                  <Button onClick={() => setCreating(true)} disabled={offline} disabledReason={offReason} className={TOUCH_TARGET}><Plus size={15} /> New project</Button>
                </div>
              </OutlinedNote>
            ) : (
              <>
                {/* Two columns once each card can be about 600 px. A grid flows row by row, so the
                    reading order (and the tab order) is row order, then column. */}
                {mine.length > WINDOWING_THRESHOLD ? (
                  // Past the shared threshold the list windows, one card to a row: a windowed list
                  // stacks its rows, so its cards keep their reading order and their count.
                  <WindowedList items={mine} rowKey={(p) => p.id} rowHeights="variable" estimateRowHeight={76} gap={12}
                    noun="projects" findHint="use Search projects and chats in the header to find one." ariaLabel="Projects"
                    className="flex flex-col gap-m">
                    {(p) => card(p, 'div')}
                  </WindowedList>
                ) : (
                  <ul aria-label="Projects" className={CARD_GRID}>
                    {mine.map((p) => card(p))}
                  </ul>
                )}
                {firstRun && !needle && (
                  <OutlinedNote>
                    <p data-type="body-m" className="min-w-0 flex-1">Projects keep related work together. Each one shows what needs you, what is running and what its work made.</p>
                    <Button onClick={() => setCreating(true)} disabled={offline} disabledReason={offReason} className={TOUCH_TARGET}><Plus size={15} /> New project</Button>
                  </OutlinedNote>
                )}
                {madeFor.length > 0 && (
                  <FoldRow label="Made for goal loops" count={projectsWord(madeFor.length)} live={liveOf(madeForNeeds, madeForLive)}
                    open={madeForOpen} onToggle={() => setOpen((o) => ({ ...o, madeFor: !madeForOpen }))}>
                    <ul aria-label="Made for goal loops" className={CARD_GRID}>
                      {madeFor.map((p) => card(p))}
                    </ul>
                  </FoldRow>
                )}
                {looseShown && (
                  <FoldRow label="Not in a project" count={loose.length > 0 ? chatsWord(loose.length) : ''} live={liveOf(looseNeeds, looseRunning)}
                    open={looseOpen} onToggle={() => setOpen((o) => ({ ...o, loose: !looseOpen }))}>
                    <div className="flex flex-col gap-s rounded-lg bg-surface-container px-m py-s">
                      {(needle || !pulseFailed) && (
                        <ul className="flex flex-col">
                          {needle
                            ? loose.slice(0, MATCH_ROWS).map((t) => <ProjectWorkRow key={t.key} work={{ kind: 'thread', thread: t, waiting: firstAsk(waitingList?.waiting ?? [], t) }} onOpen={navigate} mark={(text) => marked(text, needle)} />)
                            : looseLive.map((w) => <ProjectWorkRow key={w.kind === 'waiting' ? w.item.id : w.row.work} work={w} onOpen={navigate} />)}
                        </ul>
                      )}
                      <TextLink onClick={() => navigate('chat/history')} ink="emphasis" size="sm" className={cx('self-start', LINK_TARGET)}>Open Chat history</TextLink>
                    </div>
                  </FoldRow>
                )}
              </>
            )}
        </div>
      </div>
    </div>
  )
}

/** The list's grid of cards: one column, two once the list (its `@container`) is 72rem wide, where
 *  each card is about 600 px. Past the shared windowing threshold the list is `WindowedList`'s one
 *  column of cards instead, which keeps a long list fast. */
const CARD_GRID = 'grid grid-cols-1 items-start gap-m @6xl:grid-cols-2'

/** A note above the list for a read that could not be made: a tinted box with its warn icon, and
 *  Try again at its end. Only the unreadable sources' note announces (`announce`); the rest are
 *  read where they are. */
function ReadNote({ children, onRetry, announce = false }: { children: React.ReactNode; onRetry: () => void; announce?: boolean }) {
  return (
    <div role={announce ? 'status' : undefined} data-read-note data-type="body-s"
      className="flex flex-wrap items-center gap-x-s gap-y-xs rounded-md bg-surface-container px-m py-s text-on-surface-var">
      <TriangleAlert aria-hidden size={15} className="shrink-0 text-warn" />
      <span className="min-w-0 flex-1">{children}</span>
      <TextLink onClick={onRetry} ink="emphasis" size="sm" className={LINK_TARGET}>Try again</TextLink>
    </div>
  )
}

/** An empty state or the first-run line: an outlined box with its words and what to do next. */
function OutlinedNote({ children }: { children: React.ReactNode }) {
  return (
    <div data-outlined-note className="flex flex-wrap items-center gap-x-m gap-y-s rounded-lg px-l py-m text-on-surface-var shadow-[inset_0_0_0_1px_var(--color-outline-variant)]">
      {children}
    </div>
  )
}

/** The first item that waits on the owner for *thread*'s work, in the list's order. */
function firstAsk(waiting: readonly WaitingItem[], thread: Thread): WaitingItem | undefined {
  const work = thread.kind === 'chat' ? `chat:${thread.id}` : `loop:${thread.id}`
  return waiting.find((w) => w.work === work)
}

/** A row of a project's "…" and its context menu: a `ContextMenuItem`, its reason while it is off,
 *  and whether a break follows it in the "…". */
type RowMenuItem = ContextMenuItem & { disabledReason?: string; breakAfter?: boolean }

/** One project's card on the list: its name, pin, one fact chip, its pulse and last activity, and
 *  its "…"; its folder and brief (or the loop it was made for, or its latest row); then what needs
 *  you and what is running in it, or, while searching, its matches. */
function ProjectCard({ project: p, pulse, activeAt, pinned, isDefault, last, menu, onOpen, onOpenRoute, matches, needle, waiting, onMoreMatches, as: Item = 'li' }: {
  project: ProjectItem
  /** Null while the pulse could not be read: no counts and no live rows. */
  pulse: ProjectPulse | null
  /** When it last moved, epoch ms: read from its threads and its own `updated_at` too, so it is
   *  said when the pulse could not be read. */
  activeAt: number
  /** Its element: an `li` in the list, a `div` inside the windowed list's own item. */
  as?: 'li' | 'div'
  pinned: boolean
  isDefault: boolean
  /** Its newest thread, for "Last: …" when it has no folder and no brief. */
  last?: Thread
  menu: RowMenuItem[]
  onOpen: () => void
  onOpenRoute: (route: string) => void
  /** The threads a search matched under it, or null while not searching. */
  matches: Thread[] | null
  needle: string
  waiting: readonly WaitingItem[]
  onMoreMatches: () => void
}) {
  const headingId = useId()
  const words = pulse ? pulseWords(pulse) : []
  const active = activeWords(activeAt)
  const fact = p.status === 'archived' ? 'Archived' : p.is_builtin ? 'Built-in' : isDefault ? 'Default' : ''
  // Only a folder that is gone is "missing": one the picker will not open keeps its path.
  const missing = p.folder_state === 'missing'
  const folder = p.workspace_dir && !missing ? folderTail(p.workspace_dir) : ''
  const brief = p.brief ? firstSentence(p.brief) : ''
  const lastLine = last ? `Last: ${previewLine(last.title)}` : ''
  // Line 2: the loop it was made for; a built-in's purpose, then its latest row; its folder and
  // brief; or, with neither, its latest row.
  const parts: { text: string; mono?: boolean }[] = p.made_for ? [{ text: madeForLine(p.made_for) }]
    : p.is_builtin ? [BUILTIN_PURPOSE[p.name] ?? '', lastLine].filter(Boolean).map((text) => ({ text }))
      : folder || brief ? [...(folder ? [{ text: folder, mono: true }] : []), ...(brief ? [{ text: brief }] : [])]
        : lastLine ? [{ text: lastLine }] : []
  const heading = [p.name, pinned && 'pinned', ...words, activeSpoken(activeAt)].filter(Boolean).join(', ')
  const live = pulse?.live ?? []
  const shownMatches = matches?.slice(0, MATCH_ROWS) ?? []
  // The context menu shows a row's reason the way the "…" does, under its label.
  const contextItems = menu.map((it) => ({ ...it, hint: it.disabled ? it.disabledReason : it.hint }))
  return (
    // In the grid the `li` is the list's item and carries the name. Past the windowing threshold the
    // card is a `div` inside the windowed list's own item, so it is a group, which a name may label.
    <Item role={Item === 'div' ? 'group' : undefined} aria-labelledby={headingId} data-project-card className="@container/card relative min-w-0 rounded-lg bg-surface-container">
      <ContextMenu items={contextItems}>
        <div className="flex min-w-0 flex-col gap-xs px-m py-s">
          <div tabIndex={-1} onClick={onOpen}
            className="group relative flex min-w-0 cursor-pointer flex-col gap-0.5 rounded-lg max-lg:min-h-11 has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary">
            <RowHitTarget label={p.name} />
            <div className="flex min-w-0 flex-wrap items-center gap-x-s gap-y-0.5 pr-[2rem] max-lg:pr-[2.5rem]">
              <ProjectMark name={p.name} />
              <h2 id={headingId} aria-label={heading} data-type="title-m" className="min-w-0 truncate text-on-surface" title={p.name}>{p.name}</h2>
              {pinned && <Pin size={13} aria-hidden className="shrink-0 text-on-surface-low" />}
              {fact && <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low">{fact}</span>}
              <span data-pulse className="ml-auto flex shrink-0 flex-wrap items-center gap-x-m max-md:ml-0 max-md:basis-full max-md:pl-[2.25rem]">
                {pulse && pulse.needsYou.length > 0 && <PulseWord tone={STATE_TONE.waiting} text={words[0]} />}
                {pulse && pulse.running.length > 0 && <PulseWord tone={STATE_TONE.active} text={words[words.length - 1]} />}
                {active && <span data-type="caption" className="text-on-surface-low">{active}</span>}
              </span>
            </div>
            {(parts.length > 0 || missing) && (
              <div data-type="body-s" data-line-two className="flex min-w-0 items-center gap-s pl-[2.25rem] text-on-surface-low">
                {missing && <span data-type="caption" data-folder-missing className="shrink-0 rounded-pill bg-warn/15 px-1.5 py-0.5 text-warn">Folder missing</span>}
                {parts.length > 0 && (
                  <span className="min-w-0 truncate max-md:line-clamp-2 max-md:whitespace-normal">
                    {parts.map((part, i) => (
                      <span key={i}>
                        {/* The dot is decoration; the spaces keep the parts apart when read aloud. */}
                        {i > 0 && <>{' '}<span aria-hidden>·</span>{' '}</>}
                        <span className={part.mono ? 'font-mono text-on-surface-var' : undefined}>{part.text}</span>
                      </span>
                    ))}
                  </span>
                )}
              </div>
            )}
          </div>
          {/* Drawn in line 1's reserved end; after the open target, so Tab reaches it before the live rows. */}
          <RowMenu name={p.name} items={menu} />
          {matches ? (
            shownMatches.length > 0 && (
              <ul className="flex flex-col lg:pl-7">
                {shownMatches.map((t) => <ProjectWorkRow key={t.key} work={{ kind: 'thread', thread: t, waiting: firstAsk(waiting, t) }} onOpen={onOpenRoute} mark={(text) => marked(text, needle)} />)}
              </ul>
            )
          ) : live.length > 0 && (
            <ul className="flex flex-col lg:pl-7">
              {live.slice(0, LIVE_ROWS).map((w) => <ProjectWorkRow key={w.kind === 'waiting' ? w.item.id : w.row.work} work={w} onOpen={onOpenRoute} />)}
            </ul>
          )}
          {matches && matches.length > MATCH_ROWS && (
            <TextLink onClick={onMoreMatches} ink="emphasis" size="sm" className={cx('self-start lg:ml-7', LINK_TARGET)}>{`${matches.length - MATCH_ROWS} more matches in ${p.name}`}</TextLink>
          )}
          {!matches && live.length > LIVE_ROWS && (
            <TextLink onClick={onOpen} ink="emphasis" size="sm" className={cx('self-start lg:ml-7', LINK_TARGET)}>{`${live.length - LIVE_ROWS} more in ${p.name}`}</TextLink>
          )}
        </div>
      </ContextMenu>
    </Item>
  )
}

/** One of the pulse's counts: an 8 px dot in its state's tone, ringed in it, and its words. */
function PulseWord({ tone, text }: { tone: string; text: string }) {
  return (
    <span data-type="label-s" className="inline-flex items-center gap-xs text-on-surface">
      <span aria-hidden data-mark="dot" className="size-2 rounded-pill border-[1.5px]" style={{ background: tone, borderColor: tone }} />{text}
    </span>
  )
}

/** A project row's "…": the same items its context menu opens, always drawn, never hover-only. It
 *  opens under a caption naming the project, with a break after starting a chat and after Export. */
function RowMenu({ name, items }: { name: string; items: RowMenuItem[] }) {
  return (
    <div className="absolute right-s top-s max-lg:right-0 max-lg:top-0">
      <Popover portal placement="bottom" align="right" width={290}
        trigger={(open, toggle) => (
          <SquareIconButton icon={MoreHorizontal} label={`More actions for ${name}`} title="More actions" ariaExpanded={open}
            onClick={(e) => { e.stopPropagation(); toggle() }} className={TOUCH_TARGET} />
        )}>
        {(close) => (
          <div className="flex flex-col gap-0.5">
            <p data-type="caption" data-menu-caption className="px-m pb-0.5 pt-xs text-on-surface-low">{`More actions for ${name}`}</p>
            {items.map((it) => (
              <div key={it.label} data-menu-item className="flex flex-col gap-0.5">
                <div className={it.danger && !it.disabled ? '[&_button]:text-danger' : undefined}>
                  <MenuRow icon={it.icon} label={it.label} name={it.name} checkMark={it.checkMark} disabled={it.disabled}
                    hint={it.disabled ? it.disabledReason : undefined} nameIsLabel={it.disabled && !!it.disabledReason}
                    title={it.disabled ? it.disabledReason : undefined}
                    onClick={() => { if (it.disabled) return; it.onSelect(); close() }} />
                </div>
                {it.breakAfter && <div aria-hidden data-menu-break className="mx-m my-0.5 h-px bg-outline-variant" />}
              </div>
            ))}
          </div>
        )}
      </Popover>
    </div>
  )
}

function ImportProjectModal({ plan, fileName, busy, onClose, onImport }: {
  plan: ProjectImportResult
  fileName: string
  busy: boolean
  onClose: () => void
  onImport: () => void
}) {
  return (
    <Modal title="Import a project" icon={<Upload size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-l">
        <div className="flex flex-col gap-xs">
          <p data-type="body-m" className="text-on-surface">{plan.summary}</p>
          <p data-type="body-s" className="text-on-surface-low">
            From <span className="font-mono">{fileName}</span>. It arrives as a new project named{' '}
            <span className="text-on-surface">{plan.project_name}</span>. Nothing is written until you import it.
          </p>
        </div>
        {plan.refused.length > 0 && (
          <div className="flex flex-col gap-xs">
            <span data-type="label-m" className="text-on-surface">Not imported</span>
            <ul className="flex flex-col gap-xs">
              {plan.refused.map((issue) => (
                <li key={issue.path} data-type="body-s" className="text-on-surface-var">
                  <span className="font-mono">{issue.path}</span>: {issue.message}
                </li>
              ))}
            </ul>
          </div>
        )}
        {plan.secrets_expected.length > 0 && (
          <div className="flex flex-col gap-xs">
            <span data-type="label-m" className="flex items-center gap-xs text-on-surface"><KeyRound size={14} /> Credentials to re-enter</span>
            <p data-type="body-s" className="text-on-surface-var">
              An archive never carries credentials. After importing, add these in Settings → Secrets:{' '}
              <span className="font-mono">{plan.secrets_expected.join(', ')}</span>
            </p>
          </div>
        )}
        <div className="flex justify-end gap-s">
          <Button variant="ghost" onClick={onClose} className={TOUCH_TARGET}>Cancel</Button>
          <Button onClick={onImport} loading={busy} disabled={busy || !plan.ok} className={TOUCH_TARGET}
            disabledReason={!plan.ok ? 'Nothing in this archive can be imported' : undefined}>
            Import
          </Button>
        </div>
      </div>
    </Modal>
  )
}
