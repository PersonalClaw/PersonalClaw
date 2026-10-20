import { useCallback, useEffect, useState } from 'react'
import { GitBranch, Play, RefreshCw, RotateCcw, Trash2 } from 'lucide-react'
import { api, hasApiCode, type LoopConflictReview, type LoopMergeConflict } from '../../lib/api'
import { withWeight } from '../../design/fontWeight'
import { Button } from '../../ui/Button'
import { confirmDestructive } from '../../ui/dialog'
import { BUSY_REASON } from '../../ui/unavailable'
import { failureSentence, reportActionFailure } from '../../app/reportingWrite'
import { TaskChanges } from './MergeReview'

const commits = (n: number) => `${n} commit${n === 1 ? '' : 's'}`

/** The sentence that names the files a conflict is in: the first five, then an ellipsis. */
export function conflictFiles(files: string[]): string {
  return files.slice(0, 5).join(', ') + (files.length > 5 ? '…' : '')
}

/** The confirm bodies for the two choices that discard a task's work, said from what is discarded. */
export function redoBody(into: string, branch: string, n: number): string {
  return `The task runs again from ${into} as it is now. The work it did, ${commits(n)} on ${branch} and `
    + "anything not committed in its folder, is discarded. This can't be undone."
}
export function dropBody(branch: string, n: number): string {
  return `Its branch ${branch} is deleted from your repository with its ${commits(n)}, its folder is deleted, `
    + "and the task is cancelled. The loop carries on without it. This can't be undone."
}

/** A finished task whose work conflicts with your branch (`loop/conflicts.py`).
 *
 *  The loop merges a finished task's branch into your branch. When that merge conflicts it is undone
 *  and nothing of the work is lost: it stays on the task's own branch, at the commit it reached, and
 *  the loop pauses here with what it would bring in. You choose: redo the task on top of your branch
 *  as it is now, resolve the conflict yourself and Resume (the loop merges it then), or drop the
 *  work. Redo and Drop name the commit shown; work that moved after you read it is read again. */
export function MergeConflict({ loopId, conflict, onChosen }: {
  loopId: string
  conflict: LoopMergeConflict
  onChosen: () => void
}) {
  const [review, setReview] = useState<LoopConflictReview | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [busy, setBusy] = useState<'redo' | 'drop' | 'resume' | null>(null)
  const [moved, setMoved] = useState(false)

  const load = useCallback(async () => {
    try { setReview(await api.uLoopConflictReview(loopId)); setLoadErr('') }
    catch (e) { setLoadErr(failureSentence('read the work', e)) }
  }, [loopId])
  useEffect(() => { void load() }, [load])

  const title = conflict.title || conflict.branch
  const into = review?.into || conflict.into
  const files = review ? review.files : conflict.files
  const task = review?.task

  async function choose(choice: 'redo' | 'drop') {
    if (!task || busy) return
    const n = task.commits.length
    const ok = choice === 'redo'
      ? await confirmDestructive(`Redo “${title}” on top of ${into}?`, redoBody(into, task.branch, n), { confirmLabel: 'Redo' })
      : await confirmDestructive(`Drop the work on “${title}”?`, dropBody(task.branch, n), { confirmLabel: 'Drop' })
    if (!ok) return
    setBusy(choice); setMoved(false)
    try { await api.uLoopConflict(loopId, choice, task.task_id, task.tip); onChosen() }
    catch (e) {
      if (hasApiCode(e, 'loop_conflict_moved')) { setMoved(true); await load() }
      else reportActionFailure(choice === 'redo' ? 'redo the task' : 'drop its work')(e)
    } finally { setBusy(null) }
  }

  async function resume() {
    if (busy) return
    setBusy('resume')
    try { await api.uLoopAction(loopId, 'resume'); onChosen() }
    catch (e) { reportActionFailure('resume this project')(e) }
    finally { setBusy(null) }
  }

  return (
    <section role="region" aria-label={`“${title}” conflicts with ${into}`} data-type="body-s"
      className="mb-s rounded-lg border border-outline-variant bg-surface-container p-m">
      <div className="mb-xs inline-flex items-center gap-xs" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
        <GitBranch size={14} aria-hidden /> “{title}” conflicts with {into}
      </div>
      <p className="text-on-surface">
        {files.length === 0 && review
          ? <>It merges cleanly with {into} now. Resume, and the loop merges it.</>
          : <>The task is done, but its work conflicts with {into}{files.length ? ` in ${conflictFiles(files)}` : ''}, so
            none of it was merged. It is kept as it is on branch <code>{conflict.branch}</code> until you choose.</>}
      </p>
      {moved && (
        <p role="status" data-type="caption" className="mt-xs text-on-surface-var">
          The work changed since you read it, so nothing was redone or dropped. This is the work as it is now.
        </p>
      )}
      {loadErr ? (
        <div className="mt-s flex items-center gap-s">
          <p role="alert" data-type="caption" className="flex-1" style={{ color: 'var(--color-warn)' }}>{loadErr}</p>
          <Button variant="ghost" size="xs" onClick={load}><RefreshCw size={12} aria-hidden /> Retry</Button>
        </div>
      ) : !task ? (
        <p data-type="caption" className="mt-s text-on-surface-low">Reading the work…</p>
      ) : (
        <div className="mt-s flex flex-col gap-s">
          <TaskChanges task_id={task.task_id} title={title} branch={task.branch} commits={task.commits}
            stat={task.stat} diff={task.diff} summary="What its work changes" />
          {review?.cut && (
            <p data-type="caption" className="text-on-surface-low">The diff is longer than one review shows; the rest is on the branch.</p>
          )}
          <ul className="flex flex-col gap-s">
            <li className="flex flex-wrap items-center gap-s">
              <p data-type="caption" className="min-w-0 flex-1 text-on-surface-var">
                Run the task again from {into} as it is now. The work it did is set aside.
              </p>
              <Button variant="ghost-accent" size="xs" disabled={!!busy} disabledReason={BUSY_REASON}
                loading={busy === 'redo'} onClick={() => choose('redo')}>
                <RotateCcw size={13} aria-hidden /> Redo on top of {into}…
              </Button>
            </li>
            <li className="flex flex-wrap items-center gap-s">
              <p data-type="caption" className="min-w-0 flex-1 text-on-surface-var">
                Resolve it yourself: merge <code>{task.branch}</code> into {into} in your workspace and resolve the conflict
                there, or resolve it on the branch in its folder <code className="select-all break-all">{task.path}</code>.
                Then Resume: the loop merges it, and asks again if it still conflicts.
              </p>
              <Button variant="ghost" size="xs" disabled={!!busy} disabledReason={BUSY_REASON}
                loading={busy === 'resume'} onClick={resume}>
                <Play size={13} aria-hidden /> Resume
              </Button>
            </li>
            <li className="flex flex-wrap items-center gap-s">
              <p data-type="caption" className="min-w-0 flex-1 text-on-surface-var">
                Drop its work: its branch and folder are deleted and the task is cancelled. The loop carries on without it.
              </p>
              <Button variant="ghost" size="xs" disabled={!!busy} disabledReason={BUSY_REASON}
                loading={busy === 'drop'} onClick={() => choose('drop')}>
                <Trash2 size={13} aria-hidden /> Drop its work…
              </Button>
            </li>
          </ul>
        </div>
      )}
    </section>
  )
}
