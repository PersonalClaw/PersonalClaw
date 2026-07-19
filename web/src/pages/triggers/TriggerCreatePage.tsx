import { useMemo, useState } from 'react'
import { ArrowLeft, Check, Zap, Settings2 } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { api, type ActionProvider } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { Field, TextInput, Segmented } from '../tasks/formControls'
import { Combobox } from '../../ui/Combobox'
import { ScheduleForm, emptyDraft as emptySchedule, type ScheduleDraft } from '../schedule/ScheduleForm'
import { intervalToSecs } from '../schedule/scheduleMeta'
import { ActionConfig, seedActionConfig } from './ActionConfig'
import { schemaProps } from '../tools/schema'
import {
  TRIGGER_KINDS, type TriggerKind, useTriggerVariables, lifecycleEventMeta, eventTakesToolMatcher,
} from './triggerMeta'

/** Create flow for a Trigger, with a CLEAN split between the Trigger mechanism
 *  and the Action:
 *    • Section 1 — TRIGGER: pick the type (schedule | lifecycle) and configure
 *      the mechanism (schedule WHEN+delivery, or lifecycle event+matcher).
 *    • Section 2 — ACTION: the SAME action picker + schema-driven config for any
 *      trigger; only the $variables offered differ, derived from the trigger.
 *  Both kinds POST to the unified /api/triggers facade (any action on any kind). */
export function TriggerCreatePage({ onBack, onCreated }: { onBack: () => void; onCreated: () => void }) {
  const [kind, setKind] = useState<TriggerKind>('schedule')
  // Shared with TriggersListPage under the same key, so the action-provider
  // dropdown is instant on reopen. persist:true — providers rarely change.
  const { data: providers = [] } = useCachedData('triggers:action-providers', () => api.actionProviders().catch(() => [] as ActionProvider[]), { persist: true })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  // shared across both trigger types
  const [name, setName] = useState('')
  const [provider, setProvider] = useState('')
  const [config, setConfig] = useState<Record<string, unknown>>({})

  // schedule trigger mechanism (WHEN + delivery only — action lives in the shared section)
  const [sched, setSched] = useState<ScheduleDraft>(emptySchedule)
  // lifecycle trigger mechanism
  const [event, setEvent] = useState('UserPromptSubmit')
  const [matcher, setMatcher] = useState('')

  const catalog = useTriggerVariables()

  const em = lifecycleEventMeta(catalog, event)
  const eventOptions = useMemo(() => (catalog?.lifecycle ?? []).map((e) => ({ value: e.event, label: e.label, description: e.desc })), [catalog])
  // The variables available to the ACTION depend on the configured TRIGGER.
  const actionVars = kind === 'schedule' ? (catalog?.schedule ?? []) : em.vars

  function pickProvider(p: string) {
    setProvider(p)
    setConfig(seedActionConfig(providers.find((x) => x.name === p)))
  }

  // Gate submit on the selected action's REQUIRED schema fields too — not just
  // name+provider. Without this, picking e.g. Bash Command (whose schema requires
  // `command`) enabled Create with an empty command, so the button looked ready
  // but the backend rejected the submit. Every required prop must have a
  // non-empty value in `config`.
  const requiredConfigMet = useMemo(() => {
    if (!provider) return false
    const sel = providers.find((p) => p.name === provider)
    const { required } = schemaProps(sel?.settingsSchema)
    for (const key of required) {
      const v = config[key]
      if (v === undefined || v === null || (typeof v === 'string' && v.trim() === '')) return false
    }
    return true
  }, [provider, providers, config])

  const canSave = !!name.trim() && !!provider && requiredConfigMet

  async function create() {
    if (!canSave) { setErr('Fill in the trigger name, action, and any required action fields'); return }
    setSaving(true); setErr('')
    try {
      if (kind === 'schedule') {
        const body: Record<string, unknown> = {
          name: name.trim(),
          timezone: sched.timezone || '', silent: sched.silent, strict_schedule: sched.strict_schedule,
          approval_mode: sched.approval_mode || '', channel: sched.channel.trim(), skip_dates: sched.skip_dates,
        }
        if (sched.kind === 'cron') body.cron = sched.cron.trim()
        else if (sched.kind === 'every') body.every = intervalToSecs(sched.intervalValue, sched.intervalUnit)
        else if (sched.kind === 'at') body.at = sched.at
        // The unified facade derives exec fields from the canonical action.
        body.action = { provider, config }
        await api.createSchedule(body)
      } else {
        await api.createHook({ name: name.trim(), event, matcher: matcher.trim(), provider, provider_config: config })
      }
      onCreated()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Create failed') } finally { setSaving(false) }
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><span data-type="title-l" className="text-on-surface">New trigger</span></div>} />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-l pb-2xl flex flex-col gap-xl" style={{ maxWidth: 'var(--content-width)' }}>
          <Field label="Name" hint="A short label for this trigger."><TextInput value={name} onChange={setName} placeholder="Morning briefing" autoFocus /></Field>

          {/* ── SECTION 1 · TRIGGER ── */}
          <SectionHeader icon={Zap} title="Trigger" subtitle="When this fires" />
          <Field label="Trigger type" hint={TRIGGER_KINDS.find((k) => k.key === kind)?.hint}>
            <Segmented options={TRIGGER_KINDS.map((k) => ({ key: k.key, label: k.label, tone: k.tone, icon: k.icon }))} value={kind} onChange={(v) => setKind(v as TriggerKind)} />
          </Field>
          {kind === 'schedule' ? (
            <ScheduleForm draft={sched} onChange={setSched} triggerOnly />
          ) : (
            <>
              <Field label="Fires on" hint={em.desc}>
                <Combobox options={eventOptions} value={event} onChange={(v) => { setEvent(v); setMatcher('') }} placeholder="Pick a lifecycle event…" emptyText="No events" />
              </Field>
              <Field label={eventTakesToolMatcher(event) ? 'Tool matcher' : 'Context matcher'} hint={eventTakesToolMatcher(event) ? 'Glob on tool name (e.g. write_file, mcp__*). Empty = all tools.' : 'Glob on the event context. Empty = always.'}>
                <TextInput value={matcher} onChange={setMatcher} placeholder={eventTakesToolMatcher(event) ? 'write_file' : '*'} />
              </Field>
            </>
          )}

          {/* ── SECTION 2 · ACTION (identical for any trigger; only $variables differ) ── */}
          <SectionHeader icon={Settings2} title="Action" subtitle="What runs when it fires" />
          <ActionConfig providers={providers} provider={provider} config={config} onProvider={pickProvider} onConfig={setConfig} vars={actionVars} />

          {err && <p className="text-danger text-[0.8125rem]">{err}</p>}
        </div>
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <Button variant="ghost" onClick={onBack}>Cancel</Button>
          <Button onClick={create} disabled={saving || !canSave}><Check size={16} /> {saving ? 'Creating…' : 'Create trigger'}</Button>
        </div>
      </div>
    </div>
  )
}

function SectionHeader({ icon: Icon, title, subtitle }: { icon: typeof Zap; title: string; subtitle: string }) {
  return (
    <div className="flex items-center gap-s border-b border-outline-variant/40 pb-2">
      <Icon size={16} className="text-primary" />
      <span className="text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 600' }}>{title}</span>
      <span className="text-on-surface-low text-[0.8125rem]">· {subtitle}</span>
    </div>
  )
}
