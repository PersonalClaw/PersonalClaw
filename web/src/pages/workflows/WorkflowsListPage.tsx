import { useCallback, useEffect, useMemo, useState } from 'react'
import { Play, Trash2, Workflow } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { EmptyState, ListRow, Loading } from '../../ui/ListScaffold'
import { SearchField } from '../../ui/SearchField'
import { Segmented } from '../../ui/Segmented'
import { QuietButton } from '../../ui/QuietButton'
import { api, type WorkflowDefSummary, type WorkflowRunSummary } from '../../lib/api'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { confirmDelete } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { fmtElapsed, runLook } from './workflowMeta'

const TABS = [
  { key: 'runs', label: 'Runs' },
  { key: 'defs', label: 'Definitions' },
]

/** Workflows — the list surface (WORKFLOWS-V2 Slice 7b).
 *
 *  Runs are the DEFAULT tab, not definitions: a user coming here is far more often asking
 *  "what is happening / what needs me" than "what templates exist". Runs needing input sort
 *  to the top for the same reason — that is the only row they can act on.
 *
 *  Toolbar state is URL-backed so a filtered view is shareable, matching the other entity
 *  pages. */
export function WorkflowsListPage({ navigate, query: routeQuery, setQuery }: RouteProps) {
  const [tab, setTab] = useQueryParam(routeQuery, setQuery, 'tab', 'runs', { replace: true })
  const [q, setQ] = useQueryParam(routeQuery, setQuery, 'q', '', { replace: true })

  const [defs, setDefs] = useState<WorkflowDefSummary[]>([])
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [d, r] = await Promise.all([
        api.workflowDefs().catch(() => ({ defs: [], total: 0 })),
        api.workflowRuns({ limit: 100 }).catch(() => ({ runs: [], total: 0, limit: 0, offset: 0 })),
      ])
      setDefs(d.defs)
      setRuns(r.runs)
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  const filteredRuns = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const matched = needle
      ? runs.filter((r) => `${r.workflow_name}\n${r.id}\n${r.status}`.toLowerCase().includes(needle))
      : runs
    // needs_input first: it is the only status a user can act on. Everything else keeps the
    // server's newest-first order.
    const rank = (s: string) => (s === 'needs_input' ? 0 : s === 'running' || s === 'paused' ? 1 : 2)
    return [...matched].sort((a, b) => rank(a.status) - rank(b.status))
  }, [runs, q])

  const filteredDefs = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return defs
    return defs.filter((d) => `${d.name}\n${d.description}\n${d.tags.join(' ')}`.toLowerCase().includes(needle))
  }, [defs, q])

  const start = useCallback(async (name: string) => {
    try {
      const res = await api.startWorkflowRun({ name })
      navigate(`workflows/runs/${res.run_id}`)
    } catch (e) {
      // A preflight refusal is the common case and it is ACTIONABLE (missing credential, no
      // model) — surfacing the message is the whole point of failing at start.
      notify(e instanceof Error ? e.message : 'Could not start the workflow')
    }
  }, [navigate])

  const remove = useCallback(async (name: string) => {
    const ok = await confirmDelete('workflow definition', name, {
      body: 'Existing runs keep their own copy of the spec and are unaffected.',
    })
    if (!ok) return
    try {
      await api.deleteWorkflowDef(name)
      load()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not delete the definition')
    }
  }, [load])

  const needingInput = runs.filter((r) => r.status === 'needs_input').length

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={<div className="flex min-w-0 items-center gap-m">
          <span data-type="title-l" className="shrink-0 text-on-surface">Workflows</span>
          {needingInput > 0 && (
            <span className="shrink-0 text-warning text-[0.75rem]">{needingInput} waiting on you</span>
          )}
        </div>}
        right={<div className="flex items-center gap-s">
          <SearchField value={q} onChange={setQ} placeholder="Search runs and definitions" size="md" />
          <Segmented options={TABS} value={tab} onChange={setTab} ariaLabel="Workflows view" />
        </div>}
      />
      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        {loading ? <Loading /> : tab === 'defs' ? (
          filteredDefs.length === 0 ? (
            <EmptyState
              icon={Workflow}
              title={q ? 'No matching definitions' : 'No workflow definitions yet'}
              hint={q
                ? 'Try a different search.'
                : 'Ask in chat to author one — "set up a workflow that…" — or install a template pack.'}
            />
          ) : (
            <div className="flex flex-col gap-xs">
              {filteredDefs.map((d, i) => (
                <ListRow key={d.name} index={i} onClick={() => navigate(`workflows/defs/${d.name}`)}>
                  <div className="flex min-w-0 flex-1 items-center gap-m">
                    <Workflow size={15} className="shrink-0 text-on-surface-low" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-on-surface text-[0.9375rem]">{d.name}</div>
                      {d.description && <div className="truncate text-on-surface-low text-[0.75rem]">{d.description}</div>}
                    </div>
                    <span className="shrink-0 text-on-surface-low text-[0.75rem]">v{d.version}</span>
                    {d.source === 'bundled' && <span className="shrink-0 text-on-surface-low text-[0.75rem]">bundled</span>}
                    <QuietButton onClick={(e) => { e.stopPropagation(); start(d.name) }} title={`Run ${d.name}`}>
                      <Play size={13} /> Run
                    </QuietButton>
                    {d.source !== 'bundled' && (
                      <QuietButton onClick={(e) => { e.stopPropagation(); remove(d.name) }} title={`Delete ${d.name}`}>
                        <Trash2 size={13} />
                      </QuietButton>
                    )}
                  </div>
                </ListRow>
              ))}
            </div>
          )
        ) : filteredRuns.length === 0 ? (
          <EmptyState
            icon={Workflow}
            title={q ? 'No matching runs' : 'No workflow runs yet'}
            hint={q ? 'Try a different search.' : 'Start one from the Definitions tab, or ask in chat.'}
          />
        ) : (
          <div className="flex flex-col gap-xs">
            {filteredRuns.map((r, i) => {
              const look = runLook(r.status)
              const Icon = look.icon
              const elapsed = fmtElapsed(r.elapsed_seconds)
              return (
                <ListRow key={r.id} index={i} onClick={() => navigate(`workflows/runs/${r.id}`)}>
                  <div className="flex min-w-0 flex-1 items-center gap-m">
                    <Icon size={15} className={`shrink-0 ${look.tone}${look.spin ? ' animate-spin' : ''}`} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-on-surface text-[0.9375rem]">{r.workflow_name}</div>
                      <div className="truncate text-on-surface-low text-[0.75rem]">
                        {look.label}{r.error_message ? ` · ${r.error_message}` : ''}
                      </div>
                    </div>
                    {elapsed && <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{elapsed}</span>}
                    <span className="shrink-0 font-mono text-on-surface-low text-[0.75rem]">{r.id}</span>
                  </div>
                </ListRow>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
