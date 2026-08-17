import { useCallback, useEffect, useRef, useState } from 'react'
import { Search, RefreshCw, ShieldCheck, ShieldAlert, KeyRound, Loader2, Download, SlidersHorizontal } from 'lucide-react'
import { api, type AuditFilters, type SelEvent, type SelVerify } from '../../lib/api'
import { invalidateCache } from '../../lib/useCachedData'
import { confirm } from '../../ui/dialog'
import { InvestigateButton } from '../../ui/InvestigateButton'
import { PanelHeader } from './settingsUI'
import { Button } from '../../ui/Button'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { Field, TextInput, DateInput } from '../../ui/forms'

const OUTCOME_TONE: Record<string, string> = {
  success: 'var(--color-success)', allowed: 'var(--color-success)', approved: 'var(--color-success)',
  completed: 'var(--color-success)', ok: 'var(--color-success)',
  denied: 'var(--color-danger)', failure: 'var(--color-danger)', failed: 'var(--color-danger)',
  blocked: 'var(--color-danger)', refused: 'var(--color-danger)', rejected: 'var(--color-danger)',
  error: 'var(--color-danger)',
  not_triggered: 'var(--color-on-surface-low)', scanned: 'var(--color-on-surface-low)',
  needs_confirm: 'var(--color-warning)',
}

// Outcome presets. Each writes the SERVER-side `outcome` filter, so the pill and the
// pagination agree — the old client-side pills filtered the page AFTER it was fetched,
// which made "Load more" fetch rows the pill then hid, and the count meaningless.
// Each label is therefore one real substring of an outcome value, not a regex union.
const OUTCOME_PRESETS = [
  { key: '', label: 'All' },
  { key: 'denied', label: 'Denied' },
  { key: 'failed', label: 'Failed' },
] as const

const PAGE_SIZE = 50

/** One JSONL line per event — the export format. Pure + exported so the round-trip is
 *  testable: `toJsonl(rows).trim().split('\n').map(JSON.parse)` must equal `rows`.
 *
 *  Credential safety is NOT re-implemented here. These rows arrive already redacted by
 *  `/api/security/audit` (`sel.redact_event`), so the export can only ever contain what
 *  the table already shows — one redaction definition, server-side, for both surfaces. */
export function toJsonl(events: SelEvent[]): string {
  return events.map((e) => JSON.stringify(e)).join('\n') + (events.length ? '\n' : '')
}

function downloadJsonl(events: SelEvent[]): void {
  const url = URL.createObjectURL(new Blob([toJsonl(events)], { type: 'application/x-ndjson' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `personalclaw-audit-${new Date().toISOString().slice(0, 10)}.jsonl`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** Audit log — "what did my agent do". The live security-event log (SEL): a
 *  tamper-evident hash chain of every tool invocation, approval/denial, redaction, and
 *  config write. Server-side filters, cursor pagination, per-row integrity, chain-verify,
 *  credential-safe JSONL export, key-rotate. */
export function AuditPanel() {
  const [filters, setFilters] = useState<AuditFilters>({})
  const [showMore, setShowMore] = useState(false)
  const [events, setEvents] = useState<SelEvent[] | null>(null)
  const [cursor, setCursor] = useState('')
  const [truncated, setTruncated] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [verify, setVerify] = useState<SelVerify | null>(null)

  // Every fetch is stamped; only the newest one may write state. Without this, a slow
  // page-1 response landing after a filter change would overwrite the filtered list
  // with stale rows — on an audit surface that reads as "these are your events" while
  // showing someone else's query.
  const runId = useRef(0)

  const load = useCallback(async (opts: { cursor?: string; filters: AuditFilters }) => {
    const id = ++runId.current
    setBusy(true)
    try {
      const page = await api.auditEvents({ limit: PAGE_SIZE, cursor: opts.cursor, filters: opts.filters })
      if (id !== runId.current) return
      setEvents((prev) => (opts.cursor && prev ? [...prev, ...page.events] : page.events))
      setCursor(page.next_cursor)
      setTruncated(page.truncated)
      setError(null)
    } catch (e) {
      if (id !== runId.current) return
      // An audit log that cannot be read is the one list where "nothing happened" is the
      // most dangerous possible lie. Never swallow to an empty array.
      setError(e)
    } finally {
      if (id === runId.current) setBusy(false)
    }
  }, [])

  // Debounced refetch on any filter change — the text fields would otherwise fire a
  // request per keystroke against a hash-checking endpoint.
  useEffect(() => {
    const t = setTimeout(() => { void load({ filters }) }, 300)
    return () => clearTimeout(t)
  }, [filters, load])

  const setFilter = (k: keyof AuditFilters, v: string) => setFilters((f) => ({ ...f, [k]: v }))
  const reload = () => { setEvents(null); void load({ filters }) }
  const loadMore = () => { void load({ cursor, filters }) }

  const runVerify = async () => {
    setVerify(null)
    try { setVerify(await api.auditVerify()) } catch { setVerify({ ok: false, checked: 0, error: 'verify failed' }) }
  }
  const rotate = async () => {
    if (!(await confirm({ title: 'Rotate the audit-log signing key?', body: 'Past entries stay verifiable under the old key.', confirmLabel: 'Rotate key' }))) return
    await api.selRotate().catch(() => {})
    invalidateCache('settings:audit-verify')
    reload()
  }

  if (!events && error) return <LoadError what="audit log" error={error} onRetry={reload} />
  if (!events) return <ListSkeleton rows={8} what="audit log" />

  const broken = events.filter((e) => e.integrity_ok === false).length

  return (
    <div>
      <PanelHeader title="Audit log" hint="What your agent did — every tool call, approval, denial, and redaction, hash-chained and tamper-evident." />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-pill bg-surface-container p-0.5" role="group" aria-label="Filter by outcome">
          {OUTCOME_PRESETS.map((f) => {
            const active = (filters.outcome ?? '') === f.key
            return (
              <button key={f.key || 'all'} type="button" onClick={() => setFilter('outcome', f.key)} aria-pressed={active}
                className="rounded-pill px-3 h-7 text-[0.8125rem] transition-colors"
                style={active ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>{f.label}</button>
            )
          })}
        </div>
        <div className="min-w-40 flex-1">
          <TextInput value={filters.operation ?? ''} onChange={(v) => setFilter('operation', v)} placeholder="Filter by operation" ariaLabel="Filter by operation"
            size="md" surface="high" leadingIcon={<Search size={14} />} />
        </div>
        <Button variant="secondary" size="sm" onClick={() => setShowMore((s) => !s)} aria-expanded={showMore}><SlidersHorizontal size={14} /> Filters</Button>
        {/* Icon-only, so it needs its own name — `title` is the kit's convention for a bare glyph. */}
        <Button variant="secondary" size="sm" onClick={reload} disabled={busy} title={busy ? 'Refreshing the audit log' : 'Refresh the audit log'}>{busy ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={14} />}</Button>
        <Button variant="secondary" size="sm" onClick={runVerify}><ShieldCheck size={14} /> Verify</Button>
        {/* `!events.length` is a state the user can fix (clear a filter, or wait for activity),
            not an in-flight gate — so it carries a reason, which keeps the tab stop and lets a
            keyboard user land on it and hear why it is off. */}
        <Button variant="secondary" size="sm" onClick={() => downloadJsonl(events)} disabled={!events.length}
          disabledReason="Nothing to export — no events match the current filters"
          title={`Export the ${events.length} listed events as JSONL (credential-safe)`}><Download size={14} /> Export</Button>
        <Button variant="ghost" size="sm" onClick={rotate}><KeyRound size={14} /> Rotate</Button>
      </div>

      {showMore && (
        <div className="mb-3 grid gap-3 rounded-lg bg-surface-container p-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Caller"><TextInput value={filters.caller ?? ''} onChange={(v) => setFilter('caller', v)} placeholder="session key" size="md" surface="high" /></Field>
          <Field label="Downstream service"><TextInput value={filters.downstream_service ?? ''} onChange={(v) => setFilter('downstream_service', v)} placeholder="MCP server" size="md" surface="high" /></Field>
          <Field label="From"><DateInput value={filters.since ?? ''} onChange={(v) => setFilter('since', v)} /></Field>
          <Field label="To"><DateInput value={filters.until ?? ''} onChange={(v) => setFilter('until', v)} /></Field>
        </div>
      )}

      {verify && (
        <div className="mb-3 flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-2 text-[0.8125rem]"
          style={{ color: verify.ok ? 'var(--color-success)' : 'var(--color-danger)' }}>
          {verify.ok ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
          {verify.ok
            ? `Chain intact — ${verify.checked} events verified.`
            : `Chain broken — ${verify.tampered ?? '?'} of ${verify.checked} events altered${verify.error ? ` (${verify.error})` : ''}.`}
        </div>
      )}

      {/* The per-row verdict, summarized. `integrity_ok === false` is the server's own
          HMAC recheck of that record, so this counts real breaks in what is on screen. */}
      {broken > 0 && (
        <div role="alert" className="mb-3 flex items-center gap-1.5 rounded-lg px-3 py-2 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}>
          <ShieldAlert size={14} />
          {broken === 1 ? '1 listed event fails its integrity check — it was altered on disk.' : `${broken} listed events fail their integrity check — they were altered on disk.`}
        </div>
      )}

      {events.length === 0 ? (
        <p className="py-6 text-center text-on-surface-low text-[0.8125rem]">No matching events.</p>
      ) : (
        <div className="flex flex-col gap-1">
          {events.map((e) => <EventRow key={e.event_id} ev={e} />)}
        </div>
      )}

      <div className="mt-3 flex flex-col items-center gap-1.5">
        {cursor && (
          <Button variant="secondary" size="sm" onClick={loadMore} disabled={busy}>
            {busy ? <><Loader2 size={14} className="animate-spin" /> Loading</> : 'Load older events'}
          </Button>
        )}
        {!cursor && events.length > 0 && (
          <p className="text-on-surface-low text-[0.75rem]">
            {truncated ? `End of the ${events.length} most recent matching events — older entries exist beyond the scanned window.` : `All ${events.length} matching events shown.`}
          </p>
        )}
      </div>
    </div>
  )
}

function EventRow({ ev }: { ev: SelEvent }) {
  const [open, setOpen] = useState(false)
  const tone = OUTCOME_TONE[ev.outcome ?? ''] ?? 'var(--color-on-surface-low)'
  const tampered = ev.integrity_ok === false
  return (
    <div className="rounded-md px-3 py-1.5" style={tampered
      ? { background: 'color-mix(in srgb, var(--color-danger) 10%, var(--color-surface-container))', boxShadow: 'inset 2px 0 0 var(--color-danger)' }
      : { background: 'var(--color-surface-container)' }}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center gap-2 text-left text-[0.75rem]">
        <span className="w-14 shrink-0 font-mono text-[0.75rem]" style={{ color: tone }}>{ev.outcome || '—'}</span>
        <span className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low text-[0.75rem]">{ev.event_type}</span>
        <span className="min-w-0 flex-1 truncate text-on-surface">{ev.operation || ev.resources || '—'}</span>
        {/* Not colour alone: the glyph + its accessible label carry the meaning too. */}
        {tampered && <ShieldAlert size={13} className="shrink-0" style={{ color: 'var(--color-danger)' }} aria-label="Integrity check failed — this record was altered" />}
        <span className="shrink-0 text-on-surface-low text-[0.75rem]">{fmtTime(ev.timestamp)}</span>
      </button>
      {open && (
        <>
          {tampered && (
            <p className="mt-1.5 text-[0.75rem]" style={{ color: 'var(--color-danger)' }}>
              This record's HMAC does not match its contents — it was modified after it was written.
            </p>
          )}
          <div className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 border-t border-outline-variant/30 pt-1.5 text-[0.75rem]">
            <Kv k="caller" v={ev.caller_identity} /><Kv k="agent" v={ev.agent} />
            <Kv k="source" v={ev.source} /><Kv k="tool kind" v={ev.tool_kind} />
            <Kv k="downstream" v={ev.downstream_service} />
            {ev.resources && <Kv k="resources" v={ev.resources} span />}
            {ev.error && <Kv k="error" v={ev.error} span />}
          </div>
          {/* Investigate (plan 60): opens a chat with this entry AND the others from
              the same approval flow, so one decision reads as one story. */}
          <div className="mt-1 flex justify-end">
            <InvestigateButton kind="audit_event" id={ev.event_id} backLink="#/settings/security" size={28} />
          </div>
        </>
      )}
    </div>
  )
}
function Kv({ k, v, span }: { k: string; v?: string; span?: boolean }) {
  if (!v) return null
  return <div className={span ? 'col-span-2' : ''}><span className="text-on-surface-low">{k}: </span><span className="font-mono text-on-surface">{v}</span></div>
}
function fmtTime(iso?: string): string {
  if (!iso) return ''
  const m = iso.match(/[T ](\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : iso.slice(11, 19)
}
