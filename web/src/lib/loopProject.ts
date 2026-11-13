import { useEffect, useMemo, useRef } from 'react'
import { api, type ProjectItem } from './api'
import { invalidateKeys, useQuery } from './data'

/** ── Which project a loop is in, on the page ─────────────────────────────────────────────────
 *
 *  The gateway's one rule (`loop_store.loop_project_id`), so every surface that files a loop under
 *  a project files it under the same one: its `project_id` (the project it was launched into) when
 *  the project list knows it, else its `tasks_project_id` (the project made for its tasks when it
 *  was launched into none) when that is known, else "" ("No project"). A code session whose
 *  project was made for it is in that project, not in none.
 *
 *  *knownIds* is the project list's ids, or `null` while the list has not been read: the first id
 *  the loop names stands until the list answers (the gateway checks each one, and reads none when
 *  it cannot), and the loop moves to its one project then. */
export function loopProjectId(
  loop: { project_id?: unknown; tasks_project_id?: unknown },
  knownIds: ReadonlySet<string> | null,
): string {
  for (const id of [loop.project_id, loop.tasks_project_id]) {
    if (typeof id !== 'string' || id === '') continue
    if (knownIds === null || knownIds.has(id)) return id
  }
  return ''
}

/** The project list's own query key: one read for every surface that names a project. */
export const PROJECT_LIST_KEY = 'projects:list'

export interface ProjectList {
  /** Each project by its id. Empty until the list is read. */
  byId: ReadonlyMap<string, ProjectItem>
  /** The ids the list knows, or `null` until it is read (`loopProjectId`). */
  ids: ReadonlySet<string> | null
  /** The read's error, or null: a surface that must say the list could not load reads it. */
  error: unknown
}

/** The project list, read once under `PROJECT_LIST_KEY`, by id. A read not yet answered, or one
 *  that failed, is no list: every chip waits for a name, and `loopProjectId` takes the first id a
 *  loop names until the list answers.
 *
 *  *wanted* is the ids the reader names (a loop's two, a row's): one the list does not know is read
 *  again once, so a project made while the page is open (the one a code session's start makes) is
 *  named as soon as it is there, and one that is gone is not read for again. */
export function useProjectList(wanted: readonly unknown[] = []): ProjectList {
  // `async`, so a call that throws as it is made is a failed read, never a thrown render.
  const { data, error } = useQuery<ProjectItem[]>(PROJECT_LIST_KEY, async () => api.projects(), { persist: true })
  const asked = useRef(new Set<string>())
  const known = Array.isArray(data) ? new Set(data.map((p) => p?.id)) : null
  const unknown = known === null ? '' : [...new Set(wanted)]
    .filter((id): id is string => typeof id === 'string' && id !== '' && !known.has(id) && !asked.current.has(id))
    .sort().join('\n')
  useEffect(() => {
    if (!unknown) return
    for (const id of unknown.split('\n')) asked.current.add(id)
    // An invalidation, not a refresh: a read already on the wire may predate the project, and an
    // invalidation starts a new one rather than joining it.
    invalidateKeys(PROJECT_LIST_KEY)
  }, [unknown])
  return useMemo(() => {
    if (!Array.isArray(data)) return { byId: new Map(), ids: null, error }
    const byId = new Map(data.filter((p) => p && typeof p.id === 'string').map((p) => [p.id, p]))
    return { byId, ids: new Set(byId.keys()), error }
  }, [data, error])
}
