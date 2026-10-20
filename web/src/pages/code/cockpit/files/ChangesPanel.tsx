import { useEffect, useState } from 'react'
import { GitBranch } from 'lucide-react'
import { Button } from '../../../../ui/Button'
import { Eyebrow } from '../../../../ui/Eyebrow'
import { api, type LoopMerge } from '../../../../lib/api'
import { useVisiblePoll } from '../../../../lib/useVisiblePoll'
import { useGitStatus } from '../../../files/filesData'
import { MergedWork } from '../../MergeReview'

// ── The cockpit's changes ──────────────────────────────────────────────────────────────────────
//
// The workspace's working changes against HEAD and its recent commits, each opening in the editor,
// with the run's merged work beneath.

/** Git changes panel — the workspace's branch + changed files, so the human can
 *  review what the agent did. Clicking a file opens it in the editor. Polls on
 *  mount + a manual refresh; git status is keyed by path. */
export function ChangesPanel({ ws, running, isProjectDir = false, merges = [] }: { ws: string; running: boolean; isProjectDir?: boolean; merges?: LoopMerge[] }) {
  const [nonce, setNonce] = useState(0)
  // A no-workspace project's "ws" is its engine files dir, which is NOT a git repo,
  // so git status there is meaningless — running it would render a misleading
  // "(no branch) · working tree clean". Skip git entirely + say so plainly.
  const gitWs = isProjectDir ? null : (ws || null)
  const { branch, statuses, state, repoRoot } = useGitStatus(gitWs, nonce)
  // While the worker runs it edits files + the supervisor commits each stage; the
  // status + history would otherwise stay stale until a manual refresh. Slow-poll
  // (visible only) so the Changes tab tracks the work live, like the file tree.
  useVisiblePoll(() => { if (gitWs) setNonce((n) => n + 1) }, running && gitWs ? 8000 : null)
  // A manual editor save → refresh the Changes tab now (the poll only runs while
  // running; a save on a paused/idle project would otherwise show stale status).
  useEffect(() => {
    const onSaved = () => { if (gitWs) setNonce((n) => n + 1) }
    window.addEventListener('ne:code-file-saved', onSaved)
    return () => window.removeEventListener('ne:code-file-saved', onSaved)
  }, [gitWs])
  // 🔴 A NEGATIVE ABOUT THE ROOT IS NOT A NEGATIVE ABOUT THE WORKSPACE. `repoRoot` is empty
  // whenever the bound root ITSELF is not a repo — but a project checked out one level down
  // is the normal layout, and this panel then told the user their work "isn't version-tracked"
  // when it is (#428). The Files page merely showed nothing; this surface stated the negative.
  // So on that branch, ask the listing which children ARE repo roots (the flag api_file_list
  // now returns) and name them instead of denying version control outright. One request, only
  // on the branch that would otherwise make a false claim.
  const [nestedRepos, setNestedRepos] = useState<string[]>([])
  useEffect(() => {
    if (isProjectDir || !ws || state !== 'loaded' || repoRoot) { setNestedRepos([]); return }
    let alive = true
    api.fileList(ws)
      .then((r) => { if (alive) setNestedRepos((r.entries || []).filter((e) => e.is_dir && e.repo).map((e) => e.name)) })
      .catch(() => { if (alive) setNestedRepos([]) })
    return () => { alive = false }
  }, [ws, state, repoRoot, isProjectDir])

  // The per-stage commit history (the supervisor checkpoints each passed stage)
  // — a reviewable timeline of what each stage produced.
  const [commits, setCommits] = useState<{ hash: string; subject: string; relative: string }[]>([])
  useEffect(() => {
    if (!gitWs) { setCommits([]); return }
    let alive = true
    api.fileGitLog(gitWs, 20).then((r) => { if (alive) setCommits(r.commits || []) }).catch(() => { if (alive) setCommits([]) })
    return () => { alive = false }
  }, [gitWs, nonce])

  // A no-workspace (greenfield) project: its files live in the engine dir with no
  // git, so there's no diff/branch history to show — say so honestly instead of a
  // misleading "clean working tree".
  if (isProjectDir) return <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">This project has no git workspace — changes aren't version-tracked. Files the worker created are in the Files tab.</p>
  if (!ws) return <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">No workspace directory.</p>
  // A bound brownfield workspace that ISN'T a git repo (empty repoRoot once loaded):
  // there's no branch/diff/history to show, and "working tree is clean" would be a
  // lie (it's just not version-controlled). Say so honestly — distinct from a clean
  // repo + from a transient read error.
  if (state === 'loaded' && !repoRoot) {
    return (
      <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">
        {nestedRepos.length > 0
          // Says what is TRUE (the root isn't a repo) and where the version control actually
          // is, instead of asserting the workspace has none.
          ? <>The workspace <em>root</em> isn’t a git repository, but {nestedRepos.length === 1 ? 'a repository inside it is' : `${nestedRepos.length} repositories inside it are`}: {nestedRepos.slice(0, 4).join(', ')}{nestedRepos.length > 4 ? ', …' : ''}. Open one in the Files tab to see its branch and changes.</>
          : <>This workspace isn’t a git repository — changes aren’t version-tracked. Browse the files in the Files tab.</>}
      </p>
    )
  }
  const entries = Object.entries(statuses)
  // git status codes → a short, readable label + tone.
  const label = (code: string): { text: string; color: string } => {
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
  // git-status keys are absolute paths whose root may differ from `ws` by a
  // realpath prefix (e.g. /tmp vs /private/tmp on macOS). Relativize by the
  // workspace's LAST path segment so the display is clean regardless.
  const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
  const rel = (p: string) => {
    const marker = `/${wsBase}/`
    const i = wsBase ? p.lastIndexOf(marker) : -1
    return i >= 0 ? p.slice(i + marker.length) : p
  }
  // Open a working-vs-HEAD DIFF (under `ws`, not the git realpath, so the editor's
  // workspace-scoped guards apply). A modifier-less click reviews the change as a
  // diff; this is the natural action in the Changes tab.
  const open = (gitPath: string, code: string) => {
    const r = rel(gitPath)
    const path = `${ws.replace(/\/$/, '')}/${r}`
    // A deleted file has no working copy — tell the diff so it treats the missing
    // read as expected (all-removed) rather than a load error.
    const deleted = code.includes('D')
    window.dispatchEvent(new CustomEvent('ne:code-open-diff', { detail: { name: r.split('/').pop(), path, deleted } }))
  }
  return (
    <div className="flex flex-col">
      <div data-type="caption" className="flex items-center justify-between gap-2 px-3 py-2 text-on-surface-low">
        <span className="inline-flex items-center gap-1.5 min-w-0">
          <GitBranch size={12} className="shrink-0" />
          <span className="truncate text-on-surface-var">{branch || (state === 'loaded' ? '(no branch)' : '…')}</span>
        </span>
        {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
        <Button variant="ghost" size="xs" onClick={() => setNonce((n) => n + 1)} className="shrink-0 px-1.5 text-[0.75rem] text-on-surface-low">refresh</Button>
      </div>
      {/* A merge leaves the tree clean, so what the loop merged is said here, not left to the log. */}
      <MergedWork merges={merges} />
      {entries.length === 0 ? (
        // Distinguish the three empty-map states: a failed/in-flight fetch must NOT
        // masquerade as a genuinely clean tree (the old bug — all three read "clean").
        state === 'loading' || state === 'idle' ? (
          <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">Loading changes…</p>
        ) : state === 'error' ? (
          <div data-type="body-s" className="flex flex-col items-center gap-2 px-3 py-6 text-center">
            <span style={{ color: 'var(--color-warn)' }}>Couldn't read git status for this workspace.</span>
            <Button variant="ghost" size="xs" onClick={() => setNonce((n) => n + 1)}>Retry</Button>
          </div>
        ) : (
          <p data-type="body-s" className="px-3 py-6 text-center text-on-surface-low">No uncommitted changes — the working tree is clean.</p>
        )
      ) : (
        <div className="flex flex-col">
          {/* count label — at-a-glance magnitude + parity with the "History" label
              below, and it distinguishes working changes from the commit list. */}
          <Eyebrow className="px-3 pt-1 pb-1">
            Changes ({entries.length})
          </Eyebrow>
          {entries.map(([path, code]) => {
            const l = label(code)
            // Raw: a full-row clickable list item, which no primitive draws.
            return (
              <button key={path} type="button" onClick={() => open(path, code)}
                data-type="body-s" className="flex items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-surface-high">
                <span data-type="caption" className="w-[58px] shrink-0" style={{ color: l.color }}>{l.text}</span>
                <span className="min-w-0 truncate font-mono text-on-surface-var" title={path}>{rel(path)}</span>
              </button>
            )
          })}
        </div>
      )}
      {/* per-stage commit history — the reviewable timeline (supervisor + worker
          commits). Shown below the working changes. */}
      {commits.length > 0 && (
        <div className="mt-1 border-t border-outline-variant/40">
          <div className="flex items-baseline justify-between gap-2 px-3 pt-2 pb-1">
            <Eyebrow as="span">History</Eyebrow>
            {/* The log is capped server-side (20); when we get a full page back there
                are almost certainly older commits not shown — say so rather than let
                the list read as the repo's entire history. */}
            {commits.length >= 20 && <span data-type="caption" className="text-on-surface-low/70">latest 20</span>}
          </div>
          {commits.map((c) => (
            // Raw: a full-row clickable list item, which no primitive draws.
            <button key={c.hash} type="button"
              onClick={() => window.dispatchEvent(new CustomEvent('ne:code-open-commit', { detail: { hash: c.hash, subject: c.subject, path: ws } }))}
              data-type="body-s" className="flex w-full items-baseline gap-2 px-3 py-1 text-left transition-colors hover:bg-surface-high">
              <span data-type="caption" className="shrink-0 font-mono text-on-surface-low">{c.hash}</span>
              <span className="min-w-0 flex-1 truncate text-on-surface-var" title={c.subject}>{c.subject}</span>
              <span data-type="caption" className="shrink-0 text-on-surface-low">{c.relative}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
