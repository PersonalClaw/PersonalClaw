import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertCircle, AlertTriangle, ArrowLeft, CheckCircle2, ChevronDown, ChevronRight, EyeOff, Lightbulb, ShieldCheck } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { PageTitle } from '../../ui/PageTitle'
import { HeaderActions } from '../../ui/HeaderActions'
import { QuietButton } from '../../ui/QuietButton'
import { Button } from '../../ui/Button'
import { Segmented } from '../../ui/Segmented'
import { Loading, LoadError } from '../../ui/ListScaffold'
import { ChipInput, Checkbox, Field, FieldError, Select, TextArea, TextInput } from '../../ui/forms'
import { Toggle } from '../../ui/Toggle'
import { confirm } from '../../ui/dialog'
import { notify } from '../../app/appSdk'
import { api, ApiError, type WorkflowNode } from '../../lib/api'
import { ConsentDeclined } from '../../lib/securityConsent'
import { HELD_CHANGE_REASON, rebaseRecord, type Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { HeldChange, StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { WorkflowJsonEditor } from './WorkflowJsonEditor'
import {
  NAME_RE, copyCandidates, docToJson, editableDoc, hiddenName, inlineSecretIssues, isError, isHidden,
  issuesFrom, jsonToDoc, placeIssues, stepAction, stepRows, updateNode,
  type EditIssue, type EditableDoc, type StepRow,
} from './defEditing'

/** The types a declared input may have — the engine's `contracts.DECLARED_TYPES`. */
const INPUT_TYPES = ['string', 'number', 'integer', 'boolean', 'array', 'object']

/** Every name a definition already holds, shipped templates included. A copy saved under one of
 *  them would REPLACE a workflow the user already has, so a copy is never offered or saved under
 *  one. A failed read propagates: it is not an answer, and reading it as "nothing is taken" is how
 *  a copy would overwrite something. */
async function takenNames(): Promise<Set<string>> {
  return new Set((await api.workflowDefs()).defs.map((d) => d.name))
}

type Tab = 'steps' | 'json'

/** Edit one workflow definition: its steps' settings, its declared inputs, or all of it as
 *  JSON — checked by the engine's own dry run, saved through the engine's own save.
 *
 *  Three starting points, one save:
 *    · a definition of your own — edited in place, and each save is a new version;
 *    · a shipped template — read-only, so the edit is saved as a COPY under a name of yours;
 *    · one recorded version (`fromVersion`) — the restore: saving it makes it the newest version,
 *      which is the one a run executes.
 *
 *  The editor edits only what the definition already has. The Steps view offers a control for
 *  every setting a step carries; adding, removing or moving a step is the JSON tab's job, because
 *  a structural edit is a new graph and the engine is the only judge of whether it is one. Every
 *  problem comes back from the engine at the step it names (`issue.path`), in its own words.
 *
 *  A save over a definition of yours (an edit or a restore) replaces the whole of it, so it names
 *  the revision of the definition this page read. When another tab, the agent or the publish
 *  toggle saved it since, the save is refused and the change is kept: Reload and reapply puts an
 *  edit back on top of what is stored now; a restore is not re-applied on its own — it is offered
 *  over the newer version only after the review (`StaleWriteNotice`). */
export function WorkflowDefEditor({ name, fromVersion, onCancel, onSaved }: {
  name: string
  fromVersion?: number
  onCancel: () => void
  onSaved: (savedName: string) => void
}) {
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [reload, setReload] = useState(0)
  const [doc, setDoc] = useState<EditableDoc | null>(null)
  const [initial, setInitial] = useState('')
  // A shipped template (anything not the user's own) is read-only: the edit becomes a copy.
  const [copyMode, setCopyMode] = useState(false)
  const [newName, setNewName] = useState('')
  const [tab, setTab] = useState<Tab>('steps')
  const [jsonText, setJsonText] = useState('')
  const [jsonErr, setJsonErr] = useState('')
  // Settings whose JSON text does not parse. Save waits on them rather than sending the last
  // value that did, which is not what the field shows.
  const [badFields, setBadFields] = useState<Set<string>>(() => new Set())
  const [issues, setIssues] = useState<EditIssue[] | null>(null)
  const [topError, setTopError] = useState('')
  const [nameError, setNameError] = useState('')
  const [busy, setBusy] = useState<'check' | 'save' | null>(null)
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  // The definition an in-place save replaces, as this page read it — the current one, a restore
  // included (it replaces the current definition, not the version it started from). `null` for a
  // copy, which replaces nothing.
  const [base, setBase] = useState<Revisioned<EditableDoc> | null>(null)

  useEffect(() => {
    let alive = true
    setLoadErr(null)
    setDoc(null)
    ;(async () => {
      const detail = await api.workflowDef(name)
      const start = fromVersion ? (await api.workflowVersion(name, fromVersion)).definition : detail.definition
      const readOnly = detail.definition.source !== 'user'
      let suggested = name
      if (readOnly) {
        const taken = await takenNames()
        const candidates = copyCandidates(name)
        suggested = candidates.find((c) => !taken.has(c)) ?? candidates[0]
      }
      if (!alive) return
      const d = editableDoc(start)
      setDoc(d)
      setInitial(docToJson(d))
      setCopyMode(readOnly)
      setBase(readOnly ? null : { value: editableDoc(detail.definition), revision: detail.revision })
      setNewName(suggested)
      setJsonText(docToJson(d))
    })().catch((e) => { if (alive) setLoadErr(e) })
    return () => { alive = false }
  }, [name, fromVersion, reload])

  /** What a Check or a save sends: the whole editable definition under the name it is saved as,
   *  and what it was edited from — this definition, or the version a restore opened — so the values
   *  its read hid come back from there. One body for every send, re-applied saves included. */
  const bodyOf = useCallback((d: EditableDoc, to: string, save: boolean) => ({
    ...d,
    name: to,
    based_on: name,
    ...(fromVersion ? { based_on_version: fromVersion } : {}),
    save,
  }), [fromVersion, name])

  /** The save over your own definition. Its success line is said here, so a save re-applied from
   *  the refusal notice says it too. */
  const saveInPlace = useCallback(async (next: EditableDoc, revision: string) => {
    const res = await api.saveWorkflowDef(bodyOf(next, name, true), revision)
    setIssues(issuesFrom(res))
    const v = res.definition?.version
    notify(v ? `Saved ${name} as version ${v}` : `Saved ${name}`)
  }, [bodyOf, name])

  const guard = useStaleWriteGuard<EditableDoc>({
    read: async () => {
      const fresh = await api.workflowDef(name)
      return { value: editableDoc(fresh.definition), revision: fresh.revision }
    },
    write: saveInPlace,
    onSaved: () => onSaved(name),
    // Dropping an edit shows the definition as it is stored now; dropping a restore is not
    // restoring, so it goes back to the definition.
    onDiscard: () => { if (fromVersion) onCancel(); else setReload((n) => n + 1) },
  })
  const held = guard.conflict !== null

  const rows = useMemo(() => (doc ? stepRows(doc.root) : []), [doc])
  // Each step's name by its id, for a step that names the ones it runs after: by label, as its row.
  const nameOf = useMemo(() => {
    const names = new Map<string, string>()
    for (const { node } of rows) if (node.id && !names.has(node.id)) names.set(node.id, node.label || node.id)
    return (id: string) => names.get(id) || id
  }, [rows])
  const placed = useMemo(() => placeIssues(issues ?? [], rows), [issues, rows])
  const errors = (issues ?? []).filter(isError)
  const advice = (issues ?? []).filter((i) => !isError(i))
  const dirty = doc !== null && (tab === 'json' ? jsonText !== initial : docToJson(doc) !== initial)
  // A copy or a restore is worth saving untouched — that is the whole act. An in-place edit is not.
  const saveable = copyMode || !!fromVersion || dirty
  const target = copyMode ? newName.trim() : name

  const switchTab = useCallback((next: Tab) => {
    if (next === tab || !doc) return
    if (next === 'json') {
      setJsonText(docToJson(doc))
      setJsonErr('')
      setTab('json')
      return
    }
    const parsed = jsonToDoc(jsonText)
    if ('error' in parsed) { setJsonErr(parsed.error); return }
    setDoc(parsed.doc)
    setJsonErr('')
    setTab('steps')
  }, [doc, jsonText, tab])

  /** The definition as it stands, whichever tab holds it — or null when the JSON tab's text does not
   *  parse (the error is then on screen). */
  const current = useCallback((): EditableDoc | null => {
    if (tab === 'steps') return doc
    const parsed = jsonToDoc(jsonText)
    if ('error' in parsed) { setJsonErr(parsed.error); return null }
    return parsed.doc
  }, [doc, jsonText, tab])

  const submit = useCallback(async (save: boolean) => {
    const d = current()
    if (!d) return
    if (copyMode && !NAME_RE.test(target)) {
      setNameError('Use lowercase letters, digits and hyphens — it becomes a folder name.')
      return
    }
    setBusy(save ? 'save' : 'check')
    setTopError('')
    setNameError('')
    try {
      if (save && copyMode && (await takenNames()).has(target)) {
        setNameError(`You already have a workflow named ${target}. Pick another name — saving would replace it.`)
        return
      }
      if (save && base) {
        // An edit merges with a change saved since, field by field; a restore is the whole of an
        // old version, so merging a newer one into it would restore neither.
        await guard.save(base, d, fromVersion ? () => null : rebaseRecord(base.value, d))
        return
      }
      const res = await api.saveWorkflowDef(bodyOf(d, target, save))
      setIssues(issuesFrom(res))
      if (save && res.saved) {
        const v = res.definition?.version
        notify(v ? `Saved ${target} as version ${v}` : `Saved ${target}`)
        onSaved(target)
      }
    } catch (e) {
      if (e instanceof ConsentDeclined) { notify(e.message); return }
      if (e instanceof ApiError && e.code === 'validation_failed') {
        setIssues(issuesFrom(e.detail))
      } else if (e instanceof ApiError && e.code === 'inline_secret') {
        setIssues(inlineSecretIssues(e.detail, stepRows(d.root)))
        setTopError(e.message)
      } else if (e instanceof ApiError && copyMode && (e.code === 'name_reserved' || e.code === 'invalid_request')) {
        setNameError(e.message)
      } else if (e instanceof ApiError && copyMode && e.code === 'revision_required') {
        // A workflow of that name was saved after the check above: saving the copy would replace it.
        setNameError(`You already have a workflow named ${target}. Pick another name — saving would replace it.`)
      } else {
        setTopError(e instanceof Error ? e.message : 'Could not save the workflow')
      }
    } finally {
      setBusy(null)
    }
  }, [base, bodyOf, copyMode, current, fromVersion, guard, onSaved, target])

  const leave = useCallback(async () => {
    if (dirty && !(await confirm({ title: `Discard your changes to ${name}?`, body: 'Nothing you changed here has been saved.', confirmLabel: 'Discard', danger: true }))) return
    onCancel()
  }, [dirty, onCancel])

  const editNode = useCallback((row: StepRow, next: WorkflowNode) => {
    setDoc((d) => (d ? { ...d, root: updateNode(d.root, row.hops, () => next) } : d))
  }, [])

  const markBad = useCallback((key: string, bad: boolean) => {
    setBadFields((prev) => {
      if (bad === prev.has(key)) return prev
      const next = new Set(prev)
      if (bad) next.add(key)
      else next.delete(key)
      return next
    })
  }, [])

  const blocked = tab === 'steps' && badFields.size > 0
  const blockedReason = blocked ? 'Fix the setting that is not valid JSON first' : jsonErr ? `Fix the JSON first: ${jsonErr}` : undefined
  const title = copyMode ? `Copy ${name}` : fromVersion ? `Restore ${name} v${fromVersion}` : `Edit ${name}`

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<div className="flex min-w-0 items-center gap-m">
          <QuietButton onClick={leave} title={`Back to ${name}`}><ArrowLeft size={13} /> {name}</QuietButton>
          <PageTitle className="truncate">{title}</PageTitle>
        </div>}
        right={doc ? (
          <HeaderActions>
            <QuietButton
              onClick={() => { void submit(false) }}
              title="Check it the way Save does, without saving anything"
              disabled={busy !== null || blocked || !!jsonErr}
              disabledReason={busy ? 'A check or save is already running' : blockedReason}
            >
              <ShieldCheck size={13} /> {busy === 'check' ? 'Checking…' : 'Check'}
            </QuietButton>
            <Button
              onClick={() => { void submit(true) }}
              loading={busy === 'save'}
              disabled={busy !== null || !saveable || blocked || !!jsonErr || held}
              disabledReason={held ? HELD_CHANGE_REASON : !saveable ? 'No changes to save' : blockedReason}
            >
              {copyMode ? 'Save copy' : 'Save'}
            </Button>
          </HeaderActions>
        ) : undefined}
      />
      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        {loadErr !== null ? (
          <LoadError what="workflow definition" error={loadErr} onRetry={() => setReload((n) => n + 1)} />
        ) : !doc ? (
          <Loading what="this workflow" />
        ) : (
          <div className="mx-auto flex max-w-[var(--content-width)] flex-col gap-l">
            {copyMode && (
              <Field label="Name of your copy" hint={`${name} ships with PersonalClaw and stays as it is. Your copy is yours to change, and an update never touches it.`}>
                <TextInput value={newName} onChange={(v) => { setNewName(v); setNameError('') }} mono maxLength={63} required />
                {nameError && <FieldError className="mt-xs">{nameError}</FieldError>}
              </Field>
            )}
            {fromVersion ? (
              <p data-type="body-s" className="text-on-surface-low">
                This is version {fromVersion}. Saving it makes it the newest version, and the next run uses it. Every earlier version stays in the history.
              </p>
            ) : null}

            {/* Pinned to the top of the scroll area: a Check is usually run from deep in the step
                list, and an outcome that renders above the fold is one nobody sees — measured, the
                "No problems found" line sat outside the viewport after a Check from the 6th step. */}
            {(issues !== null || topError || held) && (
              <div className="sticky top-0 z-10 -mx-l flex flex-col gap-s bg-canvas px-l py-s">
                <StaleWriteNotice guard={guard} what="This workflow" />
                <Outcome issues={issues} errors={errors} advice={advice} unplaced={placed.unplaced} topError={topError} />
              </div>
            )}

            <Segmented
              ariaLabel="Editor view"
              value={tab}
              onChange={(k) => switchTab(k as Tab)}
              options={[{ key: 'steps', label: 'Steps' }, { key: 'json', label: 'JSON' }]}
            />

            {tab === 'json' ? (
              <div className="flex flex-col gap-s">
                <p data-type="body-s" className="text-on-surface-low">
                  The whole definition. Add, remove or move a step here; the Steps view edits the settings each step already has.
                </p>
                <div className="overflow-hidden rounded-md border border-outline-variant" style={{ height: 'min(70vh, 48rem)' }}>
                  <WorkflowJsonEditor name={target || name} value={jsonText} onChange={(v) => { setJsonText(v); setJsonErr('') }} readOnly={held} />
                </div>
                {jsonErr && <FieldError>{jsonErr}</FieldError>}
              </div>
            ) : (
              <HeldChange guard={guard}>
                <Field label="Description">
                  <TextArea value={doc.description} onChange={(v) => setDoc({ ...doc, description: v })} rows={2} />
                </Field>
                <Field label="Tags">
                  <ChipInput values={doc.tags} onChange={(v) => setDoc({ ...doc, tags: v })} ariaLabel="Tags" />
                </Field>

                <InputsEditor
                  inputs={doc.inputs}
                  issues={placed.inputs}
                  onChange={(inputs) => setDoc({ ...doc, inputs })}
                  markBad={markBad}
                />

                <section className="flex flex-col gap-s">
                  <div className="flex flex-col gap-xs">
                    <span data-type="title-m" className="text-on-surface">Steps</span>
                    <span data-type="caption" className="text-on-surface-low">In the order they run. Open a step to change its settings.</span>
                  </div>
                  {rows.map((row) => (
                    <StepCard
                      key={row.path}
                      row={row}
                      issues={placed.byPath.get(row.path) ?? []}
                      open={open.has(row.path)}
                      onToggle={() => setOpen((prev) => {
                        const next = new Set(prev)
                        if (next.has(row.path)) next.delete(row.path)
                        else next.add(row.path)
                        return next
                      })}
                      onChange={(next) => editNode(row, next)}
                      markBad={markBad}
                      nameOf={nameOf}
                    />
                  ))}
                </section>
              </HeldChange>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── the outcome of a check or save ─────────────────────────────────────────────

function Outcome({ issues, errors, advice, unplaced, topError }: {
  issues: EditIssue[] | null
  errors: EditIssue[]
  advice: EditIssue[]
  unplaced: EditIssue[]
  topError: string
}) {
  if (topError) return <FieldError>{topError}</FieldError>
  if (issues === null) return null
  return (
    <div className="flex flex-col gap-xs">
      {errors.length > 0 ? (
        <p role="alert" data-type="body-s" className="flex items-center gap-xs text-danger">
          <AlertCircle size={14} className="shrink-0" />
          {errors.length === 1 ? '1 problem stops this definition from saving.' : `${errors.length} problems stop this definition from saving.`} Each one is shown at its step.
        </p>
      ) : (
        <p role="status" data-type="body-s" className="flex items-center gap-xs text-success">
          <CheckCircle2 size={14} className="shrink-0" /> No problems found.
        </p>
      )}
      {advice.length > 0 && (
        <p data-type="caption" className="text-on-surface-low">
          {adviceLine(advice.filter((i) => i.source !== 'lint').length, advice.filter((i) => i.source === 'lint').length)}
        </p>
      )}
      {unplaced.length > 0 && <IssueList issues={unplaced} />}
    </div>
  )
}

/** The line under the outcome that counts what is not a reason it cannot save: the validator's
 *  warnings, and the workflow conventions' suggestions — two sources, each named as itself. */
export function adviceLine(warnings: number, suggestions: number): string {
  const parts: string[] = []
  if (warnings > 0) parts.push(warnings === 1 ? '1 warning' : `${warnings} warnings`)
  if (suggestions > 0) {
    parts.push(`${suggestions === 1 ? '1 suggestion' : `${suggestions} suggestions`} from the workflow conventions`)
  }
  return `${parts.join(' and ')} — advice, not a reason it cannot save.`
}

function IssueList({ issues }: { issues: EditIssue[] }) {
  return (
    <ul className="flex flex-col gap-xs">
      {issues.map((issue, i) => {
        const lint = issue.source === 'lint'
        const warn = !lint && issue.severity === 'warning'
        const Icon = lint ? Lightbulb : warn ? AlertTriangle : AlertCircle
        const tone = lint ? 'text-on-surface-low' : warn ? 'text-warning' : 'text-danger'
        return (
          <li key={`${issue.code}-${i}`} data-type="body-s" data-issue-code={issue.code} className={`flex items-start gap-xs ${tone}`}>
            <Icon size={14} className="mt-0.5 shrink-0" />
            <span className="min-w-0">{issue.message}</span>
          </li>
        )
      })}
    </ul>
  )
}

// ── declared inputs ────────────────────────────────────────────────────────────

function InputsEditor({ inputs, issues, onChange, markBad }: {
  inputs: Record<string, unknown>
  issues: EditIssue[]
  onChange: (next: Record<string, unknown>) => void
  markBad: (key: string, bad: boolean) => void
}) {
  const entries = Object.entries(inputs)
  if (entries.length === 0 && issues.length === 0) return null
  return (
    <section className="flex flex-col gap-s">
      <div className="flex flex-col gap-xs">
        <span data-type="title-m" className="text-on-surface">Run inputs</span>
        <span data-type="caption" className="text-on-surface-low">What a run is asked for when it starts.</span>
      </div>
      {issues.length > 0 && <IssueList issues={issues} />}
      {entries.map(([key, raw]) => {
        if (isHidden(key)) return <HiddenValue key={key} name={hiddenName(key)} />
        const param = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
        const type = typeof param.type === 'string' ? param.type : 'string'
        const set = (patch: Record<string, unknown>) => onChange({ ...inputs, [key]: { ...param, ...patch } })
        const types = INPUT_TYPES.includes(type) ? INPUT_TYPES : [...INPUT_TYPES, type]
        return (
          <div key={key} className="flex flex-col gap-s rounded-lg bg-surface-container p-m" data-input={key}>
            <div className="flex flex-wrap items-center gap-m">
              <span data-type="label-m" className="font-mono text-on-surface">{key}</span>
              <div className="w-40">
                <Select value={type} onChange={(v) => set({ type: v })} options={types.map((t) => ({ value: t, label: t }))} ariaLabel={`${key} type`} size="sm" />
              </div>
              <label className="flex items-center gap-xs" data-type="body-s">
                <Checkbox checked={param.required === true} onChange={(v) => set({ required: v })} ariaLabel={`${key} is required`} />
                Required
              </label>
            </div>
            <Field label="Help">
              <TextInput value={typeof param.help === 'string' ? param.help : ''} onChange={(v) => set({ help: v })} ariaLabel={`${key} help`} size="md" />
            </Field>
            <ValueField
              label="Default"
              fieldKey={`inputs.${key}.default`}
              value={param.default ?? null}
              typeHint={type}
              onChange={(v) => set({ default: v })}
              markBad={markBad}
            />
          </div>
        )
      })}
    </section>
  )
}

// ── one step ───────────────────────────────────────────────────────────────────

function StepCard({ row, issues, open, onToggle, onChange, markBad, nameOf }: {
  row: StepRow
  issues: EditIssue[]
  open: boolean
  onToggle: () => void
  onChange: (next: WorkflowNode) => void
  markBad: (key: string, bad: boolean) => void
  /** Another step's name by its id, for the steps this one runs after. */
  nameOf: (id: string) => string
}) {
  const { node } = row
  const action = stepAction(node)
  const problems = issues.filter(isError).length
  // What a person reads the step as: its label, the name every run surface gives it, else its id.
  const label = node.label || node.id || (row.path === 'root' ? 'Workflow' : node.kind)
  return (
    <div
      className="rounded-lg bg-surface-container"
      style={{ marginLeft: `calc(${row.depth} * 1rem)` }}
      data-step-path={row.path}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-label={`${row.number ? `Step ${row.number}: ` : ''}${label} (${node.kind})${problems ? `, ${problems} problem${problems === 1 ? '' : 's'}` : ''}`}
        className="flex w-full min-w-0 items-center gap-m rounded-lg px-m py-s text-left hover:bg-surface-high"
      >
        {open ? <ChevronDown size={14} className="shrink-0 text-on-surface-low" /> : <ChevronRight size={14} className="shrink-0 text-on-surface-low" />}
        {row.number && <span data-type="caption" className="shrink-0 tabular-nums text-on-surface-low">{row.number}</span>}
        <span data-type="body-m" className="min-w-0 truncate text-on-surface">{label}</span>
        <span data-type="caption" className="shrink-0 font-mono text-on-surface-low">{node.kind}</span>
        {row.slot && <span data-type="caption" className="shrink-0 text-on-surface-low">{row.slot}</span>}
        {action && <span data-type="caption" className="min-w-0 truncate text-on-surface-low">{action}</span>}
        {problems > 0 && <span data-type="caption" className="ml-auto shrink-0 text-danger">{problems === 1 ? '1 problem' : `${problems} problems`}</span>}
      </button>
      {issues.length > 0 && <div className="px-m pb-s"><IssueList issues={issues} /></div>}
      {open && <StepFields row={row} onChange={onChange} markBad={markBad} nameOf={nameOf} />}
    </div>
  )
}

function StepFields({ row, onChange, markBad, nameOf }: {
  row: StepRow
  onChange: (next: WorkflowNode) => void
  markBad: (key: string, bad: boolean) => void
  nameOf: (id: string) => string
}) {
  const { node } = row
  const config = (node.config ?? {}) as Record<string, unknown>
  const setConfig = (key: string, value: unknown) => onChange({ ...node, config: { ...config, [key]: value } })
  const entries = Object.entries(config)
  return (
    <div className="flex flex-col gap-m border-t border-outline-variant px-m py-m">
      {node.needs && node.needs.length > 0 && (
        <p data-type="caption" className="text-on-surface-low">Runs after {node.needs.map(nameOf).join(', ')}.</p>
      )}
      {entries.length === 0 && (
        <p data-type="caption" className="text-on-surface-low">This step has no settings of its own. Add one in the JSON tab.</p>
      )}
      {entries.map(([key, value]) => {
        if (isHidden(key)) return <HiddenValue key={key} name={hiddenName(key)} />
        // An action's arguments are what the step is FOR — one field each, not one JSON blob.
        if (node.kind === 'action' && key === 'with' && isPlainObject(value)) {
          const args = value as Record<string, unknown>
          return (
            <div key={key} className="flex flex-col gap-s">
              <span data-type="label-m" className="text-on-surface">Action inputs</span>
              {Object.entries(args).map(([arg, argValue]) => isHidden(arg)
                ? <HiddenValue key={arg} name={hiddenName(arg)} />
                : (
                  <ValueField
                    key={arg}
                    label={arg}
                    fieldKey={`${row.path}.config.with.${arg}`}
                    value={argValue}
                    onChange={(v) => setConfig('with', { ...args, [arg]: v })}
                    markBad={markBad}
                  />
                ))}
            </div>
          )
        }
        return (
          <ValueField
            key={key}
            label={key}
            fieldKey={`${row.path}.config.${key}`}
            value={value}
            onChange={(v) => setConfig(key, v)}
            markBad={markBad}
          />
        )
      })}
    </div>
  )
}

const isPlainObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)

/** A value the read hid. Shown for what it is — present and kept — rather than as an empty field a
 *  user would "fill in" and so overwrite. */
function HiddenValue({ name }: { name: string }) {
  return (
    <div data-type="body-s" className="flex items-center gap-xs text-on-surface-low" data-hidden-value={name}>
      <EyeOff size={14} className="shrink-0" />
      <span><span className="font-mono">{name}</span> is hidden because its name reads like a credential. It is kept when you save.</span>
    </div>
  )
}

// ── one setting ────────────────────────────────────────────────────────────────

/** A control for one value, chosen by what the value IS: text for a string (a prompt, a binding),
 *  a number field, a switch, or JSON text for anything structured. `typeHint` is a declared input's
 *  type, which decides the control while its default is still empty. */
function ValueField({ label, fieldKey, value, typeHint, onChange, markBad }: {
  label: string
  fieldKey: string
  value: unknown
  typeHint?: string
  onChange: (v: unknown) => void
  markBad: (key: string, bad: boolean) => void
}) {
  const kind = typeHint && (value === null || value === undefined)
    ? (typeHint === 'integer' ? 'number' : typeHint)
    : typeof value === 'string' ? 'string'
      : typeof value === 'number' ? 'number'
        : typeof value === 'boolean' ? 'boolean'
          : 'json'
  if (kind === 'string') {
    const text = typeof value === 'string' ? value : ''
    const lines = text.split('\n').length
    return (
      <Field label={label}>
        <TextArea value={text} onChange={onChange} rows={Math.min(14, Math.max(1, lines))} mono ariaLabel={label} size="md" />
      </Field>
    )
  }
  if (kind === 'boolean') {
    return (
      <div className="flex items-center justify-between gap-m">
        <span data-type="label-m" className="text-on-surface">{label}</span>
        <Toggle on={value === true} onChange={onChange} label={label} />
      </div>
    )
  }
  if (kind === 'number') {
    return <NumberValue label={label} fieldKey={fieldKey} value={typeof value === 'number' ? value : null} onChange={onChange} markBad={markBad} />
  }
  return <JsonValue label={label} fieldKey={fieldKey} value={value} onChange={onChange} markBad={markBad} />
}

function NumberValue({ label, fieldKey, value, onChange, markBad }: {
  label: string
  fieldKey: string
  value: number | null
  onChange: (v: unknown) => void
  markBad: (key: string, bad: boolean) => void
}) {
  const [text, setText] = useState(value === null ? '' : String(value))
  const bad = text.trim() !== '' && !Number.isFinite(Number(text))
  useEffect(() => { markBad(fieldKey, bad) }, [bad, fieldKey, markBad])
  useEffect(() => () => markBad(fieldKey, false), [fieldKey, markBad])
  return (
    <Field label={label}>
      <TextInput
        type="number"
        value={text}
        onChange={(v) => {
          setText(v)
          if (v.trim() === '') onChange(null)
          else if (Number.isFinite(Number(v))) onChange(Number(v))
        }}
        ariaLabel={label}
        size="md"
      />
      {bad && <FieldError className="mt-xs">Enter a number.</FieldError>}
    </Field>
  )
}

function JsonValue({ label, fieldKey, value, onChange, markBad }: {
  label: string
  fieldKey: string
  value: unknown
  onChange: (v: unknown) => void
  markBad: (key: string, bad: boolean) => void
}) {
  const [text, setText] = useState(() => (value === undefined ? '' : JSON.stringify(value, null, 2)))
  const [error, setError] = useState('')
  useEffect(() => { markBad(fieldKey, !!error) }, [error, fieldKey, markBad])
  useEffect(() => () => markBad(fieldKey, false), [fieldKey, markBad])
  const hidden = useMemo(() => hiddenIn(value), [value])
  return (
    <Field
      label={label}
      hint={hidden.length > 0 ? `Holds ${hidden.length === 1 ? 'a hidden value' : 'hidden values'} (${hidden.join(', ')}), kept when you save.` : 'JSON'}
    >
      <TextArea
        value={text}
        onChange={(v) => {
          setText(v)
          try {
            onChange(v.trim() === '' ? null : JSON.parse(v))
            setError('')
          } catch (e) {
            setError(e instanceof Error ? e.message : 'This is not valid JSON.')
          }
        }}
        rows={Math.min(14, Math.max(2, text.split('\n').length))}
        mono
        ariaLabel={label}
        size="md"
      />
      {error && <FieldError className="mt-xs">{error}</FieldError>}
    </Field>
  )
}

function hiddenIn(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(hiddenIn)
  if (!isPlainObject(value)) return []
  return Object.entries(value).flatMap(([k, v]) => (isHidden(k) ? [hiddenName(k)] : hiddenIn(v)))
}
