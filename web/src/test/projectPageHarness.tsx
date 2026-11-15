import { vi } from 'vitest'
import { render } from '@testing-library/react'
import { useState } from 'react'
import {
  api, ApiError, type AlwaysOnResponse, type ChatSessionSummary, type Loop, type ProjectItem, type RunningRow, type TaskItem,
  type TaskListItem, type WaitingItem, type WaitingKind, type WorkBoard, type WorkRow,
} from '../lib/api'
import { ProjectsSection } from '../pages/projects/ProjectsSection'
import { DialogHost } from '../ui/dialog/DialogHost'
import { chat, project, running, waiting } from './projectListHarness'

/** A project's page over fixtures: every read it makes, answered from what a test gives it, and its
 *  address kept in the page's own `query` as the router keeps it. Shared by the page's tests. */

export { chat, project, running, waiting }

export const PID = 'p-garden'

export function workRow(id: string, title: string, over: Partial<WorkRow> = {}): WorkRow {
  return {
    run_id: id, title, state: 'queued', origin: 'manual', project_id: PID, claim: null, collapsed: false,
    attention: false, resumable: false, outcome: '', source: 'loop', kind: 'goal', updated_at: '2026-11-12T09:00:00Z', ...over,
  }
}

/** The board as `/work` groups it: one group per state, in the board's order. */
export function board(rows: WorkRow[], sections: WorkBoard['sections'] = []): WorkBoard {
  const order: WorkRow['state'][] = ['needs_input', 'working', 'queued', 'suspended', 'review', 'done']
  return {
    board: order.map((state) => ({ state, count: rows.filter((r) => r.state === state).length, attention: 0, rows: rows.filter((r) => r.state === state) }))
      .filter((g) => g.count > 0),
    sections, completeness: 'complete', attention: 0, loadedAt: 0,
  }
}

export function task(id: string, title: string, listId: string, over: Partial<TaskItem> = {}): TaskItem {
  return { id, title, status: 'open', task_list_id: listId, updated_at: '2026-11-12T08:00:00Z', ...over }
}

export function taskList(id: string, name: string, over: Partial<TaskListItem> = {}): TaskListItem {
  return { id, name, project_id: PID, loop_id: '', ...over }
}

export function loop(id: string, name: string, over: Partial<Loop> = {}): Loop {
  return { id, name, task: name, status: 'running', kind: 'goal', project_id: PID, created_at: '2026-11-10T08:00:00Z', ...over } as unknown as Loop
}

export const TOLD: AlwaysOnResponse = {
  items: [], project_id: PID, counts: { total: 0, always_skills: 0, project_instructions: 0 }, always_skill_mechanism: '',
  project_block: {
    text: 'You are working in the project Garden.\n- Project brief (the goal/scope/background of this project — treat as foundational context): Grow the spring beds.\n- Decisions so far (1, newest last):\n    • Plant late.',
    parts: { brief: 1, overview: 0, decisions: 1, open_questions: 0, out_of_scope: 0 },
  },
}

export interface PageFixture {
  /** The project read's answer; `'pending'` never answers, nor does the list's. */
  project?: ProjectItem | Error | 'pending'
  waiting?: WaitingItem[] | Error
  unreadable?: WaitingKind[]
  running?: RunningRow[] | Error
  board?: WorkBoard | Error
  lists?: TaskListItem[] | Error
  tasks?: TaskItem[] | Error
  sessions?: ChatSessionSummary[] | Error
  loops?: Loop[] | Error
  told?: AlwaysOnResponse | Error
  proposals?: { id: string; kind: string; project_id: string }[]
  defaultProjectId?: string
  pins?: string[]
  query?: Record<string, string>
  /** What `browse-dirs` answers for the folder: its listing, or a failure with this status. */
  folder?: 'there' | number
  /** Settings › Legibility writes the context into files in the folder. */
  adapters?: boolean
}

export const navigate = vi.fn()

/** A failed read with *status*, as `api`'s fetch throws it. */
export function failed(status: number, message = `HTTP ${status}`): ApiError {
  return new ApiError(message, status)
}

/** Mounts the page over *f*. Returns the spies a test asserts on and the page's live address. */
export function mountPage(f: PageFixture = {}) {
  const answer = <T,>(v: T | Error) => (v instanceof Error ? Promise.reject(v) : Promise.resolve(v))
  const garden = f.project ?? project(PID, 'Garden', { workspace_dir: '', folder: null })
  const never = () => new Promise<never>(() => {})
  vi.spyOn(api, 'project').mockImplementation(() => (garden === 'pending' ? never() : answer(garden)))
  vi.spyOn(api, 'projects').mockImplementation(() => (garden === 'pending' ? never() : Promise.resolve(garden instanceof Error ? [] : [garden])))
  let settings = { default_project_id: f.defaultProjectId ?? '', pinned_project_ids: f.pins ?? [] }
  vi.spyOn(api, 'projectSettings').mockImplementation(async () => settings)
  const updateSettings = vi.spyOn(api, 'updateProjectSettings').mockImplementation(async (body) => {
    settings = { ...settings, ...body } as typeof settings
    return settings
  })
  const updateProject = vi.spyOn(api, 'updateProject').mockImplementation(async (id, body) => ({ ...(garden as ProjectItem), ...body, id }))
  vi.spyOn(api, 'waiting').mockImplementation(() => answer(f.waiting instanceof Error ? f.waiting : { waiting: f.waiting ?? [], unreadable: f.unreadable ?? [] }))
  vi.spyOn(api, 'inboxOpen').mockResolvedValue([])
  vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [], lastReview: null })
  vi.spyOn(api, 'running').mockImplementation(() => answer(f.running instanceof Error ? f.running : { running: f.running ?? [], finished: [] }))
  vi.spyOn(api, 'chatSessions').mockImplementation(() => answer(f.sessions ?? []))
  vi.spyOn(api, 'uLoops').mockImplementation(() => answer((f.loops ?? []) as Awaited<ReturnType<typeof api.uLoops>>))
  vi.spyOn(api, 'approvals').mockResolvedValue([])
  vi.spyOn(api, 'projectWork').mockImplementation(() => answer(f.board ?? board([])))
  vi.spyOn(api, 'taskLists').mockImplementation(() => answer(f.lists ?? []))
  vi.spyOn(api, 'allTasks').mockImplementation(() => answer(f.tasks instanceof Error ? f.tasks : { tasks: f.tasks ?? [], total: (f.tasks ?? []).length, complete: true, owner: '' }) as ReturnType<typeof api.allTasks>)
  vi.spyOn(api, 'alwaysOn').mockImplementation(() => answer(f.told ?? TOLD))
  vi.spyOn(api, 'learningProposals').mockResolvedValue({
    rows: (f.proposals ?? []) as never, total: (f.proposals ?? []).length, by_kind: {}, by_tier: {}, flagged: 0, unrenderable: [], bulk_acceptable: 0,
  })
  vi.spyOn(api, 'personalclawConfig').mockResolvedValue({ legibility: { context_adapters: !!f.adapters } } as Awaited<ReturnType<typeof api.personalclawConfig>>)
  vi.spyOn(api, 'browseDirs').mockImplementation(() => (f.folder === undefined || f.folder === 'there'
    ? Promise.resolve({ path: '', entries: [] } as unknown as Awaited<ReturnType<typeof api.browseDirs>>)
    : Promise.reject(failed(f.folder))))
  const address = { query: { ...(f.query ?? {}) } }
  /** Every address write, with its options, so a test sees which ones replaced their entry. */
  const writes: { patch: Record<string, string | null | undefined>; opts?: { replace?: boolean } }[] = []
  function Host() {
    const [query, setQueryState] = useState<Record<string, string>>(address.query)
    const setQuery = (patch: Record<string, string | null | undefined>, opts?: { replace?: boolean }) => {
      writes.push({ patch, opts })
      setQueryState((q) => {
        const next = { ...q }
        for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
        address.query = next
        return next
      })
    }
    return <ProjectsSection sub={PID} navigate={navigate} navEpoch={0} query={query} setQuery={setQuery} />
  }
  const view = render(<><Host /><DialogHost /></>)
  return { ...view, updateSettings, updateProject, address, writes }
}
