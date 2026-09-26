import { useState } from 'react'
import { MoreRow } from '../../ui/MoreRow'
import {
  Plus, Cpu, Wifi, Pencil, Trash2, X, Loader2,
  CheckCircle2, AlertTriangle, ChevronRight, RotateCcw, KeyRound,
} from 'lucide-react'
import {
  api, type ModelProvider, type ModelConnection, type ProviderModels, type ProviderTestResult,
  type ProviderOptionValue, type ProviderSchema,
} from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { confirmDelete } from '../../ui/dialog'
import { Button } from '../../ui/Button'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Skeleton, LoadingStatus } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { TextInput } from '../../ui/forms'
import { LocalModelManager } from './LocalModelManager'
import { fvs } from '../../design/fontWeight'
import { reportingWrite } from '../../app/reportingWrite'
import { notify } from '../../app/appSdk'
import { SchemaFields } from '../tools/schema'
import { SchemaField, schemaDefaults } from './ProviderConfigForm'

// Provider types + their config forms are NOT hardcoded here — they come from
// the installed model apps' manifests via /api/model-provider-types (see
// AddInstanceForm). A provider whose app isn't installed can't be added. The
// only local label map is a cosmetic fallback for an already-configured
// instance card whose type's app was later uninstalled.
const typeLabel = (type: string) => type

/** How often an unsettled list is re-read while a connection is still being measured. The
 *  gateway answers from memory, so this polls a cache, never the providers themselves. */
const CHECKING_POLL_MS = 2500

/** First-load placeholder for the instance list (a couple of instance-card shapes), so the
 *  Model section paints instantly on a cold open. */
function RemoteProvidersSkeleton() {
  return (
    <div className="mb-3 flex flex-col gap-2" role="status" aria-busy="true" >
        <LoadingStatus what="model providers" />
      {Array.from({ length: 2 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-lg bg-surface-container px-l py-m">
          <Skeleton className="size-7 shrink-0 rounded-lg" />
          <div className="flex-1 min-w-0 space-y-2"><Skeleton className="h-3.5 w-1/3" /><Skeleton className="h-3 w-1/2" /></div>
          <Skeleton className="h-5 w-20 shrink-0 rounded-pill" />
        </div>
      ))}
    </div>
  )
}

/** What `/api/models/available` says about one instance, merged across its rows. */
interface InstanceModels {
  models: NonNullable<ProviderModels['models']>
  error: string
  local: boolean
  searchable: boolean
}

/** Every configured model-provider instance (`config.json` `providers[]` — the one store chat
 *  resolves), whatever its type: each one can be tested, edited and removed here, and its card
 *  shows the connection its last test MEASURED. Backed by /api/model-providers +
 *  /api/models/available.
 *
 *  An Ollama instance used to be filtered out of this list — the only one with Test, Edit and
 *  Remove — and shown elsewhere with none of them, so a broken endpoint could not be fixed or
 *  removed from the page that displayed it. It renders here like any other instance, and its
 *  local-model card (installed models, library search, downloads) lives inside its card.
 *
 *  `onChanged` is the PANEL's refresh: the panel's own reads (`settings:providers`,
 *  `settings:models-available`) must see a write made here too. */
export function RemoteModelProviders({ onChanged }: { onChanged: () => void }) {
  const [adding, setAdding] = useState(false)
  // Cached + session-persisted: revisiting Providers (or reloading) paints the
  // instance list instantly from cache and revalidates in the background,
  // instead of re-flashing "Loading…" on every open.
  const { data, error, refresh } = useQuery('settings:remote-model-providers', async () => {
    const [provs, rows] = await Promise.all([
      // NOT `.catch(() => [])` — this list IS the panel, so a failed read has to reach the hook or
      // "No model provider instances yet." becomes the app's answer to a 500 (and `{ persist: true }`
      // caches it). The models call KEEPS its catch: it only decorates each card with its models,
      // so losing it degrades a card rather than inventing an empty list.
      api.modelProviders(),
      api.modelsAvailable().catch(() => [] as ProviderModels[]),
    ])
    // Merge (don't overwrite) rows sharing the same provider name: /api/models/available
    // returns separate rows per capability-group (chat, image_gen, video_gen) all named
    // "bedrock" — overwriting on each row would show only the LAST group's models.
    const byName: Record<string, InstanceModels> = {}
    for (const r of rows) {
      const prev = byName[r.name] ?? { models: [], error: '', local: false, searchable: false }
      byName[r.name] = {
        models: [...prev.models, ...(r.models ?? [])],
        error: prev.error || r.error || '',
        local: prev.local || !!r.local,
        searchable: prev.searchable || !!r.searchable,
      }
    }
    return { providers: provs, byName }
  }, { persist: true })
  const reload = () => { invalidateKeys('settings:remote-model-providers'); refresh(); onChanged() }
  // A never-measured instance reads `checking` while its first test runs in the background;
  // re-read until every card has its answer (and stop the moment none is left checking).
  const checking = (data?.providers ?? []).some((p) => p.connection?.state === 'checking')
  useVisiblePoll(() => { if (checking) refresh() }, checking ? CHECKING_POLL_MS : null)

  // A region inside the Providers panel, not a page body — so the failure is the canonical
  // `InlineError` band with a retry, not the full-bleed `LoadError` the page-scale lists use.
  if (!data?.providers && error) return (
    <InlineError icon className="mb-3">
      <span className="flex-1">Couldn't load your model provider instances{(error as Error)?.message ? `: ${(error as Error).message}` : '.'}</span>
      <Button variant="secondary" size="sm" onClick={reload}><RotateCcw size={14} /> Retry</Button>
    </InlineError>
  )
  if (!data?.providers) return <RemoteProvidersSkeleton />
  const providers = data.providers
  return (
    <div>
      {providers.length === 0 ? (
        <p data-type="body-s" className="mb-m text-on-surface-low">No model provider instances yet. Add an instance to contribute models to the pool.</p>
      ) : (
        <div className="mb-3 flex flex-col gap-2">
          {providers.map((p) => (
            <InstanceCard key={p.name} provider={p} listing={data.byName[p.name]} onChanged={reload} />
          ))}
        </div>
      )}

      {adding
        ? <AddInstanceForm onDone={(created) => { setAdding(false); if (created) reload() }} />
        : <Button variant="secondary" size="sm" onClick={() => setAdding(true)}><Plus size={15} /> Add instance</Button>}
    </div>
  )
}

/** The badge says what was MEASURED. It used to be the credential's presence — "Configured" on
 *  an instance with no key at all, beside its own test saying "No API key or endpoint
 *  configured" — because presence was the only thing the list knew. */
export function ConnectionBadge({ connection }: { connection: ModelConnection | undefined }) {
  const state = connection?.state ?? 'checking'
  const face = state === 'connected'
    ? { icon: <CheckCircle2 size={12} />, label: 'Connected', color: 'var(--color-success)' }
    : state === 'failed'
      ? connection?.rejected_credential
        ? { icon: <KeyRound size={12} />, label: 'Key rejected', color: 'var(--color-danger)' }
        : { icon: <AlertTriangle size={12} />, label: 'Not answering', color: 'var(--color-danger)' }
      : state === 'untestable'
        ? { icon: <AlertTriangle size={12} />, label: 'No connection test', color: 'var(--color-on-surface-low)' }
        : { icon: <Loader2 size={12} className="animate-spin" />, label: 'Checking…', color: 'var(--color-on-surface-low)' }
  return (
    <span data-type="caption" className="inline-flex shrink-0 items-center gap-1" style={{ color: face.color }}
      title={connection?.detail || undefined}>
      {face.icon} {face.label}
    </span>
  )
}

function InstanceCard({ provider, listing, onChanged }: {
  provider: ModelProvider; listing: InstanceModels | undefined; onChanged: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [showModels, setShowModels] = useState(false)
  const [test, setTest] = useState<ProviderTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [busy, setBusy] = useState(false)
  const models = listing?.models ?? []
  const local = !!listing?.local
  const connection = provider.connection

  const runTest = async () => {
    setTesting(true); setTest(null)
    try {
      setTest(await api.testModelProvider(provider.name))
      // The test's answer is RECORDED as the instance's connection, so the badge, the Models
      // picker and the next page load now agree with what the user just saw.
      onChanged()
    }
    catch (e) { setTest({ ok: false, message: e instanceof Error ? e.message : 'Test failed' }) }
    setTesting(false)
  }
  const remove = async () => {
    // 🔑 VERIFIED AGAINST `handlers/providers.py`'s `api_provider_delete`, which does three things, and
    // the body described only the first:
    //
    //   1. drops the entry from config + unregisters it — "models no longer available", as stated;
    //   2. `_drop_provider_active_models(name)` removes EVERY active-model ref for it across EVERY use
    //      case, so a use case pointed at one of its models silently loses that choice. That is the
    //      user's configuration changing, not just a capability going away, and nothing else says so;
    //   3. deletes the key(s) this instance kept in the credential store — `secret_refs.purge` of the
    //      instance's own prefix. Worth stating because it is actionable: removing a provider revokes
    //      the key saved for it, and adding it back means entering the key again. Conditional on
    //      `stored_secrets` (names only, from the list route), so it is only claimed when a key really
    //      is stored — and a reference the instance held to a Secrets-panel credential is neither
    //      counted there nor deleted, so the sentence never overstates what goes. That referenced
    //      credential is the half that STAYS, said when `key_in_store` reports one.
    const selections = ' Any use case set to one of its models loses that selection.'
    const saved = provider.stored_secrets?.length ?? 0
    const key = saved === 0 ? '' : saved === 1
      ? ' The key saved for it is deleted too.'
      : ` The ${saved} credentials saved for it are deleted too.`
    const referenced = provider.key_in_store ? ' The credential it uses from Settings → Secrets stays saved.' : ''
    if (!(await confirmDelete('provider', provider.name, {
      body: `Models it provides will no longer be available.${selections}${key}${referenced}`,
    }))) return
    setBusy(true)
    // `catch { setBusy(false) }` restored the row's opacity and said NOTHING, so a failed removal
    // was pixel-identical to never having clicked. The dialog above spells out three consequences
    // (models unavailable, use-case selections lost, the credential stays) — a user who accepted
    // those is owed the outcome.
    if (!(await reportingWrite(`remove ${provider.name}`, () => api.deleteModelProvider(provider.name)))) {
      setBusy(false)
      return
    }
    onChanged()
  }

  // The measured failure stays on the card, not only in a Test result the user has to ask for:
  // an unreachable endpoint or a rejected key is the first thing this card has to say.
  const failure = !test && connection?.state === 'failed' ? connection.detail : ''
  return (
    <div className="rounded-lg bg-surface-container px-4 py-3" style={{ opacity: busy ? 0.5 : 1 }}>
      <div className="flex items-center gap-3">
        <Cpu size={17} className="shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span data-type="title-m" className="truncate text-on-surface" style={fvs(500)}>{provider.name}</span>
            <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low">{typeLabel(provider.declared_type || provider.type)}</span>
          </div>
          {provider.capabilities.length > 0 && (
            <div data-type="caption" className="mt-0.5 flex flex-wrap items-center gap-x-2 text-on-surface-low">
              {provider.capabilities.map((c) => <span key={c}>{c}</span>)}
            </div>
          )}
        </div>
        <ConnectionBadge connection={connection} />
        <div className="flex shrink-0 items-center gap-0.5">
          {/* `loading`, not `disabled` + a hand-rolled glyph swap: the primitive owns the spinner
              and the cross-fade, and a probe in flight is "working", not "unavailable". */}
          <SquareIconButton label={`Test connection: ${provider.name}`} title="Test connection" onClick={runTest} loading={testing}><Wifi size={14} /></SquareIconButton>
          {/* Both of these reveal content further down the card (`{showModels && …}` and
              `{editing && <EditInstanceForm/>}`), so they announce expansion rather than pressedness.
              Test connection and Delete claim no state at all. */}
          <SquareIconButton label={`${local ? 'Manage models' : 'View models'}: ${provider.name}`} title={local ? 'Manage models' : 'View models'} onClick={() => setShowModels((v) => !v)} ariaExpanded={showModels}>
            <ChevronRight size={14} style={{ transform: showModels ? 'rotate(90deg)' : 'none' }} />
          </SquareIconButton>
          <SquareIconButton label={`Edit: ${provider.name}`} title="Edit" onClick={() => setEditing((v) => !v)} ariaExpanded={editing}>{editing ? <X size={14} /> : <Pencil size={14} />}</SquareIconButton>
          <SquareIconButton label={`Remove: ${provider.name}`} title="Remove" onClick={remove}><Trash2 size={14} /></SquareIconButton>
        </div>
      </div>

      {failure && (
        <div data-type="caption" className="mt-2 flex items-start gap-1.5" style={{ color: 'var(--color-danger)' }}>
          <AlertTriangle size={13} className="mt-0.5 shrink-0" /> <span className="min-w-0">{failure}</span>
        </div>
      )}
      {test && (
        <div role="status" data-type="caption" className="mt-2 flex items-start gap-1.5" style={{ color: test.ok ? 'var(--color-success)' : 'var(--color-danger)' }}>
          {test.ok ? <CheckCircle2 size={13} className="mt-0.5 shrink-0" /> : <AlertTriangle size={13} className="mt-0.5 shrink-0" />} <span className="min-w-0">{test.message}</span>
        </div>
      )}

      {showModels && (
        <div className="mt-3 border-t border-outline-variant/30 pt-3">
          {local ? (
            // A local-download instance (Ollama): the one uniform download card — installed
            // models, library search, downloads — for THIS instance's endpoint.
            <LocalModelManager provider={provider.name} models={models} searchable={listing?.searchable}
              error={listing?.error} onChanged={onChanged} />
          ) : models.length === 0 ? (
            listing?.error
              ? <p role="alert" data-type="caption" className="flex items-start gap-1.5" style={{ color: 'var(--color-danger)' }}><AlertTriangle size={12} className="mt-0.5 shrink-0" /> <span className="min-w-0">{listing.error}</span></p>
              : <p data-type="caption" className="text-on-surface-low italic">This instance lists no models.</p>
          ) : (
            <>
              <div data-type="caption" className="mb-1.5 text-on-surface-low uppercase tracking-wide">Available models ({models.length})</div>
              <div className="flex flex-wrap gap-1">
                {models.slice(0, 24).map((m) => <span key={m.id} data-type="caption" className="rounded-md bg-surface-high px-1.5 py-0.5 text-on-surface font-mono">{m.name}</span>)}
                <MoreRow total={models.length} shown={24} className="px-1" />
              </div>
            </>
          )}
        </div>
      )}

      {/* A saved edit makes the last Test's answer stale — it described the settings that were
          just replaced (measured: "Could not reach …:1" kept showing beside "Connected"). */}
      {editing && <EditInstanceForm provider={provider} onDone={(saved) => { setEditing(false); if (saved) { setTest(null); onChanged() } }} />}
    </div>
  )
}

/** Metrics + chrome only — the type size rides `data-type="body-s"` on each consumer,
 *  since a class string has no element to carry the attribute. */
const inputCls = 'h-9 w-full rounded-md bg-surface-high px-3 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'

/** A value that says nothing: absent, `null`, or an empty/whitespace string. */
function isBlank(v: unknown): boolean {
  return v === undefined || v === null || (typeof v === 'string' && !v.trim())
}

/** Add a model-provider instance. The provider-type dropdown AND the config
 *  fields are driven entirely by the installed model apps' manifests
 *  (/api/model-provider-types) — no hardcoded type list. A provider whose app
 *  isn't installed never appears; each type's settingsSchema renders its own
 *  fields (api_key / region / endpoint enum / …). */
function AddInstanceForm({ onDone }: { onDone: (created: boolean) => void }) {
  const { data: types } = useQuery('settings:model-provider-types', () => api.modelProviderTypes(), { persist: true })
  const [typeIdx, setTypeIdx] = useState(0)
  const [name, setName] = useState('')
  // TYPED, as each field's schema declares it (see `ModelProviderType`): a switch holds a
  // boolean and a number field a number, and that is what is saved.
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const [seeded, setSeeded] = useState('')

  const selected = types && types.length > 0 ? types[Math.min(typeIdx, types.length - 1)] : null
  const props = selected?.settingsSchema?.properties || {}
  const required = selected?.settingsSchema?.required || []
  // Seed the selected type's defaults — in their own types — on the first render that knows the
  // type, and again whenever the selection changes. The form shows exactly what it will send.
  if (selected && seeded !== selected.type) {
    setValues(schemaDefaults(selected.settingsSchema)); setSeeded(selected.type)
  }

  if (!types) {
    return <div data-type="body-s" className="rounded-lg border border-outline-variant/40 bg-surface p-4 text-on-surface-low">Loading provider types…</div>
  }
  if (types.length === 0) {
    return (
      <div data-type="body-s" className="rounded-lg border border-outline-variant/40 bg-surface p-4 text-on-surface-low">
        No model-provider apps installed. Install one from the Store (e.g. OpenAI, Anthropic, Amazon Bedrock) to add an instance.
      </div>
    )
  }

  const submit = async () => {
    if (!selected) return
    if (!name.trim()) { setError('Instance name is required'); return }
    for (const r of required) {
      if (isBlank(values[r] ?? props[r]?.default)) {
        setError(`${props[r]?.['x-meta']?.label || r} is required`); return
      }
    }
    setSaving(true); setError('')
    const options: Record<string, ProviderOptionValue> = {}
    for (const [k, f] of Object.entries(props)) {
      const raw = values[k] ?? f.default
      const v = typeof raw === 'string' ? raw.trim() : raw
      // `false` and `0` are settings, not blanks — only an absent or empty value is left out.
      if (!isBlank(v)) options[k] = v as ProviderOptionValue
    }
    try {
      await api.createModelProvider({ name: name.trim(), type: selected.type, model: '', options })
      // Say it landed. The only feedback for a successful add used to be the section's own
      // "No remote model providers yet." — a sentence that contradicted it — and a user shown
      // no evidence clicks again, which the backend answers 409 "already exists" (#3488).
      notify(`Added ${name.trim()}.`, 'success')
      onDone(true)
    }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Failed to add instance'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch { /* raw */ }
      setError(msg); setSaving(false)
    }
  }

  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface p-4">
      <div data-type="label-s" className="mb-3 text-on-surface" style={fvs(600)}>Add model provider instance</div>
      <div className="grid grid-cols-2 gap-2">
        <select aria-label="Provider type" value={typeIdx}
          onChange={(e) => { setTypeIdx(Number(e.target.value)); setError('') }}
          data-type="body-s" className={inputCls + ' cursor-pointer'}>
          {types.map((t, i) => <option key={t.type} value={i}>{t.label}</option>)}
        </select>
        <TextInput ariaLabel="Instance name" value={name} onChange={setName} placeholder="Instance name (e.g. my-bedrock)" size="md" surface="high" />
      </div>
      <div className="mt-2 flex flex-col gap-2">
        <SchemaFields
          key={selected?.type}
          fields={Object.entries(props)}
          required={required}
          values={values}
          advancedFieldClassName="flex flex-col gap-s"
          renderField={(k, field) => (
            <SchemaField fieldKey={k} prop={field} value={values[k]}
              onChange={(v) => setValues((prev) => ({ ...prev, [k]: v }))} />
          )}
        />
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" onClick={submit} loading={saving} loadingLabel="Adding…">Add instance</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {error && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{error}</span>}
      </div>
    </div>
  )
}

/** The fields an instance of a type whose app is no longer installed can still edit: the
 *  endpoint and key every protocol client reads (`options.endpoint`, `options.api_key`). */
const FALLBACK_SCHEMA: ProviderSchema = {
  type: 'object',
  properties: {
    endpoint: { type: 'string', 'x-meta': { label: 'Endpoint', placeholder: 'http://localhost:11434' } },
    api_key: { type: 'string', 'x-meta': { label: 'API key', sensitive: true } },
  },
}

/** The instance's settings as the form starts them: every stored secret BLANK (the list hands
 *  them out masked, and editing a row of bullets is nonsense — a blank submit means "keep"). */
function editableSettings(provider: ModelProvider): Record<string, unknown> {
  const next: Record<string, unknown> = { ...(provider.options ?? {}) }
  for (const k of provider.secret_set ?? []) next[k] = ''
  return next
}

/** Edit an instance with its TYPE's own settings form — the same `settingsSchema` the Add form
 *  renders — so every setting an instance was created with, its API key included, can be
 *  changed here. It used to offer only an endpoint, a context window and a model: a key could
 *  be neither added to a keyless instance nor replaced after the vendor rejected it.
 *
 *  Writes through `PUT /api/model-providers/{name}`, whose options MERGE: a field left as it was
 *  stays out of the body, an emptied field is sent as `null` (clear), and a stored secret left
 *  blank is kept. Values are saved in their declared types, as the Add form saves them, and a
 *  key typed here goes to the credential store — `config.json` keeps only a reference to it. */
function EditInstanceForm({ provider, onDone }: { provider: ModelProvider; onDone: (saved: boolean) => void }) {
  const { data: types, status: typesStatus } = useQuery('settings:model-provider-types', () => api.modelProviderTypes(), { persist: true })
  const declared = provider.declared_type || provider.type
  const type = types?.find((t) => t.type === declared) ?? types?.find((t) => t.type === provider.type)
  const schema: ProviderSchema = type?.settingsSchema ?? FALLBACK_SCHEMA
  const props = Object.entries(schema.properties ?? {})
  const secretSet = provider.secret_set ?? []
  const [initial] = useState(() => editableSettings(provider))
  const [values, setValues] = useState<Record<string, unknown>>(initial)
  const [clearing, setClearing] = useState<string[]>([])
  const [model, setModel] = useState(provider.model ?? '')
  // The served context window, editable only for a non-AWS (endpoint-based) binding —
  // it describes what a LOCAL runtime actually serves, which is the one thing the model
  // table cannot know. A managed cloud endpoint has no such knob, so the AWS arm omits
  // it rather than offering a field that could only ever misreport the window.
  // …and only when the type's own schema does not already carry the setting (ollama-models'
  // declares `context_window`), so the form never shows it twice.
  const endpointBased = declared !== 'bedrock' && provider.type !== 'bedrock'
    && !props.some(([k]) => k === 'context_window')
  const [contextWindow, setContextWindow] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // A schema with its own default-model setting owns that fact; the entry's top-level `model`
  // is offered only when the type declares none, so one instance never shows two of them.
  const ownsModel = props.some(([k]) => k === 'default_model')

  const save = async () => {
    setSaving(true); setError('')
    const options: Record<string, ProviderOptionValue> = {}
    for (const [k] of props) {
      const secret = secretSet.includes(k) || !!schema.properties?.[k]?.['x-meta']?.sensitive
      const now = values[k]
      if (clearing.includes(k)) { options[k] = null; continue }
      if (secret) {
        // Blank = keep what is stored; only a value the user typed replaces it.
        if (typeof now === 'string' && now.trim()) options[k] = now.trim()
        continue
      }
      if (now === initial[k]) continue
      // In the field's own type: a switch saves a boolean and a number field a number, never
      // their text. Only an emptied field is a clear.
      const v = typeof now === 'string' ? now.trim() : now
      options[k] = isBlank(v) ? null : (v as ProviderOptionValue)
    }
    if (endpointBased && contextWindow.trim()) options.context_window = contextWindow.trim()
    const body: { model?: string; options?: Record<string, ProviderOptionValue> } = {}
    if (Object.keys(options).length) body.options = options
    if (!ownsModel && model.trim() !== (provider.model ?? '')) body.model = model.trim()
    if (!body.options && body.model === undefined) { onDone(false); return }
    try { await api.updateModelProvider(provider.name, body); onDone(true) }
    catch (e) { setError(e instanceof Error ? e.message : 'Save failed'); setSaving(false) }
  }

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-outline-variant/30 pt-3">
      {/* Said, not silently substituted: without the type list the form falls back to the fields
          every protocol client reads, and the user should know that is what they are looking at. */}
      {!type && typesStatus === 'error' && (
        <p role="alert" data-type="caption" className="text-on-surface-low">
          Couldn't load this provider type's settings — showing the endpoint and API key only.
        </p>
      )}
      {!type && typesStatus !== 'error' && types && (
        <p data-type="caption" className="text-on-surface-low">
          The app for this provider type isn't installed — showing the endpoint and API key only.
        </p>
      )}
      <SchemaFields
        fields={props}
        required={schema.required ?? []}
        values={values}
        configured={secretSet.filter((k) => !clearing.includes(k))}
        advancedFieldClassName="flex flex-col gap-3"
        renderField={(k, prop) => {
          const stored = secretSet.includes(k)
          return (
            <div>
              <SchemaField fieldKey={k} prop={prop} value={values[k]} secretAlreadySet={stored && !clearing.includes(k)}
                onChange={(v) => { setValues((prev) => ({ ...prev, [k]: v })); setClearing((c) => c.filter((x) => x !== k)) }} />
              {/* A stored key can be REMOVED, not only replaced: "keep" is what a blank means, so
                  taking a key away needs its own control. */}
              {stored && (
                <Button variant="ghost" size="xs" className="-ml-m mt-0.5"
                  onClick={() => setClearing((c) => (c.includes(k) ? c.filter((x) => x !== k) : [...c, k]))}>
                  {clearing.includes(k) ? 'Keep the saved value' : 'Remove the saved value'}
                </Button>
              )}
            </div>
          )
        }}
      />
      {provider.key_in_store && (
        <p data-type="caption" className="text-on-surface-low">This instance authenticates with a credential from Settings → Secrets.</p>
      )}
      {endpointBased && (
        <TextInput ariaLabel="Served context window" type="number" min={1} value={contextWindow} onChange={setContextWindow}
          placeholder="Served context window in tokens (leave empty to auto-detect)" size="md" surface="high" />
      )}
      {!ownsModel && (
        <TextInput ariaLabel="Default model" value={model} onChange={setModel} placeholder="Default model (optional)" size="md" surface="high" />
      )}
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} loading={saving}>Save</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {error && <span role="alert" data-type="caption" style={{ color: 'var(--color-danger)' }}>{error}</span>}
      </div>
    </div>
  )
}
