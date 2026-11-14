import { vi } from 'vitest'
import { render } from '@testing-library/react'
import { useState } from 'react'
import { api, type ChatSessionSummary, type ProjectItem, type RunningRow, type WaitingItem, type WaitingKind } from '../lib/api'
import { ProjectsSection } from '../pages/projects/ProjectsSection'

/** The Projects list over fixtures: every read it makes, answered from what a test gives it, and its
 *  address kept in the page's own `query` as the router keeps it. Shared by the list's tests. */

export const NOW = Date.parse('2026-11-12T10:00:00Z')
const iso = (minsAgo: number) => new Date(NOW - minsAgo * 60_000).toISOString()

export function project(id: string, name: string, over: Partial<ProjectItem> = {}): ProjectItem {
  return { id, name, status: 'active', is_builtin: false, workspace_dir: '', brief: '', updated_at: iso(600), made_for: null, folder_state: '', ...over }
}

export function waiting(id: string, kind: WaitingKind, projectId: string, over: Partial<WaitingItem> = {}): WaitingItem {
  return {
    id, kind, work: `loop:${id}`, session: '', brief: {}, consequence: 'blocks', risk: '', asked_at: iso(6), age_secs: 360,
    expires_at: null, current: true, interrupt: 'none', answer: { method: 'POST', path: '/x', takes: [], body: {} },
    route: `loops/${id}`, project_id: projectId, loop_kind: 'goal', ...over,
  } as WaitingItem
}

export function running(work: string, name: string, projectId: string, over: Partial<RunningRow> = {}): RunningRow {
  return {
    work, kind: 'loop', session: '', phase: 'tool', line: '', since: iso(12), name, agent: '', started_at: iso(30),
    route: `code/${work.split(':')[1]}`, blocked: false, waiting: [], project_id: projectId, loop_kind: 'code', ...over,
  } as RunningRow
}

export function chat(key: string, title: string, projectId: string, over: Partial<ChatSessionSummary> = {}): ChatSessionSummary {
  return { key, title, project_id: projectId, origin: 'manual', last_activity_ts: iso(30), run_state: { session: key, phase: 'idle' }, ...over } as unknown as ChatSessionSummary
}

export interface ListFixture {
  projects: ProjectItem[] | Error
  waiting?: WaitingItem[] | Error
  unreadable?: WaitingKind[]
  running?: RunningRow[] | Error
  sessions?: ChatSessionSummary[] | Error
  loops?: unknown[]
  defaultProjectId?: string
  pins?: string[]
  /** The settings read failing (no pins and no default known). */
  settings?: Error
  query?: Record<string, string>
}

export const navigate = vi.fn()

/** Mounts the list over *f*. Returns the spies a test asserts on and the page's live address. */
export function mountList(f: ListFixture) {
  const answer = <T,>(v: T | Error) => (v instanceof Error ? Promise.reject(v) : Promise.resolve(v))
  vi.spyOn(api, 'projects').mockImplementation(() => answer(f.projects))
  let settings = { default_project_id: f.defaultProjectId ?? '', pinned_project_ids: f.pins ?? [] }
  vi.spyOn(api, 'projectSettings').mockImplementation(async () => { if (f.settings) throw f.settings; return settings })
  const updateSettings = vi.spyOn(api, 'updateProjectSettings').mockImplementation(async (body) => {
    settings = { ...settings, ...body } as typeof settings
    return settings
  })
  vi.spyOn(api, 'waiting').mockImplementation(() => answer(f.waiting instanceof Error ? f.waiting : { waiting: f.waiting ?? [], unreadable: f.unreadable ?? [] }))
  vi.spyOn(api, 'inboxOpen').mockResolvedValue([])
  vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [], lastReview: null })
  vi.spyOn(api, 'running').mockImplementation(() => answer(f.running instanceof Error ? f.running : { running: f.running ?? [], finished: [] }))
  vi.spyOn(api, 'chatSessions').mockImplementation(() => answer(f.sessions ?? []))
  vi.spyOn(api, 'uLoops').mockResolvedValue((f.loops ?? []) as Awaited<ReturnType<typeof api.uLoops>>)
  vi.spyOn(api, 'approvals').mockResolvedValue([])
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
    return <ProjectsSection sub="" navigate={navigate} navEpoch={0} query={query} setQuery={setQuery} />
  }
  const view = render(<Host />)
  return { ...view, updateSettings, address, writes }
}
