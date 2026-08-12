import { useEffect, useRef, useState } from 'react'
import { FieldError } from '../../ui/forms'
import { Pencil, Trash2, Check, X, PlayCircle, Loader2, MessagesSquare, ChevronRight, AlertTriangle, FlaskConical } from 'lucide-react'
import { Button } from '../../ui/Button'
import { FormFooter } from '../../ui/FormFooter'
import { TextLink } from '../../ui/TextLink'
import { Toggle } from '../../ui/Toggle'
import { InvestigateButton } from '../../ui/InvestigateButton'
import { Markdown } from '../../ui/Markdown'
import { confirmDelete } from '../../ui/dialog'
import { api, type ScheduleJob, type ScheduleRun } from '../../lib/api'
import { kindMeta, modeMeta, deriveKind, deriveMode, statusMeta, isInertOutcome, relFuture, relPast, absTime, mdToPlain } from './scheduleMeta'
import { actionLabel, actionIcon } from '../triggers/triggerMeta'
import { ScheduleForm, toDraft, draftToPayload, type ScheduleDraft } from './ScheduleForm'

/** Schedule inspector for the SidePanel: view ↔ in-panel edit (same pattern as
 *  WorkflowDetail), the schedule + execution summary, last result/error, and a
 *  paginated run history that expands each run to its full trace. */
export function ScheduleDetail({ job, onSaved, onDeleted, onChanged, editing, onEditingChange }: {
  job: ScheduleJob
  onSaved: () => void
  onDeleted: () => void
  onChanged: () => void
  editing: boolean
  onEditingChange: (v: boolean) => void
}) {
  // Edit mode is owned by the URL (?edit=1), threaded in fully controlled.
  const setEditing = onEditingChange
  const [draft, setDraft] = useState<ScheduleDraft>(() => toDraft(job))
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [histKey, setHistKey] = useState(0)  // bump to refetch run history after a run
  // Local "I just triggered a run" flag. The backend dispatches the run in the
  // background and returns immediately, and job.is_running only updates on the
  // next list poll (~10s), so without this the UI would look like nothing
  // happened. We hold this true from click until the run is observed finished.
  const [triggered, setTriggered] = useState(false)
  // Brief post-run flash ON the Run button: 'ok'|'error' shows a result label
  // for a couple seconds, fades, then the button reverts to "Run now". `fading`
  // drives the opacity transition before we clear the flash.
  const [ranFlash, setRanFlash] = useState<null | 'ok' | 'error'>(null)
  const [fading, setFading] = useState(false)
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

  useEffect(() => { setDraft(toDraft(job)) }, [job.id])

  // While a run we triggered is in flight, actively poll (the parent list's own
  // poll is every 10s — too slow for responsive feedback). Detect completion
  // when the job reports not-running AND its last_run_ts advanced past where it
  // was at trigger time; then refresh history + confirm.
  useEffect(() => {
    if (!triggered) return
    const finished = !job.is_running && job.last_run_ts != null && job.last_run_ts !== runStartRef.current
    if (finished) {
      setTriggered(false)
      setHistKey((k) => k + 1)
      setRanFlash(job.last_status === 'error' ? 'error' : 'ok')
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

  async function save() {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    setSaving(true); setErr('')
    try { await api.updateSchedule(job.id, draftToPayload(draft)); onSaved(); setEditing(false) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (!(await confirmDelete('schedule', job.name, { body: 'Its run history is removed too. This cannot be undone.' }))) return
    try { await api.deleteSchedule(job.id); onDeleted() } catch { setErr('Delete failed') }
  }
  async function runNow() {
    setBusy(true); setErr(''); setNote('')
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
      setTriggered(true)
      onChanged()
    } catch (e) {
      // 409 = already running; surface honestly rather than as a silent no-op.
      const msg = e instanceof Error ? e.message : 'Run failed'
      setErr(/already running/i.test(msg) ? 'This schedule is already running.' : msg)
    } finally { setBusy(false) }
  }
  async function dryRun() {
    setBusy(true); setErr(''); setNote('')
    runStartRef.current = job.last_run_ts ?? null
    try {
      await api.runSchedule(job.id, true)
      setTriggered(true)
      setNote('Dry-run replay started — write tools are previewed (no side effects). See history for the result.')
      onChanged()
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Dry run failed'
      setErr(/already running/i.test(msg) ? 'This schedule is already running.' : msg)
    } finally { setBusy(false) }
  }
  async function toggle() {
    setBusy(true); try { await api.enableSchedule(job.id, !job.enabled); onChanged() } finally { setBusy(false) }
  }
  async function openChat() {
    setBusy(true); setNote('')
    try { const r = await api.scheduleToChat(job.id); if (r?.session) setNote(`Opened as chat session "${r.session}" — find it in Chat.`) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Open chat failed') } finally { setBusy(false) }
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-l">
        <ScheduleForm draft={draft} onChange={setDraft} compact />
        {err && <FieldError>{err}</FieldError>}
        <FormFooter>
          <Button variant="ghost" size="sm" onClick={() => { setDraft(toDraft(job)); setEditing(false); setErr('') }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} disabled={saving || !draft.name.trim()}
            disabledReason={!draft.name.trim() ? 'Enter a name first' : undefined}><Check size={15} /> {saving ? 'Saving…' : 'Save'}</Button>
        </FormFooter>
      </div>
    )
  }

  // Honest last-run badge (T7): prefer the newest run record's status (persists
  // across restarts, carries launched/failure/timeout) over job.last_status
  // (only ok/error — a fire-and-forget run shows "ok" there, overstating it).
  const ss = statusMeta(job.last_run_status || job.last_status)
  return (
    <div className="flex flex-col gap-l">
      {/* action row */}
      <div className="flex flex-wrap items-center gap-s">
        <Button size="sm" variant="secondary" onClick={runNow} disabled={busy || running || !!ranFlash}>
          <span className={`inline-flex items-center gap-1.5 transition-opacity duration-500 ${fading ? 'opacity-0' : 'opacity-100'}`}
            style={ranFlash === 'ok' ? { color: 'var(--color-ok)' } : ranFlash === 'error' ? { color: 'var(--color-danger)' } : undefined}>
            {running ? <Loader2 size={14} className="animate-spin" />
              : ranFlash === 'ok' ? <Check size={14} />
              : ranFlash === 'error' ? <AlertTriangle size={14} />
              : <PlayCircle size={14} />}
            {running ? 'Running…' : ranFlash === 'ok' ? 'Run finished' : ranFlash === 'error' ? 'Run failed' : 'Run now'}
          </span>
        </Button>
        <span title="Dry-run replay — preview what this would do, with no side effects (write tools are not executed)">
          <Button size="sm" variant="ghost" onClick={dryRun} disabled={busy || running || !!ranFlash}>
            <FlaskConical size={14} /> Dry run
          </Button>
        </span>
        <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</Button>
        {job.has_result && <Button size="sm" variant="ghost" onClick={openChat} disabled={busy}><MessagesSquare size={14} /> Open as chat</Button>}
        <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
        <label className="ml-auto inline-flex items-center gap-2 text-[0.8125rem] cursor-pointer">
          <span className="text-on-surface-var">{job.enabled ? 'Enabled' : 'Disabled'}</span>
          <Toggle on={job.enabled} onChange={toggle} disabled={busy} label="Toggle enabled" size="sm" />
        </label>
      </div>
      {err && <FieldError>{err}</FieldError>}
      {note && !running && <p className="text-ok text-[0.8125rem]">{note}</p>}

      {/* schedule + mode summary */}
      <div className="flex flex-wrap items-center gap-s">
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${km.tone} 16%, transparent)`, color: km.tone }}><km.icon size={13} /> {job.schedule}</span>
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: `color-mix(in srgb, ${mm.tone} 16%, transparent)`, color: mm.tone }}><ActionIcon size={13} /> {actLabel}</span>
        {job.enabled && job.next_run_ts && <span className="text-on-surface-low text-[0.8125rem]">next {relFuture(job.next_run_ts)} · {absTime(job.next_run_ts)}</span>}
      </div>

      {/* what runs — provider-aware: show the action's defining field(s) */}
      {provider === 'run-prompt' ? (
        <Section label="Prompt">
          <div className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] font-mono break-words">
            {String(cfg.prompt_id || '') || <span className="text-on-surface-low">loop.md (default recurring prompt)</span>}
          </div>
        </Section>
      ) : provider === 'run-workflow' ? (
        <Section label="Workflow">
          <div className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] font-mono break-words">
            {String(cfg.workflow_id || '—')}
          </div>
        </Section>
      ) : (
        <Section label={mm.key === 'agent' ? 'Prompt' : mm.key === 'script' ? 'Script' : 'Command'}>
          <div className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] leading-relaxed whitespace-pre-wrap break-words font-mono">
            {mm.key === 'agent' ? (job.message || '—') : mm.key === 'script' ? job.script : job.command}
          </div>
          {mm.key === 'agent' && (job.agent || job.model) && (
            <div className="mt-1.5 flex flex-wrap gap-1.5 text-[0.75rem]">
              {job.agent && <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var font-mono">{job.agent}</span>}
              {job.model && <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var font-mono">{job.model}</span>}
            </div>
          )}
        </Section>
      )}

      {/* context chips */}
      {(job.timezone || job.channel || job.silent || job.strict_schedule || (job.skip_dates?.length ?? 0) > 0) && (
        <div className="flex flex-wrap gap-1.5 text-[0.75rem]">
          {job.timezone && <Chip>{job.timezone}</Chip>}
          {job.channel && <Chip>↳ {job.channel}</Chip>}
          {job.silent && <Chip>silent</Chip>}
          {job.strict_schedule && <Chip>strict</Chip>}
          {(job.skip_dates?.length ?? 0) > 0 && <Chip>{job.skip_dates!.length} skip date{job.skip_dates!.length > 1 ? 's' : ''}</Chip>}
        </div>
      )}

      {/* last outcome */}
      <Section label="Last run">
        <div className="flex items-center gap-2 text-[0.8125rem]">
          <ss.icon size={15} style={{ color: ss.tone }} />
          <span className="text-on-surface-var">{job.last_run_ts ? `${ss.label} · ${relPast(job.last_run_ts)}` : 'never run'}</span>
        </div>
        {job.last_error && <div className="mt-2 rounded-md px-m py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}><AlertTriangle size={13} className="inline mr-1" />{job.last_error}</div>}
        {job.last_result && <div className="mt-2 rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] leading-relaxed"><Markdown>{job.last_result}</Markdown></div>}
      </Section>

      <RunHistory triggerId={`schedule:${job.id}`} reloadKey={histKey} />
    </div>
  )
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var font-mono">{children}</span>
}

/** Paginated per-trigger run history; each row expands to its full trace via
 *  /history/{run_id}.
 *
 *  🔴 EXPORTED and keyed on the FULL facade id (S168). It was private and took a bare schedule id,
 *  so a store trigger had no history UI at all even after the backend began serving one
 *  (S166 the list, S167 the detail) — the panel showed "When it runs" and "What it runs" and nothing
 *  about whether it ever had. Reused rather than reimplemented: a second history renderer is how two
 *  surfaces start disagreeing about what a run looks like, the same argument S163/S164 made about
 *  status mappers.
 *
 *  `supported: false` is rendered as its REASON, not as an empty list: a lifecycle trigger keeps no
 *  run store, and "no runs recorded yet" would be a false claim about a kind that records none. */
export function RunHistory({ triggerId, reloadKey = 0 }: { triggerId: string; reloadKey?: number }) {
  const [runs, setRuns] = useState<ScheduleRun[] | null>(null)
  const [total, setTotal] = useState(0)
  const [limit, setLimit] = useState(5)
  const [openRun, setOpenRun] = useState<string | null>(null)
  const [unsupported, setUnsupported] = useState<string | null>(null)
  // `schedule:abc` → `abc`; `store:file:notes` → `file:notes`, which IS the run-store key.
  const rawId = triggerId.replace(/^(?:schedule|store|lifecycle|event):/, '')

  useEffect(() => {
    let alive = true
    api.triggerHistory(triggerId, limit).then((d) => {
      if (!alive) return
      setUnsupported(d.supported === false ? (d.reason || 'this kind keeps no run records') : null)
      setRuns(d.runs); setTotal(d.total)
    }).catch(() => { if (alive) setRuns([]) })
    return () => { alive = false }
  }, [triggerId, limit, reloadKey])

  if (runs === null) return <Section label="History"><div className="text-on-surface-low text-[0.8125rem]">Loading…</div></Section>
  if (unsupported) return <Section label="History"><div className="text-on-surface-low text-[0.8125rem]">{unsupported}</div></Section>
  if (runs.length === 0) return <Section label="History"><div className="text-on-surface-low text-[0.8125rem]">No runs recorded yet.</div></Section>

  return (
    <Section label={`History · ${total}`}>
      <div className="flex flex-col gap-1">
        {runs.map((r, i) => {
          const id = r.run_id ?? String(i)
          const sm = statusMeta(r.status)
          const expanded = openRun === id
          return (
            <div key={id} className="rounded-md bg-surface-container overflow-hidden">
              <button type="button" onClick={() => setOpenRun(expanded ? null : id)} className="flex w-full items-center gap-s px-m py-2 text-left hover:bg-surface-high transition-colors">
                <ChevronRight size={14} className={`shrink-0 text-on-surface-low transition-transform ${expanded ? 'rotate-90' : ''}`} />
                <sm.icon size={14} style={{ color: sm.tone }} className="shrink-0" />
                <span className="flex-1 truncate text-on-surface text-[0.8125rem]">{mdToPlain(r.summary || r.error) || sm.label}</span>
                {r.trigger === 'manual' && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.75rem]">manual</span>}
                {r.trigger === 'replay' && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-info text-[0.75rem]">dry run</span>}
                <span className="shrink-0 text-on-surface-low text-[0.75rem]">{relPast(r.started_at ?? r.finished_at)}</span>
              </button>
              {/* Investigate (plan 60) — "why did this run fail?" with the job's
                  cadence + action + this run's trace already in context. Only for
                  rows carrying a real run_id: legacy rows fall back to the array
                  index, which addresses nothing server-side. */}
              {r.run_id && (
                <div className="flex justify-end px-m pb-1.5" onClick={(e) => e.stopPropagation()}>
                  {/* `kind="schedule_run"` addresses the RUN STORE, whose key is the raw id — the
                      part after the facade prefix. Derived from `triggerId` rather than passed
                      separately so the two can never disagree (S168). */}
                  <InvestigateButton kind="schedule_run" id={`${rawId}:${r.run_id}`}
                    backLink={`#/triggers?open=${triggerId}`} size={28} />
                </div>
              )}
              {expanded && <RunTrace triggerId={triggerId} runId={id} preview={r} />}
            </div>
          )
        })}
      </div>
      {runs.length < total && (
        <TextLink onClick={() => setLimit((l) => l + 10)} size="sm" className="mt-1.5">Show more ({total - runs.length} more)</TextLink>
      )}
    </Section>
  )
}

/** Lazy-load one run's full record (with trace) on expand. */
function RunTrace({ triggerId, runId, preview }: { triggerId: string; runId: string; preview: ScheduleRun }) {
  const [run, setRun] = useState<ScheduleRun | null>(preview.trace ? preview : null)
  useEffect(() => {
    if (run) return
    let alive = true
    api.triggerRunDetail(triggerId, runId).then((r) => { if (alive) setRun(r) }).catch(() => { if (alive) setRun(preview) })
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
      {/* 🔴 A SUPPRESSION'S REASON IS NOT AN ERROR (S172). S171 began persisting a suppressed fire's
          row, and the reason lands in `ScheduleRun.error` — which this box renders in danger red.
          Measured: a quiet-hours skip showed a neutral grey "gate" dot beside its reason in RED,
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
      {/* `trace` is the full result; `summary` is just a prefix of it — render the
          richest one we have as markdown, not raw text. */}
      {(run.trace || run.summary) && <div className="rounded-md bg-surface px-m py-2 text-on-surface-var leading-relaxed"><Markdown>{run.trace || run.summary || ''}</Markdown></div>}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
