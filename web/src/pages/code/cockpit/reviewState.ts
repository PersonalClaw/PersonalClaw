// ── What a code cockpit keeps in its address ──────────────────────────────────────────────────
//
// The cockpit's address (`#/code/<id>?…`) holds the state a person sets by clicking, so a reload,
// Back, a copied address or a `personalclaw://` link opens the same review:
//
//   - `file`, `diff` or `commit`: the one thing the center shows. A file and a diff are written as
//     their path inside the workspace; a commit as its hash.
//   - `pane=files|changes`: the files rail's tab.
//   - `task=<id>`: the task open in the Tasks panel.
//
// Every one of these only shows something. A commit hash goes to the read of that commit and
// nowhere else, and an open task's steer box starts empty. The desktop's link parser holds a link
// to the same rules; the page holds the address to them again, since an address can be typed.

/** The longest path the address carries, as the link parser caps it. */
const PATH_CAP = 1024
/** C0 controls, DEL, C1 controls, and the direction overrides and isolates. */
const UNSAFE_PATH = /[\u0000-\u001F\u007F-\u009F\u202A-\u202E\u2066-\u2069]/
/** A commit, by its hash: 7 to 40 lowercase hex characters, so no ref name, range or revision syntax. */
const COMMIT = /^[0-9a-f]{7,40}$/

/** Whether the address can hold a commit by `hash`. Git's short hash can be 4 to 6 characters
 *  where a repository abbreviates that far; such a commit is shown, and the address names none. */
export function isReviewCommit(hash: string): boolean {
  return COMMIT.test(hash)
}

/** Whether `p` is a path inside the workspace: relative, `/`-separated, with no `..` segment, no
 *  home or drive prefix, no backslash and no control character. */
export function isReviewPath(p: string): boolean {
  if (!p || p.length > PATH_CAP || UNSAFE_PATH.test(p)) return false
  if (/^[/~]/.test(p) || /^[A-Za-z]:/.test(p) || p.includes('\\')) return false
  return p.split('/').every((part) => part !== '..')
}

const trimSlash = (ws: string) => ws.replace(/\/$/, '')

/** A workspace file's path inside the workspace, or null for one outside it. The file tree names
 *  files by their resolved path (`/private/tmp/…` for `/tmp/…`), so the workspace is also found by
 *  its last segment, as the Changes panel does. */
export function toReviewPath(ws: string, abs: string): string | null {
  if (!ws || !abs) return null
  const root = trimSlash(ws)
  let rel: string | null = null
  if (abs.startsWith(`${root}/`)) rel = abs.slice(root.length + 1)
  else {
    const base = root.split('/').pop() || ''
    const marker = `/${base}/`
    const i = base ? abs.lastIndexOf(marker) : -1
    if (i >= 0) rel = abs.slice(i + marker.length)
  }
  return rel && isReviewPath(rel) ? rel : null
}

/** The workspace path of a path inside it. */
export function fromReviewPath(ws: string, rel: string): string {
  return `${trimSlash(ws)}/${rel}`
}

export type Center = { kind: 'file' | 'diff'; path: string } | { kind: 'commit'; hash: string }

/** The one thing the address says the center shows, null for none, or 'invalid' for an address
 *  that names more than one or one outside its rules. */
export function centerFromQuery(query: Record<string, string>): Center | null | 'invalid' {
  const named = (['file', 'diff', 'commit'] as const).filter((k) => !!query[k])
  if (named.length === 0) return null
  if (named.length > 1) return 'invalid'
  const key = named[0]
  const value = query[key]
  if (key === 'commit') return isReviewCommit(value) ? { kind: 'commit', hash: value } : 'invalid'
  return isReviewPath(value) ? { kind: key, path: value } : 'invalid'
}

/** The address patch that names `center` and drops the other two. */
export function centerPatch(center: Center | null): Record<'file' | 'diff' | 'commit', string | null> {
  return {
    file: center?.kind === 'file' ? center.path : null,
    diff: center?.kind === 'diff' ? center.path : null,
    commit: center?.kind === 'commit' ? center.hash : null,
  }
}
