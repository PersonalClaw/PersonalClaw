import { useEffect, useState } from 'react'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { AnimatePresence } from 'framer-motion'
import { FolderKanban, Plus, Loader2, FolderOpen, Folder, FolderTree, File as FileIcon, X, ChevronRight, Pencil, Check, ListChecks, Star, MessageSquare, Repeat, Target, Code2, Telescope, Palette, FileText, CircleDot, AlertTriangle, RefreshCw, Square, Download, Archive, ArchiveRestore, type LucideIcon } from 'lucide-react'
import { statusMeta, TERMINAL } from '../tasks/taskMeta'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl, HeaderMenu } from '../../ui/HeaderActions'
import { IconButton } from '../../ui/IconButton'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { cx } from '../../ui/cx'
import { useThreads } from '../../lib/useThreads'
import { ProjectThreads, projectThreads } from './ProjectThreads'
import { confirm } from '../../ui/dialog'
import { SidePanel } from '../../ui/SidePanel'
import { Button } from '../../ui/Button'
import { TextArea, TextInput, useSyncedDraft } from '../../ui/forms'
import { InlineError } from '../../ui/InlineError'
import { StatusPill } from '../../ui/StatusPill'
import { WorkspacePicker } from '../code/WorkspacePicker'
import { useWorkspaceMissing } from '../../lib/useWorkspaceMissing'
import { api, ApiError, MAX_NAME_LEN, type TaskListItem, type LoopKind, type TaskItem, type FsEntry, type WorkRow, type WorkState, type WorkBoard } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { downloadFrom } from '../../lib/download'
import { DEFAULT_PROJECT_KEY, useDefaultProject } from '../../lib/defaultProject'
import { notify } from '../../app/appSdk'
import { PageTitle } from '../../ui/PageTitle'
import { NEEDS_YOU_DOT, WORK_OUTCOME_LOOK, WORK_STATE_LABEL, workRoute } from './ProjectWorkRow'
import { ProjectListPage } from './ProjectList'
import { loopRoute } from '../../lib/loopKind'
import { reportingWrite } from '../../app/reportingWrite'
import { stopLoop } from '../loop/stopLoop'
import { TextLink } from '../../ui/TextLink'
import { TaskList } from '../tasks/TasksSection'
import { MarkdownLine } from '../../ui/MarkdownLine'
import { previewLine } from '../../lib/previewText'

/** All tasks is a 44 px target at the compact widths (320 to 768 px), where it is a phone's way to
 *  every project's tasks, and the header's own size above them (#3905). */
export const ALL_TASKS_TARGET = 'max-lg:min-h-11 max-lg:min-w-11'

/** Projects navigation — the first-class work unit tying Goal Loops, Code projects,
 *  and Tasks together under one context-continuous container.
 *    #/projects        → the project list (`ProjectList`)
 *    #/projects/<id>   → that project's detail (workspace, context, task lists)
 *    #/projects/<id>?tab=tasks → its Tasks view (#3818): the task list and board, held to it, with
 *                        the keys `#/tasks` reads (view, filter, q, sort, list, tag, open)
 *    #/projects/<id>?rename=1 → its title in edit mode, from a list row's Rename: dropped at once with
 *                        a replace, and the editor opened when the project arrives (a built-in's
 *                        page ignores it)
 */
export function ProjectsSection({ sub, navigate, query, setQuery, navEpoch }: RouteProps) {
  const seg = (sub || '').split('/')[0]
  if (seg) return <ProjectDetailPage id={seg} onBack={() => navigate('projects')} navigate={navigate} query={query} setQuery={setQuery} navEpoch={navEpoch} />
  return <ProjectListPage navigate={navigate} onOpenTasks={() => navigate('tasks')} query={query} setQuery={setQuery} />
}

/** The keys only the Tasks view reads, which the overview leaves behind. */
const TASK_VIEW_KEYS = ['view', 'filter', 'q', 'sort', 'list', 'tag', 'open', 'edit'] as const

function ProjectDetailPage({ id, onBack, navigate, query, setQuery, navEpoch }: { id: string; onBack: () => void; navigate: (to: string) => void } & Pick<RouteProps, 'query' | 'setQuery' | 'navEpoch'>) {
  // `error` was previously discarded, so a 500 fell through to `!project` and the page claimed the
  // project was deleted. A failed read is not a deletion.
  const { data: project, loading, error: detailErr, refresh } = useQuery(`projects:detail:${id}`, () => api.project(id))
  const { data: lists } = useQuery(`projects:lists:${id}`, () => api.taskLists(id))
  // The Work board: runs + legacy loops + tasks in one
  // state-grouped board. Local-first (persist) + stale-while-revalidate, the same seam
  // the rest of the detail page uses — the board paints from cache, then swaps in the
  // fresh projection when it lands.
  const { data: work, loading: workLoading } = useQuery(`projects:work:${id}`, () => api.projectWork(id), { persist: true })
  // The project's threads, as the sidebar lists them under it (#3814): the page is the project's
  // thread list beside its detail.
  const threadsRead = useThreads()
  const threadCount = projectThreads(threadsRead, id).length
  // Every row opens the work it is, where its own page is (`workRoute`), and a loop at work can
  // be stopped from it: a loop that showed here as "Working" offered nothing to click.
  const openWork = (row: WorkRow) => navigate(workRoute(row))
  const stopWork = async (row: WorkRow) => {
    if (await stopLoop(row.run_id)) invalidateKeys(`projects:work:${id}`)
  }
  // Resume does what the work's OWN page does, then opens that page. It used to navigate to
  // `loop/<id>`, the loop COMPOSER, which ignores the id: Resume opened an empty "new loop" form
  // and resumed nothing. A failed resume still opens the work — its page says why, one look away,
  // and `reportingWrite` has already said that it failed.
  const resumeWork = async (row: WorkRow) => {
    if (row.source === 'loop') {
      await reportingWrite('resume this loop', () => api.uLoopAction(row.run_id, 'resume'))
      invalidateKeys(`projects:work:${id}`)
      navigate(loopRoute({ id: row.run_id, kind: row.kind }))
      return
    }
    await reportingWrite('resume this run', () => api.resumeWorkflowRun(row.run_id, {}))
    invalidateKeys(`projects:work:${id}`)
    navigate(`workflows/runs/${row.run_id}`)
  }
  // Whether context adapters are enabled (Settings › Legibility). The
  // "Refresh context files" action only makes sense when this is on AND a workspace is
  // bound, so the button appears only then (server still re-checks + 403s if stale).
  const { data: pcfg } = useQuery('config:personalclaw', () => api.personalclawConfig())
  const adaptersEnabled = Boolean((pcfg as { legibility?: { context_adapters?: boolean } } | undefined)?.legibility?.context_adapters)
  // The bound workspace folder is GONE — a container recreated without it on its volume, or a
  // repo moved or deleted. The page kept showing the dead path as if it were fine, so a user found
  // out only when work there failed. Re-probed whenever the project record refreshes.
  const wsMissing = useWorkspaceMissing(project?.workspace_dir, { recheck: project })
  const [renaming, setRenaming] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  // A project row's Rename opens this page at `?rename=1`. The key is dropped at once with a
  // replace, whether or not the project loads, so Back and a reload never reopen the editor; the
  // ask is held until the project arrives, then its title opens in edit mode with the name in it.
  // A built-in's page, whose name is its identity, ignores it.
  const [renameAsked, setRenameAsked] = useState(false)
  useEffect(() => {
    if (query.rename !== '1') return
    setRenameAsked(true)
    setQuery({ rename: null }, { replace: true })
  }, [query.rename, setQuery])
  useEffect(() => {
    if (!renameAsked || (project === undefined && !detailErr)) return
    setRenameAsked(false)
    if (project && !project.is_builtin) { setNameDraft(project.name); setRenaming(true) }
  }, [renameAsked, project, detailErr])
  const [pickWs, setPickWs] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [statusBusy, setStatusBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // The right-docked SidePanel content: a task list's tasks, or a directory tree for
  // the workspace/context dir. One panel at a time; null = closed. URL-backed via
  // ?panel (push, so Back closes it): `tasks:<listId>` | `dir:workspace` | `dir:context`.
  // The panel object is resolved from that token against loaded lists / the project.
  const [panelTok, setPanelTok] = useQueryParam(query, setQuery, 'panel', '')
  // Which view of the project shows (`?tab`): its overview, or its Tasks (#3818). A view of one
  // page, so choosing one replaces the history entry, as every in-page view does.
  const showsTasks = query.tab === 'tasks'
  const chooseView = (v: string) => setQuery(v === 'tasks'
    ? { tab: 'tasks', panel: null }
    : { tab: null, ...Object.fromEntries(TASK_VIEW_KEYS.map((k) => [k, null])) }, { replace: true })
  const panel: { kind: 'tasks'; list: TaskListItem } | { kind: 'dir'; label: string; path: string } | null = (() => {
    if (!panelTok) return null
    const [kind, rest] = [panelTok.slice(0, panelTok.indexOf(':')), panelTok.slice(panelTok.indexOf(':') + 1)]
    if (kind === 'tasks') {
      const list = (lists || []).find((l) => l.id === rest)
      return list ? { kind: 'tasks', list } : null
    }
    if (kind === 'dir') {
      if (rest === 'workspace' && project?.workspace_dir) return { kind: 'dir', label: 'Workspace', path: project.workspace_dir }
      if (rest === 'context' && project?.context_dir) return { kind: 'dir', label: 'Context', path: project.context_dir }
    }
    return null
  })()
  const setPanel = (p: { kind: 'tasks'; list: TaskListItem } | { kind: 'dir'; label: string; path: string } | null) => {
    if (!p) return setPanelTok('')
    if (p.kind === 'tasks') return setPanelTok(`tasks:${p.list.id}`)
    // dir panels are exactly the workspace/context dirs; encode which, not the raw path.
    setPanelTok(`dir:${p.path === project?.context_dir ? 'context' : 'workspace'}`)
  }
  // Whether this is the user's default project — where the create forms (a new task, a new
  // loop) start. Server-stored, so it reads the same in every browser.
  const { defaultProjectId, setDefaultProject, pending: defaultPending } = useDefaultProject()
  const isDefault = defaultProjectId === id

  async function patch(body: Record<string, unknown>) {
    setErr(null)
    // The settings key too: archiving the default project makes it resolve to "none", and
    // restoring it brings it back, so the Default toggle must re-read after a status write.
    try { await api.updateProject(id, body); invalidateKeys(`projects:detail:${id}`); invalidateKeys('projects:list'); invalidateKeys(DEFAULT_PROJECT_KEY); refresh() }
    catch (e) { setErr((e as Error).message || 'Could not update the project') }
  }

  async function archiveProject() {
    if (!(await confirm({
      title: `Archive project "${project?.name ?? ''}"?`,
      body: 'This project stays in Projects, but is removed from project pickers until you restore it. Nothing is deleted: its work, tasks, context, and workspace files stay in place.',
      confirmLabel: 'Archive',
      icon: Archive,
    }))) return
    setStatusBusy(true)
    try { await patch({ status: 'archived' }) }
    finally { setStatusBusy(false) }
  }

  async function restoreProject() {
    setStatusBusy(true)
    try { await patch({ status: 'active' }) }
    finally { setStatusBusy(false) }
  }

  // (Re)render the marker-fenced PClaw context block into the project's
  // workspace_dir adapter files (CLAUDE.md / AGENTS.md / .cursorrules), replace-in-place.
  async function regenerateContext() {
    setErr(null)
    setRegenerating(true)
    try {
      const r = await api.regenerateContextAdapters(id)
      if (r.errors.length) notify(`Wrote ${r.written.length}, ${r.errors.length} failed — see ${r.errors[0].file}`, 'error')
      else notify(`Context files refreshed (${r.written.length}) in ${r.workspace_dir}`, 'success')
    } catch (e) {
      setErr((e as Error).message || 'Could not refresh context files')
    } finally {
      setRegenerating(false)
    }
  }

  if (loading && !project) {
    return <Shell onBack={onBack} title="Project"><div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div></Shell>
  }
  if (!project && detailErr) {
    return <Shell onBack={onBack} title="Project"><LoadError what="project" error={detailErr} onRetry={refresh} /></Shell>
  }
  if (!project) {
    return <Shell onBack={onBack} title="Project"><div className="flex h-full flex-col items-center justify-center gap-3 text-center"><p className="text-on-surface text-[0.9375rem]">This project no longer exists.</p><Button onClick={onBack}><ListChecks size={15} /> Back to projects</Button></div></Shell>
  }

  // Launch scoped under THIS project: deep-link the unified Loop composer with the project +
  // chosen KIND. The link carries the project, so launching from here scopes that one piece of
  // work and leaves the user's default project where they put it.
  const launchKind = (kind?: LoopKind) => {
    navigate(`loop?project=${encodeURIComponent(id)}` + (kind ? `&kind=${kind}` : ''))
  }
  const launchChat = () => { navigate(`chat?project=${encodeURIComponent(id)}`) }
  // Icons are the canonical per-kind glyphs (loopKind.ts) so the New menu reads the
  // same identity as the widgets, list, and cockpits. `general` deep-links with no kind.
  const NEW_KINDS: { kind?: LoopKind; label: string; hint: string; icon: LucideIcon }[] = [
    { kind: undefined, label: 'Loop', hint: 'A generic iterative loop', icon: Repeat },
    { kind: 'goal', label: 'Goal', hint: 'Verifiable / open-ended / monitor', icon: Target },
    { kind: 'code', label: 'Code', hint: 'SDLC work in a codebase', icon: Code2 },
    { kind: 'research', label: 'Research', hint: 'Deep web research → report', icon: Telescope },
    { kind: 'design', label: 'Design', hint: 'Design system + tokens', icon: Palette },
  ]
  // The board's own total (server-grouped, needs-input pinned first) for the column
  // title. The board itself renders inside `WorkBoardColumn`.
  const workCount = (work?.board ?? []).reduce((n, g) => n + g.count, 0)

  // The hub is a full-height SHELL. The header (Shell) owns identity + the Active/New/
  // Chat controls (no duplicated name in the body). The body is: a slim band (brief +
  // workspace/context directory bar) over a 2-region grid — Work (live loop/session
  // widgets) + Tasks — with task detail opening in a SidePanel.
  const titleNode = renaming ? (
    <div className="flex items-center gap-2">
      <TextInput autoFocus ariaLabel="Rename this project" value={nameDraft} onChange={setNameDraft}
        maxLength={MAX_NAME_LEN}
        onKeyDown={(e) => { if (e.key === 'Enter') { patch({ name: nameDraft.trim(), name_locked: true }); setRenaming(false) } else if (e.key === 'Escape') setRenaming(false) }}
        size="sm" surface="high" />
      {/* Its sibling Cancel below carries an `aria-label`; this one did not — the confirm half of a
          two-button pair announced as bare "button". That asymmetry is the tell. */}
      <Button size="sm" ariaLabel="Save the project name" onClick={() => { patch({ name: nameDraft.trim(), name_locked: true }); setRenaming(false) }} disabled={!nameDraft.trim()}
        disabledReason={!nameDraft.trim() ? 'Enter a project name first' : undefined}><Check size={14} /></Button>
      <IconButton icon={X} label="Cancel" size={24} iconSize={15} onClick={() => setRenaming(false)} />
    </div>
  ) : (
    <div className="flex items-center gap-2 min-w-0">
      <FolderKanban size={18} className="shrink-0 text-primary" />
      {/* 🪤 The shared shell's default title (below) is NOT what renders here: the project view passes
          this `titleNode`, so converting the shell alone left `#/projects/<id>` still h1-less — measured,
          after the "fix". Follow the value that actually reaches the slot. */}
      <PageTitle className="truncate">{project.name}</PageTitle>
      {/* No padlock here (issue 449). `name_locked` does not lock the name — it records that the
          user owns it so the LLM stops auto-renaming (`tasks/models.py`), and `update_project`
          guards only built-ins and duplicates, so renaming works with the flag set. The icon
          therefore locked nothing, named the wrong actor, and — because create and both rename
          confirms all write `name_locked: true` — was on by default for every project a user
          made, so it carried no signal even relabelled. Deleted rather than reworded: the state
          it marked is about agent behaviour, and there is no user-facing control to clear it.

          🪤 Issue numbers in THIS comment carry no leading hash on purpose. `design/tokenLint`
          skips `//` and `*` comment lines but not a JSX brace comment, so a three-digit issue
          reference inside one is indistinguishable from a raw hex colour and fails the gate. */}
      {!project.is_builtin && (
        <IconButton icon={Pencil} label="Rename" size={24} iconSize={13} onClick={() => { setNameDraft(project.name); setRenaming(true) }} className="shrink-0 -m-0.5" />
      )}
    </div>
  )
  // The Tasks view: the task list and board at the keys `#/tasks` reads, held to this project.
  if (showsTasks) {
    // Where you are, as a breadcrumb: the project's name, back to its Overview, then Tasks. A
    // project's Overview and Tasks are two pages of it, so the way between them is navigation, and
    // the header's one view switch stays the task list's layout, as on All tasks.
    // "› Tasks" is marked as a part the title cannot shed (`data-title-keep`): the header counts it
    // in the slot's minimum (`slotWidths`) and keeps it whole, so the heading says which page this
    // is, and the name gets what the row leaves (it truncates; its tooltip and link keep it). Only
    // where the row at its narrowest cannot fit beside it (320 px) does it clip, before a control
    // would. Below 1024 px the folder glyph goes, for the name.
    const crumb = (
      <div className="flex min-w-0 items-center gap-2">
        <FolderKanban size={18} className="shrink-0 text-primary max-lg:hidden" />
        <PageTitle className="flex min-w-0 items-baseline gap-xs">
          {/* The spaces are for the heading's text ("Garden › Tasks"); the row's gap lays it out. */}
          <TextLink onClick={() => chooseView('overview')} title={`${project.name}: its overview`} className="min-w-0 shrink-[100] truncate">{project.name}</TextLink>{' '}
          <span data-title-keep className="flex shrink-0 items-baseline gap-xs whitespace-nowrap">
            <span aria-hidden className="text-on-surface-low">›</span>{' '}
            <span>Tasks</span>
          </span>
        </PageTitle>
      </div>
    )
    return (
      <TaskList navigate={navigate} query={query} setQuery={setQuery} navEpoch={navEpoch}
        inProject={{
          name: project.name,
          // Renaming draws the Overview's own editor in the title, and the crumb comes back on
          // Save, Enter, Cancel or Escape.
          title: renaming ? titleNode : crumb,
          // Rename is the project's, so its Tasks page offers it too: low, so it is the first
          // into the `…` when the row is tight.
          actions: <>
            {!project.is_builtin && (
              <HeaderControl icon={Pencil} label="Rename" priority="low" onClick={() => { setNameDraft(project.name); setRenaming(true) }} />
            )}
            <HeaderControl icon={FolderKanban} label="All projects" onClick={onBack} />
          </>,
        }} />
    )
  }
  // Every control the Overview has, in the header's cluster so they shed together, in the
  // row's order: Archive (or Restore) first, then the default, Chat, Export and Tasks, the
  // project's other page. All projects and New, the primary action, close the row (`Shell`).
  const headerActions = (
    <>
      {project.status === 'archived' ? (
        <HeaderControl icon={ArchiveRestore} label={statusBusy ? 'Restoring…' : 'Restore'} variant="secondary"
          disabled={statusBusy} onClick={restoreProject} />
      ) : (
        <HeaderControl icon={Archive} label={statusBusy ? 'Archiving…' : 'Archive'} priority="low"
          disabled={statusBusy} onClick={archiveProject} />
      )}
      {/* 🔑 THE LABEL IS TRUE ON EVERY DEVICE NOW. It said "Default project" while the pointer
          lived in this browser's localStorage, so the same project read "Make default" in the next
          browser and a new task there started in Personal. The value is server-stored, and the
          title says what the setting governs — the create forms — and that it follows the user.
          An archived project is off every picker, so it cannot be where new work starts: the
          control stays reachable and says why rather than answering a click with a 409. */}
      <HeaderControl icon={Star} label={isDefault ? 'Default project' : 'Make default'} active={isDefault}
        onClick={() => { setDefaultProject(isDefault ? '' : id) }}
        disabled={project.status === 'archived' || defaultPending}
        disabledReason={project.status === 'archived' ? 'Restore this project to make it your default' : undefined}
        title={isDefault
          ? 'Your default project: new tasks and loops start here, on every device. Click to clear it.'
          : 'Make this your default project: new tasks and loops start here, on every device.'} />
      <HeaderControl icon={MessageSquare} label="Chat" onClick={launchChat} />
      {/* Export this project as a manifest ZIP — the brief, context ledgers, templates, artifact
          metadata and run digests, each sha256'd. Credentials never travel; the archive names the
          ones the far side must re-enter. A download link rather than a fetch, so the browser's
          own download machinery handles a multi-megabyte archive instead of buffering it in JS,
          and rather than pointing the tab at it, so a refusal never replaces the app. */}
      <HeaderControl icon={Download} label="Export" priority="low"
        hint="Download this project as a portable archive (no credentials)"
        onClick={() => downloadFrom(api.projectExportUrl(id))} />
      {/* The project's other page, a secondary action that sheds to the `…` when the row is tight. */}
      <HeaderControl icon={ListChecks} label="Tasks" title={`${project.name}'s tasks`} onClick={() => chooseView('tasks')} />
    </>
  )
  const newWork = (
    <HeaderMenu icon={Plus} label="New" variant="primary" priority="primary" title="Launch new work scoped under this project"
      items={NEW_KINDS.map((k) => ({ label: k.label, icon: k.icon, hint: k.hint, onSelect: () => launchKind(k.kind) }))} />
  )
  // The side panel: a task list's tasks, or a directory tree. Keyed so switching
  // targets remounts (resets scroll + lazy fetch). fillHeight — it docks below the
  // hub's TopBar, like every WorkbenchLayout panel.
  const panelNode = panel && (
    <SidePanel key={`${panel.kind}:${panel.kind === 'tasks' ? panel.list.id : panel.path}`} fillHeight
      storeKey="project-hub-panel-w" urlKey={{ key: 'panel', setQuery }} onClose={() => setPanel(null)}
      icon={panel.kind === 'tasks' ? <ListChecks size={18} className="text-primary" /> : <FolderTree size={18} className="text-primary" />}
      title={panel.kind === 'tasks' ? panel.list.name : panel.label}>
      {panel.kind === 'tasks'
        ? <TaskListPanel list={panel.list} onOpenTask={(tid) => navigate(`tasks?open=${encodeURIComponent(tid)}`)} />
        : <DirTreePanel path={panel.path} onOpenInFiles={() => navigate(`files?dir=${encodeURIComponent(panel.path)}`)} />}
    </SidePanel>
  )
  return (
    <Shell onBack={onBack} title={project.name} titleNode={titleNode} actions={headerActions} primary={newWork} scroll={false} panel={panelNode}>
      <div className="flex h-full min-h-0 flex-col">
        {/* ── slim band: brief + workspace/context directory bar ── */}
        <div className="shrink-0 border-b border-outline-variant/30 px-l py-2.5 flex flex-col gap-2">
          {err && (
            <InlineError onDismiss={() => setErr(null)}>{err}</InlineError>
          )}
          {/* project brief — the goal/scope shared with every agent; editable inline. */}
          <BriefRow brief={project.brief ?? ''} onSave={(b) => patch({ brief: b })} />
          {/* ── workspace + context directory: a slim horizontal bar (moved out of the
              grid so Work + Tasks own the screen). Clicking the path opens the dir tree
              in the side panel; an arrow jumps to the full Files page. ── */}
          <div className="flex flex-wrap items-center gap-2 text-[0.75rem]">
            {/* A missing folder offers no peek or "Open in Files": both would open a folder that
                is not there. Change and Unbind stay — they are the way out. */}
            <FolderChip label="Workspace" icon={project.workspace_dir && !wsMissing ? FolderOpen : Folder}
              path={project.workspace_dir}
              onPeek={project.workspace_dir && !wsMissing ? () => setPanel({ kind: 'dir', label: 'Workspace', path: project.workspace_dir! }) : undefined}
              onBrowse={project.workspace_dir && !wsMissing ? () => navigate(`files?dir=${encodeURIComponent(project.workspace_dir!)}`) : undefined}
              onAction={() => setPickWs(true)} actionLabel={project.workspace_dir ? 'Change' : 'Bind'}
              onClear={project.workspace_dir ? () => patch({ workspace_dir: '' }) : undefined}
              emptyText="No workspace bound" />
            <FolderChip label="Context" icon={FolderOpen} path={project.context_dir}
              onPeek={project.context_dir ? () => setPanel({ kind: 'dir', label: 'Context', path: project.context_dir! }) : undefined}
              onBrowse={project.context_dir ? () => navigate(`files?dir=${encodeURIComponent(project.context_dir!)}`) : undefined}
              emptyText="—" title="System-managed — shared context across this project's loops + chats" />
            {/* Refresh the marker-fenced context block into the bound
                workspace's adapter files. Only when adapters are enabled AND a workspace
                is bound (writing into a user's project dir is consent-gated). */}
            {adaptersEnabled && project.workspace_dir && (
              <Button variant="ghost" size="xs" onClick={regenerateContext} loading={regenerating}
                title="Rewrite the PClaw context block into this workspace's CLAUDE.md / AGENTS.md / .cursorrules">
                <RefreshCw size={12} /> Refresh context files
              </Button>
            )}
          </div>
          {wsMissing && project.workspace_dir && (
            <p role="status" data-type="body-s" className="flex flex-wrap items-center gap-2 text-on-surface-var">
              <StatusPill tone="warn">Folder missing</StatusPill>
              <span className="min-w-0">
                The workspace folder <span className="font-mono">{folderName(project.workspace_dir)}</span> no longer
                exists at <span className="break-all font-mono">{project.workspace_dir}</span>. Choose Change to bind a
                folder that exists, or restore it at that path.
              </span>
            </p>
          )}
        </div>

        {/* ── 2-region grid (Work + Tasks fill remaining height; files moved to the bar
            above so the live work widgets get the screen) ── */}
        {/* 🔑 ONE COLUMN BELOW `md`, BECAUSE THE 280px FLOOR STARVED THE PRIMARY PANE. The columns were
            an unconditional inline `minmax(0, 2fr) minmax(280px, 1fr)` with no breakpoint anywhere in
            this file. The right column's 280px floor is reached at a 834px viewport and from there the
            LEFT column absorbs every remaining pixel of shrink — measured on the seeded fixture:

              viewport   container   Work (2fr)   Tasks (1fr)
                1440       1244         829          414
                 834        638         357          280   ← floor reached
                 390        390         109          280
                 320        320          39          280   ← 320 − 280 − 1px gap

            So at 320px the PRIMARY pane is 39px and the secondary is 7× wider — the proportions the
            `2fr / 1fr` ratio exists to express, inverted. axe caught the consequence rather than the
            cause: at 39px the Work pane's content overflows horizontally (`scrollWidth 56 / clientWidth
            39`) and, having no focusable children and no `tabindex`, it becomes a keyboard-inaccessible
            scroll region (`scrollable-region-focusable`, serious).

            WCAG SC 1.4.10 (Reflow) requires this content at 320px, so the fix is to stop asking two
            columns to share a width that cannot hold them. `md` (768px) is the threshold because at that
            viewport the container is ~572px, which still leaves the primary pane ~291px — above the 280px
            the secondary demands. Below it, Work stacks above Tasks: the `2fr` column is the primary one,
            so it keeps reading order. */}
        <div className="min-h-0 flex-1 grid grid-cols-1 gap-px overflow-hidden bg-outline-variant/20 md:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
          {/* WORK — the state-grouped board: runs + legacy
              loops + tasks in one board, needs-input pinned first (server order). Each
              source is per-section isolated — a failing source degrades ONE section
              (inline degraded note) rather than blanking the board. */}
          <HubColumn title={`Work · ${workCount}`}>
            <WorkBoardColumn work={work} loading={workLoading} onResume={resumeWork} onOpen={openWork} onStop={stopWork} />
          </HubColumn>

          {/* CHATS over TASKS, sharing the secondary column: the project's chats, goal loops and
              code sessions, then its task lists. */}
          <div className="min-h-0 flex flex-col gap-px bg-outline-variant/20">
            <HubColumn title={`Chats · ${threadCount}`} className="max-h-[50%] shrink-0">
              <ProjectThreads projectId={id} read={threadsRead} onOpen={navigate} />
            </HubColumn>

            {/* TASKS — a plain list of task lists; clicking one opens its tasks in the
                side panel (no inline expansion). */}
            <HubColumn title={`Tasks · ${lists?.length ?? 0} list${(lists?.length ?? 0) === 1 ? '' : 's'}`} className="flex-1">
              {(lists ?? []).length === 0 ? (
                <p className="text-on-surface-low text-[0.8125rem]">No task lists yet — Goal Loop and Code attach their work here when scoped to this project.</p>
              ) : (
                <div className="flex flex-col gap-1">
                  {(lists as TaskListItem[]).map((tl) => (
                    <TaskListRow key={tl.id} list={tl} active={panel?.kind === 'tasks' && panel.list.id === tl.id}
                      onOpen={() => setPanel({ kind: 'tasks', list: tl })} />
                  ))}
                </div>
              )}
            </HubColumn>
          </div>
        </div>
      </div>

      {pickWs && (
        <WorkspacePicker mode="brownfield" allowCreate onClose={() => setPickWs(false)}
          onPick={(dir) => { setPickWs(false); patch({ workspace_dir: dir }) }} />
      )}
    </Shell>
  )
}

/** One scrollable region of the project hub grid. */
function HubColumn({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cx('min-h-0 flex flex-col bg-surface', className)}>
      <div className="shrink-0 px-l pt-3 pb-1.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide">{title}</div>
      <div className="min-h-0 flex-1 overflow-y-auto px-l pb-l">{children}</div>
    </div>
  )
}

/** A sub-group label inside the Work column (one per board state group). */
function WorkGroupLabel({ text, count, dot }: { text: string; count: number; dot?: string }) {
  return (
    <div className="flex items-center gap-1.5 text-[0.75rem] uppercase tracking-wide">
      {dot && <span className="size-1.5 rounded-full" style={{ background: dot }} />}
      <span className="text-on-surface-low">{text}</span>
      <span className="text-on-surface-low">· {count}</span>
    </div>
  )
}

/** The board states a loop row is in while it can be stopped (`STOPPABLE_STATUSES` through the
 *  server's `_LOOP_STATE`: at work or still planning, paused, or waiting on you). `queued` is a
 *  loop not launched yet, and `review` a plan waiting for its review: neither has anything running. */
const STOPPABLE_WORK: ReadonlySet<WorkState> = new Set(['working', 'suspended', 'needs_input'])

/** The state-grouped Work board body. Exported so the
 *  board's contract is unit-tested directly: groups render in server order (needs-input
 *  pinned first), a failed source shows an inline degraded note WITHOUT blanking the
 *  board (the FE half of per-section isolation), a suspended row offers Resume, and a
 *  collapsed row starts collapsed. */
export function WorkBoardColumn({ work, loading, onResume, onOpen, onStop }: {
  work: WorkBoard | undefined; loading: boolean; onResume: (row: WorkRow) => void
  onOpen: (row: WorkRow) => void; onStop: (row: WorkRow) => void
}) {
  if (loading && !work) return <ListSkeleton rows={4} />
  const board = work?.board ?? []
  const sections = work?.sections ?? []
  const degraded = sections.filter((s) => s.status !== 'ok')
  const allOk = sections.every((s) => s.status === 'ok')
  return (
    <div className="flex flex-col gap-3">
      {/* per-section status: loading → skeleton, error → inline degraded note. One broken
          source shows here without taking the rest of the board down. */}
      {degraded.map((s) => (
        s.status === 'loading'
          ? <ListSkeleton key={s.name} rows={2} />
          : <div key={s.name} data-testid={`work-section-error-${s.name}`}
              className="flex items-start gap-2 rounded-md bg-surface-high/60 px-2.5 py-2 text-[0.8125rem] text-on-surface-low">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" style={{ color: 'var(--color-warn)' }} />
              <span className="min-w-0">Couldn't load <span className="text-on-surface-var">{s.name}</span> — the rest of the board is still shown.</span>
            </div>
      ))}
      {board.length === 0 && allOk ? (
        <p className="text-on-surface-low text-[0.8125rem]">No work here yet — use <span className="text-on-surface-var">New</span> above to launch a loop, or start a chat.</p>
      ) : (
        board.map((group) => (
          <div key={group.state} data-testid={`work-group-${group.state}`} className="flex flex-col gap-1.5">
            <WorkGroupLabel text={WORK_STATE_LABEL[group.state]} count={group.count}
              dot={group.state === 'needs_input' ? NEEDS_YOU_DOT : undefined} />
            {group.rows.map((row) => (
              <WorkRowCard key={`${row.origin}:${row.run_id}`} row={row}
                onResume={() => onResume(row)} onOpen={() => onOpen(row)} onStop={() => onStop(row)} />
            ))}
          </div>
        ))
      )}
    </div>
  )
}

/** One row on the Work board — a run, a legacy loop, or a task, rendered uniformly.
 *  Collapsed rows (subagent-tool noise) start collapsed behind a disclosure; a claimed
 *  row shows who holds it; a suspended row offers Resume (row.resumable). */
function WorkRowCard({ row, onResume, onOpen, onStop }: {
  row: WorkRow; onResume: () => void; onOpen: () => void; onStop: () => void
}) {
  const [open, setOpen] = useState(!row.collapsed)
  if (row.collapsed && !open) {
    // Raw: a full-width Work-board row at its sibling rows' px-2.5 inset; no primitive draws a list row.
    return (
      <button type="button" onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 rounded-md px-2.5 py-1 text-left text-[0.75rem] text-on-surface-low hover:bg-surface-high">
        <ChevronRight size={12} className="shrink-0" />
        <span className="min-w-0 truncate" title={previewLine(row.title)}><MarkdownLine text={row.title} /></span>
      </button>
    )
  }
  // A Closed row carries its ending: the owning registry's glyph, named, and the same word
  // beside it in visible text — so "Cancelled" is read, not inferred from a colour.
  const outcome = row.outcome ? WORK_OUTCOME_LOOK[row.outcome] : null
  const OutcomeIcon = outcome?.icon
  return (
    <div data-outcome={row.outcome || undefined}
      className="group flex items-center gap-2 rounded-md bg-surface-high/60 px-2.5 py-1.5 text-left text-[0.8125rem] text-on-surface-var">
      {outcome && OutcomeIcon
        ? <OutcomeIcon size={13} className="shrink-0" style={{ color: outcome.tone }} role="img" aria-label={outcome.label} />
        : <CircleDot size={13} className="shrink-0 text-primary" />}
      {/* Raw: the row's truncating title is its open target in row ink; TextLink would re-ink it coral. */}
      <button type="button" onClick={onOpen} title={`Open ${previewLine(row.title)}`}
        className="min-w-0 flex-1 truncate text-left hover:text-on-surface hover:underline"><MarkdownLine text={row.title} /></button>
      {outcome && (
        <span aria-hidden className="shrink-0 text-[0.75rem]" style={{ color: outcome.tone }}>{outcome.label}</span>
      )}
      {row.claim && (
        <span className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-[0.75rem] text-on-surface-low" title={`Claimed by ${row.claim.holder}`}>
          {row.claim.holder}
        </span>
      )}
      {row.resumable && (
        <Button variant="ghost" size="xs" onClick={onResume} title="Resume this suspended work">
          <RefreshCw size={12} /> Resume
        </Button>
      )}
      {row.source === 'loop' && STOPPABLE_WORK.has(row.state) && (
        <Button variant="ghost" size="xs" onClick={onStop} title="Stop this loop">
          <Square size={12} /> Stop
        </Button>
      )}
    </div>
  )
}

/** A compact horizontal folder chip (workspace / context) for the hub's directory bar:
 *  label · path (click → peek dir tree in the side panel) · arrow → full Files page ·
 *  optional Bind/Change + Unbind actions. */
/** A path's last segment — the name a person knows the folder by. */
function folderName(path: string): string {
  return path.replace(/\/+$/, '').split('/').pop() || path
}

function FolderChip({ label, icon: Icon, path, emptyText, title, onPeek, onBrowse, onAction, actionLabel, onClear }: {
  label: string; icon: LucideIcon; path?: string; emptyText: string; title?: string
  onPeek?: () => void; onBrowse?: () => void; onAction?: () => void; actionLabel?: string; onClear?: () => void
}) {
  return (
    <div className="inline-flex min-w-0 items-center gap-1.5 rounded-md bg-surface-high/50 px-2 py-1" title={title}>
      <Icon size={12} className="shrink-0 text-on-surface-low" />
      <span className="shrink-0 text-on-surface-low uppercase tracking-wide text-[0.75rem]">{label}</span>
      {path ? (
        onPeek
          // Raw: a truncating mono path chip; no primitive draws a code-chip button.
          ? <button type="button" onClick={onPeek} title={`${path} — view contents`}
              className="min-w-0 max-w-[22rem] truncate rounded bg-surface-high px-1.5 py-0.5 font-mono text-on-surface-var hover:text-primary">{path}</button>
          : <code className="min-w-0 max-w-[22rem] truncate rounded bg-surface-high px-1.5 py-0.5 font-mono text-on-surface-var" title={path}>{path}</code>
      ) : (
        <span className="text-on-surface-low italic">{emptyText}</span>
      )}
      {path && onBrowse && (
        <IconButton icon={FolderOpen} label={`Open ${label} in Files`} title="Open in Files" size={24} iconSize={12} onClick={onBrowse} className="shrink-0 -m-0.5" />
      )}
      {onAction && actionLabel && (
        <Button variant="ghost" size="xs" onClick={onAction} className="shrink-0">{actionLabel}</Button>
      )}
      {onClear && <IconButton icon={X} label={`Unbind ${label}`} title="Unbind" size={20} iconSize={12} onClick={onClear} className="shrink-0" />}
    </div>
  )
}

/** A plain (non-expandable) task-list row in the hub's Tasks column. Clicking opens
 *  the list's tasks in the side panel. Shows the list's task count when known. */
export function TaskListRow({ list, active, onOpen }: { list: TaskListItem; active: boolean; onOpen: () => void }) {
  const count = list.task_count
  // Raw: a full-row clickable list item with its selected ring; no primitive draws a list row.
  return (
    <button type="button" onClick={onOpen}
      className={`group flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[0.8125rem] transition-colors ${active ? 'bg-surface-high text-on-surface ring-1 ring-primary' : 'bg-surface-high/60 text-on-surface-var hover:bg-surface-high hover:text-on-surface'}`}>
      <ListChecks size={13} className="shrink-0 text-on-surface-low" />
      <span className="min-w-0 flex-1 truncate">{list.name}</span>
      {typeof count === 'number' && <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{count}</span>}
      <ChevronRight size={13} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100" />
    </button>
  )
}

/** Side-panel body: the tasks under a list, grouped nothing-fancy, each opening the
 *  full task in the Tasks page. Lazy-fetched (panel only mounts when opened). */
export function TaskListPanel({ list, onOpenTask }: { list: TaskListItem; onOpenTask: (taskId: string) => void }) {
  const { data: tasks, loading } = useQuery<TaskItem[]>(`tasklist:tasks:${list.id}`,
    () => api.allTasks({ task_list: list.id }).then((c) => c.tasks))
  if (loading && !tasks) return <div className="flex justify-center py-l"><Loader2 size={16} className="animate-spin text-on-surface-low" /></div>
  if (!tasks?.length) return <p className="text-on-surface-low text-[0.8125rem]">No tasks in this list yet.</p>
  return (
    <ul className="flex flex-col gap-0.5">
      {tasks.map((t) => (
        <li key={t.id}>
          {/* Raw: a full-row clickable list item; no primitive draws a list row. */}
          <button type="button" onClick={() => onOpenTask(t.id)}
            className="group flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-[0.8125rem] hover:bg-surface-high">
            {/* 🔑 THE STATUS COMES FROM `taskMeta.statusMeta`, WHICH OWNS IT — icon, label and tone
                together, and 13 files already read it. This card used to hand-roll a four-branch
                ternary, and it disagreed with the canonical map on THREE of five statuses:

                  in_progress   CircleDot `text-primary`   → canonical is `--color-info`. Coral is the
                                primary/active colour, so this also spent it categorically.
                  blocked       AlertTriangle              → canonical icon is `CircleSlash`.
                  cancelled     NOT HANDLED — it fell to the `else` and rendered as an open circle, so
                                a cancelled task looked NOT STARTED. `demo-home` has one, so this was
                                reachable, not theoretical.

                And none of the four was named, so the status was carried by an unnamed 14px glyph:
                `role="img"` + `aria-label` is the settled form (six sites; see
                `tools/approvalShieldNamed.test.ts`). The label is `statusMeta`'s own, so "Not started"
                and "Completed" read the same here as on `#/tasks`.

                🪤 The strike-through follows `TERMINAL`, not `=== 'done'`. `TERMINAL` is {done,
                cancelled} and the sibling list uses it for grouping and sorting; keying on `done` alone
                left a cancelled task's TEXT looking active while its icon said otherwise. */}
            {(() => {
              const sm = statusMeta(t.status)
              const StatusIcon = sm.icon
              return <StatusIcon size={14} className="mt-0.5 shrink-0" style={{ color: sm.tone }} role="img" aria-label={sm.label} />
            })()}
            <span className={`min-w-0 flex-1 ${TERMINAL.has(t.status) ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}><MarkdownLine text={t.title} /></span>
            <ChevronRight size={14} className="mt-0.5 shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100" />
          </button>
        </li>
      ))}
    </ul>
  )
}

/** Side-panel body: a one-level directory listing (folders first, then files) for a
 *  project's workspace/context dir, with a jump to the full Files page. Folders drill
 *  in place (in-panel breadcrumb); files open in the Files page. */
/** Human-readable reason a directory couldn't be listed, by HTTP status:
 *  404 → the bound dir is gone (e.g. workspace deleted/moved on disk);
 *  400/403 → the dir exists but sits outside the app's browsable area. */
function dirErrorMessage(error: unknown): string {
  const status = error instanceof ApiError ? error.status : 0
  if (status === 404) return "This folder no longer exists on disk."
  if (status === 400 || status === 403) return "This folder is outside the area PersonalClaw can browse."
  return "Couldn't read this directory."
}

function DirTreePanel({ path, onOpenInFiles }: { path: string; onOpenInFiles: () => void }) {
  const [cur, setCur] = useState(path)
  const { data, loading, error } = useQuery(`dirtree:${cur}`, () => api.fileList(cur))
  // Breadcrumb segments relative to the panel's root `path` (don't walk above it).
  const rel = cur.startsWith(path) ? cur.slice(path.length).replace(/^\//, '') : ''
  const crumbs = rel ? rel.split('/') : []
  const entries = data?.entries ?? []
  const dirs = entries.filter((e) => e.is_dir)
  const files = entries.filter((e) => !e.is_dir)
  return (
    <div className="flex flex-col gap-2">
      <Button size="sm" variant="ghost" onClick={onOpenInFiles}><FolderOpen size={14} /> Open in Files</Button>
      {/* breadcrumb — root + drilled-into subfolders; click to pop back. Raw: crumb segments with a current-crumb ink, which no primitive draws. */}
      <div className="flex flex-wrap items-center gap-0.5 text-[0.75rem] text-on-surface-low">
        <button type="button" onClick={() => setCur(path)} className={`truncate rounded px-1 py-0.5 hover:bg-surface-high ${cur === path ? 'text-on-surface' : 'hover:text-on-surface'}`}>{path.split('/').pop() || path}</button>
        {crumbs.map((seg, i) => (
          <span key={i} className="inline-flex items-center gap-0.5">
            <ChevronRight size={11} className="shrink-0" />
            <button type="button" onClick={() => setCur(path + '/' + crumbs.slice(0, i + 1).join('/'))}
              className={`truncate rounded px-1 py-0.5 hover:bg-surface-high ${i === crumbs.length - 1 ? 'text-on-surface' : 'hover:text-on-surface'}`}>{seg}</button>
          </span>
        ))}
      </div>
      {loading && !data ? <div className="flex justify-center py-l"><Loader2 size={16} className="animate-spin text-on-surface-low" /></div>
        : error ? <p className="text-on-surface-low text-[0.8125rem]">{dirErrorMessage(error)}</p>
        : entries.length === 0 ? <p className="text-on-surface-low text-[0.8125rem]">This directory is empty.</p>
        : (
          <ul className="flex flex-col gap-0.5">
            {dirs.map((e) => <DirEntryRow key={e.path} entry={e} onClick={() => setCur(e.path)} />)}
            {files.map((e) => <DirEntryRow key={e.path} entry={e} onClick={onOpenInFiles} />)}
          </ul>
        )}
    </div>
  )
}

/** One row in the DirTreePanel — a folder (drills in) or file (opens in Files). */
function DirEntryRow({ entry, onClick }: { entry: FsEntry; onClick: () => void }) {
  return (
    <li>
      {/* Raw: a full-row clickable list item; no primitive draws a list row. */}
      <button type="button" onClick={onClick}
        className="group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[0.8125rem] text-on-surface-var hover:bg-surface-high hover:text-on-surface">
        {entry.is_dir ? <Folder size={14} className="shrink-0 text-primary" /> : <FileIcon size={14} className="shrink-0 text-on-surface-low" />}
        <span className="min-w-0 flex-1 truncate">{entry.name}</span>
        {entry.is_dir && <ChevronRight size={14} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100" />}
      </button>
    </li>
  )
}

/** Inline-editable project brief row (collapsed preview → textarea on click). */
function BriefRow({ brief, onSave }: { brief: string; onSave: (b: string) => void }) {
  const [editing, setEditing] = useState(false)
  // Follows the saved brief when it moves, never on mount (`useSyncedDraft`).
  const [draft, setDraft] = useSyncedDraft(brief)
  if (editing) {
    return (
      <div className="flex flex-col gap-1.5">
        <TextArea autoFocus value={draft} onChange={setDraft} rows={3} size="sm" surface="high"
          onKeyDown={(e) => { if (e.key === 'Escape') { setDraft(brief); setEditing(false) } }}
          placeholder="Project brief — the goal, scope, and background. Shared as context with every agent working on this project's sessions and loops." />
        <div className="flex items-center gap-1.5">
          <Button size="sm" onClick={() => { onSave(draft.trim()); setEditing(false) }}><Check size={13} /> Save brief</Button>
          <Button variant="ghost" size="xs" onClick={() => { setDraft(brief); setEditing(false) }}>Cancel</Button>
        </div>
      </div>
    )
  }
  // Raw: a click-to-edit preview of a two-line brief; no primitive draws a multi-line preview row.
  return (
    <button type="button" onClick={() => setEditing(true)} title="Edit project brief"
      className="group flex items-start gap-2 rounded-md px-1 py-0.5 text-left hover:bg-surface-high/50">
      <FileText size={13} className="mt-0.5 shrink-0 text-on-surface-low" />
      <span className={`min-w-0 flex-1 text-[0.8125rem] ${brief ? 'text-on-surface-var line-clamp-2' : 'text-on-surface-low italic'}`}>
        {brief || 'Add a project brief — shared as context with every agent working here.'}
      </span>
      <Pencil size={12} className="mt-0.5 shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100" />
    </button>
  )
}

function Shell({ title, titleNode, onBack, actions, primary, scroll = true, panel, children }: {
  title: string; titleNode?: React.ReactNode; onBack: () => void
  /** The page's header controls, in the cluster before All projects. */
  actions?: React.ReactNode
  /** The page's primary action, the cluster's last control, after All projects. */
  primary?: React.ReactNode
  scroll?: boolean
  /** Already-gated right-docked SidePanel (fillHeight) — docks below the TopBar and
   *  pushes only the content (never the header), like WorkbenchLayout. */
  panel?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        keepCornerPadding
        // `#/projects` renders h1 "Projects", and `#/projects/<id>` rendered NONE. The
        // project is the destination at that URL, so it carries the h1. `titleNode` (the rename editor)
        // keeps its own input — an editable title is not a heading.
        left={titleNode ?? <div className="flex items-center gap-2"><FolderKanban size={18} className="text-primary" /><PageTitle>{title}</PageTitle></div>}
        right={<HeaderActions>{actions}<HeaderControl icon={FolderKanban} label="All projects" onClick={onBack} />{primary}</HeaderActions>} />
      <div className="flex min-h-0 flex-1">
        {/* Detail hub fills height (scroll=false); list/loading states scroll. */}
        <div className={`min-w-0 flex-1 ${scroll ? 'overflow-y-auto' : 'overflow-hidden'}`}>{children}</div>
        <AnimatePresence>{panel}</AnimatePresence>
      </div>
    </div>
  )
}
