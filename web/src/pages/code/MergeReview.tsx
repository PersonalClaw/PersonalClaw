import { useCallback, useEffect, useState } from 'react'
import { GitMerge, RefreshCw } from 'lucide-react'
import { api, hasApiCode, type LoopMerge, type LoopMergeReview, type LoopMergeWaiting } from '../../lib/api'
import { withWeight } from '../../design/fontWeight'
import { UnifiedDiff } from '../../ui/UnifiedDiff'
import { Button } from '../../ui/Button'
import { reportActionFailure } from '../../app/reportingWrite'

/** The finished work an Attended code loop waits for you to merge into your branch.
 *
 *  An Attended loop puts nothing on your branch unasked (`loop/kinds/sdlc`): each finished task's
 *  work stays committed on its own branch, under your git identity, and the loop pauses here with
 *  what would land — each task's commits and its diff against your branch. Merge approves it at
 *  exactly the commits shown; the loop then merges them and carries on. Work that moved after you
 *  read it is not merged on that approval: the review is read again. */
export function MergeReview({ loopId, waiting, onMerged }: {
  loopId: string
  waiting: LoopMergeWaiting
  onMerged: () => void
}) {
  const [review, setReview] = useState<LoopMergeReview | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [moved, setMoved] = useState(false)

  const load = useCallback(async () => {
    try { setReview(await api.uLoopMergeReview(loopId)); setLoadErr('') }
    catch (e) { setLoadErr(e instanceof Error ? e.message : String(e)) }
  }, [loopId])
  useEffect(() => { load() }, [load])

  async function merge() {
    if (!review || busy) return
    setBusy(true)
    try {
      await api.uLoopMerge(loopId, Object.fromEntries(review.tasks.map((t) => [t.task_id, t.tip])))
      onMerged()
    } catch (e) {
      if (hasApiCode(e, 'loop_merge_moved')) { setMoved(true); await load() }
      else reportActionFailure('merge the work')(e)
    } finally { setBusy(false) }
  }

  const into = review?.into || waiting.into
  const count = waiting.tasks.length
  return (
    <section role="region" aria-label={`Work waiting to merge into ${into}`} data-type="body-s"
      className="mb-s rounded-lg border border-outline-variant bg-surface-container p-m">
      <div className="mb-xs inline-flex items-center gap-xs" style={withWeight({ color: 'var(--color-info)' }, 550)}>
        <GitMerge size={14} /> {count === 1 ? 'A task’s work is' : `${count} tasks’ work is`} ready to merge into {into}
      </div>
      <p className="text-on-surface">
        Nothing of it is on {into} until you merge it. It is committed on {count === 1 ? 'its own branch' : 'their own branches'} under
        your git name; merging brings in exactly the commits below.
      </p>
      {moved && (
        <p role="status" data-type="caption" className="mt-xs text-on-surface-var">
          The work changed since you read it, so nothing was merged. This is the work as it is now.
        </p>
      )}
      {loadErr ? (
        <div className="mt-s flex items-center gap-s">
          <p role="alert" data-type="caption" className="flex-1" style={{ color: 'var(--color-warn)' }}>The changes could not be read: {loadErr}</p>
          <Button variant="ghost" size="xs" onClick={load}><RefreshCw size={12} /> Retry</Button>
        </div>
      ) : !review ? (
        <p data-type="caption" className="mt-s text-on-surface-low">Reading the changes…</p>
      ) : (
        <div className="mt-s flex flex-col gap-s">
          {review.tasks.map((t) => (
            <details key={t.task_id} className="rounded-md bg-surface p-s">
              <summary className="cursor-pointer text-on-surface">
                {t.title || t.task_id} <span data-type="caption" className="text-on-surface-low">· {t.branch} · {t.commits.length} {t.commits.length === 1 ? 'commit' : 'commits'}</span>
              </summary>
              {t.commits.length > 0 && (
                <ul data-type="caption" className="mt-xs font-mono text-on-surface-var">
                  {t.commits.map((c) => <li key={c}>{c}</li>)}
                </ul>
              )}
              {t.stat && (
                <pre tabIndex={0} aria-label={`Files ${t.title || t.task_id} changes`} data-type="caption"
                  className="mt-xs overflow-x-auto font-mono text-on-surface-low">{t.stat}</pre>
              )}
              <UnifiedDiff patch={t.diff || '(no changes)'} label={`Changes of ${t.title || t.task_id}`} className="mt-xs max-h-[40vh] overflow-auto font-mono leading-snug" />
            </details>
          ))}
          {review.cut && (
            <p data-type="caption" className="text-on-surface-low">The diff is longer than one review shows; the rest is on the branches above.</p>
          )}
          <div className="flex justify-end">
            <Button variant="tonal" size="sm" onClick={merge} loading={busy} loadingLabel="Merging…"
              disabled={review.tasks.length === 0} disabledReason="Nothing to merge">
              <GitMerge size={14} /> Merge into {into}
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}

/** What this loop merged into your branch, newest first — your working tree is clean after a
 *  merge, so this is where the page says what landed, under which commit, and who approved it. */
export function MergedWork({ merges }: { merges: LoopMerge[] }) {
  if (merges.length === 0) return null
  return (
    <div data-type="caption" className="flex flex-col gap-xs px-m pb-s">
      <span className="text-on-surface-var" style={withWeight({}, 550)}>Merged by this loop ({merges.length})</span>
      <ul className="flex flex-col gap-xs">
        {[...merges].reverse().map((m) => (
          <li key={`${m.task_id}-${m.head}`} className="text-on-surface-var">
            <span className="text-on-surface">{m.title || m.task_id}</span> → {m.into}
            {' · '}{m.commits.length} {m.commits.length === 1 ? 'commit' : 'commits'}
            {m.head && <>{' · '}<span className="font-mono">{m.head.slice(0, 7)}</span></>}
            {' · '}{m.by === 'you' ? 'you approved it' : 'merged by the loop (Unattended)'}
          </li>
        ))}
      </ul>
    </div>
  )
}
