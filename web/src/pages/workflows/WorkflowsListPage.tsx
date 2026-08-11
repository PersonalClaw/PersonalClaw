import { useCallback, useEffect, useMemo, useState } from 'react'
import { Play, Sparkles, Trash2, Workflow } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { EmptyState, ListRow, Loading } from '../../ui/ListScaffold'
import { ListControls } from '../../ui/ListControls'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../ui/HeaderActions'
import { QuietButton } from '../../ui/QuietButton'
import { api, type WorkflowDef, type WorkflowDefSummary, type WorkflowRunSummary, type WorkflowSurfacingFinding, type WorkflowSurfacingRow } from '../../lib/api'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { confirmDelete, promptForm, promptInput } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { fmtElapsed, isTerminal, runLook } from './workflowMeta'
import { coerceInputs, inputFields, startsWithoutInput } from './templateStart'
import { suggestTemplate } from './templateSuggest'
import { cadenceLabel, findingsByDef, freshnessLook, modeLook, needsAttention, packChips } from './surfacingMeta'

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
  // Surfacing state rides ALONGSIDE the thin def list rather than replacing it: the thin list is
  // what the picker needs, and a surfacing read costs a run-history lookup per def. A failed
  // surfacing read degrades to a plain list rather than an empty page — the templates are still
  // startable without their freshness column.
  const [surfacing, setSurfacing] = useState<Record<string, WorkflowSurfacingRow>>({})
  const [findings, setFindings] = useState<WorkflowSurfacingFinding[]>([])
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [d, r, s] = await Promise.all([
        api.workflowDefs().catch(() => ({ defs: [], total: 0 })),
        api.workflowRuns({ limit: 100 }).catch(() => ({ runs: [], total: 0, limit: 0, offset: 0 })),
        api.workflowSurfacing().catch(() => ({ defs: [], total: 0, findings: [] })),
      ])
      setDefs(d.defs)
      setRuns(r.runs)
      setSurfacing(Object.fromEntries(s.defs.map((row) => [row.name, row])))
      setFindings(s.findings)
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

  const byDef = useMemo(() => findingsByDef(findings), [findings])

  const filteredDefs = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const matched = needle
      ? defs.filter((d) => `${d.name}\n${d.description}\n${d.tags.join(' ')}`.toLowerCase().includes(needle))
      : defs
    // Overdue-and-broken first, matching the backend's own overdue-first order (`sort_key`) — a
    // template that needs attention below thirty that do not is one nobody sees. Ties keep the
    // server's name order rather than being re-sorted here.
    const rank = (name: string) =>
      needsAttention(surfacing[name] ?? { overdue: false }, (byDef[name] ?? []).length) ? 0 : 1
    return [...matched].sort((a, b) => rank(a.name) - rank(b.name))
  }, [defs, q, surfacing, byDef])

  const start = useCallback(async (name: string) => {
    // Every bundled template declares a required input, and starting with none is refused by the
    // engine (`WF_RUN_MISSING_INPUTS`) — so before this, every shipped template was unstartable
    // from the UI. Fetch the definition, ask for what it declares, then start.
    let def: WorkflowDef | null = null
    try {
      def = (await api.workflowDef(name)).definition
    } catch {
      // A definition that cannot be read still gets a start attempt: the engine's own error is
      // more informative than one this page could invent, and a transient read failure should not
      // block a run the user asked for.
    }

    let inputs: Record<string, unknown> | undefined
    if (def && !startsWithoutInput(def.inputs)) {
      const fields = inputFields(def.inputs)
      const example = def.metadata?.steering_examples?.find((e) => e.event === 'kickoff')
      const answers = await promptForm({
        title: `Run ${name}`,
        // The template's own kickoff example, shown as the body: it is a concrete instance of
        // what this workflow is for, which is far more use than the description at the moment of
        // filling the form in.
        body: example?.description ? `For example: ${example.description}` : def.description,
        fields,
        confirmLabel: 'Run',
      })
      if (answers === null) return  // cancelled
      inputs = coerceInputs(answers, def.inputs)
    }

    try {
      const res = await api.startWorkflowRun(inputs ? { name, inputs } : { name })
      navigate(`workflows/runs/${res.run_id}`)
    } catch (e) {
      // A preflight refusal is the common case and it is ACTIONABLE (missing credential, no
      // model) — surfacing the message is the whole point of failing at start.
      notify(e instanceof Error ? e.message : 'Could not start the workflow')
    }
  }, [navigate])

  // "Start from template" (LOOPS-EVOLUTION criterion 11): a user who knows what they want to
  // DO ("fix a bug", "research a topic") should not have to already know that a coding job is
  // called `code-implementation` and a research one `deep-research`. Ask for the intent in
  // plain language, resolve it to a shipped template through the same alias table the cockpit
  // uses, then fall into the ordinary `start` flow for that template's inputs. Falls back to
  // the browse list — never a wrong workflow — when nothing matches, because starting a run the
  // user did not choose is worse than starting none.
  const startFromTemplate = useCallback(async () => {
    const intent = await promptInput({
      title: 'Start from template',
      label: 'What do you want to do?',
      placeholder: 'e.g. fix the login bug, or research vector databases',
      required: true,
    })
    if (!intent) return
    const template = suggestTemplate(intent, defs.map((d) => d.name))
    if (!template) {
      // No confident match. Filtering the list by the intent is more honest than guessing a
      // template — it puts the user one glance from choosing, without starting the wrong thing.
      setTab('defs')
      setQ(intent)
      notify('No single template matched — showing the closest ones to pick from.')
      return
    }
    await start(template)
  }, [defs, setTab, setQ, start])

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

  // Two-step ARMED delete for a run, not a modal: a run row is a list item and a dialog per
  // row is heavy, but a single click on a Trash icon in a dense list is how people delete the
  // wrong thing. Arming makes the second click the confirmation, in place. Per-id rather than
  // a boolean so arming one row cannot arm all of them.
  const [armed, setArmed] = useState<string | null>(null)
  // Disarm on any other interaction: an armed row left armed indefinitely becomes a trap the
  // next time the user reaches for that area.
  useEffect(() => {
    if (!armed) return
    const t = window.setTimeout(() => setArmed(null), 4000)
    return () => window.clearTimeout(t)
  }, [armed])

  const removeRun = useCallback(async (id: string) => {
    try {
      await api.deleteWorkflowRun(id)
      setArmed(null)
      load()
    } catch (e) {
      // The 409 ("cancel it before deleting") is the informative case — a run the user thought
      // was finished is still moving, and that is worth reading.
      notify(e instanceof Error ? e.message : 'Could not delete the run')
    }
  }, [load])

  const needingInput = runs.filter((r) => r.status === 'needs_input').length

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={<div className="flex min-w-0 items-center gap-m">
          {/* NOT `shrink-0`: that opted the title out of the slot's truncation and it ran 56px
              under the control row at 390px. The "waiting on you" badge keeps its own
              `shrink-0`, so the title is what yields — which is the right order anyway. */}
          <span data-type="title-l" className="text-on-surface">Workflows</span>
          {needingInput > 0 && (
            <span className="shrink-0 text-warning text-[0.75rem]">{needingInput} waiting on you</span>
          )}
        </div>}
        // The header keeps only the structural view-switch + the primary action; search moved to
        // the page's ListControls bar, which is `ListControls`' documented rule and what the other
        // 12 list pages do. Crammed in here the search input was visibly TRUNCATED at 1440px
        // ("Search runs and defini…") because it competed with the tab strip for header width.
        right={<HeaderActions>
          <HeaderSegmented options={TABS} value={tab} onChange={setTab} ariaLabel="Workflows view" />
          <HeaderControl icon={Sparkles} label="Start from template" variant="primary" priority="primary"
            onClick={startFromTemplate} hint="Describe what you want to do; we'll pick the template" />
        </HeaderActions>}
      />
      <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search runs and definitions', label: 'Search workflows' }} />
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
                <ListRow key={d.name} index={i} onClick={() => navigate(`workflows/defs/${d.name}`)} label={d.name}>
                  <div className="flex min-w-0 flex-1 items-center gap-m">
                    <Workflow size={15} className="shrink-0 text-on-surface-low" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-on-surface text-[0.9375rem]">{d.name}</div>
                      {(() => {
                        const row = surfacing[d.name]
                        const defFindings = byDef[d.name] ?? []
                        // The finding wins the subtitle when there is one: "no channel can reach
                        // this def" is more urgent than its description, and showing both would
                        // truncate the part that matters.
                        if (defFindings.length > 0) {
                          return (
                            <div className="truncate text-warning text-[0.75rem]" title={defFindings.map((f) => f.detail).join(' · ')}>
                              {defFindings[0].detail}
                            </div>
                          )
                        }
                        const cadence = row ? cadenceLabel(row) : ''
                        const subtitle = [d.description, cadence].filter(Boolean).join(' · ')
                        return subtitle ? <div className="truncate text-on-surface-low text-[0.75rem]">{subtitle}</div> : null
                      })()}
                    </div>
                    {(() => {
                      const row = surfacing[d.name]
                      if (!row) return null
                      const nodes = []
                      // Freshness only when the def declares a cadence: a band for an untracked def
                      // would imply a schedule it does not have.
                      if (row.cadence_days > 0) {
                        const look = freshnessLook(row.freshness)
                        const Icon = look.icon
                        nodes.push(
                          <span key="fresh" className={`flex shrink-0 items-center gap-xs text-[0.75rem] ${look.tone}`} title={look.hint}>
                            <Icon size={12} /> {look.label}
                          </span>,
                        )
                      }
                      // The surfacing mode is always shown, INCLUDING `off`: "this never surfaces"
                      // is the fact a user most often wants to check, and hiding it would make an
                      // off def indistinguishable from one whose chip they simply had not seen.
                      const mode = modeLook(row.surface_mode)
                      const ModeIcon = mode.icon
                      nodes.push(
                        <span key="mode" className={`flex shrink-0 items-center gap-xs text-[0.75rem] ${mode.tone}`} title={mode.hint}>
                          <ModeIcon size={12} /> {mode.label}
                        </span>,
                      )
                      for (const pack of packChips(row)) {
                        nodes.push(
                          <span key={`pack-${pack}`} className="shrink-0 text-on-surface-low text-[0.75rem]" title={`Pack: ${pack}`}>
                            {pack}
                          </span>,
                        )
                      }
                      return nodes
                    })()}
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
                <ListRow key={r.id} index={i} onClick={() => navigate(`workflows/runs/${r.id}`)} label={`${r.workflow_name} — run ${r.id}`}>
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
                    {/* Terminal runs only. A live run's delete would race its own controller,
                        so the affordance is absent rather than present-and-refusing. */}
                    {isTerminal(r.status) && (
                      armed === r.id ? (
                        <QuietButton
                          onClick={(e) => { e.stopPropagation(); removeRun(r.id) }}
                          title="Click again to delete this run and its artifacts"
                        >
                          <Trash2 size={13} className="text-danger" /> Delete?
                        </QuietButton>
                      ) : (
                        <QuietButton
                          onClick={(e) => { e.stopPropagation(); setArmed(r.id) }}
                          title={`Delete run ${r.id}`}
                        >
                          <Trash2 size={13} />
                        </QuietButton>
                      )
                    )}
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
