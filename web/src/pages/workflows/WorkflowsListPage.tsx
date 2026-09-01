import { useCallback, useEffect, useMemo, useState } from 'react'
import { Play, Search, Sparkles, Trash2, Workflow } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { EmptyState, ListRow, Loading, LoadError } from '../../ui/ListScaffold'
import { WindowedList } from '../../ui/WindowedList'
import { ListControls } from '../../ui/ListControls'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../ui/HeaderActions'
import { QuietButton } from '../../ui/QuietButton'
import { Button } from '../../ui/Button'
import { PresetEmptyState } from '../../ui/PresetEmptyState'
import { api, type WorkflowDef, type WorkflowSurfacingFinding, type WorkflowSurfacingRow } from '../../lib/api'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { useQuery, invalidateKeys } from '../../lib/data'
import { confirmDelete, promptForm, promptInput } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { fmtElapsed, isTerminal, runLook } from './workflowMeta'
import { coerceInputs, inputFields, startsWithoutInput } from './templateStart'
import { suggestTemplate } from './templateSuggest'
import { workflowPresets } from './workflowPresets'
import { cadenceLabel, findingsByDef, freshnessLook, modeLook, needsAttention, packChips } from './surfacingMeta'
import { PageTitle } from '../../ui/PageTitle'

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

  // ── DSC-14: three reads through the ONE data layer ──────────────────────────────────────────
  //
  // This page hand-rolled its own fetch-and-cache: four `useState`s, a `Promise.all` in a
  // `useCallback`, a mount effect, and its own `loading` flag. It therefore had NO cache at all —
  // every visit to `#/workflows` paid a cold three-request load and flashed a skeleton, while the
  // rest of the app read through a shared cache. That is the other half of the atom's "two caches
  // over one endpoint": one surface with a cache and one without, over the same collections,
  // disagreeing about what is current. It was never a `useCachedData` call site, so no census of
  // that helper could see it.
  //
  // The per-read error split is PRESERVED, deliberately: a definitions failure must not be
  // announced on the Runs tab. Three keys means three independent `error`s, which is what the old
  // hand-rolled pair of `useState`s was emulating.
  const { data: defsData, error: defsErr, loading: defsLoading, stale: defsStale } =
    useQuery('workflows:defs', () => api.workflowDefs().then((d) => d.defs))
  const { data: runsData, error: runsErr, loading: runsLoading, stale: runsStale } =
    useQuery('workflows:runs', () => api.workflowRuns({ limit: 100 }).then((r) => r.runs))
  // The surfacing read keeps its fallback on purpose (see the note above): it is a freshness
  // column, and a plain startable list is a better answer than an error for it.
  const surfacingQ = useQuery('workflows:surfacing', () => api.workflowSurfacing()
    .catch(() => ({ defs: [] as WorkflowSurfacingRow[], total: 0, findings: [] as WorkflowSurfacingFinding[] })))

  const defs = defsData ?? []
  const runs = runsData ?? []
  const findings = surfacingQ.data?.findings ?? []
  const surfacing = useMemo(
    () => Object.fromEntries((surfacingQ.data?.defs ?? []).map((row) => [row.name, row])),
    [surfacingQ.data],
  ) as Record<string, WorkflowSurfacingRow>
  // `loading` is the layer's: nothing cached for the tab's own read yet. Not `revalidating` — a
  // revalidation over rows already on screen must not flash this page back to a skeleton.
  const loading = tab === 'defs' ? defsLoading : runsLoading
  // The tab's rows are cached and past their window. `workflows` is a LIVE namespace, so this
  // fires on a revisit more than a second or two old — which is honest: a run list ages fast.
  const stale = tab === 'defs' ? defsStale : runsStale

  const load = useCallback(() => {
    // Bust all three keys rather than calling three `refresh()`es: the layer then re-reads them
    // for every mounted reader, so the run-detail panel beside this list moves too.
    invalidateKeys('workflows:', true)
  }, [])

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

  // The Runs empty state's on-ramp cards (PEP-2). Derived from the LOADED definitions, not from
  // a frozen list, so a card can never offer a template this install does not ship — and so the
  // grid is empty on an install with no bundled templates, which the render branches on.
  const presets = useMemo(() => workflowPresets(defs), [defs])

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
  // called `code-project` and a research one `deep-research`. Ask for the intent in
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
          <PageTitle>Workflows</PageTitle>
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
      <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search runs and definitions', label: 'Search workflows' }}
        results={{ count: tab === 'defs' ? filteredDefs.length : filteredRuns.length, noun: tab === 'defs' ? 'definitions' : 'runs', active: !!q.trim() }}
        stale={stale} />
      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        {loading ? <Loading what="workflows" /> : tab === 'defs' ? (
          defsErr ? (
            <LoadError what="workflow definitions" error={defsErr} onRetry={load} />
          ) : filteredDefs.length === 0 ? (
            // The action is on the genuinely-empty branch only. A search that matched nothing
            // gets the fact and nothing else: offering "Start from template" to someone who
            // mistyped a filter answers a question they did not ask, and the header already
            // carries that control for when they do.
            q ? (
              <EmptyState icon={Search} title="No matching definitions" hint="Try a different search." />
            ) : (
              <EmptyState
                icon={Workflow}
                title="No workflow definitions yet"
                hint="A workflow is a repeatable plan an agent runs step by step. Start from a template and describe what you want, or ask in chat to author one."
                action={{ label: 'Start from template', onClick: startFromTemplate, icon: Sparkles }}
              />
            )
          ) : (
            <div className="flex flex-col gap-xs">
              {/* Rows are keyed by source AND name. A name is unique per PROVIDER, not across
                  them, and an install that already carries a user def shadowing a bundled one
                  (possible before issue 764 closed that off at the save path) lists both — which
                  under a name-only key is a duplicate-key collision, so React reconciles two
                  distinct rows as one and the delete button can act on the wrong record. New
                  shadows are refused now; the ones already on disk still have to render, and be
                  deletable, or they would be stranded. */}
              {filteredDefs.map((d, i) => (
                <ListRow key={`${d.source}:${d.name}`} index={i} onClick={() => navigate(`workflows/defs/${d.name}`)} label={d.name}>
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
        ) : runsErr ? (
          <LoadError what="workflow runs" error={runsErr} onRetry={load} />
        ) : filteredRuns.length === 0 ? (
          q ? (
            <EmptyState icon={Search} title="No matching runs" hint="Try a different search." />
          ) : presets.length > 0 ? (
            // GENUINELY EMPTY, and this is the tab a newcomer lands on — Runs is the default.
            // The previous single CTA went to the Definitions LIST, so the first thing they met
            // was twenty-odd machine names: a signpost to the ontology rather than a way in.
            // The cards seed the SAME start flow (`start(name)` → the template's own input
            // dialog); nothing about it changes, and the browse path is repeated in the footer.
            <PresetEmptyState
              title="No workflow runs yet"
              hint="A run is one execution of a workflow — every step, its output, and where it stopped. Pick a starting point and the next screen asks for what it needs."
              presets={presets}
              onPick={(template) => { void start(template) }}
              footer={
                <Button variant="ghost" size="sm" onClick={() => setTab('defs')}>
                  <Workflow size={15} /> Browse all definitions
                </Button>
              }
            />
          ) : (
            // No bundled templates on this install, so there is nothing to offer as a card.
            // The pre-PEP-2 state, kept verbatim: a preset grid with nothing in it would be
            // worse than the fact plus the browse path.
            <EmptyState
              icon={Workflow}
              title="No workflow runs yet"
              hint="A run is one execution of a workflow — every step, its output, and where it stopped. Pick a definition to start your first."
              action={{ label: 'Browse definitions', onClick: () => setTab('defs'), icon: Workflow }}
            />
          )
        ) : (
          // DSC-13: the run ledger is the list that grows on its own — every workflow
          // execution adds a row and nothing removes one. The client asks for 100 today
          // (`api.workflowRuns({ limit: 100 })`, and the handler caps there too), so the
          // window engages on a full first page rather than waiting for a cap change.
          <WindowedList
            items={filteredRuns}
            rowKey={(r) => r.id}
            // VARIABLE: `ListRow`'s padding rides `--space-scale`. This is the most nearly
            // uniform of the five (both text lines are `truncate`), but "nearly" is not a
            // constraint worth declaring — measured 34-76px on the sibling knowledge list.
            rowHeights="variable"
            estimateRowHeight={64}
            gap={4}
            noun="runs"
            findHint="use the Search runs and definitions field above."
            className="flex flex-col gap-xs"
          >
            {(r, i, listCtx) => {
              const look = runLook(r.status)
              const Icon = look.icon
              const elapsed = fmtElapsed(r.elapsed_seconds)
              // index=0 while windowed — see ui/WindowedList's ctx.windowed doc.
              return (
                <ListRow key={r.id} index={listCtx.windowed ? 0 : i} onClick={() => navigate(`workflows/runs/${r.id}`)} label={`${r.workflow_name} — run ${r.id}`}>
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
            }}
          </WindowedList>
        )}
      </div>
    </div>
  )
}
