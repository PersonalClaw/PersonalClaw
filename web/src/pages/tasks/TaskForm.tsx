import { useEffect, useState } from 'react'
import { api, type TaskItem, type ExitCriterion, type ActionPlanItem, type TaskNote, type ProjectItem, type TaskListItem } from '../../lib/api'
import { getActiveProject } from '../../lib/activeProject'
import { STATUSES, PRIORITIES } from './taskMeta'
import { prereqIds } from './dag'
import { Field, TextInput, TextArea, DateInput, Segmented, ChipInput, ChecklistEditor, DependencyEditor, Select, NotesEditor } from './formControls'

/** Editable task draft. `depends_on` is the working list of BLOCKS-prerequisite
 *  ids the DependencyEditor manipulates; it's serialized back to typed
 *  `dependencies` in draftToPayload. */
export type TaskDraft = Partial<TaskItem> & { title: string; depends_on?: string[]; project_id?: string }

export function emptyDraft(): TaskDraft {
  return { title: '', description: '', status: 'open', priority: 'medium', labels: [], task_list_id: '', assignee: '', due: '', exit_criteria: [], action_plan: [], notes: [], research_notes: [], execution_notes: [], agent_instructions_template: '', depends_on: [] }
}
export function toDraft(t: TaskItem): TaskDraft {
  return { ...t, labels: t.labels ?? [], exit_criteria: t.exit_criteria ?? [], action_plan: t.action_plan ?? [], notes: t.notes ?? [], research_notes: t.research_notes ?? [], execution_notes: t.execution_notes ?? [], depends_on: prereqIds(t) }
}

/** The single form behind both the create PAGE and the in-panel edit mode.
 *  `compact` tightens spacing for the narrower SidePanel. Sections mirror the
 *  TasksMultiServer construct; all fields (exit criteria, action plan, typed
 *  dependencies, agent instructions) are persisted by the backend (P5a). */
export function TaskForm({ draft, onChange, compact, allTasks = [] }: { draft: TaskDraft; onChange: (d: TaskDraft) => void; compact?: boolean; allTasks?: TaskItem[] }) {
  const set = <K extends keyof TaskDraft>(k: K, v: TaskDraft[K]) => onChange({ ...draft, [k]: v })
  const gap = compact ? 'gap-l' : 'gap-xl'

  return (
    <div className={`flex flex-col ${gap}`}>
      <Section title="Basics" compact={compact}>
        <Field label="Title">
          <TextInput value={draft.title} onChange={(v) => set('title', v)} placeholder="What needs to happen?" autoFocus />
        </Field>
        <Field label="Description">
          <TextArea value={draft.description ?? ''} onChange={(v) => set('description', v)} placeholder="Context, acceptance notes, links… (markdown)" rows={compact ? 4 : 6} />
        </Field>
      </Section>

      <Section title="Classification" compact={compact}>
        <Field label="Status"><Segmented options={STATUSES.map((s) => ({ key: s.key, label: s.label, tone: s.tone, icon: s.icon }))} value={draft.status ?? 'open'} onChange={(v) => set('status', v)} /></Field>
        <Field label="Priority">
          <Segmented options={PRIORITIES.map((p) => ({ key: p.key, label: p.label, tone: p.tone }))} value={draft.priority ?? 'medium'} onChange={(v) => set('priority', v)} />
        </Field>
        <div className={`grid grid-cols-2 ${compact ? 'gap-m' : 'gap-l'}`}>
          <ProjectListPicker taskListId={draft.task_list_id ?? ''} onChange={(id) => set('task_list_id', id)}
            onProjectChange={draft.id ? undefined : (id) => set('project_id', id)} />
        </div>
        <div className={`grid grid-cols-2 ${compact ? 'gap-m' : 'gap-l'} items-start`}>
          <Field label="Assignee"><TextInput value={draft.assignee ?? ''} onChange={(v) => set('assignee', v)} placeholder="Who owns it" /></Field>
          <Field label="Due"><DateInput value={draft.due ?? ''} onChange={(v) => set('due', v)} /></Field>
        </div>
        <Field label="Tags"><ChipInput values={draft.labels ?? []} onChange={(v) => set('labels', v)} placeholder="Add a tag, Enter" max={10} /></Field>
      </Section>

      <Section title="Structure" compact={compact}>
        <Field label="Depends on" hint="Prerequisite tasks that must finish first. Choices that would form a cycle are blocked.">
          <DependencyEditor selfId={draft.id} allTasks={allTasks} value={draft.depends_on ?? []} onChange={(v) => set('depends_on', v)} />
        </Field>
        <Field label="Exit criteria" hint="Conditions that must be true for this task to count as done.">
          <ChecklistEditor<ExitCriterion> items={draft.exit_criteria ?? []} onChange={(v) => set('exit_criteria', v)} doneKey="met" placeholder="Add a completion condition" />
        </Field>
        <Field label="Action plan" hint="Ordered steps to carry the task out.">
          <ChecklistEditor<ActionPlanItem> items={draft.action_plan ?? []} onChange={(v) => set('action_plan', v)} doneKey="completed" placeholder="Add a step" ordered />
        </Field>
      </Section>

      <Section title="Notes" compact={compact}>
        <Field label="General" hint="Free notes on this task.">
          <NotesEditor items={draft.notes ?? []} onChange={(v) => set('notes', v as TaskNote[])} placeholder="Add a note, Enter" />
        </Field>
        <Field label="Research" hint="Findings gathered while investigating.">
          <NotesEditor items={draft.research_notes ?? []} onChange={(v) => set('research_notes', v as TaskNote[])} placeholder="Add a research note, Enter" />
        </Field>
        <Field label="Execution" hint="What was done while carrying the task out.">
          <NotesEditor items={draft.execution_notes ?? []} onChange={(v) => set('execution_notes', v as TaskNote[])} placeholder="Add an execution note, Enter" />
        </Field>
      </Section>

      <Section title="Agent instructions" compact={compact}>
        <Field label="Template" hint="Guidance an agent follows when it picks up this task.">
          <TextArea value={draft.agent_instructions_template ?? ''} onChange={(v) => set('agent_instructions_template', v)} placeholder="e.g. Investigate, propose a fix, open a PR…" rows={compact ? 3 : 4} mono />
        </Field>
      </Section>
    </div>
  )
}

/** Project → TaskList picker. The structural FK is `task_list_id`; the project
 *  is only a grouping filter (resolved from the chosen list, so a task can sit
 *  directly under a project's default list or under a named list). The task's
 *  human-readable `project` label is derived server-side from this list. */
// Sentinel value the Project/TaskList selects use to trigger an inline create.
const NEW = '__new__'

function ProjectListPicker({ taskListId, onChange, onProjectChange }: { taskListId: string; onChange: (id: string) => void; onProjectChange?: (id: string) => void }) {
  const [projects, setProjects] = useState<ProjectItem[]>([])
  const [lists, setLists] = useState<TaskListItem[]>([])
  const [projectId, setProjectId] = useState('')
  const [creating, setCreating] = useState<null | 'project' | 'list'>(null)
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    Promise.all([api.projects(), api.taskLists()]).then(([ps, ls]) => {
      if (!alive) return
      setProjects(ps); setLists(ls)
      // Derive the selected project: the current task list (edit case) wins; else the
      // user's ACTIVE project (what they're working on, set by the create pickers) if
      // it still exists; else the default (Personal) catch-all; else the first project.
      const cur = ls.find((l) => l.id === taskListId)
      const active = getActiveProject()
      const activeOk = active && ps.some((p) => p.id === active) ? active : ''
      // `||` (not `??`): the intermediate fallbacks are empty STRINGS, not null, so
      // each must fall through to the next when blank.
      const derived = cur?.project_id || activeOk || ps.find((p) => p.is_default)?.id || ps[0]?.id || ''
      setProjectId(derived)
      onProjectChange?.(derived)
    }).catch(() => {})
    return () => { alive = false }
  }, [])

  const projectLists = lists.filter((l) => l.project_id === projectId)

  async function createProject() {
    const name = newName.trim()
    if (!name) return
    setBusy(true); setErr('')
    try {
      const p = await api.createProject({ name })
      setProjects((ps) => [...ps, p]); setProjectId(p.id); onChange(''); onProjectChange?.(p.id)
      setCreating(null); setNewName('')
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not create project') }
    finally { setBusy(false) }
  }
  async function createList() {
    const name = newName.trim()
    if (!name || !projectId) return
    setBusy(true); setErr('')
    try {
      const l = await api.createTaskList({ name, project_id: projectId })
      setLists((ls) => [...ls, l]); onChange(l.id)
      setCreating(null); setNewName('')
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not create task list') }
    finally { setBusy(false) }
  }

  if (creating) {
    const isProject = creating === 'project'
    return (
      <Field label={isProject ? 'New project' : 'New task list'}>
        <div className="flex items-center gap-s">
          <TextInput value={newName} onChange={setNewName} autoFocus
            placeholder={isProject ? 'Project name' : 'Task list name'}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); isProject ? createProject() : createList() } }} />
          <button type="button" disabled={busy || !newName.trim()} onClick={isProject ? createProject : createList}
            className="shrink-0 rounded-md bg-primary px-3 h-9 text-on-primary text-[0.8125rem] disabled:opacity-40">{busy ? 'Adding…' : 'Add'}</button>
          <button type="button" onClick={() => { setCreating(null); setNewName(''); setErr('') }}
            className="shrink-0 rounded-md bg-surface-high px-3 h-9 text-on-surface-var text-[0.8125rem]">Cancel</button>
        </div>
        {err && <p className="mt-1 text-danger text-[0.75rem]">{err}</p>}
      </Field>
    )
  }

  return (
    <>
      <Field label="Project">
        <Select value={projectId}
          onChange={(id) => { if (id === NEW) { setCreating('project'); setNewName('') } else { setProjectId(id); onChange(''); onProjectChange?.(id) } }}
          options={[
            ...projects.map((p) => ({ value: p.id, label: p.is_default ? `${p.name} (default)` : p.name })),
            { value: NEW, label: '＋ New project…' },
          ]} />
      </Field>
      <Field label="Task list">
        <Select value={taskListId}
          onChange={(id) => { if (id === NEW) { setCreating('list'); setNewName('') } else onChange(id) }}
          disabled={!projectId}
          options={[
            { value: '', label: '(none)' },
            ...projectLists.map((l) => ({ value: l.id, label: l.name })),
            ...(projectId ? [{ value: NEW, label: '＋ New task list…' }] : []),
          ]} />
      </Field>
    </>
  )
}

function Section({ title, right, compact, children }: { title: string; right?: React.ReactNode; compact?: boolean; children: React.ReactNode }) {
  return (
    <div className={`flex flex-col ${compact ? 'gap-m' : 'gap-l'}`}>
      <div className="flex items-center gap-s border-b border-outline-variant/30 pb-1.5">
        <h3 className="text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 550' }}>{title}</h3>
        {right}
      </div>
      {children}
    </div>
  )
}

/** Serialize a draft to the task wire shape. Prerequisites go out as typed
 *  BLOCKS `dependencies` (the server's canonical form). */
export function draftToPayload(d: TaskDraft): Record<string, unknown> {
  return {
    title: d.title.trim(),
    description: d.description ?? '',
    status: d.status ?? 'open',
    priority: d.priority ?? 'medium',
    task_list_id: d.task_list_id ?? '',
    assignee: d.assignee ?? '',
    due: d.due ?? '',
    labels: d.labels ?? [],
    exit_criteria: d.exit_criteria ?? [],
    action_plan: d.action_plan ?? [],
    notes: d.notes ?? [],
    research_notes: d.research_notes ?? [],
    execution_notes: d.execution_notes ?? [],
    agent_instructions_template: d.agent_instructions_template ?? '',
    dependencies: (d.depends_on ?? []).map((id) => ({ depends_on_task_id: id, dependency_type: 'BLOCKS' })),
    // No explicit list chosen → let the server attach the task to the selected
    // project's "General" list (else the project choice would silently drop).
    ...(!d.task_list_id && d.project_id ? { project_id: d.project_id } : {}),
  }
}
