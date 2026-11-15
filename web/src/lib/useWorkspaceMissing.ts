import { useEffect, useState } from 'react'
import { ApiError, api } from './api'

/** Whether a failed `browse-dirs` read means the folder is GONE.
 *
 *  Only a 404. browse-dirs answers 404 for a path that does not exist (or is a file), 403 for a
 *  protected location and 400 for a permission failure — in both of those the folder most likely
 *  still exists — and a network blip or a 5xx says nothing about the folder at all. So this keys on
 *  the STATUS, never the message: the message is human copy that gets reworded, and the code
 *  cockpit's old `message.includes('no such directory')` would have gone silent on the first
 *  rewording without a single test noticing. */
export function isWorkspaceGone(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404
}

/** What the disk says of a bound folder: `ok` once `browse-dirs` read it, `missing` once it answered
 *  404 (`isWorkspaceGone`), `unreadable` for any other failure (a protected location, a permission
 *  failure, a 5xx: the folder may well still be there, so it is never called missing), and
 *  `unknown` while the answer is out, with no path, or while `enabled` is off. */
export type WorkspaceState = 'ok' | 'missing' | 'unreadable' | 'unknown'

/** `useWorkspaceMissing`'s read, with the failure that is not a 404 kept apart as `unreadable`, for
 *  a surface that says each in its own words (a project's page: "PersonalClaw can't read this folder
 *  right now. It may still be there."). `recheck` re-asks the disk whenever it changes. */
export function useWorkspaceState(
  path: string | null | undefined,
  { enabled = true, recheck }: { enabled?: boolean; recheck?: unknown } = {},
): WorkspaceState {
  const [state, setState] = useState<WorkspaceState>('unknown')
  const target = (path ?? '').trim()
  useEffect(() => {
    if (!target || !enabled) { setState('unknown'); return }
    let alive = true
    api.browseDirs(target)
      .then(() => { if (alive) setState('ok') })
      .catch((e) => { if (alive) setState(isWorkspaceGone(e) ? 'missing' : 'unreadable') })
    return () => { alive = false }
  }, [target, enabled, recheck])
  return state
}

/** True once `browse-dirs` has answered 404 for `path`: the bound workspace folder no longer exists.
 *
 *  `false` while the answer is unknown, while `enabled` is off, and for every failure that is not a
 *  404, so a surface never warns about a folder that is merely unreadable. The case this exists for
 *  is real and quiet: a container recreated without the folder on its volume, or a repo moved on
 *  disk, leaves a project pointing at a path that is gone, and the page went on showing that path as
 *  if it were fine. `recheck` re-asks the disk whenever it changes — pass the record the path belongs
 *  to, so a refreshed project re-probes. */
export function useWorkspaceMissing(
  path: string | null | undefined,
  opts: { enabled?: boolean; recheck?: unknown } = {},
): boolean {
  return useWorkspaceState(path, opts) === 'missing'
}
