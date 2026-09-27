import { useEffect, useState } from 'react'
import { Plug, Plus, Wifi, Pencil, Trash2, X, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react'
import { api, type SettingsProvider, type ProviderInstance, type ProviderSchema, type ProviderTestResult } from '../../lib/api'
import { useQuery, invalidateKeys } from '../../lib/data'
import { confirmDelete } from '../../ui/dialog'
import { Button } from '../../ui/Button'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { InlineLoadError } from '../../ui/ListScaffold'
import { Toggle } from './settingsUI'
import { SchemaField, schemaDefaults } from './ProviderConfigForm'
import { TextInput } from '../../ui/forms'
import { fvs } from '../../design/fontWeight'
import { accentChip } from '../../design/accent'
import { reportingWrite } from '../../app/reportingWrite'
import { setActivation } from '../../app/appActivation'
import { HELD_CHANGE_REASON, presentSecrets, rebaseRecord, type Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { HeldChange, StaleWriteNotice } from '../../ui/StaleWriteNotice'

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
  const { data: schema } = useQuery(
    `settings:provider-schema:${ext.name}`,
    () => api.providerSchema(ext.name).catch(() => ({ properties: {} } as ProviderSchema)),
    { persist: true },
  )
  // 🔴 THE INSTANCE LIST IS NOT THE SCHEMA READ, and that is why only one of this file's two
  // fallbacks is deliberate (#532). The schema's `{ properties: {} }` renders NOTHING — every
  // caller turns it into `props.length === 0` → no form — so it claims nothing. This read backs
  // a SENTENCE: `[]` printed "No instances yet. Add one to start using this provider." over a
  // provider with five configured MCP servers, and the header chip said "0 instances" beside it.
  // An unreadable list is not an empty list, and only one of those two may be said out loud.
  //
  // The `Promise.resolve([])` on the disabled branch is not a swallow — a disabled provider has no
  // instances to fetch and nothing rejected.
  const { data: instances, error: instancesErr, refresh: refreshInstances } = useQuery(
    `settings:provider-instances:${ext.name}:${ext.enabled ? 'on' : 'off'}`,
    () => ext.enabled ? api.providerInstances(ext.name) : Promise.resolve([] as ProviderInstance[]),
    { persist: true },
  )
  const reloadInstances = () => { invalidateKeys(`settings:provider-instances:${ext.name}`, true); refreshInstances() }

  // The switch is its app's, and reported and re-read either way — see `ProviderCard`'s toggle: a
  // refused enable leaves the card off with the server's sentence under it, and the toast says it
  // at the moment it happens.
  const toggle = async () => {
    setBusyToggle(true)
    try {
      await reportingWrite(`turn ${ext.displayName || ext.name} ${ext.enabled ? 'off' : 'on'}`, () => setActivation(ext))
      onChanged()
    } finally { setBusyToggle(false) }
  }

  const count = instances?.length ?? 0
  return (
    <div className="rounded-lg bg-surface-container px-4 py-3" style={{ opacity: busyToggle ? 0.6 : 1 }}>
      <div className="flex items-center gap-3">
        <span className="size-2 shrink-0 rounded-full" style={{ background: ext.enabled ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span data-type="title-m" className="truncate text-on-surface" style={fvs(500)}>{ext.displayName || ext.name}</span>
            {ext.version && <span data-type="caption" className="text-on-surface-low">v{ext.version}</span>}
            <span data-type="caption" className="rounded-pill px-1.5 py-0.5" style={accentChip}>multi-instance</span>
            {/* Gated on `instances`, not just on `enabled`: "0 instances" is a count, and a count
                is the one thing a failed read does not have. */}
            {ext.enabled && instances && <span data-type="caption" className="text-on-surface-low">{count} {count === 1 ? 'instance' : 'instances'}</span>}
          </div>
          {ext.description && <p data-type="body-s" className="mt-0.5 truncate text-on-surface-low">{ext.description}</p>}
        </div>
        <Toggle on={ext.enabled} onChange={toggle} label={`Toggle ${ext.name}`} />
      </div>
      {ext.error && <div data-type="caption" className="mt-2 flex items-center gap-1.5" style={{ color: 'var(--color-danger)' }}><AlertTriangle size={12} /> {ext.error}</div>}

      {ext.enabled && (
        <div className="mt-3 flex flex-col gap-2 border-t border-outline-variant/30 pt-3">
          {instancesErr && !instances ? (
            // One line, not a centred alert block: this sits inside a provider card in a stack of
            // them, which is the shape `InlineLoadError` exists for. Add-instance stays offered
            // below — a failed list read is no reason to withhold the action that still works.
            <InlineLoadError what="this provider's instances" error={instancesErr} onRetry={reloadInstances} />
          ) : instances === undefined ? (
            <div data-type="caption" className="py-1 text-on-surface-low"><Loader2 size={12} className="inline animate-spin" /> Loading instances…</div>
          ) : instances.length === 0 && !adding ? (
            <p data-type="body-s" className="text-on-surface-low">No instances yet. Add one to start using this provider.</p>
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

/** The instance's config with its already-stored secrets blanked for editing.
 *
 *  A sensitive field arrives MASKED (write-only over the API), and a row of bullets is both
 *  indistinguishable from a real value and nonsense to edit. Blank it instead: the field then
 *  says "saved — leave blank to keep", and a blank submit is what the PUT reads as "keep the
 *  stored secret". Without this the editor would PUT the mask back over a working API key the
 *  first time someone changed a model id — the same treatment `ProviderConfigForm` gives the
 *  single-config form, applied to each instance's own `_secret_set`. */
export function editableConfig(inst: ProviderInstance): Record<string, unknown> {
  const next = { ...inst.config }
  for (const k of inst._secret_set ?? []) next[k] = ''
  return next
}

/** The instance as its editor starts from — the editable config (`editableConfig`) with the
 *  revision the list read reported for it, which a save names. */
function editableInstance(inst: ProviderInstance): Revisioned<Record<string, unknown>> {
  return { value: editableConfig(inst), revision: inst.revision }
}

function InstanceRow({ ext, inst, schema, onChanged }: {
  ext: SettingsProvider; inst: ProviderInstance; schema: ProviderSchema | null | undefined; onChanged: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [config, setConfig] = useState<Record<string, unknown>>(() => editableConfig(inst))
  const secretSet = inst._secret_set ?? []
  const [test, setTest] = useState<ProviderTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  // A closed editor always opens on the instance as it is stored now, not on the copy it had.
  useEffect(() => { if (!editing) setConfig(editableConfig(inst)) }, [inst, editing])
  // 🔴 THE CONFIG IS SAVED WHOLE, over the revision the list read it at. The editor used to PUT its
  // copy back — so a card opened before another tab saved this instance replaced that save. A stale
  // copy is now refused and the edit re-applied field by field on top of what is stored
  // (`ui/StaleWriteNotice`). The re-read uses the same editable form, secrets blanked, so a secret
  // left alone compares as unchanged rather than as a conflict with its mask.
  const guard = useStaleWriteGuard<Record<string, unknown>>({
    read: () => api.providerInstances(ext.name).then((all) => {
      const now = all.find((i) => i.id === inst.id)
      if (!now) throw new Error('This instance was removed elsewhere.')
      return editableInstance(now)
    }),
    write: (next, base) => api.updateProviderInstance(ext.name, inst.id, next, base),
    onSaved: () => { setEditing(false); onChanged() },
    onDiscard: () => { setEditing(false); onChanged() },
  })

  const runTest = async () => {
    setTesting(true); setTest(null)
    try { setTest(await api.testProviderInstance(ext.name, inst.id)) }
    catch (e) { setTest({ ok: false, message: e instanceof Error ? e.message : 'Test failed' }) }
    setTesting(false)
  }
  const save = async () => {
    setSaving(true)
    // 🪤 This had NO catch at all, so a failed save rejected unhandled — same user outcome as the
    // empty catch below (silence), by a different mechanism. Leaving the editor OPEN on failure is
    // deliberate: the config the user typed is still on screen to retry from.
    try {
      // Only a failure is reported here: a landed save closes the editor (`onSaved`), and a stale
      // copy keeps it open with the notice below holding the edit.
      const base = editableInstance(inst)
      await reportingWrite('save this instance', () => guard.save(base, config, rebaseRecord(base.value, config)))
    } finally { setSaving(false) }
  }
  const remove = async () => {
    if (!(await confirmDelete('instance', inst.display_name || inst.id))) return
    setBusy(true)
    if (!(await reportingWrite(`remove ${inst.display_name || inst.id}`, () => api.deleteProviderInstance(ext.name, inst.id)))) {
      setBusy(false)
      return
    }
    onChanged()
  }

  const props = Object.entries(schema?.properties ?? {})
  return (
    <div className="rounded-md bg-surface-high px-3 py-2" style={{ opacity: busy ? 0.5 : 1 }}>
      <div className="flex items-center gap-2">
        <Plug size={14} className="shrink-0 text-on-surface-low" />
        <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">{inst.display_name || inst.id}</span>
        <div className="flex shrink-0 items-center gap-0.5">
          {/* `loading`, not `disabled` + a hand-rolled glyph swap — the primitive owns the spinner. */}
          <SquareIconButton label="Test" onClick={runTest} loading={testing} iconSize={13}><Wifi size={13} /></SquareIconButton>
          {/* Bound to the SAME expression that gates the form below (`editing && props.length > 0`),
              not merely to `editing`: with an empty schema this button reveals nothing, and
              `aria-expanded="true"` there would be a promise the card does not keep. */}
          <SquareIconButton label="Edit" onClick={() => setEditing((v) => !v)}
            ariaExpanded={editing && props.length > 0}>{editing ? <X size={13} /> : <Pencil size={13} />}</SquareIconButton>
          <SquareIconButton label="Delete" onClick={remove}><Trash2 size={13} /></SquareIconButton>
        </div>
      </div>
      {test && (
        <div data-type="caption" className="mt-1.5 flex items-center gap-1.5" style={{ color: test.ok ? 'var(--color-success)' : 'var(--color-danger)' }}>
          {test.ok ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />} {test.message}
        </div>
      )}
      {editing && props.length > 0 && (
        <div className="mt-3 flex flex-col gap-3 border-t border-outline-variant/30 pt-3">
          <HeldChange guard={guard}>
            {props.map(([k, p]) => <SchemaField key={k} fieldKey={k} prop={p} value={config[k]}
              secretAlreadySet={secretSet.includes(k)}
              onChange={(v) => setConfig((c) => ({ ...c, [k]: v }))} />)}
          </HeldChange>
          <StaleWriteNotice guard={guard} what="This instance's settings"
            present={presentSecrets((k) => !!schema?.properties?.[k]?.['x-meta']?.sensitive, secretSet)} />
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={save} loading={saving} disabled={guard.conflict !== null}
              disabledReason={guard.conflict !== null ? HELD_CHANGE_REASON : undefined}>Save</Button>
            {/* Cancelling with a refused edit waiting drops that edit too, rather than leaving it to
                reappear the next time the editor opens. */}
            <Button variant="ghost" size="sm"
              onClick={() => { if (guard.conflict) guard.discard(); else setEditing(false) }}>Cancel</Button>
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
      <div className="mb-2">
        <TextInput ariaLabel="Instance name" value={name} onChange={setName} placeholder="Instance name (e.g. filesystem-mcp)" size="md" surface="high" />
      </div>
      {props.length > 0 && (
        <div className="flex flex-col gap-3">
          {props.map(([k, p]) => <SchemaField key={k} fieldKey={k} prop={p} value={config[k]} onChange={(v) => setConfig((c) => ({ ...c, [k]: v }))} />)}
        </div>
      )}
      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" onClick={submit} loading={saving} disabled={saving || !name.trim()}
          disabledReason={!name.trim() ? 'Enter a name first' : undefined}>Create</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {error && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{error}</span>}
      </div>
    </div>
  )
}
