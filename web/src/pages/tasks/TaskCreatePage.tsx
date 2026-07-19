import { useState } from 'react'
import { ArrowLeft, Check } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { api, type TaskItem } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { TaskForm, emptyDraft, draftToPayload, type TaskDraft } from './TaskForm'

/** Dedicated full-page create flow (not the sidebar, per the directive). The
 *  same TaskForm the edit panel uses, laid out at content width with a sticky
 *  footer. On success, hands the new task back to the section to open it. */
export function TaskCreatePage({ onBack, onCreated }: { onBack: () => void; onCreated: (t: TaskItem) => void }) {
  const [draft, setDraft] = useState<TaskDraft>(emptyDraft)
  // A cheap cached snapshot for the dependency picker — NOT the list page's 'tasks'
  // key (that's persist:false for live status); a separate persisted key is fine here.
  const { data: allTasks = [] } = useCachedData<TaskItem[]>('tasks-all', () => api.tasks().then((d) => d.tasks).catch(() => []), { persist: true })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function create() {
    if (!draft.title.trim()) { setErr('Title is required'); return }
    setSaving(true); setErr('')
    try {
      const t = await api.createTask(draftToPayload(draft))
      onCreated(t)
    } catch (e) { setErr(e instanceof Error ? e.message : 'Create failed') } finally { setSaving(false) }
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><span data-type="title-l" className="text-on-surface">New task</span></div>} />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          <TaskForm draft={draft} onChange={setDraft} allTasks={allTasks} />
          {err && <p className="mt-l text-danger text-[0.8125rem]">{err}</p>}
        </div>
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <Button variant="ghost" onClick={onBack}>Cancel</Button>
          <Button onClick={create} disabled={saving || !draft.title.trim()}><Check size={16} /> {saving ? 'Creating…' : 'Create task'}</Button>
        </div>
      </div>
    </div>
  )
}
