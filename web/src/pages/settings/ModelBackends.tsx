import { useState } from 'react'
import { MoreRow } from '../../ui/MoreRow'
import {
  Plus, Cpu, Wifi, Pencil, Trash2, X, Eye, EyeOff,
  CheckCircle2, AlertTriangle, ChevronRight, RotateCcw,
} from 'lucide-react'
import { api, type ModelProvider, type AvailableModel, type ProviderTestResult, type ModelProviderTypeField } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { confirmDelete } from '../../ui/dialog'
import { Button } from '../../ui/Button'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { Skeleton, LoadingStatus } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { TextInput } from '../../ui/forms'
import { OllamaModelManager } from './OllamaModelManager'
import { fvs } from '../../design/fontWeight'

// Provider types + their config forms are NOT hardcoded here — they come from
// the installed model apps' manifests via /api/model-provider-types (see
// AddInstanceForm). A provider whose app isn't installed can't be added. The
// only local label map is a cosmetic fallback for an already-configured
// instance card whose type's app was later uninstalled.
const typeLabel = (type: string) => type

/** First-load placeholder for the remote-provider list (a couple of instance-card
 *  shapes), so the Model section paints instantly on a cold open. */
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

/** Remote model providers — multi-instance connections (Ollama / OpenAI-Compatible
 *  / Anthropic-Compatible). Each instance contributes models to the pool you bind
 *  in Models. Add (with known-service endpoint prefill), test, inspect models,
 *  edit, delete. Backed by /api/model-providers + /api/models/available. */
export function RemoteModelProviders() {
  const [adding, setAdding] = useState(false)
  // Cached + session-persisted: revisiting Providers (or reloading) paints the
  // remote-provider list instantly from cache and revalidates in the background,
  // instead of re-flashing "Loading…" on every open.
  const { data, error, refresh } = useQuery('settings:remote-model-providers', async () => {
    const [provs, rows] = await Promise.all([
      // NOT `.catch(() => [])` — this list IS the panel, so a failed read has to reach the hook or
      // "No remote model providers yet." becomes the app's answer to a 500 (and `{ persist: true }`
      // caches it). The models call KEEPS its catch: it only decorates each card with a model count,
      // so losing it degrades a card rather than inventing an empty list.
      api.modelProviders(),
      api.modelsAvailable().catch(() => [] as { name: string; models?: AvailableModel[] }[]),
    ])
    // Merge (don't overwrite) models from rows sharing the same provider name:
    // /api/models/available returns separate rows per capability-group (chat,
    // image_gen, video_gen) all named "bedrock" — overwriting the map on each
    // row would show only the LAST group's models in the card.
    const map: Record<string, AvailableModel[]> = {}
    for (const r of rows) map[r.name] = [...(map[r.name] ?? []), ...(r.models ?? [])]
    return { providers: provs, available: map }
  }, { persist: true })
  const reload = () => { invalidateKeys('settings:remote-model-providers'); refresh() }
  const available = data?.available ?? {}

  // A region inside the Providers panel, not a page body — so the failure is the canonical
  // `InlineError` band with a retry, not the full-bleed `LoadError` the page-scale lists use.
  if (!data?.providers && error) return (
    <InlineError icon className="mb-3">
      <span className="flex-1">Couldn't load your remote model providers{(error as Error)?.message ? `: ${(error as Error).message}` : '.'}</span>
      <Button variant="secondary" size="sm" onClick={reload}><RotateCcw size={14} /> Retry</Button>
    </InlineError>
  )
  if (!data?.providers) return <RemoteProvidersSkeleton />
  // Ollama is a LOCAL downloadable provider (searchable) — it renders in the Native
  // (bundled) section with the unified download card, NOT here. Filter it out so it
  // isn't listed twice. (Its endpoint config remains editable via that card's provider.)
  const providers = data.providers.filter((p) => p.type !== 'ollama')
  return (
    <div>
      {providers.length === 0 ? (
        <p className="mb-3 text-on-surface-low text-[0.8125rem]">No remote model providers yet. Add an instance to contribute models to the pool.</p>
      ) : (
        <div className="mb-3 flex flex-col gap-2">
          {providers.map((p) => (
            <InstanceCard key={p.name} provider={p} models={available[p.name] ?? []} onChanged={reload} />
          ))}
        </div>
      )}

      {adding
        ? <AddInstanceForm onDone={(created) => { setAdding(false); if (created) reload() }} />
        : <Button variant="secondary" size="sm" onClick={() => setAdding(true)}><Plus size={15} /> Add instance</Button>}
    </div>
  )
}

function CredBadge({ status }: { status: string }) {
  const ok = status === 'ok'
  const missing = status === 'missing'
  const color = ok ? 'var(--color-success)' : missing ? 'var(--color-danger)' : 'var(--color-on-surface-low)'
  // `status` is the backend's credential_status (credential PRESENCE, never a
  // connectivity probe) — "ok" must not claim "Connected"; the Test button is
  // the connectivity check. Say what we know: the instance is configured.
  return (
    <span className="inline-flex shrink-0 items-center gap-1 text-[0.75rem]" style={{ color }}>
      {ok ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />} {ok ? 'Configured' : missing ? 'Missing key' : 'Unconfigured'}
    </span>
  )
}

function InstanceCard({ provider, models, onChanged }: { provider: ModelProvider; models: AvailableModel[]; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const [showModels, setShowModels] = useState(false)
  const [test, setTest] = useState<ProviderTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [busy, setBusy] = useState(false)

  const runTest = async () => {
    setTesting(true); setTest(null)
    try { setTest(await api.testModelProvider(provider.name)) }
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
    //   3. does NOT touch the credential store. Worth stating because it is actionable — a user who
    //      removes a provider to revoke access still has the key saved — and because the app's house
    //      style pairs what goes with what stays. Conditional on `credential_status`, which the row
    //      already renders as a badge, so it is only claimed when a credential really is stored.
    const selections = ' Any use case set to one of its models loses that selection.'
    const key = provider.credential_status === 'ok' ? ' Its saved credential stays in the store.' : ''
    if (!(await confirmDelete('provider', provider.name, {
      body: `Models it provides will no longer be available.${selections}${key}`,
    }))) return
    setBusy(true)
    try { await api.deleteModelProvider(provider.name); onChanged() } catch { setBusy(false) }
  }

  return (
    <div className="rounded-lg bg-surface-container px-4 py-3" style={{ opacity: busy ? 0.5 : 1 }}>
      <div className="flex items-center gap-3">
        <Cpu size={17} className="shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{provider.name}</span>
            <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.75rem]">{typeLabel(provider.type)}</span>
          </div>
          {provider.capabilities.length > 0 && (
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-on-surface-low text-[0.75rem]">
              {provider.capabilities.map((c) => <span key={c}>{c}</span>)}
            </div>
          )}
        </div>
        <CredBadge status={provider.credential_status} />
        <div className="flex shrink-0 items-center gap-0.5">
          {/* `loading`, not `disabled` + a hand-rolled glyph swap: the primitive owns the spinner
              and the cross-fade, and a probe in flight is "working", not "unavailable". */}
          <SquareIconButton label="Test connection" onClick={runTest} loading={testing}><Wifi size={14} /></SquareIconButton>
          {/* Both of these reveal content further down the card (`{showModels && …}` and
              `{editing && <EditInstanceForm/>}`), so they announce expansion rather than pressedness.
              Test connection and Delete claim no state at all. */}
          <SquareIconButton label={provider.type === 'ollama' ? 'Manage models' : 'View models'} onClick={() => setShowModels((v) => !v)} ariaExpanded={showModels}>
            <ChevronRight size={14} style={{ transform: showModels ? 'rotate(90deg)' : 'none' }} />
          </SquareIconButton>
          <SquareIconButton label="Edit" onClick={() => setEditing((v) => !v)} ariaExpanded={editing}>{editing ? <X size={14} /> : <Pencil size={14} />}</SquareIconButton>
          <SquareIconButton label="Delete" onClick={remove}><Trash2 size={14} /></SquareIconButton>
        </div>
      </div>

      {test && (
        <div className="mt-2 flex items-center gap-1.5 text-[0.75rem]" style={{ color: test.ok ? 'var(--color-success)' : 'var(--color-danger)' }}>
          {test.ok ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />} {test.message}
        </div>
      )}

      {showModels && (
        provider.type === 'ollama' ? (
          // First-class management for Ollama: install/pull/delete/inspect (#48).
          <OllamaModelManager provider={provider.name} />
        ) : (
          <div className="mt-3 border-t border-outline-variant/30 pt-3">
            {models.length === 0 ? (
              <p className="text-on-surface-low text-[0.75rem] italic">No models discovered — test the connection or check the endpoint.</p>
            ) : (
              <>
                <div className="mb-1.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide">Available models ({models.length})</div>
                <div className="flex flex-wrap gap-1">
                  {models.slice(0, 24).map((m) => <span key={m.id} className="rounded-md bg-surface-high px-1.5 py-0.5 text-on-surface text-[0.75rem] font-mono">{m.name}</span>)}
                  <MoreRow total={models.length} shown={24} className="px-1" />
                </div>
              </>
            )}
          </div>
        )
      )}

      {editing && <EditInstanceForm provider={provider} onDone={(saved) => { setEditing(false); if (saved) onChanged() }} />}
    </div>
  )
}

const inputCls = 'h-9 w-full rounded-md bg-surface-high px-3 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50'

/** A single schema-driven field: enum → select, sensitive → password, else text.
 *  Exported so the onboarding essential-apps step renders a provider's key/config
 *  fields with the IDENTICAL semantics (label from x-meta, password masking + reveal
 *  for a `sensitive` field) instead of growing a second, subtly different key-entry
 *  idiom for the same settingsSchema. */
export function SchemaField({ field, name, value, onChange }: {
  field: ModelProviderTypeField; name: string; value: string; onChange: (v: string) => void
}) {
  const [show, setShow] = useState(false)
  const meta = field['x-meta'] || {}
  const label = meta.label || name
  const enumVals = field.enum
  if (Array.isArray(enumVals) && enumVals.length > 0) {
    return (
      <label className="flex flex-col gap-1">
        <span className="text-on-surface-low text-[0.75rem]">{label}</span>
        <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} className={inputCls + ' cursor-pointer'}>
          {enumVals.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        {meta.help && <span className="text-on-surface-low text-[0.75rem]">{meta.help}</span>}
      </label>
    )
  }
  const sensitive = !!meta.sensitive
  return (
    <label className="flex flex-col gap-1">
      <span className="text-on-surface-low text-[0.75rem]">{label}</span>
      <div className="relative">
        <input aria-label={label} type={sensitive && !show ? 'password' : 'text'} value={value}
          onChange={(e) => onChange(e.target.value)} placeholder={meta.help || label}
          className={inputCls + (sensitive ? ' pr-10' : '')} />
        {sensitive && (
          <span className="absolute right-1.5 top-1/2 -translate-y-1/2">
            <SquareIconButton label={show ? 'Hide' : 'Show'} onClick={() => setShow((s) => !s)}>
              {show ? <EyeOff size={14} /> : <Eye size={14} />}
            </SquareIconButton>
          </span>
        )}
      </div>
      {meta.help && !sensitive && <span className="text-on-surface-low text-[0.75rem]">{meta.help}</span>}
    </label>
  )
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
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const selected = types && types.length > 0 ? types[Math.min(typeIdx, types.length - 1)] : null
  const props = selected?.settingsSchema?.properties || {}
  const required = selected?.settingsSchema?.required || []
  // Seed defaults when the selected type changes.
  const seedFor = (t: typeof selected) => {
    const seed: Record<string, string> = {}
    for (const [k, f] of Object.entries(t?.settingsSchema?.properties || {})) seed[k] = String(f.default ?? '')
    return seed
  }

  if (!types) {
    return <div className="rounded-lg border border-outline-variant/40 bg-surface p-4 text-on-surface-low text-[0.8125rem]">Loading provider types…</div>
  }
  if (types.length === 0) {
    return (
      <div className="rounded-lg border border-outline-variant/40 bg-surface p-4 text-on-surface-low text-[0.8125rem]">
        No model-provider apps installed. Install one from the Store (e.g. OpenAI, Anthropic, Amazon Bedrock) to add an instance.
      </div>
    )
  }

  const submit = async () => {
    if (!selected) return
    if (!name.trim()) { setError('Instance name is required'); return }
    for (const r of required) {
      if (!String(values[r] ?? props[r]?.default ?? '').trim()) {
        setError(`${props[r]?.['x-meta']?.label || r} is required`); return
      }
    }
    setSaving(true); setError('')
    const options: Record<string, string> = {}
    for (const [k, f] of Object.entries(props)) {
      const v = (values[k] ?? String(f.default ?? '')).trim()
      if (v) options[k] = v
    }
    try { await api.createModelProvider({ name: name.trim(), type: selected.type, model: '', options }); onDone(true) }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Failed to add instance'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch { /* raw */ }
      setError(msg); setSaving(false)
    }
  }

  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface p-4">
      <div className="mb-3 text-on-surface text-[0.8125rem]" style={fvs(600)}>Add model provider instance</div>
      <div className="grid grid-cols-2 gap-2">
        <select aria-label="Provider type" value={typeIdx}
          onChange={(e) => { const i = Number(e.target.value); setTypeIdx(i); setValues(seedFor(types[i])); setError('') }}
          className={inputCls + ' cursor-pointer'}>
          {types.map((t, i) => <option key={t.type} value={i}>{t.label}</option>)}
        </select>
        <TextInput ariaLabel="Instance name" value={name} onChange={setName} placeholder="Instance name (e.g. my-bedrock)" size="md" surface="high" />
      </div>
      <div className="mt-2 flex flex-col gap-2">
        {Object.entries(props).map(([k, f]) => (
          <SchemaField key={k} name={k} field={f}
            value={values[k] ?? String(f.default ?? '')}
            onChange={(v) => setValues((prev) => ({ ...prev, [k]: v }))} />
        ))}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" onClick={submit} disabled={saving}>{saving ? 'Adding…' : 'Add instance'}</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {error && <span className="text-[0.75rem]" style={{ color: 'var(--color-danger)' }}>{error}</span>}
      </div>
    </div>
  )
}

function EditInstanceForm({ provider, onDone }: { provider: ModelProvider; onDone: (saved: boolean) => void }) {
  const isAws = provider.type === 'bedrock'
  const [endpoint, setEndpoint] = useState('')
  const [region, setRegion] = useState('')
  const [profile, setProfile] = useState('')
  const [model, setModel] = useState(provider.model ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    setSaving(true); setError('')
    const body: { model?: string; options?: Record<string, string> } = {}
    const options: Record<string, string> = {}
    if (isAws) {
      if (region.trim()) options.region = region.trim()
      if (profile.trim()) options.profile = profile.trim()
    } else if (endpoint.trim()) {
      options.endpoint = endpoint.trim()
    }
    if (Object.keys(options).length) body.options = options
    if (model.trim() !== (provider.model ?? '')) body.model = model.trim()
    if (!body.options && body.model === undefined) { onDone(false); return }
    try { await api.updateModelProvider(provider.name, body); onDone(true) }
    catch (e) { setError(e instanceof Error ? e.message : 'Save failed'); setSaving(false) }
  }

  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-outline-variant/30 pt-3">
      {isAws ? (
        <>
          <TextInput ariaLabel="AWS region" value={region} onChange={setRegion} placeholder="AWS region (leave empty to keep current)" size="md" surface="high" />
          <TextInput ariaLabel="AWS profile" value={profile} onChange={setProfile} placeholder="AWS profile (leave empty to keep current)" size="md" surface="high" />
        </>
      ) : (
        <TextInput ariaLabel="Endpoint" value={endpoint} onChange={setEndpoint} placeholder="Endpoint (leave empty to keep current)" size="md" surface="high" />
      )}
      <TextInput ariaLabel="Default model" value={model} onChange={setModel} placeholder="Default model (optional)" size="md" surface="high" />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {error && <span className="text-[0.75rem]" style={{ color: 'var(--color-danger)' }}>{error}</span>}
      </div>
    </div>
  )
}
