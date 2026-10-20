import { useMemo } from 'react'
import { api, type WorkflowRunStatus } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { Field, FieldError, Segmented } from '../../ui/forms'
import { Combobox } from '../../ui/Combobox'

/** What a "Run finishes" trigger runs after: one workflow run going now (`source_run`), or any run
 *  of a workflow (`source_def`). The two keys the gateway's `run_completed` kind reads for a
 *  workflow run, and exactly one of them is sent. */
export type RunCompletedAfter = 'run' | 'workflow'
export interface RunCompletedDraft { after: RunCompletedAfter; run: string; workflow: string }
export const emptyRunCompleted = (): RunCompletedDraft => ({ after: 'run', run: '', workflow: '' })

/** The statuses of a run still going: the ones a trigger can still wait on. */
const GOING: ReadonlySet<WorkflowRunStatus> = new Set(['running', 'paused', 'needs_input'])

/** The source key and value the create body carries, or null while none is picked. */
export function runCompletedSource(d: RunCompletedDraft): { source_run: string } | { source_def: string } | null {
  if (d.after === 'run') return d.run ? { source_run: d.run } : null
  return d.workflow ? { source_def: d.workflow } : null
}

/** Why the source is not ready yet, in the words the save reason uses, or ''. */
export function runCompletedReason(d: RunCompletedDraft): string {
  if (runCompletedSource(d)) return ''
  return d.after === 'run' ? 'Pick the run it runs after' : 'Pick the workflow it runs after'
}

export function RunCompletedSource({ draft, onChange }: { draft: RunCompletedDraft; onChange: (d: RunCompletedDraft) => void }) {
  // The Workflows page's own reads, under its keys, so the two share one copy of each list.
  const { data: runs, error: runsErr } = useQuery('workflows:runs', () => api.workflowRuns({ limit: 100 }).then((r) => r.runs))
  const { data: defs, error: defsErr } = useQuery('workflows:defs', () => api.workflowDefs().then((d) => d.defs))
  const runOptions = useMemo(() => (runs ?? [])
    .filter((r) => GOING.has(r.status))
    .map((r) => ({ value: r.id, label: `${r.workflow_name} · ${r.id}`, description: r.title || r.status })), [runs])
  const defOptions = useMemo(() => (defs ?? []).map((d) => ({
    value: d.name, label: d.name, description: d.description,
  })), [defs])
  return (
    <>
      <Field label="Runs after" hint={draft.after === 'run'
        ? 'One workflow run that is going now. It fires once, when that run ends.'
        : 'Any run of this workflow. It fires each time one ends.'}>
        <Segmented
          options={[{ key: 'run', label: 'A run going now' }, { key: 'workflow', label: 'Any run of a workflow' }]}
          value={draft.after} onChange={(v) => onChange({ ...draft, after: v as RunCompletedAfter })} />
      </Field>
      {draft.after === 'run' ? (
        <Field label="Run">
          <Combobox options={runOptions} value={draft.run} onChange={(v) => onChange({ ...draft, run: v })}
            placeholder="Pick a run…" emptyText="No workflow run is going now" />
          {runsErr != null && <FieldError className="mt-s">Couldn't load the workflow runs.</FieldError>}
        </Field>
      ) : (
        <Field label="Workflow">
          <Combobox options={defOptions} value={draft.workflow} onChange={(v) => onChange({ ...draft, workflow: v })}
            placeholder="Pick a workflow…" emptyText="No workflows" />
          {defsErr != null && <FieldError className="mt-s">Couldn't load the workflows.</FieldError>}
        </Field>
      )}
    </>
  )
}
