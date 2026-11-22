import { workspaceRelative } from '../workspacePath'

// ── A workspace's uncommitted changes, as the cockpit lists them ──────────────────────────────
//
// What the Changes tab and the review pane both say about a change: its status in a word and a
// tone, its path inside the workspace, and the diff it opens.

/** A git status code as a short label and its tone. */
export function changeLabel(code: string): { text: string; color: string } {
  const c = code.trim()
  if (c === '??') return { text: 'new', color: 'var(--color-ok)' }
  // UNMERGED (conflict) — porcelain XY where either side is 'U', or the AA/DD
  // double forms. The worker merges parallel task worktrees, so conflicts are a real
  // state here, and they need RESOLUTION — must not read as a bland "modified".
  // Checked before the D/A letter rules (DD/AU/UD all contain D or A).
  if (c.includes('U') || c === 'AA' || c === 'DD') return { text: 'conflict', color: 'var(--color-danger)' }
  if (c.includes('D')) return { text: 'deleted', color: 'var(--color-danger)' }
  if (c.includes('A')) return { text: 'added', color: 'var(--color-ok)' }
  if (c.includes('R')) return { text: 'renamed', color: 'var(--color-primary)' }
  return { text: 'modified', color: 'var(--color-warn)' }
}

/** A git status path inside the workspace. Git-status keys are absolute paths whose root may differ
 *  from `ws` as git resolves it (/tmp vs /private/tmp on macOS, or the status's own `repoRoot`,
 *  *root*), so the workspace is read in each of its forms (`workspacePath`). A path outside it is
 *  shown as git names it. */
export function changePath(ws: string, gitPath: string, root = ''): string {
  return workspaceRelative(ws, gitPath, root) ?? gitPath
}

/** The diff a change opens: working copy against HEAD, under `ws` rather than the git realpath so
 *  the editor's workspace-scoped guards apply. A deleted file has no working copy, so the diff is
 *  told to read its missing side as expected (all removed) rather than as a load error. */
export function changeDiff(ws: string, gitPath: string, code: string, root = ''): { path: string; name: string; deleted: boolean } {
  const rel = changePath(ws, gitPath, root)
  return { path: `${ws.replace(/\/$/, '')}/${rel}`, name: rel.split('/').pop() || rel, deleted: code.includes('D') }
}

/** Opens a change's diff in the review pane. */
export function openChange(ws: string, gitPath: string, code: string, root = '') {
  window.dispatchEvent(new CustomEvent('ne:code-open-diff', { detail: changeDiff(ws, gitPath, code, root) }))
}
