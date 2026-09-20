import { useMemo, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { ScheduleJob, ScheduleKind, ScheduleExecMode } from '../../lib/api'
import { useAgentCatalog, useModelCatalog } from '../../lib/agents'
import { Combobox, type ComboOption } from '../../ui/Combobox'
import { Toggle } from '../../ui/Toggle'
import { Field, TextInput, TextArea, Segmented, ChipInput } from '../../ui/forms'
import { SoonTag } from '../tasks/taskMeta'
import { epochSeconds } from '../../lib/epoch'
import {
  KINDS, EXEC_MODES, deriveKind, deriveMode, kindMeta, modeMeta,
  secsToInterval, intervalToSecs, INTERVAL_UNITS, MIN_INTERVAL_SECS, CRON_PRESETS,
} from './scheduleMeta'
import { cronExprInvalidReason } from './cronExpr'

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
  /** The cadence this draft OPENED on, in seconds — `undefined` for a brand-new schedule.
   *
   *  🔴 Carried because `secsToInterval`/`intervalToSecs` are not invertible and the edit path
   *  re-derived `every` unconditionally, so an edit that never touched the cadence still wrote a
   *  DIFFERENT one (5s → 60s, 3601s → 3600s — #531). Keeping the original lets `draftToPayload`
   *  answer the only question that matters: did the user actually change this? A round-trip that
   *  lands on the same displayed `{value, unit}` means no, and then the original seconds are what
   *  ship — not the lossy re-derivation of them.
   *
   *  Deliberately NOT a fix to the two converters. Making them invertible is a larger change and
   *  would still leave an untouched field being rewritten on every save, which is the actual
   *  defect and the same one #689 (a rename destroyed the action) and #268 (`approval_mode`
   *  discarded) were: this form sending something it was not asked to change. */
  everySecsOriginal?: number
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
  // failure routing (WF2AUT-15) — a SEPARATE route for failures, so an automation the user asked to
  // stay quiet can still say it broke. '' inherits the result route (`delivery.route_for`'s
  // fall-back branch); see FAILURE_ROUTES.
  failure_delivery: string  // '' | 'inbox' | 'none'
  failure_dedupe: boolean   // → `failure_policy.dedupe_hash`
}

/** The failure routes `delivery.route_for` can actually honour.
 *
 *  🔴 WHY THESE THREE AND NO MORE. `route_for(trigger, ok=False)` reads `failure_delivery` and falls
 *  back to `delivery` when it is empty, and `delivery.is_muted` recognises exactly `'none'`. So the
 *  vocabulary is: send it to the inbox, mute it, or inherit whatever results do. `channel:<id>` is
 *  deliberately absent — the Notify-channel field a few rows up owns a channel id, and a second
 *  place to type one is how two settings start disagreeing about where a message goes.
 *
 *  'inbox' is FIRST because it is the entity default (`Trigger.failure_delivery = "inbox"`) and the
 *  contract the field exists for: "failures reach the inbox even when `delivery` is none".
 */
export const FAILURE_ROUTES: Array<{ value: string; label: string }> = [
  { value: 'inbox', label: 'Inbox' },
  { value: 'none', label: "Don't tell me" },
  { value: '', label: 'Same as results' },
]

export function emptyDraft(): ScheduleDraft {
  return {
    name: '', message: '', kind: 'every', intervalValue: 1, intervalUnit: 'h', cron: '0 9 * * *', at: '',
    mode: 'agent', agent: '', model: '', script: '', command: '',
    channel: '', silent: false, strict_schedule: false, timezone: '', approval_mode: '', skip_dates: [],
    // Both match the entity's own defaults: `failure_delivery = "inbox"` and an empty
    // `failure_policy` (so dedup is opt-in). A form that defaulted dedup ON would silently coalesce
    // repeat alerts for every automation created through the UI.
    failure_delivery: 'inbox', failure_dedupe: false,
  }
}

export function toDraft(j: ScheduleJob): ScheduleDraft {
  const iv = secsToInterval(j.every_secs)
  return {
    id: j.id, name: j.name ?? '', message: j.message ?? '',
    kind: deriveKind(j), intervalValue: iv.value, intervalUnit: iv.unit,
    // Recorded BEFORE the lossy display conversion above is ever sent back (#531).
    everySecsOriginal: j.every_secs ?? undefined,
    cron: j.cron_expr ?? '0 9 * * *', at: '',
    mode: deriveMode(j), agent: j.agent ?? '', model: j.model ?? '',
    script: j.script ?? '', command: j.command ?? '',
    channel: j.channel ?? '', silent: !!j.silent, strict_schedule: !!j.strict_schedule,
    timezone: j.timezone ?? '', approval_mode: j.approval_mode ?? '', skip_dates: j.skip_dates ?? [],
    // `??`, not `||`: an EMPTY `failure_delivery` is a real setting ("inherit the result route"),
    // and coercing it to 'inbox' here would show the user a route their trigger does not have —
    // then WRITE that invented route the next time they saved the form. Only a row that carries the
    // field not at all (`null`/`undefined`) falls back to the entity default.
    failure_delivery: j.failure_delivery ?? 'inbox', failure_dedupe: !!j.failure_dedupe,
  }
}

/** Why this draft's SCHEDULE cannot be submitted, or `null` (#687).
 *
 *  Both surfaces that render `ScheduleForm` gate their Save on this, so neither can post a cron
 *  expression the server will refuse: `TriggerCreatePage`'s `canSave` had no cron term at all and
 *  posted `body.cron` regardless, and `ScheduleDetail`'s Save gated only on a non-empty name. One
 *  exported function rather than a copy in each, because a per-surface copy is how one of them ends
 *  up still submitting the bad value.
 *
 *  Scoped to the CADENCE on purpose: `every` and `at` drafts carry no cron and must stay
 *  submittable, and the name/action requirements belong to the surfaces that own those fields.
 */
export function scheduleDraftInvalidReason(d: ScheduleDraft): string | null {
  return d.kind === 'cron' ? cronExprInvalidReason(d.cron) : null
}

/** Build the create/update payload. The backend create handler accepts every/cron/at + agent
 *  fields; `script` and `command` ride along so the payload is forward-compatible once the
 *  backend lands them (gated SoonTag).
 *
 *  🔴 `at` SHIPS AS EPOCH SECONDS, CONVERTED HERE IN THE BROWSER — never as the raw
 *  `datetime-local` string. The handler wants a Unix timestamp (`'at' must be a Unix timestamp in
 *  seconds`, `handlers/triggers.py`), so sending `"2026-09-20T14:30"` made One-shot uncreatable
 *  from this form for its whole life — a 400 on every submit (issue 530).
 *
 *  And the conversion belongs on THIS side of the wire, not in the handler: a `datetime-local`
 *  value carries no zone, so parsing it server-side would resolve it in the gateway's OS zone
 *  rather than the zone the user typed it in — which is issue 497's live defect on the week-grid
 *  window. `epochSeconds` resolves it against the BROWSER's zone (`Date.parse` on a date-TIME
 *  form is local), which is the only reading that matches what the picker showed. */
export function draftToPayload(d: ScheduleDraft): Record<string, unknown> {
  // 🔴 `message` is omitted in 'other' mode, and that omission is the signal. This form cannot
  // edit a non-agent action provider, so it must send nothing that describes one — otherwise
  // `_scheduleBodyToWire` builds a blank `invoke-agent` from these fields and the server, which
  // correctly applies any action it is sent, replaces a `notify` action with it (issue 689).
  // Renaming a notification trigger destroyed its action that way.
  const body: Record<string, unknown> = {
    name: d.name.trim(),
    timezone: d.timezone || '',
    silent: d.silent,
    strict_schedule: d.strict_schedule,
    channel: d.channel.trim(),
    skip_dates: d.skip_dates,
    // 🔴 FAILURE ROUTING, unconditionally (WF2AUT-15). Both are DELIVERY on the trigger entity —
    // `Trigger.failure_delivery` and `failure_policy.dedupe_hash` — not `invoke-agent` action
    // config, so unlike `approval_mode` two lines down they have a home on the wire top level
    // whatever the action is and must ride EVERY mode. Guarding them behind a mode would strip
    // failure routing from every notify / script / command automation.
    //
    // Sent by PRESENCE, not truthiness: 'Same as results' is the empty string and turning dedup off
    // is `false`, and omitting either would leave the stored value in place — the user's edit would
    // silently not happen (the rule issues 268/689 established).
    failure_delivery: d.failure_delivery,
    failure_dedupe: d.failure_dedupe,
  }
  if (d.kind === 'cron') body.cron = d.cron.trim()
  // 🔴 The cadence is sent ONLY when the user actually changed it (#531). `secsToInterval` and
  // `intervalToSecs` clamp in OPPOSITE directions and neither is invertible — `5s` displays as
  // `1m` and comes back as `60s`; `3601s` displays as `1h` and comes back as `3600s` — so
  // re-deriving `every` on every save meant a name-only edit silently rewrote the schedule.
  //
  // The test is "does the untouched draft still describe the original", not "are the numbers
  // equal": the form can only express what `secsToInterval` produced, so the honest comparison is
  // against that same projection. When it matches, the ORIGINAL seconds ship, which preserves a
  // sub-minute or non-round cadence the form has no way to display. When it differs the user
  // really did edit the field, and their value ships — clamped, as it must be for a new value.
  else if (d.kind === 'every') {
    const orig = d.everySecsOriginal
    const untouched = orig !== undefined
      && secsToInterval(orig).value === d.intervalValue
      && secsToInterval(orig).unit === d.intervalUnit
    body.every = untouched ? orig : intervalToSecs(d.intervalValue, d.intervalUnit)
  }
  // Omitted (not sent as NaN/null) when the picker is empty or unreadable: the handler's
  // "every, cron, or at required" 400 is the honest answer, and `scheduleWhenMet` gates Save so a
  // user does not reach it by accident.
  else if (d.kind === 'at') { const at = epochSeconds(d.at); if (at !== undefined) body.at = at }
  if (d.mode !== 'other') body.message = d.message.trim()
  // 🔴 `approval_mode` rides with the AGENT fields, not with the delivery block it is drawn next
  // to. It is `invoke-agent` action config (`schedule.py`'s `approval_mode` property returns ''
  // for every other provider, and the field is declared in `invoke-agent-action/app.json`), and
  // `_scheduleBodyToWire` folds it into the action it builds ONLY on the invoke-agent branch. Sent
  // unconditionally it was silently discarded for every other mode — the destructure dropped it
  // and the top level never carried it (issue 268).
  if (d.mode === 'agent') { body.agent = d.agent; body.model = d.model; body.approval_mode = d.approval_mode || '' }
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
      {draft.kind === 'every' && <IntervalField draft={draft} set={set} />}
      {draft.kind === 'cron' && <CronField value={draft.cron} onChange={(v) => set('cron', v)} />}
      {draft.kind === 'at' && (
        <input type="datetime-local" value={draft.at} onChange={(e) => set('at', e.target.value)}
          name="run-at" aria-label="Run once at date and time"
          className="h-10 rounded-md bg-surface-container px-m text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
      )}

      {/* ── WHAT (omitted in triggerOnly — action is configured separately) ── */}
      {!triggerOnly && (
        <>
          {/* 'other' is not offered as a choice — it means "this action is a provider this form
              cannot edit", so showing the picker would invite switching to Agent and losing it.
              The note names what the action IS instead of misreporting it as a prompt. */}
          {draft.mode === 'other' ? (
            <Field label="Runs" hint={mm.hint}>
              <div className="text-on-surface-var text-[0.8125rem]">
                This automation runs a configured action, not an agent prompt. Its action is left
                exactly as it is when you save changes here.
              </div>
            </Field>
          ) : (
            <Field label="Runs" right={mm.soon ? <SoonTag /> : undefined} hint={mm.hint}>
              <Segmented options={EXEC_MODES.map((m) => ({ key: m.key, label: m.label, tone: m.tone, icon: m.icon }))} value={draft.mode} onChange={(v) => set('mode', v as ScheduleExecMode)} />
            </Field>
          )}
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
      <Advanced draft={draft} set={set} triggerOnly={triggerOnly} />
    </div>
  )
}

function Advanced({ draft, set, triggerOnly }: { draft: ScheduleDraft; set: <K extends keyof ScheduleDraft>(k: K, v: ScheduleDraft[K]) => void; triggerOnly?: boolean }) {
  const [open, setOpen] = useState(false)
  const tzOptions = useMemo<ComboOption[]>(() => {
    let zones: string[] = []
    try { zones = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.('timeZone') ?? [] } catch { /* older runtime */ }
    return zones.map((z) => ({ value: z, label: z }))
  }, [])

  return (
    <div className="rounded-lg bg-surface-container/60">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex w-full items-center gap-s px-m h-11 text-on-surface-var text-[0.8125rem]">
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
          {/* 🔴 FAILURE ROUTING (WF2AUT-15). The whole contract was built and wired on the backend —
              `Trigger.failure_delivery`, the PATCH allowlist, `delivery.route_for` picking the route
              per OUTCOME, and the opt-in repeat-failure dedup — and had no control anywhere, so a
              user could not reach any of it. It belongs HERE, beside Notify channel and Silent,
              because it answers the same question those do: where does this automation talk to me.
              The hint states the contract rather than the field name: the reason a separate route
              exists is that a silent automation still has to be able to say it broke. */}
          <Field label="If it fails" hint="Failures can reach you even when results stay silent.">
            <NativeSelect value={draft.failure_delivery} onChange={(v) => set('failure_delivery', v)}
              options={FAILURE_ROUTES} label="Where failures go" name="failure-delivery" />
          </Field>
          <div className="flex flex-col gap-s">
            {/* `failure_policy.dedupe_hash`. Opt-in, matching the declared schema: coalescing alerts
                for a user who did not ask is the opposite failure — a broken automation going
                quieter than they expect. The hint names the 1h re-alert window because "collapse
                repeats" and "stop telling me" must not read alike. */}
            <CheckRow label="Collapse repeat failures" hint="Only alert once an hour while the same error keeps happening." checked={draft.failure_dedupe} onChange={(v) => set('failure_dedupe', v)} />
            <CheckRow label="Silent" hint="Suppress auto-delivery; the agent decides when to send." checked={draft.silent} onChange={(v) => set('silent', v)} />
            <CheckRow label="Strict schedule" hint="Fire exactly on schedule with no jitter." checked={draft.strict_schedule} onChange={(v) => set('strict_schedule', v)} />
            {/* Shown only where it can actually persist. `approval_mode` is `invoke-agent` action
                config, so on the create page the Action block's own "Approval" field owns it
                (`triggerOnly` exists to omit exactly the action fields), and for a non-agent
                action there is nothing on the wire to carry it — the server would read '' back
                whatever the switch said. A switch that cannot hold its value is worse than an
                absent one: it reports a setting the run will not honor (issue 268). */}
            {!triggerOnly && draft.mode === 'agent' && (
              <CheckRow label="Auto-approve tools" hint="Run tools without asking for approval each time." checked={draft.approval_mode === 'auto'} onChange={(v) => set('approval_mode', v ? 'auto' : '')} />
            )}
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
        <span className="block text-on-surface text-[0.8125rem]">{label}</span>
        <span className="block text-on-surface-low text-[0.75rem]">{hint}</span>
      </span>
    </label>
  )
}

/** The interval composer — a count, a unit, and the cadence floor said out loud.
 *
 *  The floor line is the ONLY guard this control has, and deliberately so: `MIN_INTERVAL_SECS`
 *  mirrors the backend's `MIN_CLOCK_INTERVAL_SECS`, which WARNS rather than refuses (R1 makes it
 *  overridable — a fast local-model poll is a legitimate choice), so gating Save on it here would
 *  refuse a cadence the API accepts. Before this, `min={1}` was the whole story and the floor was
 *  mentioned nowhere, so the one thing standing between a typo and a per-minute LLM invocation was a
 *  warning the backend computed and every surface then dropped (issue 531).
 */
function IntervalField({ draft, set }: { draft: ScheduleDraft; set: <K extends keyof ScheduleDraft>(k: K, v: ScheduleDraft[K]) => void }) {
  const secs = intervalToSecs(draft.intervalValue, draft.intervalUnit)
  const belowFloor = secs > 0 && secs < MIN_INTERVAL_SECS
  return (
    <div className="flex flex-col gap-s">
      <div className="flex items-center gap-s">
        <input type="number" min={1} value={draft.intervalValue} onChange={(e) => set('intervalValue', Math.max(1, Number(e.target.value) || 1))}
          name="interval-value" aria-label="Run every — interval count"
          aria-describedby={belowFloor ? 'interval-floor-hint' : undefined}
          className="w-24 h-10 rounded-md bg-surface-container px-m text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        <NativeSelect value={draft.intervalUnit} onChange={(v) => set('intervalUnit', v)} options={INTERVAL_UNITS.map((u) => ({ value: u.key, label: u.label }))} label="Run every — interval unit" name="interval-unit" />
      </div>
      {/* `role="status"`, not `role="alert"`: this is an advisory the user may knowingly accept,
          and an assertive interruption on every keystroke under fifteen minutes would train them
          to ignore it. Same reason it is warn-toned rather than danger-toned. */}
      {belowFloor && (
        <p role="status" id="interval-floor-hint" data-type="caption" className="text-warn">
          Every {secs}s is below the {MIN_INTERVAL_SECS}s floor for an LLM-invoking trigger. It will still run — confirm this is what you want.
        </p>
      )}
    </div>
  )
}

function NativeSelect({ value, onChange, options, label, name }: { value: string; onChange: (v: string) => void; options: Array<{ value: string; label: string }>; label?: string; name?: string }) {
  return (
    <div className="relative">
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} name={name}
        className="h-10 appearance-none rounded-md bg-surface-container pl-m pr-9 text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary">
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <ChevronDown size={15} className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-low pointer-events-none" />
    </div>
  )
}

/** Cron field — text input + live human description + quick presets. */
function CronField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  // 🔴 A REAL validator, not a token count. `value.trim().split(/\s+/).length === 5` was wrong in
  // BOTH directions — `'99 99 * * *'` has five tokens and the server refuses it, `'@daily'` has one
  // and the server accepts it — so this field reddened a working expression and cleared a broken
  // one. `cronExprInvalidReason` is sound against croniter (railed by `cronExpr.test.ts`), so a red
  // here is an expression `POST /api/triggers` would refuse too.
  const reason = cronExprInvalidReason(value)
  return (
    <div className="flex flex-col gap-s">
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder="0 9 * * *"
        name="cron-expression" aria-label="Cron expression (minute hour day-of-month month day-of-week)"
        aria-invalid={reason ? true : undefined}
        aria-describedby={reason ? 'cron-expression-error' : undefined}
        className={`w-full h-10 rounded-md bg-surface-container px-m font-mono text-on-surface text-[0.8125rem] outline-none focus:ring-2 ${reason ? 'ring-1 ring-danger/50' : 'focus:ring-primary'}`} />
      <div className="flex flex-wrap gap-1.5">
        {CRON_PRESETS.map((p) => (
          <button key={p.expr} type="button" onClick={() => onChange(p.expr)}
            className={`rounded-pill px-m h-7 text-[0.75rem] transition-colors ${value.trim() === p.expr ? 'bg-primary-container text-on-primary-container' : 'bg-surface-high text-on-surface-var hover:bg-surface-highest'}`}>{p.label}</button>
        ))}
      </div>
      {/* `role="alert"` because this line became a DYNAMIC failure. It used to be one static
          sentence ("Cron needs five fields…") — a label, which is why `fieldErrorAnnounced`'s
          census never covered it. Rendering `{reason}` puts it in the family that rail owns, and a
          failure with no role announces to nobody; the four other `0.75rem` sites carry the role on
          the raw element for the same reason (`FieldError` is size-locked to `0.8125rem`). */}
      {reason && <p role="alert" id="cron-expression-error" className="text-danger text-[0.75rem]">{reason}</p>}
    </div>
  )
}
