import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Play } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { Loading } from '../../ui/ListScaffold'
import { QuietButton } from '../../ui/QuietButton'
import { Button } from '../../ui/Button'
import { Field, TextInput } from '../../ui/forms'
import { api, type WorkflowDef, type WorkflowNode } from '../../lib/api'
import { notify } from '../../app/appSdk'

interface FlatNode { depth: number; kind: string; id: string; label: string; summary: string }

/** Flatten a spec tree for display, one row per node.
 *
 *  Rendered as an indented list rather than a graph: a workflow's shape IS a tree (the
 *  engine's own model), and a force-directed graph of six nodes communicates less than six
 *  indented lines. The label carries what a reader needs to identify the node; the summary
 *  carries the one config field that says what it DOES. */
function flatten(node: WorkflowNode, depth = 0, label = ''): FlatNode[] {
  const cfg = node.config ?? {}
  const summary =
    typeof cfg.prompt === 'string' ? cfg.prompt
      : typeof cfg.expr === 'string' ? cfg.expr
      : typeof cfg.provider === 'string' ? `provider: ${cfg.provider}`
      : typeof cfg.kind === 'string' ? `${cfg.kind} gate`
      : ''
  const out: FlatNode[] = [{
    depth,
    kind: node.kind,
    id: node.id ?? '',
    label,
    summary: typeof summary === 'string' ? summary.replace(/\s+/g, ' ').slice(0, 140) : '',
  }]
  for (const child of node.children ?? []) out.push(...flatten(child, depth + 1))
  if (node.body) out.push(...flatten(node.body, depth + 1, 'body'))
  for (const [caseLabel, caseNode] of Object.entries(node.cases ?? {})) {
    out.push(...flatten(caseNode, depth + 1, `case ${caseLabel}`))
  }
  if (node.default) out.push(...flatten(node.default, depth + 1, 'default'))
  return out
}

/** One workflow definition — its tree, its declared inputs, and a way to run it.
 *
 *  Read-mostly by design: authoring a spec by hand is what `workflow_author` and the chat
 *  planner are for, and a half-built visual editor here would be worse than either. What
 *  this page owes the user is an honest view of what the workflow DOES plus the ability to
 *  supply inputs and start it. */
export function WorkflowDefDetail({ name, onBack, onStarted }: {
  name: string
  onBack: () => void
  onStarted: (runId: string) => void
}) {
  const [def, setDef] = useState<WorkflowDef | null>(null)
  const [loading, setLoading] = useState(true)
  const [inputs, setInputs] = useState<Record<string, string>>({})
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    api.workflowDef(name)
      .then((d) => { if (alive) setDef(d.definition) })
      .catch(() => { if (alive) setDef(null) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [name])

  const rows = useMemo(() => (def ? flatten(def.root) : []), [def])
  const declared = useMemo(() => Object.entries(def?.inputs ?? {}), [def])

  const start = useCallback(async () => {
    setStarting(true)
    try {
      // Declared inputs are typed on the backend; the form collects strings and only sends
      // the ones actually filled, so an untouched optional input keeps its default rather
      // than being overridden with "".
      const payload: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(inputs)) if (value !== '') payload[key] = value
      const res = await api.startWorkflowRun({ name, inputs: payload })
      onStarted(res.run_id)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not start the workflow')
    } finally {
      setStarting(false)
    }
  }, [inputs, name, onStarted])

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<div className="flex min-w-0 items-center gap-m">
          <QuietButton onClick={onBack} title="Back to workflows"><ArrowLeft size={13} /> Workflows</QuietButton>
          <span data-type="title-l" className="truncate text-on-surface">{name}</span>
          {def?.source === 'bundled' && <span className="shrink-0 text-on-surface-low text-[0.75rem]">bundled</span>}
        </div>}
        right={def ? (
          <Button onClick={start} loading={starting} disabled={starting}>
            <Play size={14} /> Run
          </Button>
        ) : undefined}
      />
      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        {loading ? <Loading /> : !def ? (
          <p className="text-on-surface-low text-[0.8125rem]">This definition could not be loaded.</p>
        ) : (
          <div className="mx-auto flex max-w-[var(--content-width)] flex-col gap-l">
            {def.description && <p className="text-on-surface text-[0.8125rem]">{def.description}</p>}

            {declared.length > 0 && (
              <div className="flex flex-col gap-s">
                <span data-type="title-m" className="text-on-surface">Inputs</span>
                {declared.map(([key, meta]) => (
                  <Field
                    key={key}
                    label={`${key}${meta.required ? ' *' : ''}`}
                    hint={meta.help || (meta.default !== undefined && meta.default !== null ? `Default: ${String(meta.default)}` : undefined)}
                  >
                    <TextInput
                      value={inputs[key] ?? ''}
                      onChange={(v) => setInputs((p) => ({ ...p, [key]: v }))}
                      placeholder={meta.default !== undefined && meta.default !== null ? String(meta.default) : ''}
                      ariaLabel={key}
                    />
                  </Field>
                ))}
              </div>
            )}

            <div className="flex flex-col gap-2xs">
              <span data-type="title-m" className="text-on-surface">Steps</span>
              {rows.map((r, i) => (
                <div
                  key={`${r.depth}-${r.id || r.kind}-${i}`}
                  className="flex items-baseline gap-m py-2xs"
                  style={{ paddingLeft: `calc(${r.depth} * 1rem)` }}
                >
                  <span className="shrink-0 font-mono text-on-surface-low text-[0.75rem]">{r.kind}</span>
                  <div className="min-w-0 flex-1">
                    {r.id && <span className="text-on-surface text-[0.8125rem]">{r.id}</span>}
                    {r.label && <span className="ml-s text-on-surface-low text-[0.75rem]">{r.label}</span>}
                    {r.summary && <div className="truncate text-on-surface-low text-[0.75rem]">{r.summary}</div>}
                  </div>
                </div>
              ))}
            </div>

            {/* Requirements are what preflight will check at start — showing them here means
                a user learns about a missing credential before they press Run, not after. */}
            {def.metadata?.requirements && Object.keys(def.metadata.requirements).length > 0 && (
              <div className="flex flex-col gap-2xs">
                <span data-type="title-m" className="text-on-surface">Requires</span>
                {Object.entries(def.metadata.requirements).map(([group, items]) => (
                  <span key={group} className="text-on-surface-low text-[0.75rem]">
                    {group}: {items.join(', ')}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
