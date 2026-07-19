import { useEffect, useMemo, useState } from 'react'
import { Pencil, Trash2, Check, X, Beaker, Loader2, ArrowRight, Lock, ArrowUpFromLine } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { Markdown } from '../../ui/Markdown'
import { confirmDelete } from '../../ui/dialog'
import { api, type WorkflowItem, type WorkflowMatch, type WorkflowScope, type WorkflowGraph } from '../../lib/api'
import { scopeMeta, relTime } from './workflowMeta'
import { WorkflowForm, toDraft, draftToPayload, type WorkflowDraft } from './WorkflowForm'
import { DagView, type DagNode, type DagEdge } from '../tasks/DagView'
import { layeredLayout, cyclicNodes, type DepMap } from '../tasks/dag'

/** Workflow inspector for the SidePanel: view ↔ in-panel edit (same pattern as
 *  TaskDetail), the numbered steps, and a "Test match" box that calls
 *  /api/workflows/preview-match so you can see whether this SOP would auto-fire
 *  for a sample turn. Non-native providers are read-only. */
export function WorkflowDetail({ workflow, onSaved, onDeleted, editing: editingProp, onEditingChange, allWorkflows = [] }: {
  workflow: WorkflowItem
  onSaved: (w: WorkflowItem) => void
  onDeleted: () => void
  editing: boolean
  onEditingChange: (v: boolean) => void
  allWorkflows?: WorkflowItem[]
}) {
  const [draft, setDraft] = useState<WorkflowDraft>(() => toDraft(workflow))
  const [saving, setSaving] = useState(false)
  const [promoting, setPromoting] = useState(false)
  const [err, setErr] = useState('')
  const readOnly = !!workflow.provider && workflow.provider !== 'native'
  const sm = scopeMeta(workflow.scope)
  // Edit mode is owned by the URL (?edit=1) and threaded in fully controlled — a
  // read-only workflow can never show the edit form even if the flag is hand-set.
  const editing = editingProp && !readOnly
  const setEditing = onEditingChange

  useEffect(() => { setDraft(toDraft(workflow)) }, [workflow.id])

  async function save() {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    setSaving(true); setErr('')
    try { const u = await api.updateWorkflow(workflow.id, draftToPayload(draft)); onSaved(u); setEditing(false) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (!(await confirmDelete('workflow', workflow.name))) return
    try { await api.deleteWorkflow(workflow.id); onDeleted() } catch { setErr('Delete failed') }
  }
  async function toggleEnabled() {
    const u = await api.updateWorkflow(workflow.id, { enabled: !workflow.enabled }).catch(() => null)
    if (u) onSaved(u)
  }
  // Scope-widening: promote toward GLOBAL (reachable everywhere). Intermediate
  // scopes (agent/workspace) need a scope_ref pick, so the one-click action targets
  // global — the unambiguous "make this available everywhere". Hidden once global.
  const nextScope: WorkflowScope | null = workflow.scope !== 'global' ? 'global' : null
  async function promote() {
    if (!nextScope || promoting) return
    setPromoting(true); setErr('')
    try { const u = await api.promoteWorkflow(workflow.id, nextScope); onSaved(u) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Promote failed') }
    finally { setPromoting(false) }
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-l">
        <WorkflowForm draft={draft} onChange={setDraft} compact allWorkflows={allWorkflows} />
        {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
        <div className="sticky bottom-0 -mx-l px-l py-3 bg-surface/95 border-t border-outline-variant/40 flex justify-end gap-s">
          <Button variant="ghost" size="sm" onClick={() => { setDraft(toDraft(workflow)); setEditing(false); setErr('') }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} disabled={saving || !draft.name.trim()}><Check size={15} /> {saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-l">
      <div className="flex items-center gap-s">
        {readOnly ? (
          <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]"><Lock size={13} /> {workflow.provider} — read-only</span>
        ) : (
          <>
            <Button size="sm" variant="secondary" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</Button>
            {nextScope && (
              <Button size="sm" variant="ghost" onClick={promote} disabled={promoting}>
                {promoting ? <Loader2 size={14} className="animate-spin" /> : <ArrowUpFromLine size={14} />} Promote to {nextScope}
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
          </>
        )}
        <label className={`ml-auto inline-flex items-center gap-2 text-[0.8125rem] ${readOnly ? 'opacity-50' : 'cursor-pointer'}`}>
          <span className="text-on-surface-var">{workflow.enabled ? 'Enabled' : 'Disabled'}</span>
          <Toggle on={!!workflow.enabled} disabled={readOnly} onChange={toggleEnabled} size="sm" />
        </label>
      </div>
      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}

      <div className="flex flex-wrap items-center gap-s">
        <span className="inline-flex items-center rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${sm.tone} 16%, transparent)`, color: sm.tone }}>{sm.label}</span>
        {workflow.scope_ref && <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center font-mono text-on-surface-var text-[0.75rem]">{workflow.scope_ref}</span>}
        {workflow.updated_at && <span className="text-on-surface-low text-[0.8125rem]">{relTime(workflow.updated_at)}</span>}
      </div>

      {workflow.description && <p className="text-on-surface text-[0.9375rem] leading-relaxed">{workflow.description}</p>}

      {workflow.match_text && (
        <Section label="Fires when">
          <p className="text-on-surface-var text-[0.875rem] leading-relaxed italic">“{workflow.match_text}”</p>
        </Section>
      )}

      {(workflow.tags?.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-1.5">{workflow.tags!.map((t) => <span key={t} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{t}</span>)}</div>
      )}

      <Section label={`Steps · ${workflow.steps.length}`}>
        <ol className="flex flex-col gap-m">
          {workflow.steps.map((s, i) => {
            const refWf = s.ref ? allWorkflows.find((w) => w.id === s.ref) : null
            return (
              <li key={s.id ?? i} className="flex gap-m">
                <span className="shrink-0 inline-flex size-6 items-center justify-center rounded-pill text-[0.75rem] tabular-nums" style={{ background: 'color-mix(in srgb, var(--color-primary) 18%, transparent)' }}>{i + 1}</span>
                <div className="flex-1 min-w-0">
                  {s.ref ? (
                    <div className="inline-flex items-center gap-1.5 rounded-md px-m py-1.5 text-[0.875rem]" style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
                      <Beaker size={13} className="text-primary" />
                      <span className="text-on-surface-var">Run workflow</span>
                      <span className="text-on-surface" style={{ fontVariationSettings: '"wght" 600' }}>{refWf?.name ?? s.ref}</span>
                      {!refWf && <span className="text-danger text-[0.7rem]">(missing)</span>}
                    </div>
                  ) : (
                    <>
                      <div className="text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{s.title}</div>
                      {s.instruction && <div className="mt-1 text-on-surface-var text-[0.875rem] leading-relaxed"><Markdown>{s.instruction}</Markdown></div>}
                    </>
                  )}
                </div>
              </li>
            )
          })}
        </ol>
      </Section>

      {workflow.steps.some((s) => s.ref) && <CompositionGraph workflowId={workflow.id} />}

      <TestMatch workflowId={workflow.id} />
    </div>
  )
}

// Node box geometry for the workflow-reference DAG (smaller than the task DAG —
// workflow names are short and the panel is narrow).
const WF_NODE_W = 168, WF_NODE_H = 40, WF_ROW_GAP = 44, WF_COL_GAP = 20, WF_PAD = 8

/** The workflow-REFERENCE DAG: one node per workflow in the composition, edges =
 *  "references" (a ref-step pulling in another workflow). Rendered via the shared
 *  DagView (P17) — the same SVG DAG the Tasks graph uses — so the composition's real
 *  shape is visible, not just the flattened step list. A workflow caught in a
 *  reference cycle is flagged error-toned (matching the cycle banner). */
function WorkflowDag({ graph }: { graph: WorkflowGraph }) {
  const { nodes, edges, height } = useMemo(() => {
    // depMap: workflow id → the workflows it REFERENCES (its prerequisites, drawn
    // above it). Normalize the edge shape (from|source → to|target).
    const m: DepMap = new Map(graph.nodes.map((n) => [n.id, []]))
    for (const e of graph.edges) {
      const from = e.from ?? e.source, to = e.to ?? e.target
      if (!from || !to || !m.has(from)) continue
      // `from` references `to` → `to` is a dependency of `from` (edge to→from, upward).
      m.get(from)!.push(to)
    }
    const bad = cyclicNodes(m)
    const { nodes: layout } = layeredLayout(m)

    // Group by layer → assign x within each layer's row (centered), y by layer.
    const byLayer = new Map<number, string[]>()
    for (const n of layout.values()) { const a = byLayer.get(n.layer) ?? []; a.push(n.id); byLayer.set(n.layer, a) }
    const layers = [...byLayer.keys()].sort((a, b) => a - b)
    const maxCols = Math.max(1, ...[...byLayer.values()].map((a) => a.length))
    const innerW = maxCols * WF_NODE_W + (maxCols - 1) * WF_COL_GAP
    const pos = new Map<string, { x: number; y: number }>()
    layers.forEach((l, li) => {
      const ids = byLayer.get(l)!
      const totalW = ids.length * WF_NODE_W + (ids.length - 1) * WF_COL_GAP
      const startX = WF_PAD + Math.max(0, (innerW - totalW) / 2)
      ids.forEach((id, c) => pos.set(id, { x: startX + c * (WF_NODE_W + WF_COL_GAP), y: WF_PAD + li * (WF_NODE_H + WF_ROW_GAP) }))
    })

    const dagNodes: DagNode[] = graph.nodes.map((n): DagNode => {
      const p = pos.get(n.id) ?? { x: WF_PAD, y: WF_PAD }
      return {
        id: n.id, x: p.x, y: p.y, w: WF_NODE_W, h: WF_NODE_H, radius: 10,
        state: bad.has(n.id) ? 'error' : 'todo',
        content: (
          <div className="flex h-full items-center">
            <span className="truncate text-[0.75rem]" style={{ color: 'var(--color-on-surface)', fontVariationSettings: '"wght" 500' }}>{n.name}</span>
          </div>
        ),
      }
    })
    const dagEdges: DagEdge[] = []
    for (const [id, deps] of m) {
      const to = pos.get(id); if (!to) continue
      for (const d of deps) {
        const from = pos.get(d); if (!from) continue
        dagEdges.push({ id: `${d}->${id}`, from: d, to: id, x1: from.x + WF_NODE_W / 2, y1: from.y + WF_NODE_H, x2: to.x + WF_NODE_W / 2, y2: to.y, bad: bad.has(id) && bad.has(d) })
      }
    }
    const h = WF_PAD * 2 + layers.length * (WF_NODE_H + WF_ROW_GAP) - WF_ROW_GAP
    return { nodes: dagNodes, edges: dagEdges, height: Math.max(WF_NODE_H + WF_PAD * 2, h) }
  }, [graph])

  if (nodes.length < 2) return null // a single-node "graph" isn't worth drawing
  return <div className="mb-3 overflow-x-auto rounded-lg bg-surface-container/40 p-2"><DagView nodes={nodes} edges={edges} width={640} height={height} /></div>
}

/** Composition view for a workflow that references others: the depth-expanded step
 *  tree from /graph plus any detected reference cycles (the server-authoritative
 *  DAG). Only rendered when the workflow actually composes other workflows. */
function CompositionGraph({ workflowId }: { workflowId: string }) {
  const [g, setG] = useState<WorkflowGraph | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    api.workflowGraph(workflowId).then((d) => { if (alive) setG(d) }).catch(() => { if (alive) setG(null) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [workflowId])
  if (loading) return <Section label="Composition"><Loader2 size={15} className="animate-spin text-on-surface-low" /></Section>
  if (!g) return null
  return (
    <Section label={`Composition · ${g.nodes.length} workflows`}>
      {g.cycles.length > 0 && (
        <div className="mb-2 flex items-start gap-1.5 rounded-md px-2.5 py-1.5 text-[0.8rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}>
          <X size={13} className="mt-0.5 shrink-0" />
          <span>Reference cycle detected: {g.cycles.map((c) => c.join(' → ')).join('; ')}</span>
        </div>
      )}
      {/* Workflow-reference DAG — the real composition shape (which workflow pulls in
          which), rendered via the shared DagView. Above the flattened step list. */}
      <WorkflowDag graph={g} />
      {/* Expanded step tree — depth>0 = a step pulled in from a referenced (nested)
          workflow; indented + tagged with its source_workflow provenance. depth is the
          real nesting signal (the backend emits {title, source_workflow, depth}). */}
      <ol className="flex flex-col gap-1.5">
        {g.expanded.map((step, i) => {
          const depth = step.depth ?? 0
          const nested = depth > 0
          return (
            <li key={i} className="flex items-start gap-2 text-[0.8125rem]" style={{ paddingLeft: depth * 16 }}>
              <span className="shrink-0 text-on-surface-low tabular-nums">{i + 1}.</span>
              {nested && <Beaker size={12} className="mt-0.5 shrink-0 text-primary" />}
              <span className="min-w-0">
                <span className={nested ? 'text-on-surface-var' : 'text-on-surface'} style={nested ? undefined : { fontVariationSettings: '"wght" 550' }}>{step.title}</span>
                {step.source_workflow && <span className="ml-1.5 text-on-surface-low text-[0.7rem]">· from {step.source_workflow}</span>}
              </span>
            </li>
          )
        })}
      </ol>
    </Section>
  )
}

/** Type a sample turn → preview-match shows if THIS workflow would be selected. */
function TestMatch({ workflowId }: { workflowId: string }) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState<WorkflowMatch | null>(null)
  const [loading, setLoading] = useState(false)

  async function run() {
    const query = q.trim(); if (!query) return
    setLoading(true); setRes(null)
    try { setRes(await api.previewWorkflowMatch(query)) } catch { /* ignore */ } finally { setLoading(false) }
  }

  const eligible = res?.eligible.some((e) => e.id === workflowId)
  const isMatch = res?.match?.id === workflowId

  return (
    <Section label="Test match">
      <div className="flex items-end gap-s">
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') run() }}
          name="workflow-test-turn" aria-label="Sample turn to test workflow match"
          placeholder="Type a sample turn to test…" className="flex-1 h-9 rounded-md bg-surface-container px-m text-on-surface text-[0.875rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <Button size="sm" onClick={run} disabled={loading || !q.trim()}>{loading ? <Loader2 size={15} className="animate-spin" /> : <Beaker size={15} />} Test</Button>
      </div>
      {res && (
        <div className="mt-2 rounded-md bg-surface-container px-m py-2 text-[0.8125rem]">
          {isMatch ? (
            <span className="inline-flex items-center gap-1.5 text-ok"><Check size={14} /> This SOP would fire — score {res.match!.score.toFixed(2)} ({res.match!.method})</span>
          ) : eligible ? (
            <span className="inline-flex items-center gap-1.5 text-warn"><ArrowRight size={14} /> Eligible, but {res.match ? `“${res.match.name}” won (${res.match.score.toFixed(2)})` : 'no SOP selected'}</span>
          ) : (
            <span className="text-on-surface-low">Not eligible for this turn{res.match ? ` — “${res.match.name}” matched instead` : ''}.</span>
          )}
        </div>
      )}
    </Section>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
