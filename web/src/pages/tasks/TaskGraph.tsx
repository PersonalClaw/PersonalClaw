import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { GitFork, Route, TriangleAlert, Activity } from 'lucide-react'
import { api, type TaskItem, type DependencyAnalysis } from '../../lib/api'
import { statusMeta, priorityMeta, TERMINAL } from './taskMeta'
import { depMap, layeredLayout, cyclicNodes } from './dag'
import { DagView, type DagNode, type DagEdge, type DagNodeState } from './DagView'
import { EmptyState } from '../../ui/ListScaffold'

/** Task status → DAG node state. A cycle node is an error; otherwise a blocked task
 *  is gated (pulsing ring), in_progress is active, terminal is done, else todo. */
function nodeStateFor(status: string, bad: boolean): DagNodeState {
  if (bad) return 'error'
  if (status === 'blocked') return 'blocked'
  if (status === 'in_progress') return 'active'
  if (TERMINAL.has(status)) return 'done'
  return 'todo'
}

/** DAG view, flowing TOP → BOTTOM: prerequisites sit above the tasks that
 *  depend on them; edges point downward. To stay within the available width
 *  (never scroll sideways), each dependency layer is WRAPPED into as many
 *  sub-rows as needed to fit the measured container width — so a layer with
 *  many independent tasks stacks downward instead of overflowing. Cycle nodes
 *  are flagged red. Click a node to open its detail panel. */
const NODE_W = 210, NODE_H = 58, ROW_GAP = 64, COL_GAP = 28, PAD = 24, RADIUS = 12

export function TaskGraph({ tasks, onOpen }: { tasks: TaskItem[]; onOpen: (id: string) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [containerW, setContainerW] = useState(0)
  // Server-authoritative dependency analysis (critical path, bottlenecks,
  // completion %) — computed by /api/tasks/graph; refetched as the task set
  // changes so the summary + critical-path highlight stay in sync.
  const [analysis, setAnalysis] = useState<DependencyAnalysis | null>(null)
  const sig = tasks.map((t) => `${t.id}:${t.status}`).join(',')
  useEffect(() => {
    let alive = true
    api.taskGraph().then((g) => { if (alive) setAnalysis(g.analysis) }).catch(() => {})
    return () => { alive = false }
  }, [sig])
  const criticalSet = useMemo(() => new Set(analysis?.critical_path ?? []), [analysis])
  const titleById = useMemo(() => new Map(tasks.map((t) => [t.id, t.title])), [tasks])
  useLayoutEffect(() => {
    if (!ref.current) return
    const el = ref.current
    const update = () => setContainerW(el.clientWidth)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const { nodes, edges, height, cyclic } = useMemo(() => {
    const m = depMap(tasks)
    const { nodes: layout } = layeredLayout(m)
    const cyclic = cyclicNodes(m)
    const byId = new Map(tasks.map((t) => [t.id, t]))

    const innerW = Math.max(NODE_W, (containerW || 960) - PAD * 2)
    const cols = Math.max(1, Math.floor((innerW + COL_GAP) / (NODE_W + COL_GAP)))

    // group node ids by layer (preserve insertion order for stable rows)
    const byLayer = new Map<number, string[]>()
    for (const n of layout.values()) { const a = byLayer.get(n.layer) ?? []; a.push(n.id); byLayer.set(n.layer, a) }
    const layers = [...byLayer.keys()].sort((a, b) => a - b)

    const pos = new Map<string, { x: number; y: number; t: TaskItem }>()
    let rowCursor = 0 // in node-row units, accumulated across layers
    for (const l of layers) {
      const ids = byLayer.get(l)!
      const subRows = Math.ceil(ids.length / cols)
      for (let r = 0; r < subRows; r++) {
        const rowIds = ids.slice(r * cols, r * cols + cols)
        const m2 = rowIds.length
        const totalW = m2 * NODE_W + (m2 - 1) * COL_GAP
        const startX = PAD + Math.max(0, (innerW - totalW) / 2)
        rowIds.forEach((id, c) => {
          const t = byId.get(id); if (!t) return
          pos.set(id, { x: startX + c * (NODE_W + COL_GAP), y: PAD + (rowCursor + r) * (NODE_H + ROW_GAP), t })
        })
      }
      rowCursor += subRows
    }

    const edges: { id: string; from: string; to: string; x1: number; y1: number; x2: number; y2: number; bad: boolean; active: boolean }[] = []
    for (const [id, deps] of m) {
      const to = pos.get(id); if (!to) continue
      for (const d of deps) {
        const from = pos.get(d); if (!from) continue
        // An edge is ACTIVE when work is flowing into the dependent: the prerequisite
        // is done and the dependent is in progress (the "unblocked, now running" hop).
        const active = from.t.status === 'done' && to.t.status === 'in_progress'
        edges.push({ id: `${d}->${id}`, from: d, to: id, x1: from.x + NODE_W / 2, y1: from.y + NODE_H, x2: to.x + NODE_W / 2, y2: to.y, bad: cyclic.has(id) && cyclic.has(d), active })
      }
    }
    const height = PAD * 2 + Math.max(1, rowCursor) * (NODE_H + ROW_GAP) - ROW_GAP
    return { nodes: [...pos.values()], edges, height, cyclic, cols }
  }, [tasks, containerW])

  const svgW = Math.max(NODE_W + PAD * 2, containerW || 0)

  const bottlenecks = analysis?.bottleneck_tasks ?? []

  return (
    <div ref={ref} className="rounded-xl bg-surface-container/40 p-2">
      {tasks.length === 0 ? (
        <EmptyState icon={GitFork} title="No tasks to graph" hint="Add tasks and link prerequisites to see the dependency DAG." />
      ) : (
        <>
        {analysis && (
          <div className="mb-2 flex flex-wrap items-center gap-x-l gap-y-1 px-2 py-1.5 text-[0.75rem] text-on-surface-low">
            <span className="inline-flex items-center gap-1.5"><Activity size={13} className="text-ok" /> {Math.round(analysis.completion_pct)}% complete</span>
            <span className="inline-flex items-center gap-1.5"><Route size={13} className="text-primary" /> Critical path: {criticalSet.size} {criticalSet.size === 1 ? 'task' : 'tasks'}</span>
            {bottlenecks.length > 0 && (
              <span className="inline-flex items-center gap-1.5" title={bottlenecks.slice(0, 5).map((b) => `${titleById.get(b.id) ?? b.id} (${b.dependents})`).join('\n')}>
                <GitFork size={13} className="text-warn" /> {bottlenecks.length} {bottlenecks.length === 1 ? 'bottleneck' : 'bottlenecks'}
              </span>
            )}
            {(analysis.cycles?.length ?? 0) > 0 && (
              <span className="inline-flex items-center gap-1.5 text-danger"><TriangleAlert size={13} /> {analysis.cycles.length} cycle{analysis.cycles.length === 1 ? '' : 's'} detected</span>
            )}
            {criticalSet.size > 0 && <span className="text-on-surface-low/70">— critical-path tasks are ringed below</span>}
          </div>
        )}
        <DagView width={svgW} height={height} className="block" onNodeClick={onOpen}
          edges={edges.map((e): DagEdge => ({ id: e.id, from: e.from, to: e.to, x1: e.x1, y1: e.y1, x2: e.x2, y2: e.y2, bad: e.bad, active: e.active }))}
          nodes={nodes.map(({ x, y, t }): DagNode => {
            const sm = statusMeta(t.status)
            const pm = priorityMeta(t.priority)
            const bad = cyclic.has(t.id)
            const done = TERMINAL.has(t.status)
            return {
              id: t.id, x, y, w: NODE_W, h: NODE_H, radius: RADIUS,
              state: nodeStateFor(t.status, bad),
              accent: sm.tone,
              ringed: criticalSet.has(t.id),
              content: (
                <div className="flex h-full flex-col justify-center">
                  <div className={`truncate text-[0.8125rem] leading-tight ${done ? 'line-through opacity-60' : ''}`} style={{ color: 'var(--color-on-surface)', fontVariationSettings: '"wght" 500' }}>{t.title}</div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-[0.65rem]" style={{ color: 'var(--color-on-surface-low)' }}>
                    <span style={{ color: sm.tone }}>{sm.label}</span>
                    <span style={{ color: pm.tone }}>· {pm.label}</span>
                  </div>
                </div>
              ),
            }
          })} />
        </>
      )}
    </div>
  )
}
