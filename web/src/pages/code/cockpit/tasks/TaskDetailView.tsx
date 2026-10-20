import { fvs, withWeight } from '../../../../design/fontWeight'
import { Play, Loader2, Send, Circle, Wrench, Activity, ChevronRight, ChevronLeft, Target, HelpCircle, Rocket } from 'lucide-react'
import { Button } from '../../../../ui/Button'
import { TextLink } from '../../../../ui/TextLink'
import { SpendCapPause } from '../../../../ui/SpendCapPause'
import { NoToolsPause } from '../../../loops/NoToolsPause'
import { Eyebrow } from '../../../../ui/Eyebrow'
import { IconButton } from '../../../../ui/IconButton'
import { type CodeProject, type CodeFinding, type TaskItem } from '../../../../lib/api'
import { BUSY_REASON } from '../../../../ui/unavailable'
import { STATE_LABEL } from './taskState'
import { STEERABLE, TERMINAL_STATUSES, type ActivityItem } from '../cockpitShared'
import { FindingCard } from './FindingCard'
import { useTaskDetail } from './useTaskDetail'

// ── The cockpit's task detail ──────────────────────────────────────────────────────────────────
//
// One task, opened from the list: its plan, exit criteria and findings, the worker's live activity
// while it runs, what it is waiting on, and a steer box for this task alone. `useTaskDetail` keeps
// what it knows and does.

/** The named, clickable list of a blocked task's unresolved prerequisite tasks —
 *  so "blocked" says WHAT it's waiting on (not a generic "its dependency tasks")
 *  and the user can jump straight to a blocker. Empty → nothing renders. */
function BlockerList({ blockers, onOpenTask }: { blockers: { id: string; title: string }[]; onOpenTask: (id: string) => void }) {
  if (!blockers.length) return null
  return (
    <div className="mt-1 flex flex-col gap-0.5">
      {blockers.map((b) => (
        // Raw: a link in the list's own ink that turns accent on hover; TextLink draws accent ink at rest.
        <button key={b.id} type="button" onClick={() => onOpenTask(b.id)} title={`Open “${b.title}”`}
          className="group/bl inline-flex max-w-full items-center gap-1 self-start rounded px-1 text-on-surface-var hover:text-primary">
          <Circle size={9} className="shrink-0 opacity-60" />
          <span className="truncate">{b.title}</span>
          <ChevronRight size={11} className="shrink-0 opacity-0 transition-opacity group-hover/bl:opacity-100 focus-within:opacity-100" />
        </button>
      ))}
    </div>
  )
}

/** Task DETAIL view — the per-task scope: back nav, the task's plan + exit
 *  criteria, its agent loop events (findings + live in-flight activity), and a
 *  TASK-SCOPED steer box. Queue / un-queue happens here too, and an attended
 *  question for THIS task is answered right here. */
export function TaskDetailView({ project, task, doneIds, stageOpen, knownIds, findings, liveActivity, blockers, onOpenTask, onBack, onChanged }: {
  project: CodeProject; task: TaskItem; doneIds: Set<string>; stageOpen: boolean; knownIds: Set<string>; findings: CodeFinding[]; liveActivity: ActivityItem[]
  blockers: { id: string; title: string }[]; onOpenTask: (taskId: string) => void
  onBack: () => void; onChanged: () => void
}) {
  const {
    ended, state, text, setText, busy, showAllFindings, setShowAllFindings, steerRef, lastSteer, queued,
    autopilot, done, terminal, running, live, plan, crit, ws, scrollRef, queue, steer,
  } = useTaskDetail({ project, task, doneIds, stageOpen, knownIds, findings, liveActivity, onChanged })
  return (
    <div className="flex h-full flex-col">
      {/* header: back + status */}
      <div className="flex shrink-0 items-center gap-1.5 border-b border-outline-variant/40 px-2 py-1.5">
        {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
        <Button variant="ghost" size="xs" onClick={onBack} className="gap-1 px-1.5 text-[0.75rem] text-on-surface-low">
          <ChevronLeft size={14} /> Tasks
        </Button>
        <span data-type="caption" className="ml-auto inline-flex items-center gap-1" style={{ color: running ? 'var(--color-primary)' : done ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }}>
          {running && <Loader2 size={10} className="animate-spin" />}{running ? 'running' : queued ? 'queued' : done ? 'done' : state === 'undone' ? STATE_LABEL.undone : task.status}
        </span>
      </div>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto p-3">
        <h3 data-type="title-m" className="text-on-surface" style={fvs(600)}>{task.title}</h3>
        {task.description && <p data-type="body-s" className="mt-1 text-on-surface-var">{task.description}</p>}

        {/* The build's finished work waits for you to merge it: its review is under Tasks. */}
        {project.status === 'needs_input' && project.pending_question?.merge && (
          <p role="status" data-type="body-s" className="mt-m rounded-lg bg-surface-container p-s text-on-surface-var">
            The build’s finished work is waiting for you to merge it into {project.pending_question.merge.into}. Review it under Tasks.
          </p>
        )}
        {/* A finished task's work conflicts with the workspace: what it is and the choices are under Tasks. */}
        {project.status === 'needs_input' && project.pending_question?.conflict && (
          <p role="status" data-type="body-s" className="mt-m rounded-lg bg-surface-container p-s text-on-surface-var">
            The work of “{project.pending_question.conflict.title || project.pending_question.conflict.branch}” conflicts
            with {project.pending_question.conflict.into}, and is kept on its branch until you choose how to resolve it. Choose under Tasks.
          </p>
        )}
        {/* A spend cap refused the loop's next call: paused, nothing to answer here — the
            header's Resume carries on once the cap has room. */}
        {project.status === 'needs_input' && project.pending_question?.spend_cap && (
          <SpendCapPause className="mt-m" reason={project.pending_question.question}
            detail={project.pending_question.why} settings={project.pending_question.settings} />
        )}
        {/* Its worker's model can't use tools: paused, the header's Resume carries on once it can. */}
        {project.status === 'needs_input' && project.pending_question?.no_tools && (
          <NoToolsPause className="mt-m" question={project.pending_question.question} why={project.pending_question.why} />
        )}
        {/* attended question for THIS task — answer in the steer box below */}
        {project.status === 'needs_input' && project.pending_question?.question && !project.pending_question.merge && !project.pending_question.spend_cap && !project.pending_question.no_tools && !project.pending_question.conflict && (
          <div data-type="body-s" className="mt-3 rounded-lg p-2.5" style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-info)' }, 550)}>
              <HelpCircle size={14} /> Needs your input
            </div>
            <p className="whitespace-pre-wrap text-on-surface">{project.pending_question.question}</p>
            {project.pending_question.why && (
              <p data-type="caption" className="mt-1 whitespace-pre-wrap text-on-surface-low">{project.pending_question.why}</p>
            )}
            <div className="mt-2 flex items-center gap-2">
              <p data-type="caption" className="flex-1 text-on-surface-low">Answer in the box below to resume.</p>
              {/* One-click unblock — matches the project-level footer's affordance so a
                  user steering from the task scope isn't forced to type a full answer. */}
              {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
              <Button variant="ghost" size="xs" disabled={busy} disabledReason={BUSY_REASON}
                onClick={() => steer('Proceed with your best judgment / the sensible default you proposed. Record the assumption in your finding and continue.')}
                className="shrink-0 px-2 text-[0.75rem] text-info hover:bg-info/10">
                Use your best judgment
              </Button>
            </div>
          </div>
        )}

        {plan.length > 0 && (
          <div className="mt-3">
            <Eyebrow as="p">Action plan</Eyebrow>
            <ol className="mt-1 flex flex-col gap-0.5">
              {plan.map((a, i) => (
                <li key={i} data-type="caption" className={`flex items-start gap-1.5 ${a.completed ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}>
                  <span className="mt-[1px] shrink-0 tabular-nums text-on-surface-low">{i + 1}.</span><span>{a.content}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
        {crit.length > 0 && (
          <div className="mt-3">
            <Eyebrow as="p">Done when</Eyebrow>
            <ul className="mt-1 flex flex-col gap-0.5">
              {crit.map((c, i) => (
                <li key={i} data-type="caption" className="flex items-start gap-1.5 text-on-surface-low">
                  <Target size={9} className="mt-[4px] shrink-0 opacity-60" /><span className={c.met ? 'line-through opacity-70' : ''}>{c.description}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* agent loop events for THIS task: completed cycles + the in-flight one */}
        <div className="mt-3">
          <Eyebrow as="p">Agent activity{findings.length ? ` · ${findings.length} ${findings.length === 1 ? 'cycle' : 'cycles'}` : ''}</Eyebrow>
          {findings.length === 0 && live.length === 0 && (
            <p data-type="caption" className="mt-1 text-on-surface-low/70">{running ? 'Working…' : queued ? 'Queued — will run when ready.' : 'No activity yet.'}</p>
          )}
          <div className="mt-1 flex flex-col gap-1.5">
            {/* Cap to the most recent cycles (findings are appended oldest-first, so the
                tail is newest); a "show earlier" toggle reveals the rest in chronological
                order. Keeps a long task's activity column bounded. */}
            {(() => {
              const CAP = 6
              const hidden = showAllFindings ? 0 : Math.max(0, findings.length - CAP)
              const shown = hidden > 0 ? findings.slice(-CAP) : findings
              return (
                <>
                  {hidden > 0 && (
                    <TextLink size="xs" ink="emphasis" className="self-start"
                      onClick={() => setShowAllFindings(true)}
                      title={`Show ${hidden} earlier cycle${hidden === 1 ? '' : 's'}`}>↑ {hidden} earlier cycle{hidden === 1 ? '' : 's'}</TextLink>
                  )}
                  {shown.map((f) => <FindingCard key={f.cycle ?? `${f.summary}`} finding={f} ws={ws} />)}
                </>
              )
            })()}
          </div>
          {live.length > 0 && (
            <div className="mt-2 rounded-lg border border-primary/30 bg-primary/5 p-2.5">
              <div data-type="caption" className="mb-1.5 inline-flex items-center gap-1.5 text-primary"><Loader2 size={11} className="animate-spin" /> working now</div>
              <div className="flex flex-col gap-1">
                {live.map((it, i) => (
                  it.kind === 'say'
                    ? <p key={i} data-type="caption" className="whitespace-pre-wrap text-on-surface-var">{it.label}</p>
                    : <div key={i} data-type="caption" className="flex items-start gap-1.5 text-on-surface-low">
                        {it.kind === 'tool' ? <Wrench size={11} className="mt-0.5 shrink-0" /> : <Activity size={11} className="mt-0.5 shrink-0" />}
                        <span className="min-w-0"><span className="text-on-surface-var">{it.label}</span>{it.detail && <span className="text-on-surface-low/70"> · {it.detail.slice(0, 60)}</span>}</span>
                      </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* task actions: queue/unqueue + a task-scoped steer box. In autopilot the
          SYSTEM owns the queue (it re-queues each poll), so manual queue/unqueue is
          futile — hide it and show the active-stage scheduling state instead. */}
      <div className="shrink-0 border-t border-outline-variant/40 p-2">
        {ended ? null : autopilot ? (
          !terminal && (
            <>
              <p data-type="caption" className="mb-2 inline-flex items-center gap-1 text-on-surface-low">
                <Rocket size={11} className="text-primary" />
                {running ? 'Running on autopilot.'
                  : state === 'blocked' ? 'Autopilot will run it once its dependencies finish.'
                  : state === 'waiting' ? 'Autopilot will run it when its stage starts.'
                  : 'Queued by autopilot.'}
              </p>
              {state === 'blocked' && blockers.length > 0 && (
                <div data-type="caption" className="mb-2 text-on-surface-low">
                  <span>Waiting on:</span>
                  <BlockerList blockers={blockers} onOpenTask={onOpenTask} />
                </div>
              )}
            </>
          )
        ) : (
          <>
            {/* queueable: ready now, or waiting-for-its-stage (queue it for when the
                stage opens). Blocked tasks can't be queued usefully (deps not done). */}
            {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
            {(state === 'ready' || (state === 'waiting' && !queued)) && (
              <Button variant="tonal" size="xs" disabled={busy} disabledReason={BUSY_REASON} onClick={() => queue('queue')}
                className="mb-2 gap-1 px-2 text-[0.75rem]">
                <Play size={11} /> Queue this task{state === 'waiting' ? ' (runs when its stage starts)' : ''}
              </Button>
            )}
            {state === 'waiting' && queued && (
              <p data-type="caption" className="mb-2 text-on-surface-low">Queued · waiting for its stage to start.</p>
            )}
            {state === 'blocked' && (
              <div data-type="caption" className="mb-2 text-on-surface-low">
                <span>Blocked — waiting on{queued ? ' (queued; will run when they finish)' : ''}:</span>
                <BlockerList blockers={blockers} onOpenTask={onOpenTask} />
              </div>
            )}
            {queued && !running && (state === 'queued') && (
              <TextLink disabled={busy} onClick={() => queue('unqueue')} size="xs" className="mb-2">Remove from queue</TextLink>
            )}
            {queued && !running && (state === 'blocked' || state === 'waiting') && (
              <TextLink disabled={busy} onClick={() => queue('unqueue')} size="xs" className="mb-2 ml-2">Remove from queue</TextLink>
            )}
          </>
        )}
        {!STEERABLE.has(project.status) ? (
          // Not steerable. For a terminal project that's "finished"; for a pre-launch
          // one (a brief flash before the cockpit re-routes to Plan Review) it hasn't
          // started — say which, so the message is never a misleading "finished".
          <p data-type="caption" className="px-1.5 py-1 text-center text-on-surface-low">
            {TERMINAL_STATUSES.has(project.status) ? 'This project has finished.' : 'This project hasn’t started yet.'}
          </p>
        ) : terminal ? (
          // A finished (done) OR cancelled task's worker is gone — a task-scoped steer
          // would write guidance_<task_id>.txt that nothing reads (and parallel teardown
          // clears it). Don't offer a no-op box; point to where steering still lands.
          <p data-type="caption" className="px-1.5 py-1 text-center text-on-surface-low">
            This task is {task.status === 'cancelled' ? 'cancelled' : 'done'} — steer the project or another task to direct further work.
          </p>
        ) : (
          <>
            {/* echo the latest steer so the user sees it landed (mirrors the project
                steer box); a failed send is marked + the text was kept in the box. */}
            {lastSteer && (
              <div data-type="body-s" className="mb-2 self-end rounded-xl bg-primary/15 px-2.5 py-1.5 text-on-surface-var">
                {lastSteer.text}
                {lastSteer.failed && <span data-type="caption" className="ml-1.5 text-danger">· failed to send</span>}
              </div>
            )}
            <div className="flex items-end gap-1.5 rounded-xl bg-surface-container px-2.5 py-1.5 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
              {/* Raw: a bare text surface inside the steer card, focused through a ref; TextArea draws its own fill and takes no ref. */}
              <textarea ref={steerRef} value={text} onChange={(e) => setText(e.target.value)} rows={1}
                placeholder={project.status === 'needs_input' ? 'Answer for this task…' : `Steer “${task.title.slice(0, 24)}${task.title.length > 24 ? '…' : ''}”…`}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); steer() } }}
                data-type="body-s" className="max-h-24 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent text-on-surface outline-none placeholder:text-on-surface-low" />
              {/* Compound gate, SPLIT down the middle: `!text.trim()` is unavailable and the user can
                  fix it, so it keeps `disabled` + the reason; `busy` is in flight, so it is `loading`.
                  They used to be OR-ed into one `disabled`, which announced "unavailable" mid-send and
                  made the hand-rolled `icon={busy ? Loader2 : Send}` + `[&_svg]:animate-spin` swap
                  below necessary — the primitive owns the spinner now. */}
              <IconButton icon={Send} label="Send steer" filled size={28} iconSize={13}
                disabled={!text.trim()} disabledReason={!text.trim() ? 'Type a steer first' : undefined}
                loading={busy} onClick={() => steer()} className="shrink-0" />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
