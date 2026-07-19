import { useMemo, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { ScheduleJob, ScheduleKind, ScheduleExecMode } from '../../lib/api'
import { useAgentCatalog, useModelCatalog } from '../../lib/agents'
import { Combobox, type ComboOption } from '../../ui/Combobox'
import { Toggle } from '../../ui/Toggle'
import { Field, TextInput, TextArea, Segmented, ChipInput } from '../tasks/formControls'
import { SoonTag } from '../tasks/taskMeta'
import {
  KINDS, EXEC_MODES, deriveKind, deriveMode, kindMeta, modeMeta,
  secsToInterval, intervalToSecs, INTERVAL_UNITS, CRON_PRESETS,
} from './scheduleMeta'

/** The draft mirrors the create/update payload but keeps the kind/mode axes
 *  explicit (the wire derives them from which fields are set). */
export interface ScheduleDraft {
  id?: string
  name: string
  message: string
  kind: ScheduleKind
  // interval
  intervalValue: number
  intervalUnit: string
  // cron
  cron: string
  // at (one-shot) — local datetime-local string
  at: string
  mode: ScheduleExecMode
  agent: string
  model: string
  script: string
  command: string
  // delivery / context
  channel: string
  silent: boolean
  strict_schedule: boolean
  timezone: string
  approval_mode: string  // '' | 'auto'
  skip_dates: string[]
}

export function emptyDraft(): ScheduleDraft {
  return {
    name: '', message: '', kind: 'every', intervalValue: 1, intervalUnit: 'h', cron: '0 9 * * *', at: '',
    mode: 'agent', agent: '', model: '', script: '', command: '',
    channel: '', silent: false, strict_schedule: false, timezone: '', approval_mode: '', skip_dates: [],
  }
}

export function toDraft(j: ScheduleJob): ScheduleDraft {
  const iv = secsToInterval(j.every_secs)
  return {
    id: j.id, name: j.name ?? '', message: j.message ?? '',
    kind: deriveKind(j), intervalValue: iv.value, intervalUnit: iv.unit,
    cron: j.cron_expr ?? '0 9 * * *', at: '',
    mode: deriveMode(j), agent: j.agent ?? '', model: j.model ?? '',
    script: j.script ?? '', command: j.command ?? '',
    channel: j.channel ?? '', silent: !!j.silent, strict_schedule: !!j.strict_schedule,
    timezone: j.timezone ?? '', approval_mode: j.approval_mode ?? '', skip_dates: j.skip_dates ?? [],
  }
}

/** Build the create/update payload. The backend create handler today accepts
 *  every/cron + agent fields; `at`, `script`, `command` ride along so the
 *  payload is forward-compatible once the backend lands them (gated SoonTag). */
export function draftToPayload(d: ScheduleDraft): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: d.name.trim(),
    message: d.message.trim(),
    timezone: d.timezone || '',
    silent: d.silent,
    strict_schedule: d.strict_schedule,
    approval_mode: d.approval_mode || '',
    channel: d.channel.trim(),
    skip_dates: d.skip_dates,
  }
  if (d.kind === 'cron') body.cron = d.cron.trim()
  else if (d.kind === 'every') body.every = intervalToSecs(d.intervalValue, d.intervalUnit)
  else if (d.kind === 'at') body.at = d.at  // backend-soon
  if (d.mode === 'agent') { body.agent = d.agent; body.model = d.model }
  else if (d.mode === 'script') body.script = d.script.trim()       // backend-soon
  else if (d.mode === 'command') body.command = d.command.trim()    // backend-soon
  return body
}

/** Shared schedule form behind the create PAGE and the in-panel edit. Three
 *  axes: WHEN (interval/cron/one-shot), WHAT (agent prompt / script / command),
 *  and delivery/context (timezone, channel, silent, strict, skip dates).
 *
 *  `triggerOnly` renders just the trigger mechanism — WHEN + delivery — omitting
 *  the name (the Triggers page owns it) and the WHAT/action block (the Triggers
 *  page configures the action separately via the unified ActionConfig). This is
 *  how Schedule appears inside the Triggers page, where Trigger and Action are
 *  cleanly separated. */
export function ScheduleForm({ draft, onChange, compact, triggerOnly }: { draft: ScheduleDraft; onChange: (d: ScheduleDraft) => void; compact?: boolean; triggerOnly?: boolean }) {
  const set = <K extends keyof ScheduleDraft>(k: K, v: ScheduleDraft[K]) => onChange({ ...draft, [k]: v })
  const { options: agentOptions } = useAgentCatalog()
  const { options: modelOptions } = useModelCatalog()
  const km = kindMeta(draft.kind)
  const mm = modeMeta(draft.mode)

  return (
    <div className={`flex flex-col ${compact ? 'gap-l' : 'gap-xl'}`}>
      {!triggerOnly && (
        <Field label="Name" hint="A short label for this scheduled job.">
          <TextInput value={draft.name} onChange={(v) => set('name', v)} placeholder="Morning briefing" autoFocus />
        </Field>
      )}

      {/* ── WHEN ── */}
      <Field label="When" right={km.soon ? <SoonTag /> : undefined} hint={km.hint}>
        <Segmented options={KINDS.map((k) => ({ key: k.key, label: k.label, tone: k.tone, icon: k.icon }))} value={draft.kind} onChange={(v) => set('kind', v as ScheduleKind)} />
      </Field>
      {draft.kind === 'every' && (
        <div className="flex items-center gap-s">
          <input type="number" min={1} value={draft.intervalValue} onChange={(e) => set('intervalValue', Math.max(1, Number(e.target.value) || 1))}
            name="interval-value" aria-label="Run every — interval count"
            className="w-24 h-10 rounded-md bg-surface-container px-m text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          <NativeSelect value={draft.intervalUnit} onChange={(v) => set('intervalUnit', v)} options={INTERVAL_UNITS.map((u) => ({ value: u.key, label: u.label }))} label="Run every — interval unit" name="interval-unit" />
        </div>
      )}
      {draft.kind === 'cron' && <CronField value={draft.cron} onChange={(v) => set('cron', v)} />}
      {draft.kind === 'at' && (
        <input type="datetime-local" value={draft.at} onChange={(e) => set('at', e.target.value)}
          name="run-at" aria-label="Run once at date and time"
          className="h-10 rounded-md bg-surface-container px-m text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]" />
      )}

      {/* ── WHAT (omitted in triggerOnly — action is configured separately) ── */}
      {!triggerOnly && (
        <>
          <Field label="Runs" right={mm.soon ? <SoonTag /> : undefined} hint={mm.hint}>
            <Segmented options={EXEC_MODES.map((m) => ({ key: m.key, label: m.label, tone: m.tone, icon: m.icon }))} value={draft.mode} onChange={(v) => set('mode', v as ScheduleExecMode)} />
          </Field>
          {draft.mode === 'agent' && (
            <>
              <Field label="Prompt" hint="What the agent should do each run.">
                <TextArea value={draft.message} onChange={(v) => set('message', v)} placeholder="Summarize my unread messages and surface anything urgent." rows={compact ? 3 : 4} />
              </Field>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-l">
                <Field label="Agent" hint="Which agent runs it. Defaults to the system default.">
                  <Combobox options={agentOptions} value={draft.agent} onChange={(v) => set('agent', v)} placeholder="Default agent" emptyText="No agents found" />
                </Field>
                <Field label="Model override" hint="Optional — leave on Auto to use the agent's model.">
                  <Combobox options={modelOptions} value={draft.model} onChange={(v) => set('model', v)} placeholder="Auto — agent's model" emptyText="No models found" />
                </Field>
              </div>
            </>
          )}
          {draft.mode === 'script' && (
            <Field label="Script entrypoint" hint="path/to/file.py:func under ~/.personalclaw/crons/ — runs with zero tokens.">
              <TextInput value={draft.script} onChange={(v) => set('script', v)} placeholder="reports/daily.py:run" />
            </Field>
          )}
          {draft.mode === 'command' && (
            <Field label="Shell command" hint="Runs in the sandbox with zero tokens.">
              <TextArea value={draft.command} onChange={(v) => set('command', v)} placeholder="rsync -a ~/data /backup" rows={2} mono />
            </Field>
          )}
        </>
      )}

      {/* ── delivery / context ── */}
      <Advanced draft={draft} set={set} />
    </div>
  )
}

function Advanced({ draft, set }: { draft: ScheduleDraft; set: <K extends keyof ScheduleDraft>(k: K, v: ScheduleDraft[K]) => void }) {
  const [open, setOpen] = useState(false)
  const tzOptions = useMemo<ComboOption[]>(() => {
    let zones: string[] = []
    try { zones = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.('timeZone') ?? [] } catch { /* older runtime */ }
    return zones.map((z) => ({ value: z, label: z }))
  }, [])

  return (
    <div className="rounded-lg bg-surface-container/60">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-s px-m h-11 text-on-surface-var text-[0.8125rem]">
        <ChevronDown size={15} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
        Advanced — delivery, timezone, skip dates
      </button>
      {open && (
        <div className="flex flex-col gap-l px-m pb-l pt-1">
          <Field label="Timezone" hint="Used for cron/skip-date evaluation. Defaults to the server zone.">
            {tzOptions.length > 0
              ? <Combobox options={tzOptions} value={draft.timezone} onChange={(v) => set('timezone', v)} placeholder="Server default" emptyText="No match" />
              : <TextInput value={draft.timezone} onChange={(v) => set('timezone', v)} placeholder="America/Los_Angeles" />}
          </Field>
          <Field label="Notify channel" hint="Optional Slack channel ID to deliver results to.">
            <TextInput value={draft.channel} onChange={(v) => set('channel', v)} placeholder="C0123456789" />
          </Field>
          <Field label="Skip dates" hint="ISO dates (YYYY-MM-DD) to skip — holidays, blackout days.">
            <ChipInput values={draft.skip_dates} onChange={(v) => set('skip_dates', v)} placeholder="2026-12-25, Enter" />
          </Field>
          <div className="flex flex-col gap-s">
            <CheckRow label="Silent" hint="Suppress auto-delivery; the agent decides when to send." checked={draft.silent} onChange={(v) => set('silent', v)} />
            <CheckRow label="Strict schedule" hint="Fire exactly on schedule with no jitter." checked={draft.strict_schedule} onChange={(v) => set('strict_schedule', v)} />
            <CheckRow label="Auto-approve tools" hint="Run tools without approval prompts (approval_mode=auto)." checked={draft.approval_mode === 'auto'} onChange={(v) => set('approval_mode', v ? 'auto' : '')} />
          </div>
        </div>
      )}
    </div>
  )
}

function CheckRow({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-s cursor-pointer py-1">
      <span className="mt-0.5">
        <Toggle on={checked} onChange={onChange} label={label} size="sm" />
      </span>
      <span>
        <span className="block text-on-surface text-[0.875rem]">{label}</span>
        <span className="block text-on-surface-low text-[0.75rem]">{hint}</span>
      </span>
    </label>
  )
}

function NativeSelect({ value, onChange, options, label, name }: { value: string; onChange: (v: string) => void; options: Array<{ value: string; label: string }>; label?: string; name?: string }) {
  return (
    <div className="relative">
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} name={name}
        className="h-10 appearance-none rounded-md bg-surface-container pl-m pr-9 text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <ChevronDown size={15} className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-low pointer-events-none" />
    </div>
  )
}

/** Cron field — text input + live human description + quick presets. */
function CronField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const valid = value.trim().split(/\s+/).length === 5
  return (
    <div className="flex flex-col gap-s">
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder="0 9 * * *"
        name="cron-expression" aria-label="Cron expression (minute hour day-of-month month day-of-week)"
        className={`w-full h-10 rounded-md bg-surface-container px-m font-mono text-on-surface text-[0.875rem] outline-none focus:ring-2 ${valid ? 'focus:ring-primary/40' : 'ring-1 ring-danger/50'}`} />
      <div className="flex flex-wrap gap-1.5">
        {CRON_PRESETS.map((p) => (
          <button key={p.expr} type="button" onClick={() => onChange(p.expr)}
            className={`rounded-pill px-m h-7 text-[0.75rem] transition-colors ${value.trim() === p.expr ? 'bg-primary/20 text-primary' : 'bg-surface-high text-on-surface-var hover:bg-surface-highest'}`}>{p.label}</button>
        ))}
      </div>
      {!valid && <p className="text-danger text-[0.75rem]">Cron needs five fields: minute hour day-of-month month day-of-week.</p>}
    </div>
  )
}
