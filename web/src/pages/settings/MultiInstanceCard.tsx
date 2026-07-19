import { useState } from 'react'
import { Plug, Plus, Wifi, Pencil, Trash2, X, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react'
import { api, type SettingsProvider, type ProviderInstance, type ProviderSchema, type ProviderTestResult } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { confirmDelete } from '../../ui/dialog'
import { Button } from '../../ui/Button'
import { Toggle } from './settingsUI'
import { SchemaField, schemaDefaults, inputCls } from './ProviderConfigForm'

/** A multiInstance=true provider rendered as a frame for N named instances.
 *  Each instance has its own schema-driven config (test / edit / delete); an
 *  "Add instance" form creates more. Backed by /api/providers/{name}/instances
 *  + the provider's settingsSchema. Uniform across MCP Tools, OpenAI Tools, and
 *  any future multi-instance bundle. The provider's own enable toggle gates the
 *  whole frame. */
export function MultiInstanceCard({ ext, onChanged }: { ext: SettingsProvider; onChanged: () => void }) {
  const [adding, setAdding] = useState(false)
  const [busyToggle, setBusyToggle] = useState(false)

  // The provider's config schema barely changes — cache + persist it so the form
  // is ready instantly on revisit. Instances are mutable but still cached for an
  // instant paint, then revalidated; an empty list when disabled (no fetch).
  const { data: schema } = useCachedData(
    `settings:provider-schema:${ext.name}`,
    () => api.providerSchema(ext.name).catch(() => ({ properties: {} } as ProviderSchema)),
    { persist: true },
  )
  const { data: instances, refresh: refreshInstances } = useCachedData(
    `settings:provider-instances:${ext.name}:${ext.enabled ? 'on' : 'off'}`,
    () => ext.enabled ? api.providerInstances(ext.name).catch(() => [] as ProviderInstance[]) : Promise.resolve([] as ProviderInstance[]),
    { persist: true },
  )
  const reloadInstances = () => { invalidateCache(`settings:provider-instances:${ext.name}`, true); refreshInstances() }

  const toggle = async () => {
    setBusyToggle(true)
    try { ext.enabled ? await api.disableProvider(ext.name) : await api.enableProvider(ext.name); onChanged() }
    finally { setBusyToggle(false) }
  }

  const count = instances?.length ?? 0
  return (
    <div className="rounded-lg bg-surface-container px-4 py-3" style={{ opacity: busyToggle ? 0.6 : 1 }}>
      <div className="flex items-center gap-3">
        <span className="size-2 shrink-0 rounded-full" style={{ background: ext.enabled ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>{ext.displayName || ext.name}</span>
            {ext.version && <span className="text-on-surface-low text-[0.68rem]">v{ext.version}</span>}
            <span className="rounded-pill px-1.5 py-0.5 text-[0.62rem]" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)', color: 'var(--color-primary)' }}>multi-instance</span>
            {ext.enabled && <span className="text-on-surface-low text-[0.68rem]">{count} {count === 1 ? 'instance' : 'instances'}</span>}
          </div>
          {ext.description && <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]">{ext.description}</p>}
        </div>
        <Toggle on={ext.enabled} onChange={toggle} label={`Toggle ${ext.name}`} />
      </div>
      {ext.error && <div className="mt-2 flex items-center gap-1.5 text-[0.78rem]" style={{ color: 'var(--color-danger)' }}><AlertTriangle size={12} /> {ext.error}</div>}

      {ext.enabled && (
        <div className="mt-3 flex flex-col gap-2 border-t border-outline-variant/30 pt-3">
          {instances === undefined ? (
            <div className="py-1 text-on-surface-low text-[0.78rem]"><Loader2 size={12} className="inline animate-spin" /> Loading instances…</div>
          ) : instances.length === 0 && !adding ? (
            <p className="text-on-surface-low text-[0.8rem]">No instances yet. Add one to start using this provider.</p>
          ) : (
            instances.map((inst) => <InstanceRow key={inst.id} ext={ext} inst={inst} schema={schema} onChanged={reloadInstances} />)
          )}

          {adding
            ? <AddInstanceForm ext={ext} schema={schema} onDone={(created) => { setAdding(false); if (created) reloadInstances() }} />
            : <Button variant="secondary" size="sm" className="self-start" onClick={() => setAdding(true)}><Plus size={15} /> Add instance</Button>}
        </div>
      )}
    </div>
  )
}

function InstanceRow({ ext, inst, schema, onChanged }: {
  ext: SettingsProvider; inst: ProviderInstance; schema: ProviderSchema | null | undefined; onChanged: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [config, setConfig] = useState<Record<string, unknown>>(inst.config)
  const [test, setTest] = useState<ProviderTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)

  const runTest = async () => {
    setTesting(true); setTest(null)
    try { setTest(await api.testProviderInstance(ext.name, inst.id)) }
    catch (e) { setTest({ ok: false, message: e instanceof Error ? e.message : 'Test failed' }) }
    setTesting(false)
  }
  const save = async () => {
    setSaving(true)
    try { await api.updateProviderInstance(ext.name, inst.id, { config }); setEditing(false); onChanged() }
    finally { setSaving(false) }
  }
  const remove = async () => {
    if (!(await confirmDelete('instance', inst.display_name || inst.id))) return
    setBusy(true)
    try { await api.deleteProviderInstance(ext.name, inst.id); onChanged() } catch { setBusy(false) }
  }

  const props = Object.entries(schema?.properties ?? {})
  return (
    <div className="rounded-md bg-surface-high px-3 py-2" style={{ opacity: busy ? 0.5 : 1 }}>
      <div className="flex items-center gap-2">
        <Plug size={14} className="shrink-0 text-on-surface-low" />
        <span className="min-w-0 flex-1 truncate text-on-surface text-[0.84rem]">{inst.display_name || inst.id}</span>
        <div className="flex shrink-0 items-center gap-0.5">
          <IconBtn label="Test" onClick={runTest} active={testing}>{testing ? <Loader2 size={13} className="animate-spin" /> : <Wifi size={13} />}</IconBtn>
          <IconBtn label="Edit" onClick={() => setEditing((v) => !v)} on={editing}>{editing ? <X size={13} /> : <Pencil size={13} />}</IconBtn>
          <IconBtn label="Delete" onClick={remove}><Trash2 size={13} /></IconBtn>
        </div>
      </div>
      {test && (
        <div className="mt-1.5 flex items-center gap-1.5 text-[0.75rem]" style={{ color: test.ok ? 'var(--color-success)' : 'var(--color-danger)' }}>
          {test.ok ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />} {test.message}
        </div>
      )}
      {editing && props.length > 0 && (
        <div className="mt-3 flex flex-col gap-3 border-t border-outline-variant/30 pt-3">
          {props.map(([k, p]) => <SchemaField key={k} fieldKey={k} prop={p} value={config[k]} onChange={(v) => setConfig((c) => ({ ...c, [k]: v }))} />)}
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
            <Button variant="ghost" size="sm" onClick={() => { setEditing(false); setConfig(inst.config) }}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
  )
}

function AddInstanceForm({ ext, schema, onDone }: {
  ext: SettingsProvider; schema: ProviderSchema | null | undefined; onDone: (created: boolean) => void
}) {
  const [name, setName] = useState('')
  const [config, setConfig] = useState<Record<string, unknown>>(() => schemaDefaults(schema))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const props = Object.entries(schema?.properties ?? {})

  const submit = async () => {
    if (!name.trim()) { setError('Instance name is required'); return }
    setSaving(true); setError('')
    try { await api.createProviderInstance(ext.name, { display_name: name.trim(), config: { ...schemaDefaults(schema), ...config } }); onDone(true) }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Failed to create instance'
      try { const p = JSON.parse(msg); msg = (p.error || msg) + (Array.isArray(p.details) && p.details.length ? `: ${p.details.join('; ')}` : '') } catch { /* raw */ }
      setError(msg); setSaving(false)
    }
  }

  return (
    <div className="rounded-md border border-outline-variant/40 bg-surface p-3">
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Instance name (e.g. filesystem-mcp)" className={inputCls + ' mb-2'} />
      {props.length > 0 && (
        <div className="flex flex-col gap-3">
          {props.map(([k, p]) => <SchemaField key={k} fieldKey={k} prop={p} value={config[k]} onChange={(v) => setConfig((c) => ({ ...c, [k]: v }))} />)}
        </div>
      )}
      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" onClick={submit} disabled={saving || !name.trim()}>{saving ? 'Creating…' : 'Create'}</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {error && <span className="text-[0.75rem]" style={{ color: 'var(--color-danger)' }}>{error}</span>}
      </div>
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
