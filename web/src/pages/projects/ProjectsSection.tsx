import { useEffect, useState } from 'react'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { AnimatePresence, motion } from 'framer-motion'
import { ContextMenu, type ContextMenuItem } from '../../ui/motion'
import { spring } from '../../design/motion'
import { FolderKanban, Plus, Loader2, Trash2, FolderOpen, Folder, FolderTree, File as FileIcon, X, ChevronRight, ChevronDown, Pencil, Check, ListChecks, Lock, FileBox, Star, MessageSquare, Repeat, Target, Code2, Telescope, Palette, FileText, CheckCircle2, CircleDot, Circle, AlertTriangle, Square, type LucideIcon } from 'lucide-react'
import { Popover, MenuRow } from '../../ui/Popover'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { IconButton } from '../../ui/IconButton'
import { ListControls } from '../../ui/ListControls'
import { ListSkeleton, EmptyState } from '../../ui/ListScaffold'
import { confirm } from '../../ui/dialog'
import { Modal } from '../../ui/Modal'
import { SidePanel } from '../../ui/SidePanel'
import { Button } from '../../ui/Button'
import { WorkspacePicker } from '../code/WorkspacePicker'
import { SdlcProgressCard } from '../chat/SdlcProgressCard'
import { api, ApiError, type ProjectItem, type TaskListItem, type LoopKind, type TaskItem, type FsEntry } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { getActiveProject, setActiveProject } from '../../lib/activeProject'

/** Projects navigation — the first-class work unit tying Goal Loops, Code projects,
 *  and Tasks together under one context-continuous container.
 *    #/projects        → the project list
 *    #/projects/<id>   → that project's detail (workspace, context, task lists)
 */
export function ProjectsSection({ sub, navigate, query, setQuery }: RouteProps) {
  const seg = (sub || '').split('/')[0]
  if (seg) return <ProjectDetailPage id={seg} onBack={() => navigate('projects')} navigate={navigate} query={query} setQuery={setQuery} />
  return <ProjectListPage onOpen={(id) => navigate(`projects/${id}`)} query={query} setQuery={setQuery} />
}

function ProjectListPage({ onOpen, query, setQuery }: { onOpen: (id: string) => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data: projects, loading, refresh } = useCachedData('projects:list', () => api.projects(), { persist: true })
  // List search is URL-backed (?q, replace) — shareable + refresh-stable, no
  // per-keystroke history. (Was local useState.)
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  // Project PEEK — clicking a row opens a summary in the standard right side
  // panel first (?peek=<id>, push — Back closes); the panel's expand control is
  // the road into the dedicated project page (#/projects/<id>).
  const [peekId, setPeekId] = useQueryParam(query, setQuery, 'peek', '')
  const peekProject = peekId ? (projects ?? []).find((p) => p.id === peekId) ?? null : null
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // The active project, so its row carries a star; kept in sync with the pointer.
  const [activeId, setActiveId] = useState(getActiveProject)
  useEffect(() => {
    const onChange = () => setActiveId(getActiveProject())
    window.addEventListener('ne:active-project', onChange)
    return () => window.removeEventListener('ne:active-project', onChange)
  }, [])

  async function create(form: { name: string; brief: string; workspaceDir: string; setActive: boolean }) {
    const name = form.name.trim()
    if (!name || busy) return
    setBusy(true); setErr(null)
    try {
      // The user typed this name → lock it (same as a rename), so the detail page
      // doesn't mislabel it "Auto-named" and the LLM won't auto-rename it.
      const p = await api.createProject({
        name, brief: form.brief.trim() || undefined, name_locked: true,
        workspace_dir: form.workspaceDir.trim() || undefined,
      })
      if (form.setActive) setActiveProject(p.id)
      setCreating(false)
      invalidateCache('projects:list'); refresh()
      onOpen(p.id)
    } catch (e) { setErr((e as Error).message || 'Could not create the project') }
    finally { setBusy(false) }
  }
  async function del(proj: ProjectItem) {
    setErr(null)
    if (!(await confirm({
      title: `Delete project "${proj.name}"?`,
      body: 'Its context directory will be removed and its task lists detached. Workspace files on disk are left untouched.',
      danger: true, confirmLabel: 'Delete',
    }))) return
    const run = async (force: boolean) => {
      await api.deleteProject(proj.id, force)
      // Keep the active-project pointer honest — a deleted project must not linger as
      // the working context (the create pickers would then send a dead id + show a
      // confusing bare label).
      if (getActiveProject() === proj.id) setActiveProject('')
    }
    try {
      await run(false)
    } catch (e) {
      // 409 = the project still has bound Goal Loops / Code projects. Re-confirm and
      // delete with force (the backend then tears down its worktrees/context too).
      if ((e as { status?: number }).status === 409) {
        const force = await confirm({
          title: 'Project still has active work',
          body: `"${proj.name}" still has work scoped under it. Deleting it now STOPS and REMOVES any bound loops (Goal, Code, Design, General) — their workers are halted, parallel git worktrees + branches cleaned up, and the loops deleted (not just unlinked). Project-bound chats are kept but UNBOUND (detached from this project, not deleted). This can't be undone.`,
          danger: true, confirmLabel: 'Delete anyway',
        })
        if (!force) return
        try { await run(true) }
        catch (e2) { setErr(`Couldn't delete that project: ${(e2 as Error).message || 'unknown error'}`) }
      } else {
        setErr(`Couldn't delete that project: ${(e as Error).message || 'unknown error'}`)
      }
    }
    invalidateCache('projects:list'); refresh()
  }

  const needle = q.trim().toLowerCase()
  const shown = (projects ?? []).filter((p) => !needle || p.name.toLowerCase().includes(needle))

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        keepCornerPadding
        left={<div className="flex items-center gap-2"><FolderKanban size={18} className="text-primary" /><span data-type="title-l" className="text-on-surface">Projects</span></div>}
        right={<HeaderActions><HeaderControl icon={Plus} label="New project" onClick={() => setCreating(true)} variant="primary" priority="primary" /></HeaderActions>} />

      {!!projects?.length && (
        <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search projects', label: 'Search projects' }} />
      )}

      {err && (
        <div role="alert" className="mx-l mt-2 flex items-center gap-2 rounded-lg px-3 py-2 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 10%, transparent)', color: 'var(--color-danger)' }}>
          <span className="min-w-0 flex-1">{err}</span>
          <button type="button" onClick={() => setErr(null)} aria-label="Dismiss" className="shrink-0 hover:opacity-70"><X size={14} /></button>
        </div>
      )}

      {creating && (
        <NewProjectModal busy={busy} onClose={() => setCreating(false)} onCreate={create} />
      )}

      {/* body row: list column + (optional) the right-docked project peek panel,
          a flex sibling that pushes the list narrower (standard SidePanel dock). */}
      <div className="flex min-h-0 flex-1">
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-l py-l">
        {loading && !projects ? <ListSkeleton rows={5} />
          : !shown.length ? (
            needle
              ? <p className="py-10 text-center text-on-surface-low text-[0.8125rem]">No projects match “{q.trim()}”.</p>
              : <EmptyState icon={FolderKanban} title="No projects yet"
                  hint="A project ties your loops (General, Goal, Code, Design), chats, and tasks into one context-continuous unit."
                  action={{ label: 'New project', onClick: () => setCreating(true), icon: Plus }} />
          ) : (
            <div className="flex flex-col gap-2">
              {shown.map((p, index) => {
                // Scoped right-click actions — reuse the SAME handlers the click /
                // hover-button paths call (open, and Delete for non-default projects).
                // Clicking the already-peeked row CLOSES the panel (toggle, not re-open).
                const togglePeek = () => setPeekId(peekId === p.id ? '' : p.id)
                const menuItems: ContextMenuItem[] = [
                  { icon: <FolderKanban size={15} />, label: peekId === p.id ? 'Close peek' : 'Peek', onSelect: togglePeek },
                  { icon: <FolderOpen size={15} />, label: 'Open full page', onSelect: () => onOpen(p.id) },
                  ...(!p.is_default ? [{ icon: <Trash2 size={15} />, label: 'Delete', onSelect: () => del(p), danger: true }] : []),
                ]
                return (
                <ContextMenu key={p.id} items={menuItems}>
                <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}
                  role="button" tabIndex={0} onClick={togglePeek}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); togglePeek() } }}
                  className="group flex cursor-pointer items-center gap-3 rounded-xl border border-outline-variant/50 bg-surface-container/60 px-4 py-3 text-left transition-colors hover:bg-surface-high">
                  <FolderKanban size={16} className="shrink-0 text-on-surface-low" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      {p.id === activeId && <Star size={12} className="shrink-0 text-primary" style={{ fill: 'var(--color-primary)' }} aria-label="Active project" />}
                      <span className="truncate text-on-surface text-[0.9375rem]">{p.name}</span>
                      {p.is_default && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-[0.65rem] text-on-surface-low">default</span>}
                      {p.status === 'archived' && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-[0.65rem] text-on-surface-low">archived</span>}
                    </div>
                    <div className="truncate text-on-surface-low text-[0.75rem]">
                      {p.workspace_dir ? p.workspace_dir.split('/').slice(-2).join('/') : 'no workspace'}
                      {typeof p.task_list_count === 'number' ? ` · ${p.task_list_count} list${p.task_list_count === 1 ? '' : 's'}` : ''}
                    </div>
                  </div>
                  {!p.is_default && (
                    <button type="button" onClick={(e) => { e.stopPropagation(); del(p) }} aria-label="Delete project"
                      className="shrink-0 rounded-md p-1.5 text-on-surface-low opacity-0 transition-opacity hover:bg-surface-highest hover:text-danger group-hover:opacity-100 focus-visible:opacity-100">
                      <Trash2 size={14} />
                    </button>
                  )}
                  <ChevronRight size={15} className="shrink-0 text-on-surface-low opacity-0 transition-opacity group-hover:opacity-100" />
                </motion.div>
                </ContextMenu>
                )
              })}
            </div>
          )}
      </div>
      {/* Project peek — the standard right side panel with a summary; expand
          navigates to the dedicated project page. */}
      <AnimatePresence>
        {peekId && (
          <SidePanel key={peekId} fillHeight storeKey="project-peek-w" urlKey={{ key: 'peek', setQuery }}
            icon={<FolderKanban size={18} className="text-primary" />}
            title={peekProject?.name || 'Project'}
            onExpand={() => onOpen(peekId)}
            onClose={() => setPeekId('')}>
            <ProjectPeekBody id={peekId} project={peekProject} onOpen={() => onOpen(peekId)} />
          </SidePanel>
        )}
      </AnimatePresence>
      </div>

    </div>
  )
}

/** Body of the project PEEK panel: brief, workspace, and the linked work
 *  (loops / code projects / chats / artifacts) — enough to decide whether to
 *  dive into the dedicated page. */
function ProjectPeekBody({ id, project, onOpen }: { id: string; project: ProjectItem | null; onOpen: () => void }) {
  const [linked, setLinked] = useState<{ loops: { id: string; name: string; status: string }[]; code: { id: string; name: string; status: string }[]; chats: { key: string; title: string; running: boolean }[]; artifacts: { slug: string; name: string; kind: string }[] } | null>(null)
  const [taskLists, setTaskLists] = useState<TaskListItem[]>([])
  useEffect(() => {
    let alive = true
    setLinked(null); setTaskLists([])
    api.projectLinked(id).then((d) => { if (alive) setLinked(d) }).catch(() => { if (alive) setLinked({ loops: [], code: [], chats: [], artifacts: [] }) })
    api.taskLists(id).then((d) => { if (alive) setTaskLists(d) }).catch(() => {})
    return () => { alive = false }
  }, [id])

  const statusTone = (s: string) => {
    if (s === 'running' || s === 'active') return 'var(--color-ok)'
    if (s === 'stopped' || s === 'failed') return 'var(--color-danger)'
    if (s === 'paused' || s === 'blocked') return 'var(--color-warn)'
    return 'var(--color-on-surface-low)'
  }

  const Section = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div>
      <div className="mb-1.5 text-on-surface-low text-[0.75rem]" style={{ fontVariationSettings: '"wght" 500' }}>{label}</div>
      {children}
    </div>
  )
  return (
    <div className="flex h-full min-h-0 flex-col gap-l">
      <div className="flex min-h-0 flex-1 flex-col gap-l overflow-y-auto">
        {project?.brief && (
          <Section label="Brief">
            <p className="whitespace-pre-wrap text-on-surface-var text-[0.875rem] leading-relaxed">{project.brief}</p>
          </Section>
        )}
        <Section label="Workspace">
          <p className="break-all font-mono text-on-surface-var text-[0.8125rem]">{project?.workspace_dir || 'No workspace bound'}</p>
        </Section>
        {project?.status && (
          <Section label="Status">
            <span className="inline-flex items-center gap-1.5 rounded-pill px-2 h-6 text-[0.75rem]"
              style={{ background: `color-mix(in srgb, ${statusTone(project.status)} 14%, transparent)`, color: statusTone(project.status) }}>
              <Circle size={6} style={{ fill: 'currentColor' }} /> {project.status}
            </span>
          </Section>
        )}
        {project?.created_at && (
          <Section label="Created">
            <p className="text-on-surface-var text-[0.8125rem]">{new Date(project.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</p>
          </Section>
        )}
        {taskLists.length > 0 && (
          <Section label={`Task lists · ${taskLists.length}`}>
            <div className="flex flex-col gap-1">
              {taskLists.slice(0, 6).map((tl) => (
                <div key={tl.id} className="flex items-center gap-2 rounded-md bg-surface-container px-m py-1.5">
                  <ListChecks size={13} className="shrink-0 text-on-surface-low" />
                  <span className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]">{tl.name}</span>
                </div>
              ))}
            </div>
          </Section>
        )}
        {!linked ? (
          <ListSkeleton rows={3} />
        ) : (
          <>
            {(linked.loops.length > 0 || linked.code.length > 0) && (
              <Section label={`Loops & Code · ${linked.loops.length + linked.code.length}`}>
                <div className="flex flex-col gap-1">
                  {[...linked.code, ...linked.loops].slice(0, 10).map((w) => (
                    <div key={w.id} className="flex items-center gap-2 rounded-md bg-surface-container px-m py-1.5">
                      <CircleDot size={13} className="shrink-0" style={{ color: statusTone(w.status) }} />
                      <span className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]">{w.name}</span>
                      <span className="shrink-0 text-[0.7rem]" style={{ color: statusTone(w.status) }}>{w.status}</span>
                    </div>
                  ))}
                </div>
              </Section>
            )}
            {linked.chats.length > 0 && (
              <Section label={`Chats · ${linked.chats.length}`}>
                <div className="flex flex-col gap-1">
                  {linked.chats.slice(0, 8).map((c) => (
                    <div key={c.key} className="flex items-center gap-2 rounded-md bg-surface-container px-m py-1.5">
                      <MessageSquare size={13} className="shrink-0 text-on-surface-low" />
                      <span className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]">{c.title || c.key}</span>
                      {c.running && <span className="shrink-0 text-primary text-[0.7rem]">running</span>}
                    </div>
                  ))}
                </div>
              </Section>
            )}
            {linked.artifacts.length > 0 && (
              <Section label={`Artifacts · ${linked.artifacts.length}`}>
                <div className="flex flex-col gap-1">
                  {linked.artifacts.slice(0, 8).map((a) => (
                    <div key={a.slug} className="flex items-center gap-2 rounded-md bg-surface-container px-m py-1.5">
                      <FileBox size={13} className="shrink-0 text-on-surface-low" />
                      <span className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]">{a.name}</span>
                      <span className="shrink-0 text-on-surface-low text-[0.7rem]">{a.kind}</span>
                    </div>
                  ))}
                </div>
              </Section>
            )}
          </>
        )}
      </div>
      <button type="button" onClick={onOpen}
        className="flex shrink-0 items-center justify-center gap-1.5 rounded-pill bg-primary px-4 h-9 text-on-primary text-[0.8125rem] transition-colors hover:bg-primary-emphasis"
        style={{ fontVariationSettings: '"wght" 470' }}>
        Open project <ChevronRight size={13} className="shrink-0" />
      </button>
    </div>
  )
}

/** New-project modal: name + brief + an optional workspace binding + a set-active
 *  toggle — so the whole project can be set up in one step (the old inline form only
 *  took name + brief, leaving the user to bind a workspace + set active afterwards). */
function NewProjectModal({ busy, onClose, onCreate }: {
  busy: boolean
  onClose: () => void
  onCreate: (form: { name: string; brief: string; workspaceDir: string; setActive: boolean }) => void
}) {
  const [name, setName] = useState('')
  const [brief, setBrief] = useState('')
  const [workspaceDir, setWorkspaceDir] = useState('')
  const [setActive, setSetActive] = useState(true)
  const [pickWs, setPickWs] = useState(false)
  const submit = () => { if (name.trim() && !busy) onCreate({ name, brief, workspaceDir, setActive }) }
  return (
    <Modal title="New project" icon={<FolderKanban size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-l">
        <Field label="Name">
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
            placeholder="Project name (or let the system name it later)…"
            className="w-full rounded-md bg-surface-high px-3 py-2 text-[0.9375rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 placeholder:text-on-surface-low" />
        </Field>
        <Field label="Brief" hint="The goal, scope, and background — shared as context with every agent working on this project's sessions and loops.">
          <textarea value={brief} onChange={(e) => setBrief(e.target.value)} rows={4}
            placeholder="What is this project for? What does success look like?"
            className="w-full resize-y rounded-md bg-surface-high px-3 py-2 text-[0.875rem] leading-relaxed text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 placeholder:text-on-surface-low" />
        </Field>
        <Field label="Workspace" hint="Optional — the directory loops + code work in. You can bind or change it later.">
          <div className="flex items-center gap-2">
            {workspaceDir ? (
              <code className="min-w-0 flex-1 truncate rounded-md bg-surface-high px-2.5 py-1.5 font-mono text-[0.8125rem] text-on-surface-var" title={workspaceDir}>{workspaceDir}</code>
            ) : (
              <span className="flex-1 text-on-surface-low/70 text-[0.8125rem] italic">No workspace bound</span>
            )}
            <Button size="sm" variant="secondary" onClick={() => setPickWs(true)}><FolderOpen size={14} /> {workspaceDir ? 'Change' : 'Bind'}</Button>
            {workspaceDir && <IconButton icon={X} label="Clear workspace" size={32} onClick={() => setWorkspaceDir('')} />}
          </div>
        </Field>
        <label className="flex items-center gap-2 text-[0.875rem] text-on-surface-var cursor-pointer">
          <input type="checkbox" checked={setActive} onChange={(e) => setSetActive(e.target.checked)} className="size-4 accent-primary" />
          Make this the active project (new work defaults here)
        </label>
        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={!name.trim() || busy}>
            {busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} Create project
          </Button>
        </div>
      </div>
      {pickWs && (
        <WorkspacePicker mode="brownfield" allowCreate onClose={() => setPickWs(false)}
          onPick={(dir) => { setPickWs(false); setWorkspaceDir(dir) }} />
      )}
    </Modal>
  )
}

/** A labelled form field for the New-project modal. */
function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 600' }}>{label}</span>
      {hint && <span className="text-on-surface-low text-[0.75rem] leading-snug">{hint}</span>}
      {children}
    </div>
  )
}

function ProjectDetailPage({ id, onBack, navigate, query, setQuery }: { id: string; onBack: () => void; navigate: (to: string) => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { data: project, loading, refresh } = useCachedData(`projects:detail:${id}`, () => api.project(id))
  const { data: lists } = useCachedData(`projects:lists:${id}`, () => api.taskLists(id))
  const { data: linked } = useCachedData(`projects:linked:${id}`, () => api.projectLinked(id))
  const [renaming, setRenaming] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const [pickWs, setPickWs] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // The right-docked SidePanel content: a task list's tasks, or a directory tree for
  // the workspace/context dir. One panel at a time; null = closed. URL-backed via
  // ?panel (push, so Back closes it): `tasks:<listId>` | `dir:workspace` | `dir:context`.
  // The panel object is resolved from that token against loaded lists / the project.
  const [panelTok, setPanelTok] = useQueryParam(query, setQuery, 'panel', '')
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
  // Whether this is the user's active project (the working context the create
  // pickers + manual task form default to). Tracked locally; synced via the event.
  const [active, setActive] = useState(() => getActiveProject() === id)
  useEffect(() => {
    const onChange = () => setActive(getActiveProject() === id)
    onChange()  // re-sync on mount + whenever `id` changes (soft nav between projects,
                // or active set from another surface before this shell mounted)
    window.addEventListener('ne:active-project', onChange)
    return () => window.removeEventListener('ne:active-project', onChange)
  }, [id])

  async function patch(body: Record<string, unknown>) {
    setErr(null)
    try { await api.updateProject(id, body); invalidateCache(`projects:detail:${id}`); invalidateCache('projects:list'); refresh() }
    catch (e) { setErr((e as Error).message || 'Could not update the project') }
  }

  if (loading && !project) {
    return <Shell onBack={onBack} title="Project"><div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div></Shell>
  }
  if (!project) {
    return <Shell onBack={onBack} title="Project"><div className="flex h-full flex-col items-center justify-center gap-3 text-center"><p className="text-on-surface text-[0.9375rem]">This project no longer exists.</p><Button onClick={onBack}><ListChecks size={15} /> Back to projects</Button></div></Shell>
  }

  // Launch scoped under THIS project: set it active (so the composer's project picker
  // seeds from it) + deep-link the unified Loop composer with the project + chosen KIND.
  const launchKind = (kind?: LoopKind) => {
    setActiveProject(id)
    navigate(`loop?project=${encodeURIComponent(id)}` + (kind ? `&kind=${kind}` : ''))
  }
  const launchChat = () => { setActiveProject(id); navigate(`chat?project=${encodeURIComponent(id)}`) }
  // Icons are the canonical per-kind glyphs (loopKind.ts) so the New menu reads the
  // same identity as the widgets, list, and cockpits. `general` deep-links with no kind.
  const NEW_KINDS: { kind?: LoopKind; label: string; hint: string; icon: LucideIcon }[] = [
    { kind: undefined, label: 'Loop', hint: 'A generic iterative loop', icon: Repeat },
    { kind: 'goal', label: 'Goal', hint: 'Verifiable / open-ended / monitor', icon: Target },
    { kind: 'code', label: 'Code', hint: 'SDLC work in a codebase', icon: Code2 },
    { kind: 'research', label: 'Research', hint: 'Deep web research → report', icon: Telescope },
    { kind: 'design', label: 'Design', hint: 'Design system + tokens', icon: Palette },
  ]
  const loopRefs = [
    ...((linked?.loops ?? []).map((l) => ({ kind: 'loop' as const, id: l.id, status: l.status }))),
    ...((linked?.code ?? []).map((c) => ({ kind: 'code' as const, id: c.id, status: c.status }))),
  ]
  // Split loops into ongoing vs completed (Gap 4 — item 1 asked for "ongoing AND
  // completed" widgets, not one flat list). Terminal statuses = done/finished work.
  const TERMINAL_LOOP = new Set(['complete', 'completed', 'failed', 'stopped', 'ended_early', 'archived'])
  const ongoingLoops = loopRefs.filter((r) => !TERMINAL_LOOP.has(r.status))
  const completedLoops = loopRefs.filter((r) => TERMINAL_LOOP.has(r.status))
  const chats = linked?.chats ?? []
  // Running chats lead (ongoing); idle ones group under completed-ish.
  const ongoingChats = chats.filter((c) => c.running)
  const idleChats = chats.filter((c) => !c.running)
  const artifacts = linked?.artifacts ?? []

  // The hub is a full-height SHELL. The header (Shell) owns identity + the Active/New/
  // Chat controls (no duplicated name in the body). The body is: a slim band (brief +
  // workspace/context directory bar) over a 2-region grid — Work (live loop/session
  // widgets) + Tasks — with task detail opening in a SidePanel.
  const titleNode = renaming ? (
    <div className="flex items-center gap-2">
      <input autoFocus value={nameDraft} onChange={(e) => setNameDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { patch({ name: nameDraft.trim(), name_locked: true }); setRenaming(false) } else if (e.key === 'Escape') setRenaming(false) }}
        className="min-w-0 rounded-md bg-surface-high px-2.5 py-1 text-on-surface text-[1.0625rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      <Button size="sm" onClick={() => { patch({ name: nameDraft.trim(), name_locked: true }); setRenaming(false) }} disabled={!nameDraft.trim()}><Check size={14} /></Button>
      <button type="button" onClick={() => setRenaming(false)} aria-label="Cancel" className="text-on-surface-low hover:text-on-surface"><X size={15} /></button>
    </div>
  ) : (
    <div className="flex items-center gap-2 min-w-0">
      <FolderKanban size={18} className="shrink-0 text-primary" />
      <span data-type="title-l" className="truncate text-on-surface">{project.name}</span>
      {project.name_locked && <Lock size={12} className="shrink-0 text-on-surface-low" aria-label="Name locked" />}
      {!project.is_default && (
        <button type="button" onClick={() => { setNameDraft(project.name); setRenaming(true) }} aria-label="Rename"
          className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface"><Pencil size={13} /></button>
      )}
    </div>
  )
  const headerActions = (
    <>
      <button type="button" onClick={() => { const next = active ? '' : id; setActiveProject(next); setActive(!active) }}
        title={active ? 'Active project — new work defaults here' : 'Make this the active project'}
        className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[0.78rem] ${active ? 'text-primary' : 'text-on-surface-low hover:bg-surface-high hover:text-on-surface'}`}>
        <Star size={14} style={active ? { fill: 'var(--color-primary)' } : undefined} /> {active ? 'Active' : 'Set active'}
      </button>
      <Popover align="right" width={220} placement="bottom"
        trigger={(open, toggle) => (
          <button type="button" onClick={toggle} aria-expanded={open} title="Launch new work scoped under this project"
            className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-[0.78rem] ${open ? 'bg-primary text-on-primary' : 'bg-primary/90 text-on-primary hover:bg-primary'}`}>
            <Plus size={14} /> New <ChevronDown size={11} />
          </button>
        )}>
        {(close) => (
          <div className="flex flex-col gap-0.5">
            {NEW_KINDS.map((k) => (
              <MenuRow key={k.label} icon={<k.icon size={16} />} label={k.label} hint={k.hint}
                onClick={() => { launchKind(k.kind); close() }} />
            ))}
          </div>
        )}
      </Popover>
      <HeaderControl icon={MessageSquare} label="Chat" onClick={launchChat} />
    </>
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
        ? <TaskListPanel list={panel.list} onOpenTask={(tid) => navigate(`tasks?task=${encodeURIComponent(tid)}`)} />
        : <DirTreePanel path={panel.path} onOpenInFiles={() => navigate(`files?dir=${encodeURIComponent(panel.path)}`)} />}
    </SidePanel>
  )
  return (
    <Shell onBack={onBack} title={project.name} titleNode={titleNode} actions={headerActions} scroll={false} panel={panelNode}>
      <div className="flex h-full min-h-0 flex-col">
        {/* ── slim band: brief + workspace/context directory bar ── */}
        <div className="shrink-0 border-b border-outline-variant/30 px-l py-2.5 flex flex-col gap-2">
          {err && (
            <div role="alert" className="flex items-center gap-2 rounded-lg px-3 py-2 text-[0.8125rem]"
              style={{ background: 'color-mix(in srgb, var(--color-danger) 10%, transparent)', color: 'var(--color-danger)' }}>
              <span className="min-w-0 flex-1">{err}</span>
              <button type="button" onClick={() => setErr(null)} aria-label="Dismiss" className="shrink-0 hover:opacity-70"><X size={14} /></button>
            </div>
          )}
          {/* project brief — the goal/scope shared with every agent; editable inline. */}
          <BriefRow brief={project.brief ?? ''} onSave={(b) => patch({ brief: b })} />
          {/* ── workspace + context directory: a slim horizontal bar (moved out of the
              grid so Work + Tasks own the screen). Clicking the path opens the dir tree
              in the side panel; an arrow jumps to the full Files page. ── */}
          <div className="flex flex-wrap items-center gap-2 text-[0.72rem]">
            <FolderChip label="Workspace" icon={project.workspace_dir ? FolderOpen : Folder}
              path={project.workspace_dir}
              onPeek={project.workspace_dir ? () => setPanel({ kind: 'dir', label: 'Workspace', path: project.workspace_dir! }) : undefined}
              onBrowse={project.workspace_dir ? () => navigate(`files?dir=${encodeURIComponent(project.workspace_dir!)}`) : undefined}
              onAction={() => setPickWs(true)} actionLabel={project.workspace_dir ? 'Change' : 'Bind'}
              onClear={project.workspace_dir ? () => patch({ workspace_dir: '' }) : undefined}
              emptyText="No workspace bound" />
            <FolderChip label="Context" icon={FolderOpen} path={project.context_dir}
              onPeek={project.context_dir ? () => setPanel({ kind: 'dir', label: 'Context', path: project.context_dir! }) : undefined}
              onBrowse={project.context_dir ? () => navigate(`files?dir=${encodeURIComponent(project.context_dir!)}`) : undefined}
              emptyText="—" title="System-managed — shared context across this project's loops + chats" />
          </div>
        </div>

        {/* ── 2-region grid (Work + Tasks fill remaining height; files moved to the bar
            above so the live work widgets get the screen) ── */}
        <div className="min-h-0 flex-1 grid gap-px overflow-hidden bg-outline-variant/20"
          style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(280px, 1fr)' }}>
          {/* WORK — live loop widgets + sessions, split ONGOING vs COMPLETED (Gap 4) */}
          <HubColumn title={`Work · ${loopRefs.length + chats.length}`}>
            {loopRefs.length === 0 && chats.length === 0 && artifacts.length === 0 ? (
              <p className="text-on-surface-low text-[0.8125rem]">No work here yet — use <span className="text-on-surface-var">New</span> above to launch a loop, or start a chat.</p>
            ) : (
              <div className="flex flex-col gap-3">
                {/* ONGOING — active loops + running chats */}
                {(ongoingLoops.length > 0 || ongoingChats.length > 0) && (
                  <div className="flex flex-col gap-1.5">
                    <WorkGroupLabel text="Ongoing" count={ongoingLoops.length + ongoingChats.length} tone="ok" />
                    {ongoingLoops.map((r) => <SdlcProgressCard key={`${r.kind}:${r.id}`} refObj={{ kind: r.kind, id: r.id, created: false }}
                      controllable onDeleted={() => { invalidateCache(`projects:linked:${id}`); refresh() }} />)}
                    {ongoingChats.map((c) => <ChatRow key={c.key} c={c} onOpen={() => navigate(`chat/${c.key}`)} onChanged={() => { invalidateCache(`projects:linked:${id}`); refresh() }} />)}
                  </div>
                )}
                {/* COMPLETED — terminal loops + idle chats */}
                {(completedLoops.length > 0 || idleChats.length > 0) && (
                  <div className="flex flex-col gap-1.5">
                    <WorkGroupLabel text="Completed" count={completedLoops.length + idleChats.length} tone="muted" />
                    {completedLoops.map((r) => <SdlcProgressCard key={`${r.kind}:${r.id}`} refObj={{ kind: r.kind, id: r.id, created: false }}
                      controllable onDeleted={() => { invalidateCache(`projects:linked:${id}`); refresh() }} />)}
                    {idleChats.map((c) => <ChatRow key={c.key} c={c} onOpen={() => navigate(`chat/${c.key}`)} onChanged={() => { invalidateCache(`projects:linked:${id}`); refresh() }} />)}
                  </div>
                )}
                {/* ARTIFACTS — the project's saved outputs */}
                {artifacts.length > 0 && (
                  <div className="flex flex-col gap-1.5">
                    <WorkGroupLabel text="Artifacts" count={artifacts.length} tone="muted" />
                    {artifacts.map((a) => (
                      <button key={a.slug} type="button" onClick={() => navigate(`files/${a.slug}`)}
                        className="group flex items-center gap-2 rounded-md bg-surface-high/60 px-2.5 py-1.5 text-left text-[0.8125rem] text-on-surface-var hover:bg-surface-high">
                        <FileBox size={13} className="shrink-0 text-primary" />
                        <span className="min-w-0 flex-1 truncate">{a.name}</span>
                        <span className="shrink-0 rounded-pill bg-surface-high px-1.5 py-0.5 text-[0.65rem] text-on-surface-low">{a.kind}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </HubColumn>

          {/* TASKS — a plain list of task lists; clicking one opens its tasks in the
              side panel (no inline expansion). */}
          <HubColumn title={`Tasks · ${lists?.length ?? 0} list${(lists?.length ?? 0) === 1 ? '' : 's'}`}>
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

      {pickWs && (
        <WorkspacePicker mode="brownfield" allowCreate onClose={() => setPickWs(false)}
          onPick={(dir) => { setPickWs(false); patch({ workspace_dir: dir }) }} />
      )}
    </Shell>
  )
}

/** One scrollable region of the project hub grid. */
function HubColumn({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-h-0 flex flex-col bg-surface">
      <div className="shrink-0 px-l pt-3 pb-1.5 text-on-surface-low text-[0.68rem] uppercase tracking-wide">{title}</div>
      <div className="min-h-0 flex-1 overflow-y-auto px-l pb-l">{children}</div>
    </div>
  )
}

/** A sub-group label inside the Work column (Ongoing / Completed / Artifacts). */
function WorkGroupLabel({ text, count, tone }: { text: string; count: number; tone: 'ok' | 'muted' }) {
  return (
    <div className="flex items-center gap-1.5 text-[0.62rem] uppercase tracking-wide">
      {tone === 'ok' && <span className="size-1.5 rounded-full" style={{ background: 'var(--color-ok)' }} />}
      <span className="text-on-surface-low">{text}</span>
      <span className="text-on-surface-low/60">· {count}</span>
    </div>
  )
}

/** A project-bound chat session row in the Work column. Clicking the row opens the
 *  chat; hover reveals controls — Stop (when running) and a two-step Delete. */
function ChatRow({ c, onOpen, onChanged }: {
  c: { key: string; title: string; running: boolean }; onOpen: () => void; onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  async function stop(e: React.MouseEvent) {
    e.stopPropagation(); if (busy) return
    setBusy(true)
    try { await api.stopChat(c.key); onChanged() } catch { /* transient */ } finally { setBusy(false) }
  }
  async function del(e: React.MouseEvent) {
    e.stopPropagation()
    if (!confirmDel) { setConfirmDel(true); window.setTimeout(() => setConfirmDel(false), 4000); return }
    setConfirmDel(false); setBusy(true)
    try { await api.deleteChatSession(c.key); onChanged() } catch { /* transient */ } finally { setBusy(false) }
  }
  return (
    <div role="button" tabIndex={0} onClick={onOpen}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen() } }}
      className="group flex cursor-pointer items-center gap-2 rounded-md bg-surface-high/60 px-2.5 py-1.5 text-left text-[0.8125rem] text-on-surface-var hover:bg-surface-high">
      <MessageSquare size={13} className="shrink-0 text-primary" />
      <span className="min-w-0 flex-1 truncate">{c.title}</span>
      {c.running && <span className="shrink-0 rounded-pill px-1.5 py-0.5 text-[0.65rem]" style={{ background: 'color-mix(in srgb, var(--color-ok) 18%, transparent)', color: 'var(--color-ok)' }}>running</span>}
      <span className="shrink-0 inline-flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100" style={confirmDel ? { opacity: 1 } : undefined}>
        {busy && <Loader2 size={11} className="animate-spin text-on-surface-low" />}
        {c.running && <IconButton icon={Square} label="Stop" size={28} onClick={stop} />}
        <IconButton icon={Trash2} size={28} label={confirmDel ? 'Click again to delete' : 'Delete chat'}
          onClick={del} className={confirmDel ? 'text-danger' : undefined} />
      </span>
    </div>
  )
}

/** A compact horizontal folder chip (workspace / context) for the hub's directory bar:
 *  label · path (click → peek dir tree in the side panel) · arrow → full Files page ·
 *  optional Bind/Change + Unbind actions. */
function FolderChip({ label, icon: Icon, path, emptyText, title, onPeek, onBrowse, onAction, actionLabel, onClear }: {
  label: string; icon: LucideIcon; path?: string; emptyText: string; title?: string
  onPeek?: () => void; onBrowse?: () => void; onAction?: () => void; actionLabel?: string; onClear?: () => void
}) {
  return (
    <div className="inline-flex min-w-0 items-center gap-1.5 rounded-md bg-surface-high/50 px-2 py-1" title={title}>
      <Icon size={12} className="shrink-0 text-on-surface-low" />
      <span className="shrink-0 text-on-surface-low uppercase tracking-wide text-[0.62rem]">{label}</span>
      {path ? (
        onPeek
          ? <button type="button" onClick={onPeek} title={`${path} — view contents`}
              className="min-w-0 max-w-[22rem] truncate rounded bg-surface-high px-1.5 py-0.5 font-mono text-on-surface-var hover:text-primary">{path}</button>
          : <code className="min-w-0 max-w-[22rem] truncate rounded bg-surface-high px-1.5 py-0.5 font-mono text-on-surface-var" title={path}>{path}</code>
      ) : (
        <span className="text-on-surface-low/70 italic">{emptyText}</span>
      )}
      {path && onBrowse && (
        <button type="button" onClick={onBrowse} aria-label={`Open ${label} in Files`} title="Open in Files"
          className="shrink-0 rounded p-0.5 text-on-surface-low hover:bg-surface-high hover:text-primary"><FolderOpen size={12} /></button>
      )}
      {onAction && actionLabel && (
        <button type="button" onClick={onAction} className="shrink-0 rounded px-1.5 py-0.5 text-on-surface-low hover:bg-surface-high hover:text-on-surface">{actionLabel}</button>
      )}
      {onClear && <button type="button" onClick={onClear} aria-label={`Unbind ${label}`} className="shrink-0 rounded p-0.5 text-on-surface-low hover:text-on-surface" title="Unbind"><X size={12} /></button>}
    </div>
  )
}

/** A plain (non-expandable) task-list row in the hub's Tasks column. Clicking opens
 *  the list's tasks in the side panel. Shows the list's task count when known. */
function TaskListRow({ list, active, onOpen }: { list: TaskListItem; active: boolean; onOpen: () => void }) {
  const count = (list as { task_count?: number; count?: number }).task_count ?? (list as { count?: number }).count
  return (
    <button type="button" onClick={onOpen}
      className={`group flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[0.8125rem] transition-colors ${active ? 'bg-surface-high text-on-surface ring-1 ring-primary/40' : 'bg-surface-high/60 text-on-surface-var hover:bg-surface-high hover:text-on-surface'}`}>
      <ListChecks size={13} className="shrink-0 text-on-surface-low" />
      <span className="min-w-0 flex-1 truncate">{list.name}</span>
      {typeof count === 'number' && <span className="shrink-0 text-on-surface-low/70 text-[0.68rem] tabular-nums">{count}</span>}
      <ChevronRight size={13} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100" />
    </button>
  )
}

/** Side-panel body: the tasks under a list, grouped nothing-fancy, each opening the
 *  full task in the Tasks page. Lazy-fetched (panel only mounts when opened). */
function TaskListPanel({ list, onOpenTask }: { list: TaskListItem; onOpenTask: (taskId: string) => void }) {
  const { data: tasks, loading } = useCachedData<TaskItem[]>(`tasklist:tasks:${list.id}`,
    () => api.tasks({ task_list: list.id, limit: 200 }).then((d) => d.tasks))
  if (loading && !tasks) return <div className="flex justify-center py-l"><Loader2 size={16} className="animate-spin text-on-surface-low" /></div>
  if (!tasks?.length) return <p className="text-on-surface-low text-[0.8125rem]">No tasks in this list yet.</p>
  return (
    <ul className="flex flex-col gap-0.5">
      {tasks.map((t) => (
        <li key={t.id}>
          <button type="button" onClick={() => onOpenTask(t.id)}
            className="group flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-[0.875rem] hover:bg-surface-high">
            {t.status === 'done' ? <CheckCircle2 size={14} className="mt-0.5 shrink-0" style={{ color: 'var(--color-ok)' }} />
              : t.status === 'in_progress' ? <CircleDot size={14} className="mt-0.5 shrink-0 text-primary" />
              : t.status === 'blocked' ? <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
              : <Circle size={14} className="mt-0.5 shrink-0 text-on-surface-low/40" />}
            <span className={`min-w-0 flex-1 ${t.status === 'done' ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}>{t.title}</span>
            <ChevronRight size={14} className="mt-0.5 shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100" />
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
  const { data, loading, error } = useCachedData(`dirtree:${cur}`, () => api.fileList(cur))
  // Breadcrumb segments relative to the panel's root `path` (don't walk above it).
  const rel = cur.startsWith(path) ? cur.slice(path.length).replace(/^\//, '') : ''
  const crumbs = rel ? rel.split('/') : []
  const entries = data?.entries ?? []
  const dirs = entries.filter((e) => e.is_dir)
  const files = entries.filter((e) => !e.is_dir)
  return (
    <div className="flex flex-col gap-2">
      <Button size="sm" variant="ghost" onClick={onOpenInFiles}><FolderOpen size={14} /> Open in Files</Button>
      {/* breadcrumb — root + drilled-into subfolders; click to pop back */}
      <div className="flex flex-wrap items-center gap-0.5 text-[0.72rem] text-on-surface-low">
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
      <button type="button" onClick={onClick}
        className="group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[0.875rem] text-on-surface-var hover:bg-surface-high hover:text-on-surface">
        {entry.is_dir ? <Folder size={14} className="shrink-0 text-primary" /> : <FileIcon size={14} className="shrink-0 text-on-surface-low" />}
        <span className="min-w-0 flex-1 truncate">{entry.name}</span>
        {entry.is_dir && <ChevronRight size={14} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100" />}
      </button>
    </li>
  )
}

/** Inline-editable project brief row (collapsed preview → textarea on click). */
function BriefRow({ brief, onSave }: { brief: string; onSave: (b: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(brief)
  useEffect(() => { setDraft(brief) }, [brief])
  if (editing) {
    return (
      <div className="flex flex-col gap-1.5">
        <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} rows={3}
          onKeyDown={(e) => { if (e.key === 'Escape') { setDraft(brief); setEditing(false) } }}
          placeholder="Project brief — the goal, scope, and background. Shared as context with every agent working on this project's sessions and loops."
          className="resize-y rounded-md bg-surface-high px-2.5 py-1.5 text-[0.8125rem] leading-relaxed text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <div className="flex items-center gap-1.5">
          <Button size="sm" onClick={() => { onSave(draft.trim()); setEditing(false) }}><Check size={13} /> Save brief</Button>
          <button type="button" onClick={() => { setDraft(brief); setEditing(false) }} className="rounded-md px-2 py-1 text-[0.78rem] text-on-surface-low hover:text-on-surface">Cancel</button>
        </div>
      </div>
    )
  }
  return (
    <button type="button" onClick={() => setEditing(true)} title="Edit project brief"
      className="group flex items-start gap-2 rounded-md px-1 py-0.5 text-left hover:bg-surface-high/50">
      <FileText size={13} className="mt-0.5 shrink-0 text-on-surface-low" />
      <span className={`min-w-0 flex-1 text-[0.8125rem] ${brief ? 'text-on-surface-var line-clamp-2' : 'text-on-surface-low/70 italic'}`}>
        {brief || 'Add a project brief — shared as context with every agent working here.'}
      </span>
      <Pencil size={12} className="mt-0.5 shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100" />
    </button>
  )
}

function Shell({ title, titleNode, onBack, actions, scroll = true, panel, children }: {
  title: string; titleNode?: React.ReactNode; onBack: () => void
  actions?: React.ReactNode; scroll?: boolean
  /** Already-gated right-docked SidePanel (fillHeight) — docks below the TopBar and
   *  pushes only the content (never the header), like WorkbenchLayout. */
  panel?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        left={titleNode ?? <div className="flex items-center gap-2"><FolderKanban size={18} className="text-primary" /><span data-type="title-l" className="text-on-surface">{title}</span></div>}
        right={<div className="flex items-center gap-1.5">{actions}<HeaderActions><HeaderControl icon={ListChecks} label="All projects" onClick={onBack} priority="primary" /></HeaderActions></div>} />
      <div className="flex min-h-0 flex-1">
        {/* Detail hub fills height (scroll=false); list/loading states scroll. */}
        <div className={`min-w-0 flex-1 ${scroll ? 'overflow-y-auto' : 'overflow-hidden'}`}>{children}</div>
        <AnimatePresence>{panel}</AnimatePresence>
      </div>
    </div>
  )
}
