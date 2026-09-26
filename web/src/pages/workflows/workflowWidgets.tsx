import { useEffect, useMemo, useState } from 'react'
import { api, type WorkflowDef, type WorkflowDefSummary } from '../../lib/api'
import { Combobox } from '../../ui/Combobox'
import { InlineError } from '../../ui/InlineError'
import { declaredInputsSchema, schemaProps, SchemaField, SchemaFields, seedArgs, type WidgetMap } from '../tools/schema'

/** The schema widgets a `run-workflow` action declares: `workflow` picks one of your workflows and
 *  `workflow-inputs` fills in that workflow's declared inputs. The schema renderer stays
 *  feature-agnostic, as with `usePromptWidgets`: this hook owns the workflow API.
 *
 *  `workflow` is the name the action config holds now, so the inputs widget renders the fields of
 *  the workflow actually chosen. Before this the form rendered no field at all for the action, so
 *  a trigger saved with no workflow and failed at every fire. */
export function useWorkflowWidgets(needed: boolean, workflow: string): { widgets: WidgetMap } {
  const [defs, setDefs] = useState<WorkflowDefSummary[] | null>(null)
  const [defsError, setDefsError] = useState<unknown>(null)
  const [def, setDef] = useState<WorkflowDef | null>(null)
  const [defError, setDefError] = useState<unknown>(null)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    if (!needed) return
    let alive = true
    api.workflowDefs()
      .then((d) => { if (alive) { setDefs(d.defs); setDefsError(null) } })
      .catch((e) => { if (alive) { setDefs([]); setDefsError(e) } })
    return () => { alive = false }
  }, [needed, retry])

  useEffect(() => {
    if (!needed || !workflow) { setDef(null); setDefError(null); return }
    let alive = true
    api.workflowDef(workflow)
      .then((d) => { if (alive) { setDef(d.definition); setDefError(null) } })
      .catch((e) => { if (alive) { setDef(null); setDefError(e) } })
    return () => { alive = false }
  }, [needed, workflow, retry])

  const widgets: WidgetMap = useMemo(() => ({
    workflow: ({ value, onChange }) => defsError ? (
      <InlineError icon onRetry={() => setRetry((n) => n + 1)}>
        Couldn&rsquo;t load your workflows{(defsError as Error)?.message ? `: ${(defsError as Error).message}` : '.'}
      </InlineError>
    ) : (
      <Combobox
        options={(defs ?? []).map((d) => ({ value: d.name, label: d.name, description: d.description || undefined }))}
        value={String(value ?? '')}
        onChange={onChange}
        placeholder={defs === null ? 'Loading workflows…' : 'Pick a workflow…'}
        emptyText="No workflows"
      />
    ),
    'workflow-inputs': ({ value, onChange }) => (
      <WorkflowInputs workflow={workflow} def={def} error={defError} value={value} onChange={onChange}
        onRetry={() => setRetry((n) => n + 1)} />
    ),
  }), [defs, defsError, def, defError, workflow])

  return { widgets }
}

/** The chosen workflow's declared inputs as labelled fields, written into the action's `inputs`.
 *  A declared default is seeded into the value, so the default the field shows is the one saved. */
function WorkflowInputs({ workflow, def, error, value, onChange, onRetry }: {
  workflow: string
  def: WorkflowDef | null
  error: unknown
  value: unknown
  onChange: (v: unknown) => void
  onRetry: () => void
}) {
  const values = useMemo<Record<string, unknown>>(
    () => (value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}),
    [value],
  )
  const schema = useMemo(() => declaredInputsSchema(def?.inputs), [def])
  const { props, required } = useMemo(() => schemaProps(schema), [schema])

  useEffect(() => {
    if (!def) return
    const seeded = seedArgs(schema)
    const missing = Object.fromEntries(Object.entries(seeded).filter(([k, v]) => values[k] === undefined && v !== ''))
    if (Object.keys(missing).length > 0) onChange({ ...values, ...missing })
  }, [def])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!workflow) return <p className="text-on-surface-low text-[0.8125rem]">Pick a workflow to set its inputs.</p>
  if (error) {
    return (
      <InlineError icon onRetry={onRetry}>
        Couldn&rsquo;t load {workflow}&rsquo;s inputs{(error as Error)?.message ? `: ${(error as Error).message}` : '.'}
      </InlineError>
    )
  }
  if (!def) return <p className="text-on-surface-low text-[0.8125rem]">Loading {workflow}&rsquo;s inputs…</p>
  if (props.length === 0) return <p className="text-on-surface-low text-[0.8125rem]">{workflow} takes no inputs.</p>
  return (
    <div className="rounded-md border border-outline-variant/40 bg-surface-container/40 px-m py-3 flex flex-col gap-m">
      <SchemaFields
        fields={props}
        required={required}
        values={values}
        renderField={(name, s, isRequired) => (
          <SchemaField name={name} schema={s} required={isRequired} value={values[name]}
            onChange={(v) => onChange({ ...values, [name]: v })} />
        )}
      />
    </div>
  )
}
