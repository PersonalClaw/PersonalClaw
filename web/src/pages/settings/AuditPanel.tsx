import { useState } from 'react'
import { Search, RefreshCw, ShieldCheck, ShieldAlert, KeyRound, Loader2 } from 'lucide-react'
import { api, type SelEvent, type SelVerify } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { confirm } from '../../ui/dialog'
import { PanelHeader } from './settingsUI'
import { Button } from '../../ui/Button'
import { ListSkeleton } from '../../ui/ListScaffold'

const OUTCOME_TONE: Record<string, string> = {
  success: 'var(--color-success)', allowed: 'var(--color-success)',
  denied: 'var(--color-danger)', failure: 'var(--color-danger)',
  blocked: 'var(--color-danger)', refused: 'var(--color-danger)',
  not_triggered: 'var(--color-on-surface-low)', scanned: 'var(--color-on-surface-low)',
  needs_confirm: 'var(--color-warning)',
}
const FILTERS = [
  { key: 'all', label: 'All' }, { key: 'denied', label: 'Denials' },
  { key: 'success', label: 'Allowed' }, { key: 'redact', label: 'Redactions' },
]

/** Audit log — the live security-event log (SEL), a tamper-evident hash chain of
 *  every tool invocation, approval/denial, redaction, and config write. Read-only
 *  + chain-verify + key-rotate. */
export function AuditPanel() {
  const [filter, setFilter] = useState('all')
  const [q, setQ] = useState('')
  const [verify, setVerify] = useState<SelVerify | null>(null)

  // Live audit log — cached in-memory for instant in-app revisit, but NOT persisted
  // across reloads (it moves fast and freshness matters). `loading` drives the
  // refresh spinner; a mutation/manual refresh invalidates then revalidates.
  const { data: events, loading: busy, refresh } = useCachedData(
    'settings:audit', () => api.selEvents({ limit: 200 }).catch(() => [] as SelEvent[]), { persist: false },
  )
  const reload = () => { invalidateCache('settings:audit'); refresh() }

  const runVerify = async () => { setVerify(null); try { setVerify(await api.selVerify()) } catch { setVerify({ valid: false, error: 'verify failed' }) } }
  const rotate = async () => { if (!(await confirm({ title: 'Rotate the audit-log signing key?', body: 'Past entries stay verifiable under the old key.', confirmLabel: 'Rotate key' }))) return; await api.selRotate().catch(() => {}); reload() }

  if (!events) return <ListSkeleton rows={8} />
  const needle = q.trim().toLowerCase()
  const shown = events.filter((e) => {
    if (filter === 'denied' && !/denied|blocked|failure|refused/.test(e.outcome ?? '')) return false
    if (filter === 'success' && !/success|allowed/.test(e.outcome ?? '')) return false
    if (filter === 'redact' && !/redact/.test(`${e.event_type} ${e.operation}`)) return false
    if (needle && !`${e.event_type} ${e.operation} ${e.caller_identity} ${e.resources}`.toLowerCase().includes(needle)) return false
    return true
  })

  return (
    <div>
      <PanelHeader title="Audit log" hint="The tamper-evident security-event log — every tool call, approval, denial, and redaction, hash-chained." />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-pill bg-surface-container p-0.5">
          {FILTERS.map((f) => (
            <button key={f.key} type="button" onClick={() => setFilter(f.key)} className="rounded-pill px-3 h-7 text-[0.8125rem] transition-colors"
              style={f.key === filter ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>{f.label}</button>
          ))}
        </div>
        <div className="relative min-w-40 flex-1">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-low pointer-events-none" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by operation, caller, resource" aria-label="Filter audit log"
            className="h-9 w-full rounded-md bg-surface-high pl-8 pr-2 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        </div>
        <Button variant="secondary" size="sm" onClick={reload} disabled={busy}>{busy ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={14} />}</Button>
        <Button variant="secondary" size="sm" onClick={runVerify}><ShieldCheck size={14} /> Verify</Button>
        <Button variant="ghost" size="sm" onClick={rotate}><KeyRound size={14} /> Rotate</Button>
      </div>

      {verify && (
        <div className="mb-3 flex items-center gap-1.5 rounded-lg bg-surface-container px-3 py-2 text-[0.8rem]"
          style={{ color: verify.valid ? 'var(--color-success)' : 'var(--color-danger)' }}>
          {verify.valid ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
          {verify.valid ? `Chain intact${verify.count != null ? ` — ${verify.count} events verified` : ''}.` : `Chain broken${verify.broken_at ? ` at ${verify.broken_at}` : ''}${verify.error ? ` — ${verify.error}` : ''}.`}
        </div>
      )}

      {shown.length === 0 ? (
        <p className="py-6 text-center text-on-surface-low text-[0.82rem]">No matching events.</p>
      ) : (
        <div className="flex flex-col gap-1">
          {shown.map((e) => <EventRow key={e.event_id} ev={e} />)}
        </div>
      )}
    </div>
  )
}

function EventRow({ ev }: { ev: SelEvent }) {
  const [open, setOpen] = useState(false)
  const tone = OUTCOME_TONE[ev.outcome ?? ''] ?? 'var(--color-on-surface-low)'
  return (
    <div className="rounded-md bg-surface-container px-3 py-1.5">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 text-left text-[0.78rem]">
        <span className="w-14 shrink-0 font-mono text-[0.68rem]" style={{ color: tone }}>{ev.outcome || '—'}</span>
        <span className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low text-[0.66rem]">{ev.event_type}</span>
        <span className="min-w-0 flex-1 truncate text-on-surface">{ev.operation || ev.resources || '—'}</span>
        <span className="shrink-0 text-on-surface-low text-[0.66rem]">{fmtTime(ev.timestamp)}</span>
      </button>
      {open && (
        <div className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 border-t border-outline-variant/30 pt-1.5 text-[0.72rem]">
          <Kv k="caller" v={ev.caller_identity} /><Kv k="agent" v={ev.agent} />
          <Kv k="source" v={ev.source} /><Kv k="tool kind" v={ev.tool_kind} />
          {ev.resources && <Kv k="resources" v={ev.resources} span />}
          {ev.error && <Kv k="error" v={ev.error} span />}
        </div>
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
