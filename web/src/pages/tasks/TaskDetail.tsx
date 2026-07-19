import { useEffect, useState } from 'react'
import { Pencil, Trash2, Check, X, ExternalLink, Lock, CornerDownRight, Send, AlertTriangle, FolderKanban } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Markdown } from '../../ui/Markdown'
import { confirm } from '../../ui/dialog'
import { api, type TaskItem, type TaskComment, type TaskNote } from '../../lib/api'
import { statusMeta, priorityMeta, dueMeta, relTime, isExitComplete, exitDoneCount } from './taskMeta'
import { prereqIds } from './dag'
import { TaskForm, toDraft, draftToPayload, type TaskDraft } from './TaskForm'

/** Body for the task SidePanel. Owns the view↔edit toggle (edit reuses the same
 *  panel, per the directive) and the comment thread. Project-provider tasks are
 *  read-only: Edit/Delete are hidden and a managed-by note shows instead. */
export function TaskDetail({ task, onSaved, onDeleted, editing: editingProp, onEditingChange, allTasks = [], onOpenTask }: {
  task: TaskItem
  onSaved: (t: TaskItem) => void
  onDeleted: () => void
  editing: boolean
  onEditingChange: (v: boolean) => void
  allTasks?: TaskItem[]
  onOpenTask?: (id: string) => void
}) {
  const readOnly = task.provider === 'project'
  // Edit mode is owned by the URL (?edit=1), threaded in fully controlled;
  // project-provider tasks are read-only and can never enter the edit form.
  const editing = editingProp && !readOnly
  const setEditing = onEditingChange
  const [draft, setDraft] = useState<TaskDraft>(() => toDraft(task))
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { setDraft(toDraft(task)) }, [task.id]) // reset when switching tasks

  async function save() {
    if (!draft.title.trim()) { setErr('Title is required'); return }
    setSaving(true); setErr('')
    try {
      const updated = await api.updateTask(task.id, draftToPayload(draft))
      onSaved(updated); setEditing(false)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (!(await confirm({ title: 'Delete this task?', body: 'This cannot be undone.', danger: true, confirmLabel: 'Delete' }))) return
    try { await api.deleteTask(task.id, task.provider); onDeleted() } catch { setErr('Delete failed') }
  }

  // Inline (view-mode) toggles for exit criteria + action-plan items — tick things
  // off as you go without entering full Edit mode. Optimistic via onSaved.
  async function toggleExit(idx: number) {
    if (readOnly) return
    const next = (task.exit_criteria ?? []).map((c, i) => {
      if (i !== idx) return c
      const done = isExitComplete(c)
      return { ...c, met: !done, status: !done ? 'complete' : 'incomplete' as const }
    })
    setErr('')
    try { onSaved(await api.updateTask(task.id, { exit_criteria: next })) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not update exit criteria') }
  }
  async function toggleStep(idx: number) {
    if (readOnly) return
    const next = (task.action_plan ?? []).map((a, i) => i === idx ? { ...a, completed: !a.completed } : a)
    setErr('')
    try { onSaved(await api.updateTask(task.id, { action_plan: next })) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not update action plan') }
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-l">
        <TaskForm draft={draft} onChange={setDraft} compact allTasks={allTasks} />
        {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
        <div className="sticky bottom-0 -mx-l px-l py-3 bg-surface/95 border-t border-outline-variant/40 flex justify-end gap-s">
          <Button variant="ghost" size="sm" onClick={() => { setDraft(toDraft(task)); setEditing(false); setErr('') }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} disabled={saving || !draft.title.trim()}><Check size={15} /> {saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    )
  }

  const sm = statusMeta(task.status)
  const pm = priorityMeta(task.priority)
  const due = dueMeta(task.due)
  const exit = task.exit_criteria ?? []
  const exitDone = exitDoneCount(exit)

  return (
    <div className="flex flex-col gap-l">
      {/* action row */}
      <div className="flex items-center gap-s">
        {readOnly ? (
          <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]"><Lock size={13} /> Managed by project — read-only</span>
        ) : (
          <>
            <Button size="sm" variant="secondary" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</Button>
            <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
          </>
        )}
        {task.url && <a href={task.url} target="_blank" rel="noopener noreferrer" className="ml-auto inline-flex items-center gap-1 text-primary text-[0.8125rem] hover:underline"><ExternalLink size={13} /> Open</a>}
      </div>

      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}

      {/* chips */}
      <div className="flex flex-wrap gap-s">
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${sm.tone} 18%, transparent)`, color: sm.tone }}><sm.icon size={13} /> {sm.label}</span>
        <span className="inline-flex items-center rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${pm.tone} 16%, transparent)`, color: pm.tone }}>{pm.label}</span>
        {task.project && <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)', color: 'var(--color-primary)' }}><FolderKanban size={13} /> {task.project}</span>}
        {task.assignee && <span className="inline-flex items-center rounded-pill px-m h-7 text-[0.8125rem] bg-surface-high text-on-surface-var">@{task.assignee}</span>}
        {due && <span className="inline-flex items-center rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${due.tone} 14%, transparent)`, color: due.tone }}>{due.label}</span>}
      </div>

      {(task.labels?.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {task.labels!.map((l) => <span key={l} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{l}</span>)}
        </div>
      )}

      {task.block_reason?.is_blocked && (
        <div className="rounded-md px-m py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
          <div className="flex items-center gap-1.5 text-warn mb-1" style={{ fontVariationSettings: '"wght" 500' }}><AlertTriangle size={14} /> Blocked</div>
          <div className="text-on-surface">{task.block_reason.message || `Waiting on ${task.block_reason.blocking_task_titles?.join(', ')}`}</div>
        </div>
      )}

      {task.description && <SectionLabel label="Description"><Markdown>{task.description}</Markdown></SectionLabel>}

      {exit.length > 0 && (
        <SectionLabel label={`Exit criteria · ${exitDone}/${exit.length}`}>
          <div className="mb-2 h-1.5 rounded-pill bg-surface-high overflow-hidden"><div className="h-full rounded-pill" style={{ width: `${exit.length ? (exitDone / exit.length) * 100 : 0}%`, background: 'var(--color-ok)' }} /></div>
          <ul className="flex flex-col gap-1">
            {exit.map((e, i) => { const m = isExitComplete(e); return <li key={i} className="flex items-start gap-s text-[0.875rem]">
              <button type="button" disabled={readOnly} onClick={() => toggleExit(i)} aria-label={m ? 'Mark criterion incomplete' : 'Mark criterion complete'}
                className="mt-0.5 shrink-0 inline-flex size-4 items-center justify-center rounded-sm enabled:hover:ring-2 enabled:hover:ring-ok/40 disabled:cursor-default transition-shadow" style={{ background: m ? 'var(--color-ok)' : 'var(--color-surface-high)' }}>{m && <Check size={11} className="text-white" />}</button>
              <span className={m ? 'text-on-surface-low line-through' : 'text-on-surface'}>{e.description}</span></li> })}
          </ul>
        </SectionLabel>
      )}

      {(task.action_plan?.length ?? 0) > 0 && (
        <SectionLabel label="Action plan">
          <ol className="flex flex-col gap-1">
            {task.action_plan!.map((a, i) => <li key={i} className="flex items-start gap-s text-[0.875rem]">
              <button type="button" disabled={readOnly} onClick={() => toggleStep(i)} aria-label={a.completed ? 'Mark step incomplete' : 'Mark step done'}
                className="shrink-0 inline-flex size-5 items-center justify-center rounded-pill text-[0.7rem] tabular-nums enabled:hover:ring-2 enabled:hover:ring-primary/40 disabled:cursor-default transition-shadow"
                style={{ background: a.completed ? 'var(--color-ok)' : 'color-mix(in srgb, var(--color-primary) 18%, transparent)' }}>{a.completed ? <Check size={11} className="text-white" /> : i + 1}</button>
              <span className={a.completed ? 'text-on-surface-low line-through' : 'text-on-surface'}>{a.content ?? a.description}</span></li>)}
          </ol>
        </SectionLabel>
      )}

      {prereqIds(task).length > 0 && (() => {
        const prereqs = prereqIds(task)
        return (
        <SectionLabel label={`Depends on · ${prereqs.length}`}>
          <div className="flex flex-col gap-1.5">
            {prereqs.map((d) => {
              const dep = allTasks.find((t) => t.id === d)
              const dsm = statusMeta(dep?.status)
              const done = dep && (dep.status === 'done' || dep.status === 'cancelled')
              return (
                <button key={d} type="button" disabled={!dep || !onOpenTask} onClick={() => dep && onOpenTask?.(d)}
                  className="flex items-center gap-s rounded-md bg-surface-container px-2 py-1.5 text-left enabled:hover:bg-surface-high transition-colors disabled:cursor-default">
                  <dsm.icon size={14} className="shrink-0" style={{ color: dsm.tone }} />
                  <span className={`flex-1 truncate text-[0.875rem] ${done ? 'text-on-surface-low line-through' : 'text-on-surface'}`}>{dep?.title ?? d}</span>
                  {dep && !done && <span className="shrink-0 text-on-surface-low text-[0.7rem]">{dsm.label}</span>}
                </button>
              )
            })}
            {task.block_reason?.is_blocked && (
              <div className="flex items-center gap-1.5 text-warn text-[0.75rem] mt-0.5"><AlertTriangle size={12} /> {task.block_reason.message || 'Has unfinished prerequisites'}</div>
            )}
          </div>
        </SectionLabel>
        )
      })()}

      {(() => {
        // Reverse dependencies: tasks that have THIS task as a BLOCKS prerequisite
        // — i.e. what completing this task will unblock. Computed from the loaded set.
        const dependents = allTasks.filter((t) => prereqIds(t).includes(task.id))
        if (dependents.length === 0) return null
        return (
          <SectionLabel label={`Blocks · ${dependents.length}`}>
            <div className="flex flex-col gap-1.5">
              {dependents.map((dep) => {
                const dsm = statusMeta(dep.status)
                const done = dep.status === 'done' || dep.status === 'cancelled'
                return (
                  <button key={dep.id} type="button" disabled={!onOpenTask} onClick={() => onOpenTask?.(dep.id)}
                    className="flex items-center gap-s rounded-md bg-surface-container px-2 py-1.5 text-left enabled:hover:bg-surface-high transition-colors disabled:cursor-default">
                    <dsm.icon size={14} className="shrink-0" style={{ color: dsm.tone }} />
                    <span className={`flex-1 truncate text-[0.875rem] ${done ? 'text-on-surface-low line-through' : 'text-on-surface'}`}>{dep.title}</span>
                    {!done && <span className="shrink-0 text-on-surface-low text-[0.7rem]">{dsm.label}</span>}
                  </button>
                )
              })}
            </div>
          </SectionLabel>
        )
      })()}

      <NoteChannel label="Notes" notes={task.notes} />
      <NoteChannel label="Research notes" notes={task.research_notes} />
      <NoteChannel label="Execution notes" notes={task.execution_notes} />

      {task.agent_instructions_template && (
        <SectionLabel label="Agent instructions">
          <pre className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] font-mono whitespace-pre-wrap">{task.agent_instructions_template}</pre>
        </SectionLabel>
      )}

      <Comments taskId={task.id} provider={task.provider} />

      <div className="text-on-surface-low text-[0.7rem]">
        {task.created_at && <>Created {relTime(task.created_at)}</>}{task.updated_at && task.updated_at !== task.created_at && <> · updated {relTime(task.updated_at)}</>}
      </div>
    </div>
  )
}

/** One of the task's three note channels (general / research / execution). Each
 *  note shows its content + a relative timestamp; hidden when the channel is empty. */
function NoteChannel({ label, notes }: { label: string; notes?: TaskNote[] }) {
  const items = (notes ?? []).filter((n) => (n.content ?? '').trim())
  if (items.length === 0) return null
  return (
    <SectionLabel label={`${label} · ${items.length}`}>
      <ul className="flex flex-col gap-1.5">
        {items.map((n, i) => (
          <li key={i} className="rounded-md bg-surface-container px-m py-2">
            <p className="text-on-surface text-[0.875rem] whitespace-pre-wrap">{n.content}</p>
            {(n.timestamp || n.created_at) && (
              <div className="mt-0.5 text-on-surface-low text-[0.7rem]">{relTime(n.timestamp || n.created_at)}</div>
            )}
          </li>
        ))}
      </ul>
    </SectionLabel>
  )
}

function SectionLabel({ label, right, children }: { label: string; right?: React.ReactNode; children: React.ReactNode }) {
  return <div><div className="mb-1.5 flex items-center gap-s"><span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">{label}</span>{right}</div>{children}</div>
}

function Comments({ taskId, provider }: { taskId: string; provider?: string }) {
  const [comments, setComments] = useState<TaskComment[] | null>(null)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)

  const load = () => api.taskComments(taskId, provider).then(setComments).catch(() => setComments([]))
  useEffect(() => { load() }, [taskId])

  async function send() {
    const body = draft.trim(); if (!body) return
    setSending(true)
    try { await api.addTaskComment(taskId, body, provider); setDraft(''); await load() } catch { /* ignore */ } finally { setSending(false) }
  }

  return (
    <SectionLabel label={`Comments${comments?.length ? ` · ${comments.length}` : ''}`}>
      <div className="flex flex-col gap-s">
        {comments?.map((c) => (
          <div key={c.id} className="flex gap-s">
            <CornerDownRight size={14} className="text-on-surface-low shrink-0 mt-1" />
            <div className="flex-1 rounded-md bg-surface-container px-m py-2">
              <div className="flex items-center gap-s text-[0.7rem] text-on-surface-low mb-0.5"><span className="text-on-surface-var">{c.author || 'you'}</span><span>{relTime(c.created_at)}</span></div>
              <p className="text-on-surface text-[0.875rem] whitespace-pre-wrap">{c.body}</p>
            </div>
          </div>
        ))}
        <div className="flex items-end gap-s">
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add a comment…" rows={1}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send() } }}
            className="flex-1 rounded-md bg-surface-container px-m py-2 text-on-surface text-[0.875rem] placeholder:text-on-surface-low outline-none resize-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          <button type="button" onClick={send} disabled={sending || !draft.trim()} className="shrink-0 inline-flex size-9 items-center justify-center rounded-pill bg-primary text-on-primary disabled:opacity-40"><Send size={15} /></button>
        </div>
      </div>
    </SectionLabel>
  )
}
