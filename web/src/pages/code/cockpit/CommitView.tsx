import { useEffect, useState } from 'react'
import { Loader2, X, GitBranch, RotateCcw } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import { FieldError } from '../../../ui/forms'
import { Centered } from '../../../ui/Centered'
import { UnifiedDiff } from '../../../ui/UnifiedDiff'
import { api } from '../../../lib/api'

// ── A commit in the cockpit ────────────────────────────────────────────────────────────────────
//
// One commit of the workspace, opened from the Changes list: its diff, a commit with nothing to
// show said as such, a hash that no longer resolves said as that, and a failed read with a retry.

/** A whole-commit unified diff (git show), rendered as a color-coded patch — for
 *  reviewing what a stage's checkpoint commit changed (History → click). */
export function CommitView({ ws, hash, subject, onClose }: { ws: string; hash: string; subject: string; onClose: () => void }) {
  // null = loading; '' = loaded-but-empty (no textual diff, e.g. a merge or an
  // empty checkpoint); 'ERR' sentinel = load failed (offer Retry). A real diff is
  // any other string.
  const [diff, setDiff] = useState<string | null>(null)
  const [truncated, setTruncated] = useState(false)
  // The hash did not resolve to a commit in this repo (stale ref after a force-push/
  // rebase, or a workspace re-pointed away) — distinct from a real empty-diff commit.
  const [notFound, setNotFound] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let alive = true
    setDiff(null)
    setTruncated(false)
    setNotFound(false)
    api.fileGitCommit(ws, hash).then((r) => {
      if (!alive) return
      if (r.found === false) { setNotFound(true); setDiff('') }
      else { setDiff(r.diff || ''); setTruncated(!!r.truncated) }
    }).catch(() => { if (alive) setDiff('ERR') })
    return () => { alive = false }
  }, [ws, hash, attempt])
  const failed = diff === 'ERR'
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1">
        <span data-type="caption" className="inline-flex min-w-0 items-center gap-1.5 text-on-surface-low">
          <GitBranch size={12} className="shrink-0" /> <span className="font-mono">{hash}</span> <span className="truncate text-on-surface-var">{subject}</span>
        </span>
        <IconButton icon={X} label="Close commit" onClick={onClose} size={24} iconSize={13} className="shrink-0" />
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-surface p-2">
        {diff === null ? (
          <Centered><Loader2 size={16} className="animate-spin text-on-surface-low" /></Centered>
        ) : failed ? (
          <Centered>
            <div data-type="body-s" className="flex flex-col items-center gap-2 px-4 text-center">
              <FieldError>Couldn't load this commit.</FieldError>
              <Button variant="ghost-accent" size="xs" onClick={() => setAttempt((n) => n + 1)}><RotateCcw size={13} /> Try again</Button>
            </div>
          </Centered>
        ) : notFound ? (
          <Centered><p data-type="body-s" className="px-4 text-center text-on-surface-low">This commit is no longer in the workspace — its history may have been rewritten (rebase / force-push) or the workspace re-pointed.</p></Centered>
        ) : diff === '' ? (
          <Centered><p data-type="body-s" className="px-4 text-center text-on-surface-low">This commit has no textual changes (e.g. a merge or an empty checkpoint).</p></Centered>
        ) : (
          <>
            {/* Same patch renderer the skill-refinement approval surface uses — one
                marker→token map, so an added line is the same green in both places. */}
            <UnifiedDiff patch={diff} label={`Diff for commit ${hash}`} />
            {truncated && (
              <p data-type="caption" className="mt-2 px-1 text-on-surface-low/80">
                Diff truncated — this commit is large; only the first part is shown. Use the workspace terminal (<span className="font-mono">git show {hash}</span>) for the full patch.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
