import { useCallback, useEffect, useRef, useState } from 'react'
import { Search, RefreshCw, ShieldCheck, ShieldAlert, Archive, Download, SlidersHorizontal } from 'lucide-react'
import { api, type AuditFilters, type AuditPage, type SelEvent, type SelVerify } from '../../lib/api'
import { invalidateKeys } from '../../lib/data'
import { confirm } from '../../ui/dialog'
import { InvestigateButton } from '../../ui/InvestigateButton'
import { PanelHeader } from './settingsUI'
import { Button } from '../../ui/Button'
import { ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { Field, TextInput, DateInput } from '../../ui/forms'
import { notify } from '../../app/appSdk'

// 🔴 THIS PANEL NO LONGER KNOWS ANY OUTCOME WORDS. It used to hold a fourteen-entry
// `outcome -> colour` map, hand-maintained against a log whose writers emit 66 distinct outcome
// words — and it had already drifted from the server's own table: `not_found` is a member of the
// `failed` family and had no entry here, so a record the Failed pill calls a failure rendered in
// neutral grey. Five success words (`executed`, `auto_approved`, `granted`, `enabled`, `disabled`)
// were missing the same way. Measured live: `disabled`, `not_found`, `fail_open`, `expired` and
// `open` all rendered grey.
//
// `sel.audit_outcome_tone` stamps every row with the tone its OWN family matcher assigned, so the
// pill and the colour can no longer disagree about the same record. What is left here is a
// tone -> design-token map, which is this file's business; the vocabulary is not.
const TONE_COLOR: Record<string, string> = {
  danger: 'var(--color-danger)',
  warning: 'var(--color-warning)',
  success: 'var(--color-success)',
  neutral: 'var(--color-on-surface-low)',
}

// 🔴 THE PILLS NO LONGER DEFINE THE VOCABULARY THEY FILTER ON. They used to: two entries,
// one literal substring each (`denied`, `failed`), against a log whose writers emit fourteen
// different outcome words. Measured across `src/personalclaw`:
//
//     denied 163 · rejected 24 · blocked 5 · refused 1     "Denied" matched 163 of 193
//     failure 23 · error 21 · failed 4                     "Failed" matched 4 of 48
//
// and confirmed live before the fix: a real `DELETE /api/terminal/sessions/…` recorded
// `outcome=error` was INVISIBLE to the Failed pill (`outcome=failed` → 0 rows,
// `outcome=error` → 1). On an audit surface a filter that silently omits matching records is
// the worst available failure: the operator reads it as "nothing happened".
//
// `sel.AUDIT_OUTCOME_FAMILIES` owns the families now and the page ships them, so a word added
// to the vocabulary reaches this pill without anyone editing the dashboard. The filter stays
// SERVER-side (the pill and the pagination cursor must agree — the old client-side pills made
// "Load more" fetch rows the pill then hid); the server matches a family ANY-OF.
const ALL_PRESET = { key: '', label: 'All', values: [] as string[] }

const PAGE_SIZE = 50
/** How many budget-exhausted (zero-row) pages one click may chase before handing control back.
 *  A ceiling, not a target: the server's budget is 5,000 lines, so this bounds one click at
 *  ~100k lines of log rather than the whole chain. Past it the panel says where it stopped and
 *  the operator decides — an unbounded auto-follow is how a filtered read of a million-entry log
 *  becomes an unkillable request.
 *
 *  Named "follows", not the graph word for the same idea: `oneLitSetImplementation.test.ts`
 *  censuses the tree for the memory graph's lit-set traversal by its loop shape, and it is right
 *  to — a second file wearing that shape reads as a second traversal. That census greps RAW source,
 *  comments included, so the shape must not be spelled out here either. This walks a cursor through
 *  an append-only file: it visits nothing twice and has no frontier. */
const MAX_EMPTY_FOLLOWS = 20

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
 *  tamper-evident hash chain of every tool invocation, API access, and approval/denial.
 *  Server-side filters, cursor pagination, per-row integrity, chain-verify, credential-safe
 *  JSONL export, archive-and-restart.
 *
 *  It does NOT contain redaction events, and this used to claim it did — measured across
 *  `src/personalclaw`, no writer emits `event_type="redaction"`; the words are `api_access`,
 *  `tool_invocation` and thirteen config/approval kinds. Redaction is something the log has
 *  DONE TO it on the way out (`sel.redact_event`), not a thing it records. The panel had a
 *  Redactions tab that could therefore never match a row (issue 535); the tab is gone and so is
 *  the promise. */
/** How much of the chain a verdict actually covers, and whether anything was left out.
 *
 *  🔴 THE VERDICT USED TO OVERSTATE ITS SCOPE. `sel.verify_integrity` defaults to a 5000-entry
 *  window — the live tamper-detection window, added because a full walk "had reached >1M entries,
 *  taking 20s+ and hanging the audit UI" (its own docstring) — and the handler reports that as
 *  `windowed: true`. **Nothing in the SPA read the flag** (`git grep windowed web/src` found only the
 *  unrelated `WindowedList`), so a capped check rendered as "Chain intact — 5000 events verified",
 *  which on a tamper-evidence surface reads as *the chain is intact*, full stop.
 *
 *  Same ruling as the usage panel's unpriced total: "cannot present as complete" is not "do not
 *  present" — state the scope. And state it in BOTH directions: with 43 events in the log the window
 *  never bit, and calling that "the last 43" would understate a complete answer just as badly. That
 *  is why the server now sends `window` (the cap it applied) and not just `windowed` (that a cap
 *  existed) — the boundary case where the log is exactly the window size resolves as "capped", which
 *  is the safe direction to be wrong in. */
export function verifiedScope(v: { checked: number; windowed?: boolean; window?: number | null }): string {
  const n = v.checked.toLocaleString()
  return capped(v) ? `the last ${n} events` : `all ${n} events`
}

/** Did the cap actually leave entries unchecked? `windowed` alone cannot answer this. */
export function capped(v: { checked: number; windowed?: boolean; window?: number | null }): boolean {
  return !!v.windowed && typeof v.window === 'number' && v.checked >= v.window
}

export function AuditPanel() {
  const [filters, setFilters] = useState<AuditFilters>({})
  const [showMore, setShowMore] = useState(false)
  const [events, setEvents] = useState<SelEvent[] | null>(null)
  const [cursor, setCursor] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [verify, setVerify] = useState<SelVerify | null>(null)
  // Shipped by the endpoint (`sel.AUDIT_OUTCOME_FAMILIES`). Empty until the first page lands,
  // so the pill row renders "All" alone rather than a stale local guess at the vocabulary.
  const [families, setFamilies] = useState<AuditPage['outcome_families']>([])

  // Every fetch is stamped; only the newest one may write state. Without this, a slow
  // page-1 response landing after a filter change would overwrite the filtered list
  // with stale rows — on an audit surface that reads as "these are your events" while
  // showing someone else's query.
  const runId = useRef(0)

  const load = useCallback(async (opts: { cursor?: string; filters: AuditFilters }) => {
    const id = ++runId.current
    setBusy(true)
    try {
      // A page whose scan budget ran out before it found anything is a real answer ("still
      // looking, here is where I stopped") and the operator can resume from its cursor — but a
      // CLICK that returns zero rows looks like a broken button. Measured on a 63,653-entry log:
      // `outcome=not_found` (one matching row) needs 13 budgeted pages, so one click was thirteen
      // clicks. Chain the empty ones here, bounded, so a click either shows rows or ends.
      let page = await api.auditEvents({ limit: PAGE_SIZE, cursor: opts.cursor, filters: opts.filters })
      const rows = [...page.events]
      let follows = 0
      while (rows.length === 0 && page.truncated && page.next_cursor && follows++ < MAX_EMPTY_FOLLOWS) {
        if (id !== runId.current) return
        page = await api.auditEvents({ limit: PAGE_SIZE, cursor: page.next_cursor, filters: opts.filters })
        rows.push(...page.events)
      }
      if (id !== runId.current) return
      setEvents((prev) => (opts.cursor && prev ? [...prev, ...rows] : rows))
      // Every page carries them; taking them on each load means a family widened on the
      // backend appears without a reload, and an older payload cannot blank the pills.
      if (page.outcome_families?.length) setFamilies(page.outcome_families)
      setCursor(page.next_cursor)
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
  const presets = [ALL_PRESET, ...families]
  const reload = () => { setEvents(null); void load({ filters }) }
  const loadMore = () => { void load({ cursor, filters }) }

  const runVerify = async () => {
    setVerify(null)
    try { setVerify(await api.auditVerify()) } catch { setVerify({ ok: false, checked: 0, error: 'verify failed' }) }
  }
  // NOT offered as a button on purpose. `verify_integrity(max_entries=None)` is the exhaustive walk the
  // window exists to avoid, and `personalclaw security verify` already performs it — its own comment
  // calls it "an explicit offline audit". A button here would re-create the 20s+ hang the window was
  // added to fix, so the panel NAMES the command instead: the same choice `DurabilityPanel` makes for
  // `personalclaw restore --replace`.
  // 🔑 A CONFIRMED ACTION THAT FAILED SILENTLY, and the confirmed-delete ratchet could not see it: that
  // sweep matches `api.(delete|purge|revoke)*`, and this one is called `selRotate`. The user confirmed
  // archiving the audit log, the request failed, and the panel invalidated its verify cache and
  // reloaded as though it had worked — so the only signal was that nothing changed.
  //
  // `notify` rather than this file's `setError`: that state only renders through `LoadError` while
  // `!events`, so setting it after a successful load shows nothing at all.
  const rotate = async () => {
    if (!(await confirm({ title: 'Archive the audit log and start a new chain?', body: 'The existing entries move to a timestamped archive file next to the log — they leave the dashboard verify and browse surface. The signing key is unchanged.', confirmLabel: 'Archive & reset' }))) return
    try {
      const res = await api.selRotate()
      const archived = res.archive_path ? res.archive_path.split(/[/\\]/).pop() : ''
      notify(archived ? `Audit log archived to ${archived} — a fresh chain has started.` : 'Audit log reset — a fresh chain has started.', 'success')
    }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'the request failed'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch { /* raw text */ }
      notify(`Couldn't archive the audit log: ${msg}`, 'error')
      return   // nothing archived, so there is nothing to invalidate or reload
    }
    invalidateKeys('settings:audit-verify')
    reload()
  }

  if (!events && error) return <LoadError what="audit log" error={error} onRetry={reload} />
  if (!events) return <ListSkeleton rows={8} what="audit log" />

  const broken = events.filter((e) => e.integrity_ok === false).length

  return (
    <div>
      <PanelHeader title="Audit log" hint="What your agent did — every tool call, API access, approval and denial, hash-chained and tamper-evident." />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-pill bg-surface-container p-0.5" role="group" aria-label="Filter by outcome">
          {presets.map((f) => {
            // The pill sends the whole family, comma-joined; the server matches any-of. Comparing
            // against the SENT value (not the key) keeps the pressed state honest when a filter
            // arrives from anywhere else — a key comparison would light up the wrong pill.
            const sent = f.values.join(',')
            const active = (filters.outcome ?? '') === sent
            return (
              <button key={f.key || 'all'} type="button" onClick={() => setFilter('outcome', sent)} aria-pressed={active}
                title={f.values.length ? `Outcomes: ${f.values.join(', ')}` : undefined}
                data-type="body-s" className="rounded-pill px-3 h-7 transition-colors"
                style={active ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>{f.label}</button>
            )
          })}
        </div>
        <div className="min-w-40 flex-1">
          <TextInput value={filters.operation ?? ''} onChange={(v) => setFilter('operation', v)} placeholder="Filter by operation" ariaLabel="Filter by operation"
            size="md" surface="high" leadingIcon={<Search size={14} />} />
        </div>
        {/* `ariaExpanded`, not `aria-expanded` — see NotificationRulesMatrix: `ui/Button` declares
            camelCase aria props and spreads no rest, and a dashed JSX attribute name is not
            excess-property-checked, so the hyphenated form compiled and vanished. */}
        <Button variant="secondary" size="sm" onClick={() => setShowMore((s) => !s)} ariaExpanded={showMore}><SlidersHorizontal size={14} /> Filters</Button>
        {/* Icon-only, so it needs its own name — `title` is the kit's convention for a bare glyph. */}
        <Button variant="secondary" size="sm" onClick={reload} loading={busy} title={busy ? 'Refreshing the audit log' : 'Refresh the audit log'}><RefreshCw size={14} /></Button>
        <Button variant="secondary" size="sm" onClick={runVerify}><ShieldCheck size={14} /> Verify</Button>
        {/* `!events.length` is a state the user can fix (clear a filter, or wait for activity),
            not an in-flight gate — so it carries a reason, which keeps the tab stop and lets a
            keyboard user land on it and hear why it is off. */}
        <Button variant="secondary" size="sm" onClick={() => downloadJsonl(events)} disabled={!events.length}
          disabledReason="Nothing to export — no events match the current filters"
          title={`Export the ${events.length} listed events as JSONL (credential-safe)`}><Download size={14} /> Export</Button>
        <Button variant="ghost" size="sm" onClick={rotate}><Archive size={14} /> Rotate</Button>
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
        <div data-type="body-s" className="mb-3 rounded-lg bg-surface-container px-3 py-2">
          <div className="flex items-center gap-1.5"
            style={{ color: verify.ok ? 'var(--color-success)' : 'var(--color-danger)' }}>
            {verify.ok ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
            {verify.ok
              ? `Chain intact — ${verifiedScope(verify)} verified.`
              : `Chain broken — ${verify.tampered ?? '?'} of ${verifiedScope(verify)} altered${verify.error ? ` (${verify.error})` : ''}.`}
          </div>
          {capped(verify) && (
            <p data-type="caption" className="mt-1 text-on-surface-low">
              Older entries were not checked — this is the live tamper-detection window.
              {' '}<code className="font-mono">personalclaw security verify</code> walks the whole log
              offline, which can take a while on a long one.
            </p>
          )}
        </div>
      )}

      {/* The per-row verdict, summarized. `integrity_ok === false` is the server's own
          HMAC recheck of that record, so this counts real breaks in what is on screen. */}
      {broken > 0 && (
        <div role="alert" data-type="body-s" className="mb-3 flex items-center gap-1.5 rounded-lg px-3 py-2"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}>
          <ShieldAlert size={14} />
          {broken === 1 ? '1 listed event fails its integrity check — it was altered on disk.' : `${broken} listed events fail their integrity check — they were altered on disk.`}
        </div>
      )}

      {events.length === 0 ? (
        <p data-type="body-s" className="py-6 text-center text-on-surface-low">
          {/* Two different answers that used to read identically. "Nothing matches in the whole
              log" is a conclusion; "nothing matched in the part I have read so far" is not, and on
              an audit surface presenting the second as the first is the failure that matters. */}
          {cursor ? 'No matches yet in the events scanned so far — keep looking for older ones.' : 'No matching events.'}
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          {events.map((e) => <EventRow key={e.event_id} ev={e} />)}
        </div>
      )}

      <div className="mt-3 flex flex-col items-center gap-1.5">
        {cursor && (
          <Button variant="secondary" size="sm" onClick={loadMore} loading={busy} loadingLabel="Loading">Load older events
          </Button>
        )}
        {/* No cursor means the walk reached the START of the log, so this is the whole answer.
            It did not used to be: the scan window was a wall at the newest 50,000 entries, and
            this line said "older entries exist beyond the scanned window" with no way to reach
            them — measured at 13,653 unreachable rows on a 63,653-entry log (issue 593). The
            budget now hands back a cursor instead of ending the walk, so a missing cursor is a
            real end and `truncated` can no longer be true here. */}
        {!cursor && events.length > 0 && (
          <p data-type="caption" className="text-on-surface-low">All {events.length} matching events shown.</p>
        )}
      </div>
    </div>
  )
}

function EventRow({ ev }: { ev: SelEvent }) {
  const [open, setOpen] = useState(false)
  // The server's verdict on this row, not a lookup by word. An unclassified outcome arrives as
  // `neutral`, which is the honest reading — inventing a colour for a word nobody classified is
  // how the audit log would come to assert a verdict no one decided.
  const tone = TONE_COLOR[ev.outcome_tone ?? 'neutral'] ?? TONE_COLOR.neutral
  const tampered = ev.integrity_ok === false
  return (
    <div className="rounded-md px-3 py-1.5" style={tampered
      ? { background: 'color-mix(in srgb, var(--color-danger) 16%, var(--color-surface-container))' }
      : { background: 'var(--color-surface-container)' }}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} data-type="caption" className="flex w-full items-center gap-2 text-left">
        <span data-type="caption" className="w-14 shrink-0 font-mono" style={{ color: tone }}>{ev.outcome || '—'}</span>
        <span data-type="caption" className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low">{ev.event_type}</span>
        <span className="min-w-0 flex-1 truncate text-on-surface">{ev.operation || ev.resources || '—'}</span>
        {/* Not colour alone: the glyph + its accessible label carry the meaning too. */}
        {tampered && <ShieldAlert size={13} className="shrink-0" style={{ color: 'var(--color-danger)' }} aria-label="Integrity check failed — this record was altered" />}
        <span data-type="caption" className="shrink-0 text-on-surface-low">{fmtTime(ev.timestamp)}</span>
      </button>
      {open && (
        <>
          {tampered && (
            <p data-type="caption" className="mt-1.5" style={{ color: 'var(--color-danger)' }}>
              This record's HMAC does not match its contents — it was modified after it was written.
            </p>
          )}
          <div data-type="caption" className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 border-t border-outline-variant/30 pt-1.5">
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
