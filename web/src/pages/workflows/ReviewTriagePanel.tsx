import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, Loader2, RotateCcw, Send, Wand2, X } from 'lucide-react'
import { Button } from '../../ui/Button'
import { QuietButton } from '../../ui/QuietButton'
import { api, type ReviewFinding, type WorkflowTriageResult } from '../../lib/api'
import { notify } from '../../app/appSdk'

/** Reviewer-comment triage for a workflow run (EXECUTION-ISOLATION §7, EI-9).
 *
 *  The user's half of the loop: a review stage emitted line-anchored findings, and this is where
 *  they are accepted or rejected before anything happens to the code. Three things it must get
 *  right, and each one shapes the markup below:
 *
 *  • **An unanchored finding is never offered an Accept.** The server refuses one anyway, so a
 *    button that looked available would teach the user the UI lies. Instead the row says WHY the
 *    anchor failed in plain words — "that line is not in the current diff", "the line moved" — so
 *    the reviewer's claim reads as unverifiable rather than as either true or false.
 *  • **Nothing is dispatched until the user presses Dispatch.** Accept/Reject only build a local
 *    decision map; no request leaves until then. A full rejection is a legitimate outcome and its
 *    receipt says `nothing_accepted` rather than reading as a failure.
 *  • **Anchors are re-read on every load, never cached.** The worker keeps working, so a stored
 *    anchor verdict is stale on arrival — and a stale `anchored` is exactly how an accepted fix
 *    lands on the wrong line. Refresh re-anchors; the panel holds no anchor state of its own. */

type Decision = 'accept' | 'reject'

/** Why an anchor failed, in words a reviewer can act on. An unmapped reason falls through to the
 *  raw value rather than a friendly default — a default sentence would report a NEW failure mode as
 *  one of these four, which is worse than showing an unfamiliar token. */
const ANCHOR_REASON: Record<string, string> = {
  empty_diff: 'this run has no diff to check against yet',
  no_line_anchor: 'the reviewer gave a place, not a line — nothing to apply automatically',
  file_not_in_diff: 'that file is not in the current diff',
  ambiguous_path: 'more than one file in the diff has that name — refused rather than guessed',
  line_not_in_diff: 'that line is not in the current diff',
  content_moved: 'the line moved — what the reviewer quoted is no longer there',
}

const SEVERITY_TONE: Record<string, string> = {
  Critical: 'var(--color-danger)',
  Major: 'var(--color-warn)',
  Minor: 'var(--color-info)',
  Nit: 'var(--color-on-surface-low)',
}

export function anchorExplanation(reason: string): string {
  return ANCHOR_REASON[reason] ?? reason
}

export function ReviewTriagePanel({ runId, onDispatched }: { runId: string; onDispatched?: () => void }) {
  const [findings, setFindings] = useState<ReviewFinding[] | null>(null)
  const [counts, setCounts] = useState({ total: 0, anchored: 0, unanchored: 0 })
  const [error, setError] = useState<string | null>(null)
  const [decisions, setDecisions] = useState<Record<string, Decision>>({})
  const [result, setResult] = useState<WorkflowTriageResult | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setError(null)
    try {
      const res = await api.workflowReview(runId)
      setFindings(res.findings ?? [])
      setCounts(res.counts ?? { total: 0, anchored: 0, unanchored: 0 })
    } catch (e) {
      // A failed fetch renders as an ERROR, never as an empty state — "no findings" and "we
      // could not read the findings" are opposite facts about the same screen.
      setFindings(null)
      setError(e instanceof Error ? e.message : 'Could not read this run’s review findings.')
    }
  }, [runId])
  useEffect(() => { load() }, [load])

  const set = useCallback((key: string, choice: Decision) => {
    setDecisions((prev) => (prev[key] === choice ? (({ [key]: _drop, ...rest }) => rest)(prev) : { ...prev, [key]: choice }))
  }, [])

  const acceptedCount = useMemo(
    () => Object.values(decisions).filter((d) => d === 'accept').length,
    [decisions],
  )
  const rejectedCount = useMemo(
    () => Object.values(decisions).filter((d) => d === 'reject').length,
    [decisions],
  )

  const dispatch = useCallback(async () => {
    if (busy) return
    setBusy(true)
    try {
      const res = await api.workflowReviewTriage(runId, {
        decisions: Object.entries(decisions).map(([key, outcome]) => ({ key, outcome })),
      })
      setResult(res)
      onDispatched?.()
      await load()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not submit the triage.')
    } finally {
      setBusy(false)
    }
  }, [busy, decisions, runId, load, onDispatched])

  if (error) {
    return (
      <div className="flex flex-col items-center gap-2 py-6 text-[0.8125rem]">
        <span style={{ color: 'var(--color-warn)' }}>{error}</span>
        <QuietButton onClick={load} title="Try reading the findings again"><RotateCcw size={12} /> Try again</QuietButton>
      </div>
    )
  }
  if (findings === null) {
    return <div className="flex justify-center py-6"><Loader2 size={18} className="animate-spin text-on-surface-low" /></div>
  }
  if (findings.length === 0) {
    return (
      <p className="py-6 text-center text-on-surface-low text-[0.8125rem]">
        No review findings yet — a review stage has not reported any for this run.
      </p>
    )
  }

  // Anchored first: those are the ones the user can act on, and burying them under unverifiable
  // rows makes the panel read as noise.
  const ordered = [...findings].sort((a, b) => Number(b.anchor_state === 'anchored') - Number(a.anchor_state === 'anchored'))

  return (
    <div className="flex flex-col gap-l">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">
          {counts.total} finding{counts.total === 1 ? '' : 's'} · {counts.anchored} anchored
          {counts.unanchored > 0 ? ` · ${counts.unanchored} unverifiable` : ''}
        </span>
        <QuietButton onClick={load} title="Re-check every anchor against the diff as it is now">
          <RotateCcw size={12} /> Re-anchor
        </QuietButton>
      </div>

      <div className="flex flex-col gap-s">
        {ordered.map((f) => {
          const anchored = f.anchor_state === 'anchored'
          const choice = decisions[f.key]
          return (
            <div key={f.key} className="rounded-lg bg-surface-high px-m py-2">
              <div className="flex items-baseline gap-2">
                <span className="shrink-0 text-[0.75rem] uppercase tracking-wide" style={{ color: SEVERITY_TONE[f.severity] ?? 'var(--color-on-surface-low)' }}>
                  {f.severity || 'unrated'}
                </span>
                <span className="min-w-0 truncate font-mono text-on-surface-var text-[0.75rem]" title={f.location}>
                  {anchored ? `${f.resolved_path}:${f.resolved_line}` : f.location || 'no location'}
                </span>
                {f.auto_fixable && (
                  <span className="ml-auto flex shrink-0 items-center gap-1 text-on-surface-low text-[0.75rem]" title="A mechanical edit — appliable without judgment once accepted">
                    <Wand2 size={11} /> mechanical
                  </span>
                )}
              </div>
              <div className="mt-1 text-on-surface text-[0.8125rem]">{f.problem}</div>
              {f.why && <div className="mt-0.5 text-on-surface-low text-[0.75rem]">{f.why}</div>}
              {f.recommended_fix && (
                <div className="mt-1 text-on-surface-var text-[0.75rem]">Fix: {f.recommended_fix}</div>
              )}

              {anchored ? (
                <div className="mt-1.5 flex justify-end gap-xs">
                  <Button variant="ghost" size="xs" onClick={() => set(f.key, 'accept')} ariaPressed={choice === 'accept'}
                    title="Accept — this one is sent to the worker on Dispatch">
                    <Check size={12} /> Accept
                  </Button>
                  <Button variant="ghost" size="xs" onClick={() => set(f.key, 'reject')} ariaPressed={choice === 'reject'}
                    title="Reject — recorded against the reviewer, never sent to the worker">
                    <X size={12} /> Reject
                  </Button>
                </div>
              ) : (
                <div className="mt-1.5 flex items-start gap-1.5 text-[0.75rem]" style={{ color: 'var(--color-warn)' }}>
                  <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                  <span>
                    Can’t verify this against the diff — {anchorExplanation(f.anchor_reason)}. It can be
                    rejected, but not applied.
                  </span>
                </div>
              )}
              {!anchored && (
                <div className="mt-1.5 flex justify-end">
                  <Button variant="ghost" size="xs" onClick={() => set(f.key, 'reject')} ariaPressed={choice === 'reject'}
                    title="Reject — recorded against the reviewer">
                    <X size={12} /> Reject
                  </Button>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="flex flex-col gap-s">
        <div className="flex items-center justify-between gap-2">
          <span className="text-on-surface-low text-[0.75rem]">
            {acceptedCount} to send · {rejectedCount} to record
          </span>
          <Button size="sm" onClick={dispatch} disabled={busy || (acceptedCount === 0 && rejectedCount === 0)}
            disabledReason={acceptedCount === 0 && rejectedCount === 0 ? 'Accept or reject at least one finding first' : undefined}>
            <Send size={14} /> {busy ? 'Dispatching…' : 'Dispatch decisions'}
          </Button>
        </div>
        <p className="text-on-surface-low text-[0.75rem]">
          Only accepted findings reach the worker. Rejections are recorded against the reviewer so a
          gate that only ever cries wolf becomes visible.
        </p>
      </div>

      {result && (
        <div className="rounded-lg px-m py-2 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-info) 8%, transparent)', border: '1px dashed color-mix(in srgb, var(--color-info) 30%, transparent)' }}>
          <div className="text-info text-[0.75rem] uppercase tracking-wide mb-1">Dispatch result</div>
          <p className="text-on-surface-var">
            {result.receipt.delivered
              ? `${result.receipt.count} accepted finding${result.receipt.count === 1 ? '' : 's'} sent to the worker — applied at its next iteration.`
              : result.receipt.reason === 'nothing_accepted'
                ? 'Nothing was accepted, so nothing was sent to the worker.'
                : result.receipt.reason === 'handoff_parked'
                  ? 'This run has already finished, so the brief was saved for a follow-up run instead of being sent.'
                  : `Not sent — ${result.receipt.reason}.`}
          </p>
          {(result.calibrated ?? 0) > 0 && (
            <p className="mt-0.5 text-on-surface-low text-[0.75rem]">
              {result.calibrated} rejection{result.calibrated === 1 ? '' : 's'} recorded in the calibration record.
            </p>
          )}
          {result.refused.length > 0 && (
            <p className="mt-0.5 text-[0.75rem]" style={{ color: 'var(--color-warn)' }}>
              {result.refused.length} accepted finding{result.refused.length === 1 ? '' : 's'} could no longer be
              anchored and {result.refused.length === 1 ? 'was' : 'were'} not sent.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
