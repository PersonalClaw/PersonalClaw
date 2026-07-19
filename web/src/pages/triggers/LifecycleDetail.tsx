import { useEffect, useState } from 'react'
import { Pencil, Trash2, Check, X, FlaskConical, Loader2 } from 'lucide-react'
import { Button } from '../../ui/Button'
import { confirmDelete } from '../../ui/dialog'
import { api, type HookItem, type ActionProvider } from '../../lib/api'
import { Field, TextInput } from '../tasks/formControls'
import { Combobox } from '../../ui/Combobox'
import { Toggle } from '../../ui/Toggle'
import { ActionConfig, seedActionConfig } from './ActionConfig'
import { useTriggerVariables, lifecycleEventMeta, eventTakesToolMatcher, relPast } from './triggerMeta'

/** Lifecycle-trigger inspector for the SidePanel: view ↔ in-panel edit, plus a
 *  Test button that fires the action with a sample context. Backed by the hooks
 *  API until /api/triggers unifies. */
export function LifecycleDetail({ hook, providers, onSaved, onDeleted, editing, onEditingChange }: {
  hook: HookItem
  providers: ActionProvider[]
  onSaved: () => void
  onDeleted: () => void
  editing: boolean
  onEditingChange: (v: boolean) => void
}) {
  // Edit mode is owned by the URL (?edit=1), threaded in fully controlled.
  const setEditing = onEditingChange
  const [name, setName] = useState(hook.name)
  const [event, setEvent] = useState(hook.event)
  const [matcher, setMatcher] = useState(hook.matcher)
  const [provider, setProvider] = useState(hook.provider)
  const [config, setConfig] = useState<Record<string, unknown>>(hook.provider_config ?? {})
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [testOut, setTestOut] = useState<string | null>(null)

  useEffect(() => {
    setName(hook.name); setEvent(hook.event); setMatcher(hook.matcher)
    setProvider(hook.provider); setConfig(hook.provider_config ?? {})
    setTestOut(null)
  }, [hook.id])

  const catalog = useTriggerVariables()
  const em = lifecycleEventMeta(catalog, event)
  const eventOptions = (catalog?.lifecycle ?? []).map((e) => ({ value: e.event, label: e.label, description: e.desc }))

  async function save() {
    if (!name.trim()) { setErr('Name is required'); return }
    setSaving(true); setErr('')
    try { await api.updateHook(hook.id, { name: name.trim(), event, matcher: matcher.trim(), provider, provider_config: config }); onSaved(); setEditing(false) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (!(await confirmDelete('trigger', hook.name))) return
    try { await api.deleteHook(hook.id); onDeleted() } catch { setErr('Delete failed') }
  }
  async function toggle() { setBusy(true); try { await api.toggleHook(hook.id); onSaved() } finally { setBusy(false) } }
  async function test() {
    setBusy(true); setTestOut(null)
    try {
      const r = await api.testHook(hook.id)
      const out = r.result.stdout || r.result.error || r.result.stderr || `exit ${r.result.exit_code}`
      setTestOut(`${out} · ${r.result.duration_ms}ms`)
    } catch (e) { setTestOut(e instanceof Error ? e.message : 'Test failed') } finally { setBusy(false) }
  }

  function pickProvider(p: string) { setProvider(p); setConfig(seedActionConfig(providers.find((x) => x.name === p))) }

  if (editing) {
    return (
      <div className="flex flex-col gap-l">
        <Field label="Name"><TextInput value={name} onChange={setName} placeholder="Block risky writes" autoFocus /></Field>
        <Field label="Fires on" hint={em.desc}>
          <Combobox options={eventOptions} value={event} onChange={(v) => setEvent(v)} placeholder="Pick a lifecycle event…" emptyText="No events" />
        </Field>
        <Field label={eventTakesToolMatcher(event) ? 'Tool matcher' : 'Context matcher'} hint={eventTakesToolMatcher(event) ? 'Glob on tool name. Empty = all tools.' : 'Glob on the event context. Empty = always.'}>
          <TextInput value={matcher} onChange={setMatcher} placeholder={eventTakesToolMatcher(event) ? 'write_file' : '*'} />
        </Field>
        <ActionConfig providers={providers} provider={provider} config={config} onProvider={pickProvider} onConfig={setConfig} vars={em.vars} />
        {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
        <div className="sticky bottom-0 -mx-l px-l py-3 bg-surface/95 border-t border-outline-variant/40 flex justify-end gap-s">
          <Button variant="ghost" size="sm" onClick={() => { setEditing(false); setErr('') }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} disabled={saving || !name.trim()}><Check size={15} /> {saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap items-center gap-s">
        <Button size="sm" variant="secondary" onClick={test} disabled={busy}>{busy ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />} Test</Button>
        <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</Button>
        <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
        <label className="ml-auto inline-flex items-center gap-2 text-[0.8125rem] cursor-pointer">
          <span className="text-on-surface-var">{hook.enabled ? 'Enabled' : 'Disabled'}</span>
          <Toggle on={hook.enabled} onChange={() => toggle()} disabled={busy} label="Toggle enabled" size="sm" />
        </label>
      </div>
      {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
      {testOut && <p className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] break-words">{testOut}</p>}

      <div className="flex flex-wrap items-center gap-s">
        <span className="inline-flex items-center rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', color: 'var(--color-primary)' }}>{em.label}</span>
        <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center text-on-surface-var text-[0.8125rem]">{hook.provider}</span>
        {hook.matcher && <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center font-mono text-on-surface-var text-[0.75rem]">{hook.matcher}</span>}
      </div>

      <Section label="Action config">
        {Object.keys(hook.provider_config ?? {}).length > 0
          ? <pre className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.75rem] font-mono overflow-x-auto whitespace-pre-wrap break-words">{JSON.stringify(hook.provider_config, null, 2)}</pre>
          : <p className="text-on-surface-low text-[0.8125rem]">No configuration.</p>}
      </Section>

      <Section label="Stats">
        <span className="text-on-surface-var text-[0.875rem]">Ran {hook.run_count}× · last {relPast(hook.last_run)}{hook.last_status ? ` · ${hook.last_status}` : ''} · timeout {hook.timeout}s</span>
      </Section>

      <Section label="Used by">
        {hook.used_by.length > 0
          ? <div className="flex flex-wrap gap-1.5">{hook.used_by.map((a) => <span key={a} className="rounded-pill bg-surface-high px-m h-6 inline-flex items-center text-on-surface-var text-[0.75rem]">{a}</span>)}</div>
          : <p className="text-on-surface-low text-[0.8125rem]">No agents reference this trigger yet — it's dormant until an agent's <span className="font-mono">triggers</span> list includes it.</p>}
      </Section>
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
