import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { Check, Ban, LayoutDashboard, RefreshCw, ShieldCheck, CheckCheck, Smartphone } from 'lucide-react'
import { api, type PendingApproval } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { useChatSocket } from '../../lib/useChatSocket'
import { ApprovalPrompt } from '../../ui/ApprovalPrompt'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import type { RouteProps } from '../../app/useQueryState'
import { InboxSection, RecentSection, RunningLoopsSection, TasksSection } from './CompanionSections'

/** `#/companion` — the phone control surface, approvals first.
 *
 *  A run that is blocked on a permission decision is the one thing that genuinely cannot wait
 *  until the owner is back at a desk, so the phone route ships that decision FIRST and by
 *  itself and stays at the TOP of the column. `MC-6` adds the rest of the attention path
 *  underneath it — running loops, tasks, inbox, recent notifications — in
 *  `CompanionSections.tsx`; the ordering is the priority order and is not negotiable, because
 *  a blocked run is the only row on this page that another person is waiting on.
 *
 *  No NavRail, no shell chrome: App.tsx returns this route full-screen (like `#/onboarding`)
 *  so the whole viewport belongs to the decision. It is a normal hash route, so the URL
 *  doctrine holds — `#/companion` is addressable, bookmarkable, and installable as a phone
 *  start_url, and per-approval focus will ride `?approval=<id>` when push lands.
 *
 *  Reachable at any width on purpose. `useIsMobile` is a `max-width` media query, not a touch
 *  test, so gating the route on it would make the surface undebuggable from a desktop browser
 *  and would still be wrong for a tablet or a keyboard-only phone user. The layout is a
 *  single centered column that simply reads well narrow.
 */
export function CompanionPage({ navigate }: RouteProps) {
  // Live data, never persisted to sessionStorage: a stale approval is a dangerous thing to
  // paint. The queue re-reads on every WS approval event and on manual refresh.
  const { data, loading, error, refresh } = useQuery<PendingApproval[]>(
    'companion:approvals', () => api.approvals())
  // Optimistically resolved ids — the row leaves immediately, and comes BACK if the POST
  // failed (with the failure announced), because silently dropping a permission prompt would
  // leave the user believing they answered it.
  const [resolved, setResolved] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const busyRef = useRef(busy)
  busyRef.current = busy

  // 🪤 THE FETCHED LIST IS AUTHORITATIVE — RECONCILE THE OPTIMISTIC HIDE AGAINST IT.
  // Found by driving the live gateway: the hidden-id set was never pruned, so a queue the
  // backend was still serving rendered as "Nothing waiting on you". On an approvals surface a
  // silently hidden prompt is a denial the user never made, and it is also the exact lie this
  // route exists not to tell. So on every fetch, drop every hidden id whose POST has already
  // SETTLED; an approval the server still lists comes back. Ids whose POST is still in flight
  // stay hidden (that is what the optimistic hide is for), which is why this reads `busy`
  // through a ref and depends on `data` alone — depending on `busy` would un-hide the row the
  // instant the POST returned, before the fetch that confirms it had landed.
  useEffect(() => {
    if (data === undefined) return
    setResolved((s) => {
      const next = new Set([...s].filter((id) => busyRef.current.has(id)))
      return next.size === s.size ? s : next
    })
  }, [data])

  const onWs = useCallback((m: { type: string }) => {
    if (m.type === 'approval' || m.type === 'approval_resolved') refresh()
  }, [refresh])
  useChatSocket(onWs)

  const act = async (ap: PendingApproval, action: 'approve' | 'reject') => {
    setBusy((s) => new Set(s).add(ap.id))
    setResolved((s) => new Set(s).add(ap.id))
    try {
      await api.resolveApproval(ap.id, action)
    } catch (e) {
      setResolved((s) => { const n = new Set(s); n.delete(ap.id); return n })
      // `api.*` rejects with an ApiError whose message is already the sentence a user should
      // read (lib/errText). The toast host announces it assertively.
      window.dispatchEvent(new CustomEvent('ne:toast', {
        detail: { level: 'error', message: `Couldn't ${action} ${ap.tool} — ${(e as Error)?.message || 'the gateway did not respond'}` },
      }))
    } finally {
      setBusy((s) => { const n = new Set(s); n.delete(ap.id); return n })
      refresh()
    }
  }

  const pending = (data ?? []).filter((a) => !resolved.has(a.id))

  return (
    <div className="h-full overflow-y-auto" style={{ background: 'var(--color-canvas)' }}>
      <div className="mx-auto flex max-w-[42rem] flex-col gap-xl px-l py-l">
        <header className="flex items-center justify-between gap-s">
          <div className="min-w-0">
            <h1 data-type="headline-s" className="text-on-surface">Companion</h1>
            <p data-type="body-m" className="text-on-surface-low">Approve what your agent is waiting on.</p>
          </div>
          <IconButton icon={RefreshCw} label="Refresh approvals" onClick={refresh} size={40} iconSize={18} />
        </header>

        <section aria-labelledby="companion-approvals-heading" className="flex flex-col gap-s">
          <h2 id="companion-approvals-heading" data-type="title-m" className="flex items-center gap-s text-on-surface">
            <ShieldCheck size={16} aria-hidden style={{ color: 'var(--color-warn)' }} />
            Approvals{pending.length > 0 ? ` (${pending.length})` : ''}
          </h2>
          {/* Order matters: `data === undefined` is true for the loading, error AND empty
              branches, so the error test comes first or it is unreachable. A failed fetch on
              an APPROVALS surface must never fall through to "all clear" — that would tell
              the user nothing is waiting on them when the truth is that we could not ask. */}
          {data === undefined && error ? (
            <LoadError what="approvals" error={error} onRetry={refresh} />
          ) : data === undefined && loading ? (
            <ListSkeleton rows={2} what="approvals" />
          ) : pending.length === 0 ? (
            <EmptyState icon={CheckCheck} title="Nothing waiting on you"
              hint="Tool approvals your agent raises will appear here." />
          ) : (
            <AnimatePresence initial={false}>
              {pending.map((ap) => (
                <ApprovalPrompt
                  key={ap.id}
                  density="roomy"
                  tool={ap.tool}
                  args={argsText(ap.tool_input)}
                  purpose={ap.tool_purpose}
                  meta={<ApprovalMeta ap={ap} />}
                  choices={[
                    // The accessible name carries the tool, because a queue paints one card
                    // per approval and four bare "Allow"s announce identically.
                    { key: 'approve', icon: Check, label: 'Allow', tone: 'primary', name: `Allow ${ap.tool}`, busy: busy.has(ap.id), onClick: () => act(ap, 'approve') },
                    { key: 'reject', icon: Ban, label: 'Deny', tone: 'danger', name: `Deny ${ap.tool}`, busy: busy.has(ap.id), onClick: () => act(ap, 'reject') },
                  ]}
                />
              ))}
            </AnimatePresence>
          )}
        </section>

        {/* The rest of the attention path (`MC-6`). The "Not on the phone yet" stub list that
            stood here through MC-3/MC-4/MC-5 is DELETED, not hidden — the sections it named
            are what these four are. */}
        <RunningLoopsSection />
        <TasksSection />
        <InboxSection />
        <RecentSection />

        {/* Two ways off this page, and no third. `#/settings/devices` is the ONE device list
            (`MC-2` consumed plan 54's contract to build it); linking to it is how the phone
            reaches its own pairing/revocation without the companion growing a second copy —
            recorded as owed to this atom in MC-2's Execution log entry. */}
        <footer className="flex flex-wrap gap-s">
          <Button variant="secondary" size="sm" onClick={() => navigate('dashboard')}>
            <LayoutDashboard size={15} /> Open the full dashboard
          </Button>
          <Button variant="secondary" size="sm" onClick={() => navigate('settings/devices')}>
            <Smartphone size={15} /> Paired devices
          </Button>
        </footer>
      </div>
    </div>
  )
}

/** Where the request came from and how long it has been waiting — the context that turns a
 *  tool name into a decision. `session` is empty for gateway-originated approvals (a cron
 *  fire, a channel message, a subagent), so the row is omitted rather than shown blank. */
function ApprovalMeta({ ap }: { ap: PendingApproval }) {
  const rows: [string, string][] = []
  if (ap.session) rows.push(['Session', ap.session])
  if (ap.source) rows.push(['Requested by', ap.source])
  const waited = waitedFor(ap.ts)
  if (waited) rows.push(['Waiting', waited])
  if (!rows.length) return null
  return (
    <dl className="mt-s flex flex-col gap-xs">
      {rows.map(([k, v]) => (
        <div key={k} className="flex gap-s text-[0.75rem]">
          <dt className="shrink-0 text-on-surface-low">{k}</dt>
          <dd className="min-w-0 break-all text-on-surface-var">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

/** `tool_input` is typed `unknown` on the wire — the backend forwards whatever the provider
 *  sent (a pre-rendered string for most tools, a JSON object for some). Render a string
 *  as-is and pretty-print anything else, so the arguments are never shown as "[object
 *  Object]" on the surface whose whole job is showing what is being approved. */
function argsText(input: unknown): string | undefined {
  if (input == null || input === '') return undefined
  if (typeof input === 'string') return input
  try { return JSON.stringify(input, null, 2) } catch { return String(input) }
}

/** Coarse elapsed time from the approval's epoch-seconds `ts`. Coarse on purpose: the useful
 *  fact is "this has been blocked a while", and approvals time out in minutes. */
function waitedFor(ts: number | undefined): string {
  if (!ts) return ''
  const secs = Math.max(0, Math.round(Date.now() / 1000 - ts))
  if (secs < 60) return `${secs}s`
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m`
  return `${Math.round(mins / 60)}h`
}
