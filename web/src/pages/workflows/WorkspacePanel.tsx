import { useEffect, useState } from 'react'
import { FileDiff, FolderGit2, TriangleAlert } from 'lucide-react'
import { SidePanel } from '../../ui/SidePanel'
import { Skeleton } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { api, ApiError, type WorkflowWorkspaceReview } from '../../lib/api'

/** The code-run workspace panel (WORK-CONTAINERS §4.1 / criterion 7).
 *
 *  Two questions, one panel: WHAT did this run change, and HOW do I take it. It fetches
 *  `GET …/runs/{id}/workspace` on open — never eagerly, because answering costs the gateway a
 *  `git status` plus a conflict probe, and most runs are never reviewed.
 *
 *  **Reintegration is OFFERED, never performed.** The two verbs render as COPYABLE COMMANDS, not
 *  buttons that act: the plan's ruling is that a run which auto-merged would decide for the user,
 *  and the decision is the whole reason the work was isolated. There is deliberately no POST
 *  companion to this route — an "Apply" button here would be the gateway writing into the user's
 *  working tree on their behalf, which is precisely the thing isolation bought them out of.
 *
 *  Conflicts are named ON the offer rather than discovered at apply time, and they move the
 *  recommendation: "apply this" that then fails with a conflict is a worse experience than "apply
 *  this (2 files conflict) — checkout is safer". Checkout stays safe even WITH conflicts, because
 *  nothing merges until the user decides to merge. */
export function WorkspacePanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [data, setData] = useState<WorkflowWorkspaceReview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    setData(null)
    api.workflowRunWorkspace(runId)
      .then((d) => { if (live) setData(d) })
      .catch((e) => {
        if (!live) return
        if (e instanceof ApiError && e.status === 404) {
          setError('This run could not be found. It may have been deleted.')
        } else {
          setError(e instanceof Error ? e.message : 'Could not read this run’s workspace.')
        }
      })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId])

  return (
    <SidePanel
      fillHeight
      storeKey="wf-workspace-w"
      icon={<FolderGit2 size={18} className="text-primary" />}
      title="Workspace"
      onClose={onClose}
    >
      <div data-testid="workspace-panel-body" className="flex flex-col gap-l">
        {loading ? (
          <div role="status" aria-busy="true" aria-label="Loading the run’s workspace" className="flex flex-col gap-l">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : error ? (
          <InlineError icon multiline>{error}</InlineError>
        ) : data ? (
          <WorkspaceBody data={data} />
        ) : null}
      </div>
    </SidePanel>
  )
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-xs">
      <h3 data-type="label-m" className="text-on-surface-low">{label}</h3>
      {children}
    </section>
  )
}

/** The shell command a verb corresponds to. Shown verbatim so the user can read exactly what would
 *  happen before running it — a labelled button hides the operation, and this one touches their
 *  working tree. */
function verbCommand(verb: string, branch: string): string {
  return verb === 'checkout_branch' ? `git checkout ${branch}` : `git merge --squash ${branch}`
}

function WorkspaceBody({ data }: { data: WorkflowWorkspaceReview }) {
  const { workspace: ws, reintegration: offer, declared } = data
  const conflicts = offer?.conflicts ?? []
  const setup = declared?.setup

  // A run with no managed workspace is the COMMON case (a workspace is a declaration, not a
  // default), so it gets a real explanation rather than an empty panel that reads as a failure.
  if (!ws?.path && !ws?.branch) {
    return (
      <p data-testid="workspace-none" className="text-on-surface-low text-[0.8125rem]">
        This run did not declare a workspace, so it worked in place and has no isolated diff to
        review. Add a <code className="font-mono">workspace:</code> block to the template to run it
        in a git worktree or a scratch directory.
      </p>
    )
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-m text-[0.75rem]">
        {declared?.mode && (
          <span data-testid="workspace-mode" className="inline-flex items-center rounded-pill bg-surface-high px-2 py-0.5 font-mono text-on-surface-var text-[0.6875rem]">
            {declared.mode}
          </span>
        )}
        <span
          data-testid="workspace-liveness"
          className={ws.alive ? 'text-on-surface' : 'text-on-surface-low'}
          title={ws.alive ? 'The workspace is still on disk' : 'The workspace has been removed'}
        >
          {ws.alive ? 'on disk' : 'removed'}
        </span>
        {ws.dirty && <span className="text-on-surface">uncommitted changes</span>}
      </div>

      {/* A degraded provisioning is a DIFFERENT fact from a failure and has to be visible: a run
          that silently fell back from `worktree` to a scratch dir has no branch to check out, and
          without this the two verbs would look broken rather than inapplicable. */}
      {declared?.degraded_reason && (
        <p data-testid="workspace-degraded" className="flex items-start gap-xs text-on-surface-low text-[0.75rem]">
          <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          {declared.degraded_reason}
        </p>
      )}

      {ws.preserved_workspace_path && (
        <Block label="Preserved workspace">
          <p data-testid="preserved-path" className="break-all rounded-md bg-surface px-3 py-2 font-mono text-on-surface text-[0.75rem]">
            {ws.preserved_workspace_path}
          </p>
        </Block>
      )}

      <Block label={`Changed files${ws.changed.length ? ` (${ws.changed.length})` : ''}`}>
        {ws.changed.length === 0 ? (
          <p className="text-on-surface-low text-[0.75rem]">
            {ws.alive ? 'Nothing changed in the workspace.' : 'The workspace is gone; its work is on the branch below.'}
          </p>
        ) : (
          <ul data-testid="changed-files" className="flex max-h-72 flex-col gap-2xs overflow-auto">
            {ws.changed.map((c) => (
              <li key={c.path} className="flex items-center gap-s rounded-md bg-surface px-2.5 py-1.5 text-[0.75rem]">
                <FileDiff size={13} className="shrink-0 text-on-surface-low" />
                <span className="min-w-0 flex-1 truncate font-mono text-on-surface">{c.path}</span>
                <span className="shrink-0 text-on-surface-low">{c.status}</span>
                {/* Staged and unstaged are separate facts, not a detail: "discard" means something
                    different for each, so collapsing them would make the state ambiguous. */}
                {c.staged && <span className="shrink-0 text-on-surface-var text-[0.6875rem]">staged</span>}
              </li>
            ))}
          </ul>
        )}
      </Block>

      {/* Setup failures live HERE rather than on the run's error line, because they did NOT fail
          the run (S52's contract) — but they are the first thing to check when a stage failed on a
          missing dependency. */}
      {setup?.failed?.length ? (
        <Block label={`Setup failures (${setup.failed.length})`}>
          <ul data-testid="setup-failed" className="flex flex-col gap-2xs">
            {setup.failed.map((f, i) => (
              <li key={i} className="rounded-md bg-surface px-2.5 py-1.5 font-mono text-on-surface-var text-[0.6875rem] break-words">
                {f}
              </li>
            ))}
          </ul>
          <p className="text-on-surface-low text-[0.75rem]">
            Setup never blocks a run — these are recorded so a stage that failed on a missing
            dependency has an explanation in reach.
          </p>
        </Block>
      ) : null}

      {conflicts.length > 0 && (
        <Block label={`Conflicts (${conflicts.length})`}>
          <ul data-testid="workspace-conflicts" className="flex flex-col gap-2xs">
            {conflicts.map((p) => (
              <li key={p} className="rounded-md bg-surface px-2.5 py-1.5 font-mono text-on-surface text-[0.75rem] break-all">{p}</li>
            ))}
          </ul>
        </Block>
      )}

      {offer?.verbs?.length ? (
        <Block label="Take this work">
          <ul data-testid="reintegration-verbs" className="flex flex-col gap-s">
            {offer.verbs.map((v) => (
              <li key={v.verb} className="flex flex-col gap-2xs rounded-md bg-surface px-3 py-2">
                <div className="flex items-center gap-s">
                  <span className="text-on-surface text-[0.8125rem]">{v.label}</span>
                  {!v.safe && (
                    <span data-testid={`unsafe-${v.verb}`} className="text-on-surface-low text-[0.6875rem]">
                      conflicts
                    </span>
                  )}
                </div>
                <p className="text-on-surface-low text-[0.75rem]">{v.detail}</p>
                <code className="break-all font-mono text-on-surface-var text-[0.6875rem]">
                  {verbCommand(v.verb, offer.branch)}
                </code>
              </li>
            ))}
          </ul>
          <p data-testid="reintegration-note" className="text-on-surface-low text-[0.75rem]">{offer.note}</p>
        </Block>
      ) : null}
    </>
  )
}
