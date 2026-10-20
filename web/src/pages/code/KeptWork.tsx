import { useCallback, useEffect, useState } from 'react'
import { GitBranch, GitMerge, Trash2 } from 'lucide-react'
import { withWeight } from '../../design/fontWeight'
import { Button } from '../../ui/Button'
import { FieldError } from '../../ui/forms'
import { confirmDestructive } from '../../ui/dialog'
import { api, ApiError, hasApiCode, type KeptWork as KeptTaskWork } from '../../lib/api'
import { TaskChanges } from './MergeReview'
import { failureSentence } from '../../app/reportingWrite'
import { ENDED_LOOP_STATUSES } from '../../lib/loopStatus'
import { BUSY_REASON } from '../../ui/unavailable'

/** What one task's kept work holds that the workspace lacks, in words: "2 commits and 1 changed
 *  file". A count git could not read is said as such rather than as zero. */
export function keptWorkSize(w: Pick<KeptTaskWork, 'commits' | 'changed'>): string {
  const parts: string[] = []
  if (w.commits === null || w.changed === null) return 'changes that could not be counted'
  if (w.commits > 0) parts.push(`${w.commits} commit${w.commits === 1 ? '' : 's'}`)
  if (w.changed > 0) parts.push(`${w.changed} changed file${w.changed === 1 ? '' : 's'}`)
  return parts.join(' and ')
}

/** The name a kept task goes by: its title, else its branch. */
export function keptWorkName(w: Pick<KeptTaskWork, 'title' | 'branch' | 'task_id'>): string {
  return w.title.trim() || w.branch || w.task_id
}

/** The work an ended Code loop kept because its workspace does not have it (`loop/kept_work.py`).
 *
 *  A task worker works in its own worktree, on its own branch, and a finished task is merged back by
 *  the scheduler. One still at work when the run ended (its budget spent, a failure, a Stop) was not,
 *  and its worktree can hold edits its owner approved. The ending keeps it, and this says what is
 *  kept, where it is, and what can be done with it: Resume carries a failed run's on, Merge puts it
 *  in the workspace, and Discard deletes it — only ever because its owner said so.
 *
 *  It is the same review a waiting merge gets (`MergeReview`, `TaskChanges`): each task's commits
 *  and diff, and Merge brings in exactly the commit shown, or reads the work again if it moved. */
export function KeptWork({ loopId, status }: { loopId: string; status: string }) {
  const [kept, setKept] = useState<KeptTaskWork[]>([])
  const [into, setInto] = useState('')
  const [cut, setCut] = useState(false)
  const [resumable, setResumable] = useState(false)
  // A merge refused because the work moved since it was read: the review is read again and says so.
  const [moved, setMoved] = useState('')
  const [busy, setBusy] = useState<{ taskId: string; act: 'merge' | 'discard' } | null>(null)
  const [err, setErr] = useState<{ taskId: string; text: string } | null>(null)
  // A list that could not be read is said, not shown as nothing kept: there may be work here.
  const [loadErr, setLoadErr] = useState('')
  const ended = ENDED_LOOP_STATUSES.has(status)

  const load = useCallback(() => {
    if (!ended) { setKept([]); setLoadErr(''); return }
    api.uLoopKeptWork(loopId)
      .then((r) => {
        // A reply that lists nothing is not "nothing kept": it is a read that did not answer.
        if (!Array.isArray(r?.kept)) throw new Error('the reply did not list it')
        setKept(r.kept); setInto(r.into || ''); setCut(!!r.cut); setResumable(!!r.resumable); setLoadErr('')
      })
      .catch((e) => setLoadErr(failureSentence('check for work this run kept unmerged', e)))
  }, [loopId, ended])
  useEffect(load, [load])

  if (!ended) return null
  if (loadErr) {
    return (
      <div data-type="body-s" className="mb-m flex flex-wrap items-center gap-s">
        <FieldError>{loadErr}</FieldError>
        <Button variant="ghost" size="xs" onClick={load}>Try again</Button>
      </div>
    )
  }
  if (kept.length === 0) return null

  async function merge(w: KeptTaskWork) {
    setErr(null); setMoved(''); setBusy({ taskId: w.task_id, act: 'merge' })
    try {
      const r = await api.uLoopKeptMerge(loopId, w.task_id, w.tip)
      if (Array.isArray(r?.kept)) setKept(r.kept)
      else load()
    }
    catch (e) {
      if (hasApiCode(e, 'loop_merge_moved')) { setMoved(w.task_id); load(); return }
      const detail = e instanceof ApiError ? (e.detail as { conflicts?: string[]; commits?: string[] } | undefined) : undefined
      const files = hasApiCode(e, 'kept_work_conflicts') ? (detail?.conflicts ?? []) : []
      const others = hasApiCode(e, 'kept_work_other_name') ? (detail?.commits ?? []) : []
      const text = files.length
        ? `It conflicts with your workspace in ${files.join(', ')}, so nothing was merged and the work is kept. Resolve it on branch ${w.branch} in your workspace, or discard it.`
        : others.length
          ? `It has commits made under another name than yours (${others.slice(0, 3).join('; ')}), so it was not merged and the work is kept. Commit it again under your name on branch ${w.branch}, or discard it.`
          : failureSentence('merge it', e)
      setErr({ taskId: w.task_id, text })
    } finally { setBusy(null) }
  }

  async function discard(w: KeptTaskWork) {
    const name = keptWorkName(w)
    const where = w.path ? `Its worktree and its branch ${w.branch}` : `Its branch ${w.branch}`
    const ok = await confirmDestructive(
      `Discard the work on “${name}”?`,
      `${where} are deleted from your repository, with ${keptWorkSize(w)} that your workspace does not have. This can't be undone.`,
      { confirmLabel: 'Discard' },
    )
    if (!ok) return
    setErr(null); setBusy({ taskId: w.task_id, act: 'discard' })
    try { await api.uLoopKeptDiscard(loopId, w.task_id); load() }
    catch (e) { setErr({ taskId: w.task_id, text: failureSentence('discard it', e) }) }
    finally { setBusy(null) }
  }

  const lead = resumable
    ? 'These tasks were still at work when the run ended. Their work is kept as it is: Resume carries on with it, or merge it into your workspace or discard it now.'
    : 'This work never reached your workspace, so it is kept, each task on its own branch, until you merge it or discard it. To carry on with it yourself, open its folder or check out its branch.'

  return (
    <section role="region" aria-label="Work not in your workspace" data-type="body-s"
      className="mb-m rounded-lg border border-outline-variant/40 p-m">
      <div className="inline-flex items-center gap-s" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
        <GitBranch size={14} aria-hidden /> Work not merged into your workspace
      </div>
      <p data-type="caption" className="mt-xs text-on-surface-var">{lead}</p>
      <ul className="mt-s space-y-s">
        {kept.map((w) => (
          <li key={w.task_id} className="rounded-md bg-surface-container/50 p-s">
            <p className="text-on-surface" style={withWeight({}, 550)}>{keptWorkName(w)}</p>
            <p data-type="caption" className="text-on-surface-var">
              {keptWorkSize(w)} your workspace does not have, on branch <code>{w.branch || '(no branch)'}</code>
            </p>
            {w.path && (
              <p data-type="caption" className="break-all text-on-surface-low">
                In <code className="select-all">{w.path}</code>
              </p>
            )}
            {/* What merging it brings in, as a waiting merge's review shows it. */}
            <div className="mt-xs">
              <TaskChanges task_id={w.task_id} title={keptWorkName(w)} branch={w.branch} commits={w.log ?? []} stat={w.stat ?? ''} diff={w.diff ?? ''}
                summary="What merging it brings in" />
            </div>
            {moved === w.task_id && (
              <p role="status" data-type="caption" className="mt-xs text-on-surface-var">
                The work changed since you read it, so nothing was merged. This is the work as it is now.
              </p>
            )}
            <div className="mt-xs flex flex-wrap gap-s">
              <Button variant="ghost-accent" size="xs" disabled={!!busy} disabledReason={BUSY_REASON}
                loading={busy?.taskId === w.task_id && busy.act === 'merge'} onClick={() => merge(w)}>
                <GitMerge size={13} aria-hidden /> {into ? `Merge into ${into}` : 'Merge into workspace'}
              </Button>
              <Button variant="ghost" size="xs" disabled={!!busy} disabledReason={BUSY_REASON}
                loading={busy?.taskId === w.task_id && busy.act === 'discard'} onClick={() => discard(w)}>
                <Trash2 size={13} aria-hidden /> Discard…
              </Button>
            </div>
            {err?.taskId === w.task_id && <FieldError>{err.text}</FieldError>}
          </li>
        ))}
      </ul>
      {cut && (
        <p data-type="caption" className="mt-xs text-on-surface-low">The diff is longer than one review shows; the rest is on the branches above.</p>
      )}
    </section>
  )
}
