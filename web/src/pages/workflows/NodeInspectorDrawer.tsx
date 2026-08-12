import { useEffect, useState } from 'react'
import { ScanSearch, Link2, Package } from 'lucide-react'
import { SidePanel } from '../../ui/SidePanel'
import { Skeleton } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { api, ApiError, type NodeInspect } from '../../lib/api'
import { accentChip } from '../../design/accent'

/** The per-node inspector drawer (WORKFLOWS-V2 / WV-10).
 *
 *  A read-only forensics view over ONE terminal node's §5 reconstructability set — the resolved
 *  prompt, the resolved inputs, the output, the attempt records, this node's ledger slice, and
 *  whether the output was served from cache. It fetches `GET …/nodes/{node_id}/inspect` on open
 *  (never eagerly — the run view renders many nodes and inspecting is a deliberate act) via
 *  `api.workflowRunNodeInspect`, the sole caller of a client method WV-9 shipped.
 *
 *  SECRETS ARE ALREADY ABSENT. Every text field arrives redacted by the SAME journal redactor the
 *  ledger writer uses (WV-9). This component renders what it receives verbatim — a prompt or output
 *  that was too large to inline arrives as a `{ ref }` / `{ artifact_ref }` POINTER, which is shown
 *  as a monospace label, NOT dereferenced: fetching the raw blob would be the reconstructability
 *  path the redaction deliberately closed.
 *
 *  A non-terminal node has nothing to reconstruct yet — the endpoint 409s — so the run view gates
 *  the affordance on `isNodeTerminal`. The drawer still handles the 409 (and a 404) gracefully as a
 *  defence in depth: a node that flipped state between the click and the fetch renders an inline
 *  message, never a crash or a blank panel. */
export function NodeInspectorDrawer({ runId, nodeId, onClose }: {
  runId: string
  nodeId: string
  onClose: () => void
}) {
  const [data, setData] = useState<NodeInspect | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    setData(null)
    api.workflowRunNodeInspect(runId, nodeId)
      .then((d) => { if (live) setData(d) })
      .catch((e) => {
        if (!live) return
        // 409 = the node is not terminal yet (its state changed since the row was drawn); 404 = the
        // run or node is gone. Both are expected, actionable states — surfaced as a calm inline note
        // rather than a thrown error that would blank the drawer.
        if (e instanceof ApiError && e.status === 409) {
          setError('This node has not finished yet — there is nothing to reconstruct until it reaches a terminal state.')
        } else if (e instanceof ApiError && e.status === 404) {
          setError('This node could not be found. The run may have been deleted.')
        } else {
          setError(e instanceof Error ? e.message : 'Could not load this node.')
        }
      })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId, nodeId])

  return (
    <SidePanel
      fillHeight
      storeKey="wf-node-inspect-w"
      icon={<ScanSearch size={18} className="text-primary" />}
      title={<span className="font-mono text-[1.0625rem]">{nodeId}</span>}
      onClose={onClose}
    >
      <div data-testid="node-inspector-body" className="flex flex-col gap-l">
        {loading ? (
          <div role="status" aria-busy="true" aria-label="Loading node detail" className="flex flex-col gap-l">
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : error ? (
          <InlineError icon multiline>{error}</InlineError>
        ) : data ? (
          <NodeInspectBody data={data} />
        ) : null}
      </div>
    </SidePanel>
  )
}

/** A `{ ref }` / `{ artifact_ref }` payload is a POINTER to spilled content, not the content —
 *  narrow it here so the body renders a label rather than trying to stringify an object. */
function refOf(v: unknown): string | null {
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    if (typeof o.ref === 'string') return o.ref
    if (typeof o.artifact_ref === 'string') return o.artifact_ref
  }
  return null
}

/** A monospace chip standing in for content that was offloaded past the inline boundary. It is a
 *  LABEL, never a link: dereferencing the ref would re-fetch raw bytes the redaction pass spilled
 *  precisely to keep out of the browser. */
function RefChip({ icon: Icon, label, value }: { icon: typeof Link2; label: string; value: string }) {
  return (
    <span
      data-testid="ref-chip"
      className="inline-flex items-center gap-1.5 rounded-pill bg-surface-high px-2.5 py-1 font-mono text-on-surface-var text-[0.75rem]"
      title={`${label} — stored out-of-line; not fetched`}
    >
      <Icon size={13} className="shrink-0 text-on-surface-low" />
      {value}
    </span>
  )
}

function FieldBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-xs">
      <h3 data-type="label-m" className="text-on-surface-low">{label}</h3>
      {children}
    </section>
  )
}

/** Render an inline text value as a scrollable code block. Non-string values (inputs, attempts,
 *  ledger rows) are pretty-printed as JSON — the redactor already ran over them, so this is a
 *  faithful view of what the node saw/produced. */
function CodeBlock({ text, testid, maxH = 'max-h-72' }: { text: string; testid?: string; maxH?: string }) {
  return (
    <pre
      data-testid={testid}
      className={`${maxH} overflow-auto rounded-md bg-surface px-3 py-2 text-on-surface text-[0.75rem] whitespace-pre-wrap break-words`}
    >
      {text}
    </pre>
  )
}

function toJson(v: unknown): string {
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

function NodeInspectBody({ data }: { data: NodeInspect }) {
  const promptRef = refOf(data.resolved_prompt)
  const outputRef = refOf(data.output)
  const inputKeys = Object.keys(data.resolved_inputs ?? {})

  return (
    <>
      {/* cached badge — was this node's output served from the resume cache, or freshly produced. */}
      <div className="flex items-center gap-m text-[0.75rem]">
        <span data-type="label-m" className="text-on-surface-low">state</span>
        <span className="text-on-surface">{data.state}</span>
        <span
          data-testid="cached-badge"
          className="inline-flex items-center rounded-pill px-2 py-0.5 text-[0.6875rem]"
          style={data.cached
            ? accentChip
            : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}
          title={data.cached ? 'Output served from the resume cache' : 'Freshly produced this run'}
        >
          {data.cached ? 'cached' : 'fresh'}
        </span>
      </div>

      <FieldBlock label="Resolved prompt">
        {promptRef !== null ? (
          <RefChip icon={Link2} label="prompt ref" value={promptRef} />
        ) : (
          <CodeBlock testid="resolved-prompt" text={String(data.resolved_prompt ?? '')} />
        )}
      </FieldBlock>

      <FieldBlock label="Resolved inputs">
        {inputKeys.length === 0 ? (
          <p className="text-on-surface-low text-[0.75rem]">No inputs bound.</p>
        ) : (
          <CodeBlock testid="resolved-inputs" text={toJson(data.resolved_inputs)} />
        )}
      </FieldBlock>

      <FieldBlock label="Output">
        {outputRef !== null ? (
          <RefChip icon={Package} label="artifact ref" value={outputRef} />
        ) : (
          <CodeBlock testid="output" text={typeof data.output === 'string' ? data.output : toJson(data.output)} />
        )}
      </FieldBlock>

      <FieldBlock label={`Attempts${data.attempts.length ? ` (${data.attempts.length})` : ''}`}>
        {data.attempts.length === 0 ? (
          <p className="text-on-surface-low text-[0.75rem]">No attempt records.</p>
        ) : (
          <ol data-testid="attempts" className="flex flex-col gap-2xs">
            {data.attempts.map((a, i) => {
              const status = typeof a.status === 'string' ? a.status
                : typeof a.state === 'string' ? a.state : 'attempt'
              return (
                <li key={i} className="flex items-center gap-s rounded-md bg-surface px-2.5 py-1.5 text-[0.75rem]">
                  <span className="tabular-nums text-on-surface-low">#{i + 1}</span>
                  <span className="text-on-surface">{status}</span>
                </li>
              )
            })}
          </ol>
        )}
      </FieldBlock>

      <FieldBlock label={`Ledger events${data.ledger_events.length ? ` (${data.ledger_events.length})` : ''}`}>
        {data.ledger_events.length === 0 ? (
          <p className="text-on-surface-low text-[0.75rem]">No ledger events for this node.</p>
        ) : (
          <ul data-testid="ledger-events" className="max-h-72 overflow-auto flex flex-col gap-2xs">
            {data.ledger_events.map((e, i) => {
              const kind = typeof e.kind === 'string' ? e.kind : 'event'
              return (
                <li key={i} className="rounded-md bg-surface px-2.5 py-1.5 font-mono text-on-surface-var text-[0.6875rem]">
                  {kind}
                </li>
              )
            })}
          </ul>
        )}
      </FieldBlock>
    </>
  )
}
