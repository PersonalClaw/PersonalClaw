import { withWeight } from '../../../../design/fontWeight'
import { Send, HelpCircle, XCircle, AlertTriangle, Plus } from 'lucide-react'
import { Button } from '../../../../ui/Button'
import { SpendCapPause } from '../../../../ui/SpendCapPause'
import { NoToolsPause } from '../../../loops/NoToolsPause'
import { IconButton } from '../../../../ui/IconButton'
import { type CodeProject } from '../../../../lib/api'
import { loopStatusLabel } from '../../../../lib/loopStatus'
import { type StallInfo } from '../../../loops/runFold'
import { KeptWork } from '../../KeptWork'
import { StageGateVerdict } from '../../StageGateVerdict'
import { LoopApprovals } from '../../../loops/LoopApprovals'
import { MergeReview } from '../../MergeReview'
import { MergeConflict } from '../../MergeConflict'
import { BUSY_REASON } from '../../../../ui/unavailable'
import { STEERABLE } from '../cockpitShared'
import { OutcomeBanner } from './OutcomeBanner'
import { useProjectFooter } from './useProjectFooter'

// ── The cockpit's footer ───────────────────────────────────────────────────────────────────────
//
// The foot of the Tasks rail: the run's approvals, a merge or a conflict waiting on you, a paused
// loop, the worker's question, a failed gate or a stall, the outcome of a finished run, and the
// project-level steer box. `useProjectFooter` keeps what it knows and does.

/** Project-level footer for the Tasks panel: signals that aren't task-scoped
 *  (attended question, gate failure, stall, terminal outcome) + the steer box.
 *  Per-task agent loop events live under each task card (StageTasks), not here. */
export function ProjectFooter({ project, gateFail, stalled, onNudged, onStartNew, onAnswerOnScreen, answerWanted, onAnswerShown }: { project: CodeProject; gateFail: { label: string; command: string; output: string } | null; stalled: StallInfo | null; onNudged: () => void; onStartNew?: () => void; onAnswerOnScreen?: (onScreen: boolean) => void; answerWanted?: boolean; onAnswerShown?: () => void }) {
  const {
    answerRef, text, setText, sending, steerRef, findings, missingCommands, workerAsks, resuming, resume,
    steer, lastSteer,
  } = useProjectFooter({ project, onNudged, onAnswerOnScreen, answerWanted, onAnswerShown })
  return (
    <div ref={answerRef} className="shrink-0 border-t border-outline-variant/40">
      <div className="max-h-[40vh] overflow-y-auto px-2 pt-2">
        {/* An Attended build's workers ask before they act, and their asks are answered here. */}
        <LoopApprovals loopId={project.id} className="mb-2" />
        {/* An Attended build's finished work, waiting for you to merge it into your branch. */}
        {project.status === 'needs_input' && project.pending_question?.merge && (
          <MergeReview loopId={project.id} waiting={project.pending_question.merge} onMerged={onNudged} />
        )}
        {/* A finished task's work conflicts with your branch: kept on its branch until you redo it,
            resolve it yourself and Resume, or drop it. */}
        {project.status === 'needs_input' && project.pending_question?.conflict && (
          <MergeConflict loopId={project.id} conflict={project.pending_question.conflict} onChosen={onNudged} />
        )}
        {/* A spend cap refused the loop's next call: paused rather than asking, with the cap's
            own sentence, where it is changed, and Resume. */}
        {project.status === 'needs_input' && project.pending_question?.spend_cap && (
          <SpendCapPause className="mb-s" reason={project.pending_question.question}
            detail={project.pending_question.why} settings={project.pending_question.settings}
            onResume={() => void resume()} busy={resuming} />
        )}
        {/* Its worker's model can't use tools: paused rather than asking, naming the model, where
            another is chosen, and Resume. */}
        {project.status === 'needs_input' && project.pending_question?.no_tools && (
          <NoToolsPause className="mb-s" question={project.pending_question.question}
            why={project.pending_question.why} onResume={() => void resume()} busy={resuming} />
        )}
        {/* Attended question — the call to action; answer in the steer box below. */}
        {project.status === 'needs_input' && project.pending_question?.question && !project.pending_question.merge && !project.pending_question.spend_cap && !project.pending_question.no_tools && !project.pending_question.conflict && (
          <div role="alert" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-info)' }, 550)}>
              <HelpCircle size={14} /> The worker needs your input
            </div>
            <p className="whitespace-pre-wrap text-on-surface">{project.pending_question.question}</p>
            {/* The worker's reasoning for asking (what changes based on the answer) —
                context that helps the user answer well. */}
            {project.pending_question.why && (
              <p data-type="caption" className="mt-1 whitespace-pre-wrap text-on-surface-low">{project.pending_question.why}</p>
            )}
            <div className="mt-2 flex items-center gap-2">
              <p data-type="caption" className="flex-1 text-on-surface-low">Answer below to resume the build.</p>
              {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
              <Button variant="ghost" size="xs" disabled={sending} disabledReason={BUSY_REASON}
                onClick={() => steer('Proceed with your best judgment / the sensible default you proposed. Record the assumption in your finding and continue.')}
                className="shrink-0 px-2 text-[0.75rem] text-info hover:bg-info/10">
                Use your best judgment
              </Button>
            </div>
          </div>
        )}
        {/* needs_input but NO structured question (the question file failed to write /
            was cleared, or a generic pause): the header pill says "needs input" but
            without this the panel gave zero explanation + no call to action. Fall back
            to a generic prompt so the user always knows what to do — steer to resume. */}
        {project.status === 'needs_input' && !project.pending_question?.question && (
          <div role="alert" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-info)' }, 550)}>
              <HelpCircle size={14} /> The worker is waiting on you
            </div>
            <p data-type="caption" className="text-on-surface-var">It paused for input but didn't leave a specific question. Steer it below with direction (or tell it to use its best judgment), then it resumes.</p>
            <div className="mt-2 flex justify-end">
              {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
              <Button variant="ghost" size="xs" disabled={sending} disabledReason={BUSY_REASON}
                onClick={() => steer('Proceed with your best judgment / the sensible default. Record any assumption in your finding and continue.')}
                className="shrink-0 px-2 text-[0.75rem] text-info hover:bg-info/10">
                Use your best judgment
              </Button>
            </div>
          </div>
        )}
        <OutcomeBanner project={project} findings={findings} />
        {/* A run that ended with tasks still at work kept their work: what, where, and Merge / Discard. */}
        {project.workspace_dir && <KeptWork loopId={project.id} status={project.status} />}
        {/* The stage's exit criteria as its gate last judged them: which are met, which are not,
            and which the loop's records could not show. What a steer or a relaxed criterion is for. */}
        <StageGateVerdict project={project} />
        {/* BLOCKED — persisted (reload-safe) explanation of WHY the build paused (e.g.
            the stall-pause: a stage produced many cycles without clearing its gate).
            The transient `stalled` SSE banner below only shows while running; once the
            supervisor flips the project to blocked, this is what tells the user what
            happened + that a steer/Resume gets it going again. */}
        {/* `role="status"`, not `alert`: this banner is PERSISTED and reload-safe, so an
            assertive role would re-interrupt a screen-reader user on every visit to a state they
            already know about. Its sibling below (`gateFail`) is `alert` correctly — that one
            fires while the run is live. The two sat adjacent with only one announced at all,
            which is the half of issue 671 that made the explanation unreachable rather than
            just quiet. (Issue numbers in this block are spelled bare:
            `design/tokenLint` skips only lines that OPEN with a comment marker, so a hash
            followed by three digits on a continuation line reads as a raw hex colour.) The heading says "Blocked" (the registry's word for this status) instead of
            "Paused": `paused` is a DIFFERENT status the user chose, and calling this one by its
            name is what makes the state searchable on the page. */}
        {project.status === 'blocked' && project.error_message && (
          <div role="status" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
              <AlertTriangle size={14} /> {loopStatusLabel('blocked')} — needs you
            </div>
            <p className="whitespace-pre-wrap text-on-surface-var">{project.error_message}</p>
            <p data-type="caption" className="mt-1 text-on-surface-low">
              {missingCommands.length > 0
                ? `Edit the stored ${missingCommands.length === 1 ? 'command' : 'commands'}, then Resume.`
                : 'Steer it below (or relax a stage criterion), then Resume.'}
            </p>
          </div>
        )}
        {gateFail && project.status === 'running' && (
          <div role="alert" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
              <XCircle size={14} /> Supervisor {gateFail.label} check failed — stage held
            </div>
            {gateFail.command && <p data-type="caption" className="font-mono text-on-surface-low">{gateFail.command}</p>}
            {gateFail.output && (
              <pre data-type="caption" className="mt-1 max-h-40 overflow-auto rounded-md bg-surface-high/60 p-2 text-on-surface-var whitespace-pre-wrap break-words">{gateFail.output}</pre>
            )}
          </div>
        )}
        {/* Show while running (early stall warning) AND when blocked: the watchdog's
            stall escalation flips the project to BLOCKED, and this banner is what
            explains WHY — gating on 'running' alone hid it exactly when the user most
            needs it (the project is parked awaiting their steer). */}
        {stalled && !gateFail && (project.status === 'running' || project.status === 'blocked') && (
          <div role="status" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
              <AlertTriangle size={14} /> “{stalled.title}” {project.status === 'blocked' ? 'is stuck — paused for you' : 'seems stuck'}
            </div>
            <p data-type="caption" className="text-on-surface-var">
              {stalled.cause === 'binary'
                ? `${stalled.findings} cycles in and ${stalled.binary || 'the command binary'} is not on PATH — edit the stored command${project.status === 'blocked' ? ', then Resume' : ''}.`
                : `${stalled.findings} cycles in and the gate still hasn't passed — steer it or relax a criterion${project.status === 'blocked' ? ', then Resume' : ''}.`}
            </p>
          </div>
        )}
        {/* the most recent steer, so the user sees their message landed */}
        {lastSteer && (
          <div data-type="body-s" className="mb-2 self-end rounded-xl bg-primary/15 px-2.5 py-1.5 text-on-surface-var">
            {lastSteer.text}
            {'applied_cycle' in lastSteer && (lastSteer as { applied_cycle?: number }).applied_cycle != null && (
              <span data-type="caption" className="ml-1.5 text-on-surface-low">· applied cycle {(lastSteer as { applied_cycle?: number }).applied_cycle}</span>
            )}
            {'failed' in lastSteer && (lastSteer as { failed?: boolean }).failed && <span data-type="caption" className="ml-1.5 text-danger">· failed to send</span>}
          </div>
        )}
      </div>
      {/* steer box — only while the worker can ACT on it. */}
      <div className="p-2">
        {STEERABLE.has(project.status) ? (
          <div className="flex items-end gap-1.5 rounded-xl bg-surface-container px-2.5 py-1.5 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
            {/* Raw: a bare text surface inside the steer card, focused through a ref; TextArea draws its own fill and takes no ref. */}
            <textarea ref={steerRef} value={text} onChange={(e) => setText(e.target.value)} rows={1}
              placeholder={workerAsks ? 'Answer the worker…' : 'Steer the worker…'}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); steer() } }}
              data-type="body-s" className="max-h-24 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent text-on-surface outline-none placeholder:text-on-surface-low" />
            {/* Same split as the task-level steer box above: emptiness stays `disabled` (with the
                reason), in-flight becomes `loading` and the primitive owns the spinner. */}
            <IconButton icon={Send} label="Send steer" filled size={28} iconSize={13}
              disabled={!text.trim()} disabledReason={!text.trim() ? 'Type a steer first' : undefined}
              loading={sending} onClick={() => steer()} className="shrink-0" />
          </div>
        ) : project.status === 'ready' || project.status === 'review' ? (
          <p data-type="caption" className="px-1.5 py-1 text-center text-on-surface-low">Press Start to launch — steer the worker once it's running.</p>
        ) : (
          // Finished: the steer box is gone, so give the dangling "start a new one"
          // call-to-action a real button (was prose pointing nowhere — the only paths
          // out were buried in the overflow menu). Navigates to the new-project composer.
          <div className="flex flex-col items-center gap-1.5 px-1.5 py-1 text-center">
            <p data-type="caption" className="text-on-surface-low">This project has finished.</p>
            {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
            <Button variant="tonal" size="xs" onClick={() => onStartNew?.()} className="gap-1.5 px-3 text-[0.75rem]">
              <Plus size={13} /> Start a new project
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
