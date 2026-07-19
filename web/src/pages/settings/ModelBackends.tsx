import { useState } from 'react'
import {
  Plus, Cpu, Wifi, Pencil, Trash2, X, Eye, EyeOff, Loader2,
  CheckCircle2, AlertTriangle, ChevronRight,
} from 'lucide-react'
import { api, type ModelProvider, type AvailableModel, type ProviderTestResult, type ModelProviderTypeField } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { confirmDelete } from '../../ui/dialog'
import { Button } from '../../ui/Button'
import { Skeleton } from '../../ui/ListScaffold'
import { OllamaModelManager } from './OllamaModelManager'

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
    <div className="mb-3 flex flex-col gap-2" aria-busy="true" aria-label="Loading model providers">
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
  const { data, refresh } = useCachedData('settings:remote-model-providers', async () => {
    const [provs, rows] = await Promise.all([
      api.modelProviders().catch(() => [] as ModelProvider[]),
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
  const reload = () => { invalidateCache('settings:remote-model-providers'); refresh() }
  const available = data?.available ?? {}

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
    <span className="inline-flex shrink-0 items-center gap-1 text-[0.72rem]" style={{ color }}>
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
    if (!(await confirmDelete('provider', provider.name, { body: 'Models it provides will no longer be available.' }))) return
    setBusy(true)
    try { await api.deleteModelProvider(provider.name); onChanged() } catch { setBusy(false) }
  }

  return (
    <div className="rounded-lg bg-surface-container px-4 py-3" style={{ opacity: busy ? 0.5 : 1 }}>
      <div className="flex items-center gap-3">
        <Cpu size={17} className="shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{provider.name}</span>
            <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.65rem]">{typeLabel(provider.type)}</span>
          </div>
          {provider.capabilities.length > 0 && (
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-on-surface-low text-[0.72rem]">
              {provider.capabilities.map((c) => <span key={c}>{c}</span>)}
            </div>
          )}
        </div>
        <CredBadge status={provider.credential_status} />
        <div className="flex shrink-0 items-center gap-0.5">
          <IconBtn label="Test connection" onClick={runTest} active={testing}>{testing ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />}</IconBtn>
          <IconBtn label={provider.type === 'ollama' ? 'Manage models' : 'View models'} onClick={() => setShowModels((v) => !v)} on={showModels}>
            <ChevronRight size={14} style={{ transform: showModels ? 'rotate(90deg)' : 'none' }} />
          </IconBtn>
          <IconBtn label="Edit" onClick={() => setEditing((v) => !v)} on={editing}>{editing ? <X size={14} /> : <Pencil size={14} />}</IconBtn>
          <IconBtn label="Delete" onClick={remove}><Trash2 size={14} /></IconBtn>
        </div>
      </div>

      {test && (
        <div className="mt-2 flex items-center gap-1.5 text-[0.78rem]" style={{ color: test.ok ? 'var(--color-success)' : 'var(--color-danger)' }}>
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
              <p className="text-on-surface-low text-[0.78rem] italic">No models discovered — test the connection or check the endpoint.</p>
            ) : (
              <>
                <div className="mb-1.5 text-on-surface-low text-[0.7rem] uppercase tracking-wide">Available models ({models.length})</div>
                <div className="flex flex-wrap gap-1">
                  {models.slice(0, 24).map((m) => <span key={m.id} className="rounded-md bg-surface-high px-1.5 py-0.5 text-on-surface text-[0.7rem] font-mono">{m.name}</span>)}
                  {models.length > 24 && <span className="px-1 text-on-surface-low text-[0.7rem]">+{models.length - 24} more</span>}
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

function IconBtn({ children, label, onClick, on, active }: {
  children: React.ReactNode; label: string; onClick: () => void; on?: boolean; active?: boolean
}) {
  return (
    <button type="button" onClick={onClick} disabled={active} aria-label={label} title={label}
      className="grid size-7 place-items-center rounded-md text-on-surface-low transition-colors hover:text-on-surface disabled:opacity-50"
      style={on ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)', color: 'var(--color-primary)' } : undefined}>
      {children}
    </button>
  )
}

const inputCls = 'h-9 w-full rounded-md bg-surface-high px-3 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50'

/** A single schema-driven field: enum → select, sensitive → password, else text. */
function SchemaField({ field, name, value, onChange }: {
  field: ModelProviderTypeField; name: string; value: string; onChange: (v: string) => void
}) {
  const [show, setShow] = useState(false)
  const meta = field['x-meta'] || {}
  const label = meta.label || name
  const enumVals = field.enum
  if (Array.isArray(enumVals) && enumVals.length > 0) {
    return (
      <label className="flex flex-col gap-1">
        <span className="text-on-surface-low text-[0.72rem]">{label}</span>
        <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} className={inputCls + ' cursor-pointer'}>
          {enumVals.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        {meta.help && <span className="text-on-surface-low text-[0.68rem]">{meta.help}</span>}
      </label>
    )
  }
  const sensitive = !!meta.sensitive
  return (
    <label className="flex flex-col gap-1">
      <span className="text-on-surface-low text-[0.72rem]">{label}</span>
      <div className="relative">
        <input aria-label={label} type={sensitive && !show ? 'password' : 'text'} value={value}
          onChange={(e) => onChange(e.target.value)} placeholder={meta.help || label}
          className={inputCls + (sensitive ? ' pr-10' : '')} />
        {sensitive && (
          <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? 'Hide' : 'Show'}
            className="absolute right-1.5 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-md text-on-surface-low hover:text-on-surface">
            {show ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        )}
      </div>
      {meta.help && !sensitive && <span className="text-on-surface-low text-[0.68rem]">{meta.help}</span>}
    </label>
  )
}

/** Add a model-provider instance. The provider-type dropdown AND the config
 *  fields are driven entirely by the installed model apps' manifests
 *  (/api/model-provider-types) — no hardcoded type list. A provider whose app
 *  isn't installed never appears; each type's settingsSchema renders its own
 *  fields (api_key / region / endpoint enum / …). */
function AddInstanceForm({ onDone }: { onDone: (created: boolean) => void }) {
  const { data: types } = useCachedData('settings:model-provider-types', () => api.modelProviderTypes(), { persist: true })
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
      <div className="mb-3 text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 600' }}>Add model provider instance</div>
      <div className="grid grid-cols-2 gap-2">
        <select aria-label="Provider type" value={typeIdx}
          onChange={(e) => { const i = Number(e.target.value); setTypeIdx(i); setValues(seedFor(types[i])); setError('') }}
          className={inputCls + ' cursor-pointer'}>
          {types.map((t, i) => <option key={t.type} value={i}>{t.label}</option>)}
        </select>
        <input aria-label="Instance name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Instance name (e.g. my-bedrock)" className={inputCls} />
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
        {error && <span className="text-[0.78rem]" style={{ color: 'var(--color-danger)' }}>{error}</span>}
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
          <input aria-label="AWS region" value={region} onChange={(e) => setRegion(e.target.value)} placeholder="AWS region (leave empty to keep current)" className={inputCls} />
          <input aria-label="AWS profile" value={profile} onChange={(e) => setProfile(e.target.value)} placeholder="AWS profile (leave empty to keep current)" className={inputCls} />
        </>
      ) : (
        <input aria-label="Endpoint" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="Endpoint (leave empty to keep current)" className={inputCls} />
      )}
      <input aria-label="Default model" value={model} onChange={(e) => setModel(e.target.value)} placeholder="Default model (optional)" className={inputCls} />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {error && <span className="text-[0.78rem]" style={{ color: 'var(--color-danger)' }}>{error}</span>}
      </div>
    </div>
  )
}
