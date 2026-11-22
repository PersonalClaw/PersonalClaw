/** A path inside a code workspace, read one way everywhere in the code pages.
 *
 *  Six places used to find the workspace in an absolute path by searching for `/<its folder name>/`
 *  and taking the LAST match, so with the workspace `/work/feed` the changed file
 *  `/work/feed/src/feed/a.py` became `a.py`, and the cockpit opened `/work/feed/a.py`. Any Python src
 *  layout (`feed/src/feed/`) or Go `cmd/<name>/` folder hit it, and three of the six also counted
 *  `/work/feedback/x` as inside `/work/feed`. Now the workspace root is stripped as a PREFIX, with its
 *  trailing slash, in each form a path may name it by, and a path under none of them is outside. */

const trim = (p: string) => p.replace(/\/+$/, '')
const folderOf = (p: string) => trim(p).split('/').pop() || ''

/** The forms a workspace root is named by: as written; the resolved root a read gave (git's
 *  `repoRoot`, file search's `root`) when it names the same folder, since a repository root above
 *  the workspace is not the workspace; and each with or without the `/private` macOS resolves
 *  `/tmp`, `/var` and `/etc` to. */
function rootsOf(ws: string, resolved: string): string[] {
  const roots: string[] = []
  for (const r of [ws, resolved && folderOf(resolved) === folderOf(ws) ? resolved : '']) {
    const root = trim(r)
    if (!root) continue
    for (const form of [root, root.startsWith('/private/') ? root.slice('/private'.length) : `/private${root}`]) {
      if (!roots.includes(form)) roots.push(form)
    }
  }
  return roots
}

/** *abs* as a path inside the workspace *ws*: `''` for the workspace itself, or null for a path
 *  outside it (a sibling whose name starts with the workspace's included). *resolved* is the
 *  workspace's resolved root, when a read gave one. */
export function workspaceRelative(ws: string, abs: string, resolved = ''): string | null {
  if (!ws || !abs) return null
  for (const root of rootsOf(ws, resolved)) {
    if (abs === root) return ''
    if (abs.startsWith(`${root}/`)) return abs.slice(root.length + 1)
  }
  return null
}

/** Whether *abs* is inside the workspace *ws* (the workspace itself included). */
export function underWorkspace(ws: string, abs: string, resolved = ''): boolean {
  return workspaceRelative(ws, abs, resolved) !== null
}

/** *p* under the workspace as *ws* names it (one file opened from two sources is one tab), or *p*
 *  as it is when it is outside the workspace. */
export function inWorkspace(ws: string, p: string, resolved = ''): string {
  const rel = workspaceRelative(ws, p, resolved)
  if (rel === null) return p
  return rel ? `${trim(ws)}/${rel}` : trim(ws)
}

/** Whether an open of a file, a diff or a commit is the user taking the wheel, so the cockpit
 *  stops following the worker: not the worker's own open (`_follow`), nor one the address or a link
 *  restores (`_restore`), which a reload or a link used to make turn following off. */
export function takesTheWheel(detail: unknown): boolean {
  const open = (detail ?? {}) as { _follow?: boolean; _restore?: boolean }
  return !open._follow && !open._restore
}
