import { Repeat, CalendarClock, Calendar, Bot, FileCode2, TerminalSquare, CheckCircle2, XCircle, Circle, Rocket, Clock, ShieldAlert, PauseCircle } from 'lucide-react'
import { epochSeconds } from '../../lib/epoch'
import type { LucideIcon } from 'lucide-react'
import type { ScheduleJob, ScheduleKind, ScheduleExecMode } from '../../lib/api'

// ── schedule kind (every / cron / at) ──
// `soon` flags the axes the HTTP create/update payload can't persist yet
// (the model + scheduler support them — see schedule-entity-evolution.md).
export interface KindMeta { key: ScheduleKind; label: string; icon: LucideIcon; tone: string; hint: string; soon?: boolean }
export const KINDS: KindMeta[] = [
  { key: 'every', label: 'Interval', icon: Repeat, tone: 'var(--color-info)', hint: 'Run every N minutes/hours/days.' },
  { key: 'cron', label: 'Cron', icon: CalendarClock, tone: 'var(--color-primary)', hint: 'Five-field cron expression (min hour dom month dow).' },
  { key: 'at', label: 'One-shot', icon: Calendar, tone: 'var(--color-warn)', hint: 'Fire once at a specific date & time.', soon: true },
]
export function kindMeta(k?: ScheduleKind): KindMeta { return KINDS.find((x) => x.key === k) ?? KINDS[0] }

// ── execution mode (agent / script / command) ──
export interface ModeMeta { key: ScheduleExecMode; label: string; icon: LucideIcon; tone: string; hint: string; soon?: boolean }
export const EXEC_MODES: ModeMeta[] = [
  { key: 'agent', label: 'Agent', icon: Bot, tone: 'var(--color-primary)', hint: 'An LLM agent runs your prompt each time.' },
  { key: 'script', label: 'Script', icon: FileCode2, tone: 'var(--color-info)', hint: 'Zero-token: run a Python entrypoint (path/to/file.py:func) under ~/.personalclaw/crons/.', soon: true },
  { key: 'command', label: 'Command', icon: TerminalSquare, tone: 'var(--color-ok)', hint: 'Zero-token: run a shell command in the sandbox.', soon: true },
]
// NOT in EXEC_MODES: that list is the picker, and 'other' is not something to pick.
export const OTHER_MODE: ModeMeta = {
  key: 'other', label: 'Action', icon: Rocket, tone: 'var(--color-on-surface-var)',
  hint: "This automation runs an action provider (a notification, a digest, a remediation) rather than an agent prompt.",
}
export function modeMeta(m?: ScheduleExecMode): ModeMeta {
  if (m === 'other') return OTHER_MODE
  return EXEC_MODES.find((x) => x.key === m) ?? EXEC_MODES[0]
}

/** Derive the schedule kind from the wire job (cron_expr > every_secs > at). */
export function deriveKind(j: ScheduleJob): ScheduleKind {
  if (j.cron_expr) return 'cron'
  if (j.every_secs != null) return 'every'
  return 'at'
}
/** Derive the execution mode from the job's ACTUAL action provider.
 *
 *  🔴 This used to fall through to 'agent' for every provider it did not recognise, and that
 *  default destroyed data: the edit form then rendered agent fields, `draftToPayload` emitted
 *  `message`/`agent`/`model`, and `_scheduleBodyToWire` turned those into a blank
 *  `invoke-agent` action — so renaming a `notify` trigger replaced its action and lost the
 *  notification (issue 689). The list row went from "Notify" to "Invoke Agent" with a 200.
 *
 *  The provider was always available on the wire (`ScheduleJob.action.provider`); only this
 *  function ignored it. The legacy `script`/`command` fields are still checked first so a row
 *  written before the canonical `action` existed keeps resolving.
 */
export function deriveMode(j: ScheduleJob): ScheduleExecMode {
  if (j.script) return 'script'
  if (j.command) return 'command'
  const provider = j.action?.provider || ''
  if (provider === 'run-script') return 'script'
  if (provider === 'bash') return 'command'
  // No provider at all is a legacy agent row; anything else is a provider this form cannot edit.
  if (!provider || provider === 'invoke-agent') return 'agent'
  return 'other'
}

// ── last-run status dot ──
export interface StatusMeta { label: string; tone: string; icon: LucideIcon }
export function statusMeta(s?: string | null): StatusMeta {
  // job.last_status is "ok"/"error"; run.status is "success"/"failure"/"timeout"/"launched".
  if (s === 'ok' || s === 'success') return { label: 'ok', tone: 'var(--color-ok)', icon: CheckCircle2 }
  if (s === 'error' || s === 'failure') return { label: 'error', tone: 'var(--color-danger)', icon: XCircle }
  // 🔴 THE FIRE-RECORD VOCABULARY (S163). S137 mapped the store's `status` words
  // (`success`/`failure`) and the suppression family, and missed the three `Outcome` members a
  // FireRecord most often carries. Measured: `statusMeta('failed')` returned **"never run"** in
  // neutral grey — a genuinely FAILED automation rendered identically to one that had never run,
  // which is the one pair a user must never confuse. `ran`/`ran_late` were equally invisible.
  //
  // `ran_late` keeps a distinct label rather than folding into `ok`: §1.3 records
  // `scheduled_for` beside `started_at` precisely so lateness is a fact, not an impression, and a
  // run 40 minutes after its slot is a different story from one on time.
  if (s === 'ran') return { label: 'ran', tone: 'var(--color-ok)', icon: CheckCircle2 }
  if (s === 'ran_late') return { label: 'ran late', tone: 'var(--color-warning)', icon: Clock }
  if (s === 'failed') return { label: 'failed', tone: 'var(--color-danger)', icon: XCircle }
  if (s === 'timeout') return { label: 'timed out', tone: 'var(--color-danger)', icon: Clock }
  // "launched": started a background turn — honest "started ≠ succeeded" (T7).
  // Neutral tone, NOT ok-green: a green tick would imply the work succeeded.
  if (s === 'launched') return { label: 'launched', tone: 'var(--color-info)', icon: Rocket }
  // 🔴 A SCREENED payload (S134/S136). The backend writes `blocked_injection` rows now, and without
  // this branch they fell through to "never run" — the worst possible label for a blocked attack:
  // the user reads "this automation has never run" when it in fact refused a hostile payload, and
  // `blocked_injection` never auto-retries, so that row is the only record there will ever be.
  if (s === 'blocked_injection') return { label: 'blocked', tone: 'var(--color-danger)', icon: ShieldAlert }
  // A suppressed fire (S132's archive split). Neutral, not an error: the automation is working as
  // configured — quiet hours held it, or a slot was busy — and a red badge would send the user
  // looking for a fault that is not there.
  if (s && s.startsWith('skipped_')) return { label: s.slice(8).replace(/_/g, ' '), tone: 'var(--color-on-surface-low)', icon: PauseCircle }
  if (s === 'deferred') return { label: 'deferred', tone: 'var(--color-info)', icon: PauseCircle }
  if (s === 'refused') return { label: 'refused', tone: 'var(--color-warning)', icon: ShieldAlert }
  return { label: 'never run', tone: 'var(--color-on-surface-low)', icon: Circle }
}



/** Whether this outcome means "nothing was spent and nothing changed" (§1.3's `INERT_OUTCOMES`).
 *
 * 🔴 WHY THIS EXISTS. S171 began PERSISTING a suppressed fire's row so criterion 8's "zero silent
 * drops" is real — and the reason lands in `ScheduleRun.error`, which `RunTrace` renders in a
 * danger-tinted box. Measured: a quiet-hours skip showed a neutral grey "gate" dot beside its reason
 * in **red**, identical to a real `ConnectionError`. The row contradicted itself, and the alarming
 * half is the one a user reacts to.
 *
 * Derived from the `skipped_` prefix rather than a hand-copied list: every member of the backend's
 * `INERT_OUTCOMES` carries it (verified — 6 of 6), so a new inert outcome is covered automatically
 * instead of waiting for someone to update a second list. The same reason `statusMeta` matches the
 * family by prefix rather than enumerating it.
 */
export function isInertOutcome(s?: string | null): boolean {
  return Boolean(s) && String(s).startsWith('skipped_')
}

// ── trigger lifecycle: health + state (S164) ──

/** How a trigger's HEALTH rollup and lifecycle STATE render.
 *
 * 🔴 WHY THIS IS SHARED. `TriggersListPage` carried its own `statusDot` handling four values
 * (`ok`/`success`, `error`/`timeout`/`blocked`, `launched`) and defaulting everything else to a
 * neutral grey circle. Measured against the real `TriggerHealth` vocabulary, which is what that
 * page actually feeds it for a store trigger (`triggerMeta.storeToTrigger` sets
 * `lastStatus: t.health`):
 *
 *     health=ok        -> ok green, check
 *     health=degraded  -> grey, circle
 *     health=parked    -> grey, circle
 *     health=failing   -> grey, circle     ← identical to parked and degraded
 *
 * So on the one page a user manages automations from, a FAILING automation was pixel-identical to a
 * parked (self-healing) one. Same defect shape as S163's `statusMeta` gap, in a second local copy —
 * which is the argument for one mapper per vocabulary rather than a fix per page.
 *
 * `state` is folded in here rather than given its own mapper because the two answer one question
 * for the user ("is this thing working?") and a surface showing them separately would have to invent
 * a precedence rule. STATE WINS when it is not `active`: an autopaused trigger's health is `failing`,
 * but "stopped" is the more urgent fact — `health` says how it has been going, `state` says whether
 * it will run at all.
 */
export function triggerHealthMeta(health?: string | null, state?: string | null): StatusMeta {
  // A lifecycle state that stops the trigger firing outranks any health rollup.
  if (state === 'quarantined') {
    return { label: 'quarantined', tone: 'var(--color-danger)', icon: ShieldAlert }
  }
  if (state === 'autopaused') return { label: 'autopaused', tone: 'var(--color-danger)', icon: XCircle }
  if (state === 'paused') return { label: 'paused', tone: 'var(--color-on-surface-low)', icon: PauseCircle }
  if (state === 'retired') return { label: 'retired', tone: 'var(--color-on-surface-low)', icon: Circle }
  // `parked` is NOT an error: it self-heals once the cooldown elapses (S159's unpark), so a red
  // badge would send the user hunting a fault that resolves itself.
  if (state === 'parked' || health === 'parked') {
    return { label: 'parked', tone: 'var(--color-info)', icon: PauseCircle }
  }
  if (health === 'failing') return { label: 'failing', tone: 'var(--color-danger)', icon: XCircle }
  if (health === 'degraded') return { label: 'degraded', tone: 'var(--color-warning)', icon: Clock }
  if (health === 'ok') return { label: 'ok', tone: 'var(--color-ok)', icon: CheckCircle2 }
  return { label: '', tone: 'var(--color-on-surface-low)', icon: Circle }
}

// ── time helpers ──
// Every one of these takes `number | string`, because the endpoints disagree: the schedule
// fields (`next_run_ts`, `last_run_ts`) are epoch seconds while `/api/triggers/history`
// sends ISO-8601. `epochSeconds` is the ONE place that reconciles them, and it returns
// `undefined` for anything it cannot read so these render their empty form instead of
// arithmetic on a string. See `lib/epoch.ts` for the six rows of "in NaNd" that earned it.
export function relFuture(ts?: number | string | null): string {
  const t = epochSeconds(ts)
  if (t == null) return ''
  const s = t - Date.now() / 1000
  if (s < 0) return 'overdue'
  if (s < 60) return 'in <1m'
  if (s < 3600) return `in ${Math.floor(s / 60)}m`
  if (s < 86400) return `in ${Math.floor(s / 3600)}h`
  return `in ${Math.floor(s / 86400)}d`
}
export function relPast(ts?: number | string | null): string {
  const t = epochSeconds(ts)
  if (t == null) return 'never'
  const s = Date.now() / 1000 - t
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
export function absTime(ts?: number | string | null): string {
  const t = epochSeconds(ts)
  if (t == null) return ''
  return new Date(t * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/** Flatten markdown to a clean single-line plain-text snippet for a row title.
 *  Strips headings/emphasis/code/links/list markers and collapses whitespace,
 *  so a one-line label reads as prose, not raw markdown. */
export function mdToPlain(s?: string | null): string {
  if (!s) return ''
  return s
    .replace(/```[\s\S]*?```/g, ' ')           // fenced code blocks
    .replace(/`([^`]+)`/g, '$1')               // inline code
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')      // images
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')    // links → text
    .replace(/^[\s>]*#{1,6}\s+/gm, '')          // ATX headings
    .replace(/^\s*[-*+]\s+/gm, '')              // bullet markers
    .replace(/^\s*\d+\.\s+/gm, '')              // ordered markers
    .replace(/^\s*\|.*\|\s*$/gm, ' ')           // table rows
    .replace(/[*_~]{1,3}([^*_~]+)[*_~]{1,3}/g, '$1')  // bold/italic/strike
    .replace(/[*_~`>#|-]/g, ' ')                // stray markdown punctuation
    .replace(/\s+/g, ' ')                        // collapse whitespace
    .trim()
}

// ── interval composer (seconds ⇄ {value, unit}) ──
export const INTERVAL_UNITS: Array<{ key: string; label: string; secs: number }> = [
  { key: 'm', label: 'minutes', secs: 60 },
  { key: 'h', label: 'hours', secs: 3600 },
  { key: 'd', label: 'days', secs: 86400 },
]
export function secsToInterval(secs?: number | null): { value: number; unit: string } {
  const s = secs ?? 3600
  if (s % 86400 === 0) return { value: s / 86400, unit: 'd' }
  if (s % 3600 === 0) return { value: s / 3600, unit: 'h' }
  return { value: Math.max(1, Math.round(s / 60)), unit: 'm' }
}
export function intervalToSecs(value: number, unit: string): number {
  const u = INTERVAL_UNITS.find((x) => x.key === unit) ?? INTERVAL_UNITS[0]
  return Math.max(60, Math.round(value) * u.secs)
}

// Common cron starting points (label → expr).
export const CRON_PRESETS: Array<{ label: string; expr: string }> = [
  { label: 'Hourly', expr: '0 * * * *' },
  { label: 'Daily 9am', expr: '0 9 * * *' },
  { label: 'Weekdays 9am', expr: '0 9 * * 1-5' },
  { label: 'Weekly Mon', expr: '0 9 * * 1' },
  { label: 'Monthly 1st', expr: '0 9 1 * *' },
]
