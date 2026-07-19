import { Repeat, CalendarClock, Calendar, Bot, FileCode2, TerminalSquare, CheckCircle2, XCircle, Circle, Rocket, Clock } from 'lucide-react'
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
export function modeMeta(m?: ScheduleExecMode): ModeMeta { return EXEC_MODES.find((x) => x.key === m) ?? EXEC_MODES[0] }

/** Derive the schedule kind from the wire job (cron_expr > every_secs > at). */
export function deriveKind(j: ScheduleJob): ScheduleKind {
  if (j.cron_expr) return 'cron'
  if (j.every_secs != null) return 'every'
  return 'at'
}
/** Derive the execution mode (script > command > agent). */
export function deriveMode(j: ScheduleJob): ScheduleExecMode {
  if (j.script) return 'script'
  if (j.command) return 'command'
  return 'agent'
}

// ── last-run status dot ──
export interface StatusMeta { label: string; tone: string; icon: LucideIcon }
export function statusMeta(s?: string | null): StatusMeta {
  // job.last_status is "ok"/"error"; run.status is "success"/"failure"/"timeout"/"launched".
  if (s === 'ok' || s === 'success') return { label: 'ok', tone: 'var(--color-ok)', icon: CheckCircle2 }
  if (s === 'error' || s === 'failure') return { label: 'error', tone: 'var(--color-danger)', icon: XCircle }
  if (s === 'timeout') return { label: 'timed out', tone: 'var(--color-danger)', icon: Clock }
  // "launched": started a background turn — honest "started ≠ succeeded" (T7).
  // Neutral tone, NOT ok-green: a green tick would imply the work succeeded.
  if (s === 'launched') return { label: 'launched', tone: 'var(--color-info)', icon: Rocket }
  return { label: 'never run', tone: 'var(--color-on-surface-low)', icon: Circle }
}

// ── time helpers ──
export function relFuture(ts?: number | null): string {
  if (!ts) return ''
  const s = ts - Date.now() / 1000
  if (s < 0) return 'overdue'
  if (s < 60) return 'in <1m'
  if (s < 3600) return `in ${Math.floor(s / 60)}m`
  if (s < 86400) return `in ${Math.floor(s / 3600)}h`
  return `in ${Math.floor(s / 86400)}d`
}
export function relPast(ts?: number | null): string {
  if (!ts) return 'never'
  const s = Date.now() / 1000 - ts
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
export function absTime(ts?: number | null): string {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
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
