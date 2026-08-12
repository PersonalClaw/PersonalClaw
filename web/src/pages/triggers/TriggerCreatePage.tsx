import { useMemo, useState, useEffect, useRef } from 'react'
import { fvs } from '../../design/fontWeight'
import { ArrowLeft, Check, Zap, Settings2, AlertTriangle } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { api, type ActionProvider, type EventPattern } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { Field, TextInput, Segmented } from '../../ui/forms'
import { Combobox } from '../../ui/Combobox'
import { ScheduleForm, emptyDraft as emptySchedule, type ScheduleDraft } from '../schedule/ScheduleForm'
import { intervalToSecs } from '../schedule/scheduleMeta'
import { ActionConfig, seedActionConfig } from './ActionConfig'
import { schemaProps } from '../tools/schema'
import {
  TRIGGER_KINDS, type TriggerKind, useTriggerVariables, lifecycleEventMeta, eventTakesToolMatcher,
  eventDormancyReason, eventIsDormant, EVENT_PATTERN_META, eventPatternMeta, eventSourceIcon,
  eventSourceLabel, appEventOptions, actionIsSendCapable,
} from './triggerMeta'

/** Create flow for a Trigger, with a CLEAN split between the Trigger mechanism
 *  and the Action:
 *    • Section 1 — TRIGGER: pick the type (schedule | lifecycle | event) and
 *      configure the mechanism (schedule WHEN+delivery, lifecycle event+matcher,
 *      or a data-event pattern + its one matcher field).
 *    • Section 2 — ACTION: the SAME action picker + schema-driven config for any
 *      trigger; only the $variables offered differ, derived from the trigger.
 *  Every kind POSTs to the unified /api/triggers facade (any action on any kind).
 *  The chosen kind + the event pattern live in the URL (`?kind`/`?pattern`) so the
 *  create flow is deep-linkable and back/forward-safe. */
export function TriggerCreatePage({ onBack, onCreated, query, setQuery }: {
  onBack: () => void; onCreated: () => void
} & Pick<RouteProps, 'query' | 'setQuery'>) {
  const [kindRaw, setKindRaw] = useQueryParam(query, setQuery, 'kind', 'schedule', { replace: true })
  const kind = (TRIGGER_KINDS.some((k) => k.key === kindRaw) ? kindRaw : 'schedule') as TriggerKind
  const setKind = (k: TriggerKind) => setKindRaw(k)
  // Shared with TriggersListPage under the same key, so the action-provider
  // dropdown is instant on reopen. persist:true — providers rarely change.
  const { data: providers = [] } = useCachedData('triggers:action-providers', () => api.actionProviders().catch(() => [] as ActionProvider[]), { persist: true })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  // A failed create used to render its message at the BOTTOM OF THE SCROLLING BODY while the Create
  // button lives in a sticky footer. Measured on `#/tasks/new` at 1440x900: the button sat at y=848 and
  // the message at y=1744 — 844px BELOW the fold — with `role` null and no live region, so clicking
  // Create produced no observable effect at all. The role announces it; the ref scrolls it into view,
  // using the `scrollIntoView({ block: 'nearest' })` idiom this app already uses in 13 places.
  const errRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => { if (err) errRef.current?.scrollIntoView({ block: 'nearest' }) }, [err])

  // shared across both trigger types
  const [name, setName] = useState('')
  const [provider, setProvider] = useState('')
  const [config, setConfig] = useState<Record<string, unknown>>({})

  // schedule trigger mechanism (WHEN + delivery only — action lives in the shared section)
  const [sched, setSched] = useState<ScheduleDraft>(emptySchedule)
  // lifecycle trigger mechanism
  const [event, setEvent] = useState('UserPromptSubmit')
  const [matcher, setMatcher] = useState('')
  // data-event trigger mechanism: the pattern (URL-backed) + its one matcher field.
  const [patternRaw, setPatternRaw] = useQueryParam(query, setQuery, 'pattern', 'InboxMessage', { replace: true })
  const pattern = (EVENT_PATTERN_META.some((p) => p.pattern === patternRaw) ? patternRaw : 'InboxMessage') as EventPattern
  const [eventMatcher, setEventMatcher] = useState('')
  const pm = eventPatternMeta(pattern)
  const SourceIcon = eventSourceIcon(pm.source)

  const catalog = useTriggerVariables()

  const em = lifecycleEventMeta(catalog, event)
  const dormancyReason = eventIsDormant(catalog, event) ? (eventDormancyReason(catalog, event) || 'no code fires it yet') : ''
  // The option list marks dormant events inline too, so the warning is not the FIRST time a user
  // learns which events are dead — they can see it while choosing rather than after committing.
  const eventOptions = useMemo(() => (catalog?.lifecycle ?? []).map((e) => ({
    value: e.event,
    label: e.dormant ? `${e.label} · never fires` : e.label,
    description: e.desc,
  })), [catalog])
  const patternOptions = useMemo(() => EVENT_PATTERN_META.map((p) => ({
    value: p.pattern, label: p.label, description: p.desc,
  })), [])
  // AppEvent's matcher is a PICKER over the live app-source vocabulary (AUTO-A4), not free text:
  // the namespaced name (`app:<app>:<event>`) is core's to derive, so typing it by hand is how a
  // trigger ends up bound to an event that will never fire. Falls back to the plain glob input when
  // no app contributes a source — an empty picker with no fallback would be a dead end.
  const appEvents = useMemo(() => appEventOptions(catalog), [catalog])
  // The variables available to the ACTION depend on the configured TRIGGER.
  const actionVars = kind === 'schedule' ? (catalog?.schedule ?? []) : kind === 'lifecycle' ? em.vars : []
  // Draft-by-default surfacing (EIAT-5): a send-capable action delivers OUT to a channel, so an
  // inbox trigger that auto-replies is worth flagging before the user commits. Keyed to the
  // provider, not to a per-provider capability flag (none exists in core yet — see EIAT-3).
  const sendCapable = actionIsSendCapable(provider)

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

  // A data-event pattern whose matcher is REQUIRED (InboxSender) cannot save empty — the backend
  // rejects it with `sender_glob_required`, so gate it here and point at the field rather than
  // round-tripping to learn the same thing.
  const eventMatcherMet = kind !== 'event' || !pm.matcherRequired || !!eventMatcher.trim()
  const canSave = !!name.trim() && !!provider && requiredConfigMet && eventMatcherMet

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
      } else if (kind === 'lifecycle') {
        await api.createHook({ name: name.trim(), event, matcher: matcher.trim(), provider, provider_config: config })
      } else {
        // Data event — carry only the pattern's ONE wired matcher field; the backend derives the
        // source from the pattern. An empty matcher is fine except where matcherRequired gated it.
        const body: Parameters<typeof api.createEvent>[0] = {
          name: name.trim(), pattern, action: { provider, config },
        }
        if (pm.matcher) body[pm.matcher] = eventMatcher.trim()
        await api.createEvent(body)
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
          ) : kind === 'lifecycle' ? (
            <>
              <Field label="Fires on" hint={em.desc}>
                <Combobox options={eventOptions} value={event} onChange={(v) => { setEvent(v); setMatcher('') }} placeholder="Pick a lifecycle event…" emptyText="No events" />
                {/* S67: 7 of the 15 declared events have no fire site. The API accepts the hook and
                    the list shows it enabled, so without this the only feedback is a trigger that
                    never runs. Warned at the point of CHOICE, where picking a live event is still
                    one click away — a badge on the saved row would come after the mistake. Warn
                    tone, not danger: the selection is valid, it just will not fire yet. */}
                {dormancyReason && (
                  <div role="note" className="mt-2 flex items-start gap-2 rounded-lg px-3 py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span className="min-w-0 flex-1">
                      Nothing fires <span style={fvs(600)}>{em.label}</span> yet — {dormancyReason}. This trigger will save and stay idle.
                    </span>
                  </div>
                )}
              </Field>
              <Field label={eventTakesToolMatcher(event) ? 'Tool matcher' : 'Context matcher'} hint={eventTakesToolMatcher(event) ? 'Glob on tool name (e.g. write_file, mcp__*). Empty = all tools.' : 'Glob on the event context. Empty = always.'}>
                <TextInput value={matcher} onChange={setMatcher} placeholder={eventTakesToolMatcher(event) ? 'write_file' : '*'} />
              </Field>
            </>
          ) : (
            <>
              {/* Data event: an inbox message or a memory write. The pattern picks BOTH the source
                  (derived, never sent) and the one matcher field the backend reads — so the form
                  shows exactly that field and no inert extras. Resetting the matcher on a pattern
                  change avoids carrying e.g. a sender glob into a memory-key pattern. */}
              <Field label="Fires on" hint={pm.desc}>
                <Combobox options={patternOptions} value={pattern} onChange={(v) => { setPatternRaw(v); setEventMatcher('') }} placeholder="Pick a data event…" emptyText="No patterns" />
                {/* Source badge — the backend DERIVES the source from the pattern (never sent), so this
                    names the origin rather than offering it as a choice: inbox message vs memory write. */}
                <div className="mt-2 inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]">
                  <SourceIcon size={14} className="shrink-0" />
                  <span>Source: <span style={fvs(600)}>{eventSourceLabel(pm.source)}</span></span>
                </div>
              </Field>
              {pm.matcher === 'event_glob' && appEvents.length > 0 ? (
                /* The declared app events, from the live registry. A source whose app is disabled is
                   absent, so the list only ever offers events that can actually fire. */
                <Field label={pm.matcherLabel} hint="Pick a declared app event. Leave empty to fire on every app event.">
                  <Combobox options={appEvents} value={eventMatcher} onChange={setEventMatcher} placeholder="Pick an app event…" emptyText="No app events" />
                </Field>
              ) : pm.matcher === 'event_glob' ? (
                <>
                  <Field label={pm.matcherLabel} hint={pm.matcherHint}>
                    <TextInput value={eventMatcher} onChange={setEventMatcher} placeholder={pm.matcherPlaceholder} mono />
                  </Field>
                  {/* No app contributes a source, so nothing can fire this yet. Warned at the point
                      of CHOICE — the same treatment a dormant lifecycle event gets above, and for
                      the same reason: the trigger saves and stays idle, which is indistinguishable
                      from a working one until the user waits for a fire that never comes. */}
                  <div role="note" className="flex items-start gap-2 rounded-lg px-3 py-2 text-[0.8125rem] -mt-2" style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
                    <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                    <span className="min-w-0 flex-1">
                      No installed app contributes a trigger source yet, so nothing fires app events. This trigger will save and stay idle until you install one.
                    </span>
                  </div>
                </>
              ) : pm.matcher ? (
                <Field label={pm.matcherLabel} hint={pm.matcherHint}>
                  <TextInput value={eventMatcher} onChange={setEventMatcher} placeholder={pm.matcherPlaceholder} mono />
                </Field>
              ) : (
                <p className="text-on-surface-low text-[0.8125rem] -mt-2">
                  Fires on every {pm.source === 'inbox' ? 'accepted inbox message' : 'memory write'} — no matcher to narrow it.
                </p>
              )}
            </>
          )}

          {/* ── SECTION 2 · ACTION (identical for any trigger; only $variables differ) ── */}
          <SectionHeader icon={Settings2} title="Action" subtitle="What runs when it fires" />
          <ActionConfig providers={providers} provider={provider} config={config} onProvider={pickProvider} onConfig={setConfig} vars={actionVars} />
          {/* Draft-by-default reminder (EIAT-5): a send-capable action delivers OUT to a channel. */}
          {sendCapable && (
            <div role="note" className="flex items-start gap-2 rounded-lg px-3 py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-info) 10%, transparent)', color: 'var(--color-info)' }}>
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span className="min-w-0 flex-1">
                This action <span style={fvs(600)}>sends a message</span> when it fires. Sending apps default to <span style={fvs(600)}>draft-by-default</span> — a reply is composed but held until you enable sending in the app's settings.
              </span>
            </div>
          )}

          {err && <p ref={errRef} role="alert" className="text-danger text-[0.8125rem]">{err}</p>}
        </div>
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <Button variant="ghost" onClick={onBack}>Cancel</Button>
          {/* `canSave` ANDs four requirements; the reason names the FIRST one outstanding, in the
              order the form presents them, rather than reciting all four. Omitted while `saving`,
              where the label already reads "Creating…". */}
          <Button onClick={create} disabled={saving || !canSave}
            disabledReason={saving ? undefined
              : !name.trim() ? 'Name the trigger first'
                : !provider ? 'Pick a provider'
                  : !requiredConfigMet ? 'Complete the required settings'
                    : 'Set the event to match'}><Check size={16} /> {saving ? 'Creating…' : 'Create trigger'}</Button>
        </div>
      </div>
    </div>
  )
}

function SectionHeader({ icon: Icon, title, subtitle }: { icon: typeof Zap; title: string; subtitle: string }) {
  return (
    <div className="flex items-center gap-s border-b border-outline-variant/40 pb-2">
      <Icon size={16} className="text-primary" />
      <span className="text-on-surface text-[0.9375rem]" style={fvs(600)}>{title}</span>
      <span className="text-on-surface-low text-[0.8125rem]">· {subtitle}</span>
    </div>
  )
}
