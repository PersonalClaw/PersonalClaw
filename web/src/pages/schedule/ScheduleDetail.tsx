import { useEffect, useRef, useState } from 'react'
import { toneChipSkin } from '../../design/accent'
import { FieldError } from '../../ui/forms'
import { Pencil, Trash2, Check, X, PlayCircle, MessagesSquare, ChevronRight, AlertTriangle, FlaskConical, Folder } from 'lucide-react'
import { Button } from '../../ui/Button'
import { FormFooter } from '../../ui/FormFooter'
import { TextLink } from '../../ui/TextLink'
import { Toggle } from '../../ui/Toggle'
import { InvestigateButton } from '../../ui/InvestigateButton'
import { Markdown } from '../../ui/Markdown'
import { confirmDelete } from '../../ui/dialog'
import { api, type ActionProvider, type ScheduleJob, type ScheduleRun, type TriggerRunResult } from '../../lib/api'
import { kindMeta, modeMeta, deriveKind, deriveMode, scheduleWhenMet, statusMeta, triggerStatusMeta, explainsCause, isInertOutcome, partitionRunsByFold, relFuture, relPast, absTime, mdToPlain, runFlashMeta } from './scheduleMeta'
import { actionLabel, actionIcon } from '../triggers/triggerMeta'
import { ActionFieldList, DryRunResult, actionFields } from '../triggers/DryRunResult'
import { GrantNote, ReviewNote } from '../triggers/ReviewNote'
import { HeartbeatQueue } from '../triggers/HeartbeatQueue'
import {
  ScheduleForm, toDraft, draftToPayload, scheduleDraftInvalidReason, draftProvider, type ScheduleDraft,
} from './ScheduleForm'
import { BUSY_REASON } from '../../ui/unavailable'
import { InlineLoadError } from '../../ui/ListScaffold'
import { useQuery } from '../../lib/data'
import { channelLabel } from './notifyChannel'
import { HeldChange, StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { HELD_CHANGE_REASON, rebaseRecord, type Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'

/** A schedule as the editor starts from it: the form's draft of it, with the revision the same
 *  read reported — the base the save names. */
function baseOf(job: ScheduleJob): Revisioned<ScheduleDraft> {
  return { value: toDraft(job), revision: job.revision ?? '' }
}

/** Schedule inspector for the SidePanel: view ↔ in-panel edit (same pattern as
 *  WorkflowDetail), the schedule + execution summary, last result/error, and a
 *  paginated run history that expands each run to its full trace.
 *
 *  `providers` is the action catalog the list page already loads — the labels for an action's
 *  fields and whether it can call a model (the cadence floor) are read from it, not re-derived. */
export function ScheduleDetail({ job, providers = [], onSaved, onDeleted, onChanged, editing, onEditingChange }: {
  job: ScheduleJob
  providers?: ActionProvider[]
  onSaved: () => void
  onDeleted: () => void
  onChanged: () => void
  editing: boolean
  onEditingChange: (v: boolean) => void
}) {
  // Edit mode is owned by the URL (?edit=1), threaded in fully controlled.
  const setEditing = onEditingChange
  // 🔴 THE FORM SAVES THE WHOLE AUTOMATION, OVER THE COPY ITS DRAFT WAS SEEDED FROM. Every field it
  // shows is sent, the untouched ones as they were read — so a change made since (the agent's
  // `automation_update`, another tab adding a skip date) was put back by the next save here without
  // a word. `base` is that copy with the revision the same read reported, seeded together with the
  // draft and never refreshed under it; a stale save is refused and offered back
  // (`ui/StaleWriteNotice`).
  const [base, setBase] = useState<Revisioned<ScheduleDraft>>(() => baseOf(job))
  const [draft, setDraft] = useState<ScheduleDraft>(() => base.value)
  // Display names for the chip below. A failed read leaves the channel's key on the chip, which is
  // still true, and says the names couldn't be read. The key without Settings → Providers' catch,
  // same as the Notify channel picker.
  const { data: channels, error: channelsError, refresh: refreshChannels } = useQuery(
    'settings:channels-owners', () => api.channels(), { persist: true },
  )
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  // Local "I just triggered a run" flag. The backend dispatches the run in the
  // background and returns immediately, and job.is_running only updates on the
  // next list poll (~10s), so without this the UI would look like nothing
  // happened. We hold this true from click until the run is observed finished.
  const [triggered, setTriggered] = useState(false)
  // Brief post-run line under the Run button: what the run recorded, in its history row's words
  // ("Run finished", or "Waiting for you" for a run that stopped for you), for a couple seconds.
  // `fading` drives the opacity transition before we clear it.
  const [ranFlash, setRanFlash] = useState<ReturnType<typeof runFlashMeta> | null>(null)
  // The status the run THIS press started recorded — `/run` answers it — read when the watcher
  // below sees that run land. It used to be the trigger's health rollup, which says how the
  // automation has been going and nothing about this run.
  const ranStatusRef = useRef('')
  const [fading, setFading] = useState(false)
  // The last dry run's RESPONSE — its whole result, since a dry run records nothing anywhere else.
  const [dry, setDry] = useState<TriggerRunResult | null>(null)
  const runStartRef = useRef<number | null>(null)  // job.last_run_ts at trigger time
  const km = kindMeta(deriveKind(job))
  const mm = modeMeta(deriveMode(job))
  // Action provider drives the "what runs" label/icon for every provider
  // (run-prompt/run-workflow/notify/…); the legacy mode chip is the fallback for
  // jobs with no explicit action provider on the wire.
  const provider = job.action?.provider
  const cfg = (job.action?.config ?? {}) as Record<string, unknown>
  const ActionIcon = provider ? actionIcon(provider) : mm.icon
  const actLabel = provider ? actionLabel(provider) : mm.label
  const running = job.is_running || triggered
  // The edited draft's action, as the catalog classifies it: the cadence floor only speaks for an
  // action that can call a model. Unknown (catalog still loading, an app provider it does not
  // list) keeps the floor — the same direction the backend's table takes.
  const draftInvokesModel = providers.find((p) => p.name === draftProvider(draft, provider))?.invokes_model !== false
  const guard = useStaleWriteGuard<ScheduleDraft>({
    read: async () => {
      const stored = (await api.schedules()).jobs.find((j) => j.id === job.id)
      if (!stored) throw new Error(`the trigger “${job.name}” no longer exists`)
      return baseOf(stored)
    },
    write: (next, revision) => api.updateSchedule(job.id, draftToPayload(next), revision),
    onSaved: () => { onSaved(); setEditing(false) },
    // Dropping the change leaves the panel on what is stored: the list re-reads, and the next Edit
    // seeds from it.
    onDiscard: () => { onChanged(); setEditing(false); setErr('') },
  })

  // Seeded from the job as the panel shows it when the editor opens (or another job is picked), and
  // by Cancel — never while the editor stays open, where the list's 10s poll must not replace what
  // is typed.
  const reseed = () => { const b = baseOf(job); setBase(b); setDraft(b.value) }
  useEffect(() => { reseed() }, [job.id, editing])

  // While a run we triggered is in flight, actively poll (the parent list's own
  // poll is every 10s — too slow for responsive feedback). Detect completion
  // when the job reports not-running AND its last_run_ts advanced past where it
  // was at trigger time; then confirm. The history re-reads by itself: it is keyed
  // on that same `last_run_ts` (below).
  useEffect(() => {
    if (!triggered) return
    const finished = !job.is_running && job.last_run_ts != null && job.last_run_ts !== runStartRef.current
    if (finished) {
      setTriggered(false)
      setRanFlash(runFlashMeta(ranStatusRef.current))
      return
    }
    const t = window.setInterval(() => onChanged(), 2500)
    return () => clearInterval(t)
  }, [triggered, job.is_running, job.last_run_ts])

  // Hold the on-button result flash ~2.5s, fade it, then revert to "Run now".
  useEffect(() => {
    if (!ranFlash) return
    setFading(false)
    const fade = window.setTimeout(() => setFading(true), 2200)
    const clear = window.setTimeout(() => { setRanFlash(null); setFading(false) }, 2700)
    return () => { clearTimeout(fade); clearTimeout(clear) }
  }, [ranFlash])

  // The EDIT half of #687. `PUT /api/triggers/{id}` refuses a cron expression croniter cannot
  // parse, and Save gated only on a non-empty name — so an existing 9am automation could be edited
  // into one that never fires again. Same exported check the create page gates on, so an expression
  // is accepted or refused identically wherever it is typed.
  // A one-shot saved with its time cleared would keep the old time and say nothing, so the time is
  // a save requirement here as it is on the create page (`scheduleWhenMet`), in the same words.
  const scheduleReason = scheduleDraftInvalidReason(draft)
    ?? (scheduleWhenMet(draft.kind, draft.at) ? null : 'Pick the date & time to fire once')

  async function save() {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    if (scheduleReason) { setErr(scheduleReason); return }
    setSaving(true); setErr('')
    // A refusal keeps the draft and the editor open, with the notice below offering the way back.
    try { await guard.save(base, draft, rebaseRecord(base.value, draft)) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (!(await confirmDelete('schedule', job.name, { body: 'Its run history is removed too. This cannot be undone.' }))) return
    try { await api.deleteSchedule(job.id); onDeleted() } catch { setErr('Delete failed') }
  }
  async function runNow() {
    setBusy(true); setErr(''); setNote(''); setDry(null)
    runStartRef.current = job.last_run_ts ?? null
    try {
      const r = await api.runSchedule(job.id)
      // 🔴 A 200 is not a success (#395). `ok: false` means the fire was refused (incident mode) or
      // the action could not be resolved — and `triggered` starts the completion watcher, which
      // waits on a `last_run_ts` that will never advance because nothing ran. That is exactly the
      // "Running…" pill that stuck forever. Surface the reason instead of waiting on a run that
      // does not exist.
      if (r.ok === false) {
        setErr(r.refused || (typeof r.result === 'string' && r.result) || 'This schedule did not run.')
        return
      }
      // Accepted: the run is now executing in the background. Flip the local
      // flag so the user sees an immediate, persistent "running" state; the
      // completion-watcher effect clears it and confirms when the run lands.
      // The animated "Running…" pill below is the sole in-flight indicator —
      // no redundant note here.
      ranStatusRef.current = r.status ?? ''
      setTriggered(true)
      onChanged()
    } catch (e) {
      // 409 = already running; surface honestly rather than as a silent no-op.
      const msg = e instanceof Error ? e.message : 'Run failed'
      setErr(/already running/i.test(msg) ? 'This schedule is already running.' : msg)
    } finally { setBusy(false) }
  }
  // 🔴 A DRY RUN IS OVER WHEN ITS RESPONSE ARRIVES (failure mode 3). This used to `setTriggered(true)`
  // like a real run, which starts the completion watcher above — and that watcher waits for a
  // `last_run_ts` a dry run never moves, because it executes and records nothing. The
  // button read "Running…" 110s after a dry run of a disabled trigger, and on an enabled one it
  // cleared only when the next REAL scheduled fire landed. The note it showed ("See history for the
  // result") promised a row no code writes. The response is the result, so it is rendered and
  // nothing waits: the buttons are back the moment the request returns.
  async function dryRun() {
    setBusy(true); setErr(''); setNote(''); setDry(null)
    try {
      const r = await api.runSchedule(job.id, true)
      // `ok: false` is a dry run that could not even plan (a row with a parse error): the reason is
      // the answer, not a preview of anything.
      if (r.ok === false) { setErr(r.text || 'This schedule cannot be dry-run.'); return }
      setDry(r)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Dry run failed'
      setErr(/already running/i.test(msg) ? 'This schedule is already running.' : msg)
    } finally { setBusy(false) }
  }
  // The one sibling without a catch: a failed enable/disable moved nothing and said nothing,
  // so a disabled-looking schedule could still be armed. Reports through setErr like runNow/openChat.
  async function toggle() {
    setBusy(true)
    try { await api.enableSchedule(job.id, !job.enabled); onChanged() }
    catch (e) { setErr(e instanceof Error ? e.message : (job.enabled ? 'Disable failed' : 'Enable failed')) }
    finally { setBusy(false) }
  }
  // Allow on a schedule that is already on: the switch sent ON again, which is where the gateway
  // asks for the grant its action needs (`needs_grant`) — so it asks first, like switching on does.
  async function allow() {
    setBusy(true); setErr('')
    try { await api.enableSchedule(job.id, true); onChanged() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Allow failed') }
    finally { setBusy(false) }
  }
  async function openChat() {
    setBusy(true); setNote('')
    try { const r = await api.scheduleToChat(job.id); if (r?.session) setNote(`Opened as chat session "${r.session}" — find it in Chat.`) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Open chat failed') } finally { setBusy(false) }
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-l">
        <HeldChange guard={guard}>
          <ScheduleForm draft={draft} onChange={setDraft} compact invokesModel={draftInvokesModel} />
        </HeldChange>
        <StaleWriteNotice guard={guard} what="This trigger" />
        <FormFooter error={err}>
          {/* Cancelling a refused save drops the kept change, exactly as "Discard my change" does. */}
          <Button variant="ghost" size="sm" onClick={() => { if (guard.conflict) guard.discard(); else { reseed(); setEditing(false); setErr('') } }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} loading={saving}
            disabled={saving || !draft.name.trim() || !!scheduleReason || guard.conflict !== null}
            disabledReason={guard.conflict ? HELD_CHANGE_REASON : !draft.name.trim() ? 'Enter a name first' : scheduleReason ?? undefined}><Check size={15} /> Save</Button>
        </FormFooter>
      </div>
    )
  }

  // Honest last-run badge (T7 + #685): the health rollup dominates the run row — the
  // reaper's degraded/last_error pair must not render "ok" off a stale success row.
  // Precedence lives in ONE place (`triggerStatusMeta`) shared with the list's rows for every kind.
  //
  // 🔴 `hasRun` is passed, not omitted (issue 496). The bare pair this used to send let a healthy
  // rollup — which DEFAULTS to `ok` on a trigger that has never fired — render as a successful run,
  // so this badge said "ok" above a "never" last-run line on every never-fired schedule.
  const ss = triggerStatusMeta({
    runStatus: job.last_run_status,
    health: job.last_status,
    state: job.state,
    hasRun: job.last_run_ts != null || (job.run_count ?? 0) > 0,
  })
  const warnings = job.warnings ?? []
  const otherFields = mm.key === 'other' ? actionFields(cfg, providers.find((p) => p.name === provider)) : []
  // A Run workflow action's inputs, as the run will be handed them. Keyed by the workflow's own
  // input names, which no provider schema declares, so each reads under its name in words.
  const workflowInputs = provider === 'run-workflow' && cfg.inputs && typeof cfg.inputs === 'object' && !Array.isArray(cfg.inputs)
    ? actionFields(cfg.inputs as Record<string, unknown>)
    : []
  return (
    <div className="flex flex-col gap-l">
      {/* action row */}
      <div className="flex flex-wrap items-center gap-s">
        {/* In flight, the button says so (`loadingLabel`, announced as busy); what the run recorded
            is said on its own line below, at full strength. It used to be the button's label for a
            couple of seconds while the button was disabled, so it read at the disabled 40% opacity,
            in the run's tone, and a screen reader heard nothing. */}
        <Button size="sm" variant="secondary" onClick={runNow} disabled={busy} disabledReason={BUSY_REASON}
          loading={running} loadingLabel="Running…">
          <PlayCircle size={14} /> Run now
        </Button>
        {/* The explanation rides the button's own `title` (which `Button` joins to a blocked reason)
            rather than a wrapper's: a wrapper tooltip is unreachable from the keyboard. And it says
            what a dry run IS — the old "Dry-run replay … write tools are not executed" described a
            replay that no longer exists. */}
        <Button size="sm" variant="ghost" onClick={dryRun} disabled={busy || running}
          title="Preview what a run would do — nothing is executed and nothing is recorded"
          disabledReason={BUSY_REASON}>
          <FlaskConical size={14} /> Dry run
        </Button>
        <Button size="sm" variant="ghost" onClick={() => { setDry(null); setEditing(true) }}><Pencil size={14} /> Edit</Button>
        {job.has_result && <Button size="sm" variant="ghost" onClick={openChat} disabled={busy} disabledReason={BUSY_REASON}><MessagesSquare size={14} /> Open as chat</Button>}
        <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
        <label className="ml-auto inline-flex items-center gap-2 text-[0.8125rem] cursor-pointer">
          <span className="text-on-surface-var">{job.enabled ? 'Enabled' : 'Disabled'}</span>
          <Toggle on={job.enabled} onChange={toggle} disabled={busy} label="Toggle enabled" size="sm" />
        </label>
      </div>
      {err && <FieldError>{err}</FieldError>}
      {/* What the run this press started recorded, in its history row's words, for a couple of
          seconds. A status, so it is announced; its tone is the row's. */}
      {ranFlash && !err && (
        <p role="status" data-type="label-s"
          className={`inline-flex items-center gap-1.5 transition-opacity duration-500 ${fading ? 'opacity-0' : 'opacity-100'}`}
          style={{ color: ranFlash.tone }}>
          <ranFlash.icon size={14} aria-hidden />{ranFlash.label}
        </p>
      )}
      {note && !running && <p className="text-ok text-[0.8125rem]">{note}</p>}
      {dry && <DryRunResult result={dry} providers={providers} onDismiss={() => setDry(null)} />}

      {/* `toneChipSkin`, not a tint of the tone itself. `scheduleMeta` makes TWO of these coral —
          the `cron` kind and the `agent` mode — and coral ink over a 16% tint of itself measures
          **3.85:1** in light against a 4.5 floor. Measured live by opening each schedule trigger:
          **9 failing chips across all 5** in this home, both the kind chip and the mode chip, because
          a cron schedule that invokes an agent lands two coral chips side by side. Dark is unaffected
          (the tint darkens away from a light accent). The other tones — `every`/`script` (info),
          `at` (warn), `command` (ok) — keep the tint, which is why the helper remaps one tone rather
          than the registry. See `design/accentChipTone.test.tsx`. */}
      {/* schedule + mode summary */}
      <div className="flex flex-wrap items-center gap-s">
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={toneChipSkin(km.tone, 16)}><km.icon size={13} /> {job.schedule}</span>
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={toneChipSkin(mm.tone, 16)}><ActionIcon size={13} /> {actLabel}</span>
        {job.enabled && job.next_run_ts && <span className="text-on-surface-low text-[0.8125rem]">next {relFuture(job.next_run_ts)} · {absTime(job.next_run_ts)}</span>}
      </div>

      {/* The row's advisories IN WORDS (the cadence floor, an unknown spec key). The list badges
          them "check schedule"; the reason used to exist only as that badge's hover title, so a
          user who opened the trigger to find out why still could not see it. */}
      {warnings.length > 0 && (
        <div role="note" className="flex items-start gap-s text-warn">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <div data-type="body-s" className="flex min-w-0 flex-1 flex-col gap-xs">
            {warnings.map((w) => <p key={w} className="break-words">{w}</p>)}
          </div>
        </div>
      )}
      {/* A schedule brought over from an older version waits, switched off, for the owner to allow
          what it runs — the sections below are what they are deciding about. */}
      {job.needs_review && <ReviewNote />}
      {/* Not allowed to run what its action uses — Run now and every fire are refused until the
          owner allows it. The sections below are what they are allowing. */}
      {!job.needs_review && (job.needs_grant ?? []).length > 0 && (
        <GrantNote labels={job.needs_grant ?? []} enabled={job.enabled} busy={busy} onAllow={allow} />
      )}

      {/* what runs — provider-aware: show the action's defining field(s) */}
      {provider === 'run-prompt' ? (
        <Section label="Prompt">
          {/* What the provider runs, in its order: the saved Prompt, else the action's own message
              (an automation made in chat carries its instruction there), else loop.md. */}
          <div className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] font-mono break-words whitespace-pre-wrap">
            {String(cfg.prompt_id || '') || String(cfg.message || '') || <span className="text-on-surface-low">loop.md (default recurring prompt)</span>}
          </div>
        </Section>
      ) : provider === 'run-workflow' ? (
        // `workflow` is the key the action saves (the run-workflow manifest's only one), and its
        // inputs ride beside it. This read `workflow_id`, a key nothing writes, so every Run
        // workflow trigger showed "Workflow —" and none of the inputs it would start its run with.
        <Section label="Workflow">
          <div className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] font-mono break-words">
            {String(cfg.workflow || '—')}
          </div>
          {workflowInputs.length > 0 && (
            <div className="mt-s" aria-label="The inputs its run starts with" role="group">
              <ActionFieldList fields={workflowInputs} />
            </div>
          )}
        </Section>
      ) : mm.key === 'other' ? (
        // 🔴 NOT A COMMAND BOX. Every provider this form cannot edit (notify, the digests, …) fell
        // into the branch below and got a "Command" section reading `job.command` — which is null
        // for all of them, so a notify trigger showed an empty COMMAND box (c1b-068). Such an
        // action is defined by its own config, labelled the way its create form labels it; one
        // with no config (the system digests) simply has nothing to show here.
        otherFields.length > 0 && (
          <Section label="Settings">
            <ActionFieldList fields={otherFields} />
          </Section>
        )
      ) : (
        <Section label={mm.key === 'agent' ? 'Prompt' : mm.key === 'script' ? 'Script' : 'Command'}>
          <div className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] leading-relaxed whitespace-pre-wrap break-words font-mono">
            {mm.key === 'agent' ? (job.message || '—') : mm.key === 'script' ? job.script : job.command}
          </div>
          {mm.key === 'agent' && (job.agent || job.model || job.cwd) && (
            <div className="mt-1.5 flex flex-wrap gap-1.5 text-[0.75rem]">
              {job.agent && <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var font-mono">{job.agent}</span>}
              {job.model && <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var font-mono">{job.model}</span>}
              {/* Where its agent works: the folder its file tools reach. */}
              {job.cwd && (
                <span title="The folder its agent works in" className="rounded-pill bg-surface-high px-s h-6 inline-flex items-center gap-xs text-on-surface-var font-mono">
                  <Folder size={12} aria-hidden="true" /> {job.cwd}
                </span>
              )}
            </div>
          )}
        </Section>
      )}

      {/* The queue this trigger reads: what each task may do is the owner's yes to that task, not the
          trigger's switch, so it is shown and allowed here, task by task. */}
      {provider === 'heartbeat-tasks' && <HeartbeatQueue reloadKey={job.last_run_ts ?? 0} />}

      {/* context chips */}
      {(job.timezone || job.channel || job.silent || job.strict_schedule || (job.skip_dates?.length ?? 0) > 0) && (
        <div className="flex flex-wrap gap-1.5 text-[0.75rem]">
          {job.timezone && <Chip>{job.timezone}</Chip>}
          {job.channel && <Chip>↳ {channelLabel(job.channel, channels)}</Chip>}
          {job.silent && <Chip>silent</Chip>}
          {job.strict_schedule && <Chip>strict</Chip>}
          {(job.skip_dates?.length ?? 0) > 0 && <Chip>{job.skip_dates!.length} skip date{job.skip_dates!.length > 1 ? 's' : ''}</Chip>}
        </div>
      )}
      {job.channel && channelsError && !channels ? (
        <InlineLoadError what="your chat channels" error={channelsError} onRetry={refreshChannels} />
      ) : null}
      {/* The server's sentence for a channel its results can't reach: one that isn't set up here
          (a route saved before routes named their channel reads as a channel called `C0123`), or
          an id the channel refuses. Said, so the chip above is not read as a working route. */}
      {job.channel && job.channel_problem ? (
        <p role="status" data-type="caption" className="text-warn">
          <AlertTriangle size={12} className="mr-1 inline-block align-[-1px]" aria-hidden="true" />
          {job.channel_problem} Results reach the dashboard only.
        </p>
      ) : null}

      {/* last outcome */}
      <Section label="Last run">
        <div className="flex items-center gap-2 text-[0.8125rem]">
          <ss.icon size={15} style={{ color: ss.tone }} />
          <span className="text-on-surface-var">{job.last_run_ts ? `${ss.label} · ${relPast(job.last_run_ts)}` : 'never run'}</span>
        </div>
        {/* Gated like the list row's reason: `last_error` is never cleared by a later success, so
            an ungated box put last week's failure under "ok · just now". */}
        {job.last_error && explainsCause(ss) && <div className="mt-2 rounded-md px-m py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}><AlertTriangle size={13} className="inline mr-1" />{job.last_error}</div>}
        {job.last_result && <div className="mt-2 rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] leading-relaxed"><Markdown>{job.last_result}</Markdown></div>}
      </Section>

      {/* Keyed on `last_run_ts`, not on a counter only Run now bumped. A run the SCHEDULER fired
          while this panel was open moved "Last run" (the list poll carries it) and left the history
          as it was: measured, "Last run: ok · just now" directly above "No runs recorded yet." */}
      <RunHistory triggerId={`schedule:${job.id}`} reloadKey={job.last_run_ts ?? 0} />
    </div>
  )
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var font-mono">{children}</span>
}

/** Paginated per-trigger run history; each row expands to its full trace via
 *  /history/{run_id}.
 *
 *  🔴 EXPORTED and keyed on the FULL facade id. It was private and took a bare schedule id,
 *  so a store trigger had no history UI at all even after the backend began serving one
 *  (S166 the list, S167 the detail) — the panel showed "When it runs" and "What it runs" and nothing
 *  about whether it ever had. Reused rather than reimplemented: a second history renderer is how two
 *  surfaces start disagreeing about what a run looks like, the same argument S163/S164 made about
 *  status mappers.
 *
 *  `supported: false` is rendered as its REASON, not as an empty list: a lifecycle trigger keeps no
 *  run store, and "no runs recorded yet" would be a false claim about a kind that records none. */
export function RunHistory({ triggerId, reloadKey = 0 }: { triggerId: string; reloadKey?: number | string }) {
  const [runs, setRuns] = useState<ScheduleRun[] | null>(null)
  const [total, setTotal] = useState(0)
  const [limit, setLimit] = useState(5)
  const [openRun, setOpenRun] = useState<string | null>(null)
  const [unsupported, setUnsupported] = useState<string | null>(null)
  const [loadErr, setLoadErr] = useState<unknown>(null)
  // Retry tick. `setLimit(limit)` would be a no-op — same value, no re-render, a Retry button
  // that does nothing — so the effect needs a value that actually changes.
  const [retry, setRetry] = useState(0)
  // The did/suppressed fold: inert `skipped_*` rows stay hidden until revealed.
  const [showSuppressed, setShowSuppressed] = useState(false)
  // `schedule:abc` → `abc`; `store:file:notes` → `file:notes`, which IS the run-store key.
  const rawId = triggerId.replace(/^(?:schedule|store|lifecycle|event):/, '')

  useEffect(() => {
    let alive = true
    api.triggerHistory(triggerId, limit).then((d) => {
      if (!alive) return
      setUnsupported(d.supported === false ? (d.reason || 'this kind keeps no run records') : null)
      setLoadErr(null)
      setRuns(d.runs); setTotal(d.total)
    }).catch((e) => { if (alive) { setLoadErr(e); setRuns(null) } })
    return () => { alive = false }
  }, [triggerId, limit, reloadKey, retry])

  // 🔴 THE ERROR BRANCH COMES FIRST, AND IT HAS TO (#532). This read used to end
  // `.catch(() => setRuns([]))`, and `runs === null` is the LOADING sentinel — so a failed fetch
  // did not merely lose the error, it landed in the zero-length branch one line down and printed
  // "No runs recorded yet.": a factual claim about the server's records, emitted precisely when
  // the server could not be reached. `runs` now HOLDS at `null` on failure, which is why this
  // test has to precede the `null` check rather than follow it.
  if (loadErr) return <Section label="History"><InlineLoadError what="run history" error={loadErr} onRetry={() => setRetry((n) => n + 1)} /></Section>
  if (runs === null) return <Section label="History"><div className="text-on-surface-low text-[0.8125rem]">Loading…</div></Section>
  if (unsupported) return <Section label="History"><div className="text-on-surface-low text-[0.8125rem]">{unsupported}</div></Section>
  if (runs.length === 0) return <Section label="History"><div className="text-on-surface-low text-[0.8125rem]">No runs recorded yet.</div></Section>

  // Fold inert `skipped_*` rows out of the default view; reveal on demand.
  const { did, suppressed } = partitionRunsByFold(runs)
  const shown = showSuppressed ? runs : did

  return (
    <Section label={`History · ${total}`}>
      <div className="flex flex-col gap-1">
        {shown.length === 0 && (
          <div className="text-on-surface-low" data-type="body-s">
            {suppressed.length === 1 ? 'The only run so far was suppressed.' : `All ${suppressed.length} runs in view were suppressed.`}
          </div>
        )}
        {shown.map((r, i) => {
          const id = r.run_id ?? r.id ?? String(i)
          const sm = statusMeta(r.status)
          const expanded = openRun === id
          return (
            <div key={id} className="rounded-md bg-surface-container overflow-hidden">
              <button type="button" aria-expanded={expanded} onClick={() => setOpenRun(expanded ? null : id)}
                className="flex w-full items-center gap-s px-m py-2 text-left hover:bg-surface-high transition-colors">
                <ChevronRight size={14} className={`shrink-0 text-on-surface-low transition-transform ${expanded ? 'rotate-90' : ''}`} />
                <sm.icon size={14} style={{ color: sm.tone }} className="shrink-0" />
                <span className="flex-1 truncate text-on-surface text-[0.8125rem]">{mdToPlain(r.summary || r.error) || sm.label}</span>
                {r.trigger === 'manual' && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.75rem]">manual</span>}
                {r.trigger === 'replay' && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-info text-[0.75rem]">dry run</span>}
                <span className="shrink-0 text-on-surface-low text-[0.75rem]">{relPast(r.started_at ?? r.finished_at)}</span>
              </button>
              {/* Investigate — "why did this run fail?" with the job's
                  cadence + action + this run's trace already in context. Only for
                  rows carrying a real run_id: legacy rows fall back to the array
                  index, which addresses nothing server-side. */}
              {r.run_id && (
                <div className="flex justify-end px-m pb-1.5" onClick={(e) => e.stopPropagation()}>
                  {/* `kind="schedule_run"` addresses the RUN STORE, whose key is the raw id — the
                      part after the facade prefix. Derived from `triggerId` rather than passed
                      separately so the two can never disagree. */}
                  <InvestigateButton kind="schedule_run" id={`${rawId}:${r.run_id}`}
                    backLink={`#/triggers?open=${triggerId}`} size={28} />
                </div>
              )}
              {expanded && <RunTrace triggerId={triggerId} runId={id} preview={r} />}
            </div>
          )
        })}
      </div>
      {(suppressed.length > 0 || runs.length < total) && (
        // One row, spaced: side by side the two links read as one ("Show 4 suppressedShow more"),
        // which a queue passing every minute puts on screen at once.
        <div className="mt-1.5 flex flex-wrap items-center gap-m">
          {suppressed.length > 0 && (
            <TextLink onClick={() => setShowSuppressed((s) => !s)} size="sm"
              aria-expanded={showSuppressed}>
              {showSuppressed
                ? `Hide ${suppressed.length} suppressed`
                : `Show ${suppressed.length} suppressed`}
            </TextLink>
          )}
          {runs.length < total && (
            <TextLink onClick={() => setLimit((l) => l + 10)} size="sm">Show more ({total - runs.length} more)</TextLink>
          )}
        </div>
      )}
    </Section>
  )
}

/** Lazy-load one run's full record (with trace) on expand. */
function RunTrace({ triggerId, runId, preview }: { triggerId: string; runId: string; preview: ScheduleRun }) {
  const [run, setRun] = useState<ScheduleRun | null>(preview.trace ? preview : null)
  // 🔴 A FAILED DETAIL READ IS NOT "THIS RUN PRODUCED NO OUTPUT" (#532). This used to
  // `.catch(() => setRun(preview))`, and `preview` is the LIST row — real, but carrying no
  // `trace`. So a failed fetch rendered an expanded run with timings and an empty body, which is
  // exactly what a run that genuinely produced nothing looks like. The preview's timings are true
  // and still shown; what changes is that the missing trace is now attributed instead of implied.
  const [traceErr, setTraceErr] = useState<unknown>(null)
  useEffect(() => {
    if (run) return
    let alive = true
    api.triggerRunDetail(triggerId, runId)
      .then((r) => { if (alive) { setTraceErr(null); setRun(r) } })
      .catch((e) => { if (alive) { setTraceErr(e); setRun(preview) } })
    return () => { alive = false }
  }, [triggerId, runId])
  if (!run) return <div className="px-m pb-2 text-on-surface-low text-[0.75rem]">Loading trace…</div>
  const inert = isInertOutcome(run.outcome ?? run.status)
  return (
    <div className="px-m pb-3 flex flex-col gap-2 text-[0.8125rem]">
      <div className="flex flex-wrap gap-x-m gap-y-0.5 text-on-surface-low text-[0.75rem]">
        {run.started_at && <span>started {absTime(run.started_at)}</span>}
        {run.finished_at && <span>· finished {absTime(run.finished_at)}</span>}
        {run.duration_ms != null && <span>· {(run.duration_ms / 1000).toFixed(1)}s</span>}
      </div>
      {/* 🔴 A SUPPRESSION'S REASON IS NOT AN ERROR. S171 began persisting a suppressed fire's
          row, and the reason lands in `ScheduleRun.error` — which this box renders in danger red.
          A quiet-hours skip showed a neutral grey "gate" dot beside its reason in RED,
          identical to a real `ConnectionError`, so the row contradicted itself and the alarming half
          is the one a user reacts to. An inert outcome means the automation is working exactly as
          configured; a red badge sends the user hunting a fault that is not there. */}
      {run.error && (
        <div
          className="rounded-md px-m py-1.5"
          style={
            inert
              ? { background: 'var(--color-surface)', color: 'var(--color-on-surface-low)' }
              : { background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }
          }
        >{run.error}</div>
      )}
      {/* `trace` is the full result — what the action printed. `summary` is the row's line: a prefix
          of the trace for an action that wrote no sentence, or the sentence one wrote for a person
          (`ActionResult.summary`), which the row cuts to one line. That sentence is said here in
          full, above the trace; a prefix would only repeat the trace's own opening. */}
      {run.summary && run.trace && !run.trace.startsWith(run.summary) && (
        <div className="text-on-surface leading-relaxed">{run.summary}</div>
      )}
      {(run.trace || run.summary) && <div className="rounded-md bg-surface px-m py-2 text-on-surface-var leading-relaxed"><Markdown>{run.trace || run.summary || ''}</Markdown></div>}
      {/* `traceErr != null`, not `traceErr &&` — the state is `unknown`, and `unknown && …`
          is `unknown`, which is not a ReactNode. */}
      {traceErr != null && !run.trace && <InlineLoadError what="this run's trace" error={traceErr} />}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
