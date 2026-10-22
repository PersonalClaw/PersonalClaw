import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence } from 'framer-motion'
import { Check, Ban, BellRing, LayoutDashboard, RefreshCw, ShieldCheck, CheckCheck, Smartphone, ArrowRight } from 'lucide-react'
import { api, type PendingApproval, type PushStatus, type RunningList, type RunningRow, type WaitingItem, type WaitingList } from '../../lib/api'
import { phaseLabel } from '../../lib/workPhase'
import { WorkHead, waitingLine, waitingOrbState, waitingWorkName, workKindOf } from './allWork'
import { OneWorkView, type Opened } from './OneWorkView'
import {
  WAITING_KIND_LABEL, approvalOf, isStale, useStaleAt, waitingRoute, waitingSource, waitingText,
} from '../../lib/waitingList'

/** The id an item is hidden under while its answer is in flight: an approval's registry id. */
const approvalKey = (w: WaitingItem) => approvalOf(w)?.id ?? w.id
import { disablePush, enablePush, pushDeviceId, pushSupported } from '../../app/pushClient'
import { disableNativePush, enableNativePush, nativeBridge, watchNativePushTaps } from '../../app/nativePush'
import { useQuery } from '../../lib/data'
import { FOLLOWS, useLiveLane } from './useLiveLane'
import { ApprovalPrompt } from '../../ui/ApprovalPrompt'
import { approvalRowFields, waitedFor } from '../chat/approvalRow'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { qget, type RouteProps } from '../../app/useQueryState'
import { InboxSection, RecentSection, RunningSection, TasksSection } from './CompanionSections'
import { BUSY_REASON } from '../../ui/unavailable'

/** `#/companion` — the phone control surface, what waits on you first.
 *
 *  A run that is blocked on your decision is the one thing that genuinely cannot wait until the
 *  owner is back at a desk, so the phone route ships what waits on you FIRST and by itself and
 *  stays at the TOP of the column: everything the gateway's one list holds (`GET /api/waiting`),
 *  in its order (by consequence, then age), each with its text. An approval is answered here, on
 *  its own card; every other item opens its own page, where its card is answered with its brief.
 *  An item past answering keeps no action that could still send. The rest of the attention path sits
 *  underneath it — what is running, tasks, inbox, recent notifications — in
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
export function CompanionPage({ navigate, query, setQuery }: RouteProps) {
  // `?approval=<id>` — where a push notification's tap lands. The service
  // worker builds this URL from the ping's `item_id`; nothing else in the payload could
  // have told it which card to open.
  const focusId = qget(query, 'approval')
  // Inside the mobile shell, a native notification tap arrives through the Capacitor
  // bridge instead of the service worker. Same destination as the webpush tap: the ids-only
  // ping names the approval, and the URL is composed from ids alone.
  useEffect(() => {
    watchNativePushTaps((kind, itemId) => {
      if (kind === 'approval') navigate(`companion?approval=${encodeURIComponent(itemId)}`)
    })
  }, [navigate])
  // Live data, never persisted to sessionStorage: a stale approval is a dangerous thing to
  // paint. The list re-reads on its ring, a reconnect and the page being shown again, as every
  // lane below does (`useLiveLane`), and on manual refresh.
  const { data, loading, error, refresh } = useQuery<WaitingList>(
    'companion:waiting', () => api.waiting())
  useLiveLane('companion:waiting', FOLLOWS.waiting)
  // The running list, the same read the Running section makes, for the name of the work each
  // waiting item belongs to. A read that failed names each ask by its own source instead, and the
  // Running section below says the read failed.
  const { data: runningData, error: runningError } = useQuery<RunningList>('running-companion', () => api.running())
  const rows = useMemo(
    () => new Map(runningError ? [] : (runningData?.running ?? []).map((r) => [r.work, r])),
    [runningData, runningError],
  )
  // Optimistically resolved ids — the row leaves immediately, and comes BACK if the POST
  // failed (with the failure announced), because silently dropping a permission prompt would
  // leave the user believing they answered it.
  const [resolved, setResolved] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const busyRef = useRef(busy)
  busyRef.current = busy

  // 🪤 THE FETCHED LIST IS AUTHORITATIVE — RECONCILE THE OPTIMISTIC HIDE AGAINST IT.
  // The hidden-id set was never pruned, so a queue the
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

  const act = async (ap: PendingApproval, action: 'approve' | 'reject') => {
    setBusy((s) => new Set(s).add(ap.id))
    setResolved((s) => new Set(s).add(ap.id))
    try {
      await api.resolveApproval(ap.id, action)
      // Landed: the card stays decided (`ApprovalChoice.onClick`) until the next read drops it.
      return true
    } catch (e) {
      setResolved((s) => { const n = new Set(s); n.delete(ap.id); return n })
      // `api.*` rejects with an ApiError whose message is already the sentence a user should
      // read (lib/errText). The toast host announces it assertively.
      window.dispatchEvent(new CustomEvent('ne:toast', {
        detail: { level: 'error', message: `Couldn't ${action} ${ap.tool} — ${(e as Error)?.message || 'the gateway did not respond'}` },
      }))
      return false
    } finally {
      setBusy((s) => { const n = new Set(s); n.delete(ap.id); return n })
      refresh()
    }
  }

  const pending = (data?.waiting ?? []).filter((w) => !resolved.has(approvalKey(w)))
  const unreadable = data?.unreadable ?? []
  const now = useStaleAt(pending)
  // No autonomy rung on a card, as on the chat's: a rung is declared per ACTION PROVIDER, and a
  // tool call dispatches none (`lib/rungs.providerRungIndex`).
  // View two (#3898): one piece of work owning the screen, opened from a row (`?open=` a waiting
  // item's id or a running row's work) or from a push (`?approval=<id>`). The deep link is honest
  // in BOTH directions. Present-and-found: view two opens on it, with focus on its wrapper.
  // Present-and-missing (answered elsewhere, or it timed out while the phone was locked): say so.
  // Rendering the ordinary queue with no explanation would read as "the notification opened the
  // wrong thing", and the user would go looking for a card that no longer exists.
  const openId = qget(query, 'open') || (focusId ? `approval:${focusId}` : '')
  const openItem = openId ? pending.find((w) => w.id === openId) : undefined
  const openRow = openId && !openItem ? rows.get(openId) : undefined
  const opened: Opened | null = openItem ? { item: openItem, row: rows.get(openItem.work) ?? null }
    : openRow ? { item: null, row: openRow } : null
  const openMissing = Boolean(openId) && !opened && data !== undefined && (runningData !== undefined || runningError !== undefined)
  const close = () => setQuery({ open: null, approval: null })

  // An approval, on its own card, answered here: the decision the phone exists for. Past
  // answering, the card keeps no choice that could still send.
  const approvalCard = (ap: PendingApproval, stale: boolean, item: WaitingItem) => (
    <div key={ap.id} className="flex flex-col gap-s">
      <WaitingHead item={item} rows={rows} onOpen={() => setQuery({ open: item.id })} />
      <ApprovalPrompt
        density="roomy"
        // The row's own fields, as every surface that shows a waiting-list row shows them.
        {...approvalRowFields(ap, { stale })}
        choices={stale ? [] : [
          // The accessible name carries the tool, because a queue paints one card
          // per approval and four bare "Allow"s announce identically.
          { key: 'approve', icon: Check, label: 'Allow', tone: 'primary', approves: true, name: `Allow ${ap.tool}`, busy: busy.has(ap.id), onClick: () => act(ap, 'approve') },
          // A Deny that ends the agent's turn says so here too, as the card's line does.
          { key: 'reject', icon: Ban, label: 'Deny', tone: 'danger', name: `Deny ${ap.tool}${ap.deny_effect ? `. ${ap.deny_effect}` : ''}`, busy: busy.has(ap.id), onClick: () => act(ap, 'reject') },
        ]}
      />
    </div>
  )

  return (
    <div className="h-full overflow-y-auto" data-full-screen="companion" style={{ background: 'var(--color-canvas)' }}>
      <div className="mx-auto flex max-w-[42rem] flex-col gap-xl px-l py-l">
        <header className="flex items-center justify-between gap-s">
          <div className="min-w-0">
            <h1 data-type="headline-s" className="text-on-surface">Companion</h1>
            <p data-type="body-m" className="text-on-surface-low">Answer what is waiting on you.</p>
          </div>
          <IconButton icon={RefreshCw} label="Refresh what is waiting on you" onClick={refresh} size={40} iconSize={18} />
        </header>

        {opened ? (
          <OneWorkView opened={opened} rows={rows} onBack={close}
            onDecide={(ap, action) => { void act(ap, action).then((landed) => { if (landed) close() }) }}
            onAnswered={() => { refresh(); close() }} />
        ) : <>
        <PushRow navigate={navigate} />

        <section aria-labelledby="companion-approvals-heading" className="flex flex-col gap-s">
          <h2 id="companion-approvals-heading" data-type="title-m" className="flex items-center gap-s text-on-surface">
            <ShieldCheck size={16} aria-hidden style={{ color: 'var(--color-warn)' }} />
            Waiting on you{pending.length > 0 ? ` (${pending.length})` : ''}
          </h2>
          {/* A source the gateway could not read is said, never shown as nothing waiting. */}
          {unreadable.length > 0 ? (
            <p role="status" data-type="body-m" className="text-warn">
              Some of what may be waiting on you could not be read: {unreadable.map((k) => WAITING_KIND_LABEL[k] ?? k).join(', ')}.
            </p>
          ) : null}
          {/* Order matters: `data === undefined` is true for the loading, error AND empty
              branches, so the error test comes first or it is unreachable. A failed fetch on
              this surface must never fall through to "all clear" — that would tell the user
              nothing is waiting on them when the truth is that we could not ask. */}
          {data === undefined && error ? (
            <LoadError what="list of what waits on you" error={error} onRetry={refresh} />
          ) : data === undefined && loading ? (
            <ListSkeleton rows={2} what="list of what waits on you" />
          ) : pending.length === 0 && unreadable.length === 0 ? (
            <EmptyState icon={CheckCheck} title="Nothing waiting on you"
              hint="Approvals, questions and anything else that waits on your answer appear here." />
          ) : (
            <AnimatePresence initial={false}>
              {pending.map((w) => {
                // An approval is answered on its card, under the id its own route names; anything
                // else, an approval whose route is not an approval's among it, opens its page.
                const ap = approvalOf(w)
                return ap ? approvalCard(ap, isStale(w, now), w)
                  : <WaitingRow key={w.id} item={w} rows={rows} stale={isStale(w, now)} navigate={navigate} onOpen={() => setQuery({ open: w.id })} />
              })}
            </AnimatePresence>
          )}
          {openMissing ? (
            <p role="status" data-type="body-m" className="text-on-surface-low">
              {focusId
                ? <>The approval your notification pointed at isn&rsquo;t waiting anymore &mdash; it was answered or it timed out.</>
                : <>What you opened isn&rsquo;t waiting or running anymore &mdash; it was answered, it ended, or it timed out.</>}
            </p>
          ) : null}
        </section>

        {/* The rest of the attention path. The "Not on the phone yet" stub list that
            stood here earlier is DELETED, not hidden — the sections it named
            are what these four are. */}
        <RunningSection onOpen={(work) => setQuery({ open: work })} />
        <TasksSection />
        <InboxSection />
        <RecentSection />

        {/* Two ways off this page, and no third. `#/settings/devices` is the ONE device list;
            linking to it is how the phone reaches its own pairing/revocation without the
            companion growing a second copy. */}
        <footer className="flex flex-wrap gap-s">
          <Button variant="secondary" size="sm" onClick={() => navigate('dashboard')}>
            <LayoutDashboard size={15} /> Open the full dashboard
          </Button>
          <Button variant="secondary" size="sm" onClick={() => navigate('settings/devices')}>
            <Smartphone size={15} /> Paired devices
          </Button>
        </footer>
        </>}
      </div>
    </div>
  )
}

/** "Is this phone woken up for an approval?" — the one push control on this surface.
 *
 *  It lives here rather than in Settings because the answer is per-BROWSER: only the device
 *  holding the subscription can create or drop it, and a desktop Settings page cannot
 *  subscribe a phone. The transport CHOICE (`mobile.push_backend`, the ntfy URL) is the
 *  opposite — one gateway-wide decision — so it lives in Settings → Companion apps.
 *
 *  Every state is named. A silent row would leave the most important question on this
 *  surface ("will my phone actually ring?") answered only by waiting for a push that may
 *  never come.
 */
function PushRow({ navigate }: { navigate: RouteProps['navigate'] }) {
  const { data, refresh } = useQuery<PushStatus>('companion:push', () => api.pushStatus())
  const [busy, setBusy] = useState(false)
  // A ReactNode, like `line` below: a note that names the command which fixes it shows it as code.
  const [note, setNote] = useState<ReactNode>('')
  if (!data) return null

  const supported = pushSupported()
  const on = data.devices.includes(pushDeviceId())

  const enable = async () => {
    setBusy(true)
    setNote('')
    const result = await enablePush(data.vapid_public_key)
    setBusy(false)
    if (result.ok) { refresh(); return }
    setNote(
      result.reason === 'denied'
        ? 'Your browser refused notification permission. Allow notifications for this site, then try again.'
        : result.reason === 'no-key'
          ? <>This gateway has no push keypair yet — run <code>personalclaw push init</code>.</>
          : result.reason === 'unsupported'
            ? 'This browser cannot hold a push subscription.'
            : `Could not subscribe${result.detail ? ` — ${result.detail}` : ''}.`,
    )
  }

  const disable = async () => {
    setBusy(true)
    const ok = await disablePush()
    setBusy(false)
    setNote(ok ? '' : 'Could not unsubscribe — the gateway did not respond.')
    refresh()
  }

  let line: ReactNode = ''
  let action: ReactNode = null
  if (data.backend === 'none') {
    line = 'Push is switched off for this gateway.'
    action = <Button variant="secondary" size="sm" onClick={() => navigate('settings/companion')}>Settings</Button>
  } else if (data.backend === 'ntfy') {
    line = !data.ntfy_configured
      ? 'The backend is ntfy but no topic URL is configured.'
      : data.approval_targeted
        ? 'Pings go to your ntfy topic. Nothing to set up on this device.'
        : 'Pings go to your ntfy topic, but approvals are not routed to it. Tick “push” for “Approval needed” in Settings → Notifications.'
  } else if (data.backend === 'relay') {
    // Registration needs the store app's native bridge — a browser tab cannot hold an
    // vendor push token, and saying so beats a button that cannot work. Every state named, as
    // above; the on-state keeps the same routed/unrouted honesty split the other backends have.
    const registered = data.relay_devices.includes(pushDeviceId())
    if (!data.relay_configured) {
      line = 'The backend is relay but no relay URL is configured.'
      action = <Button variant="secondary" size="sm" onClick={() => navigate('settings/companion')}>Settings</Button>
    } else if (registered) {
      line = data.approval_targeted
        ? 'Push is on for this device.'
        : 'Push is on for this device, but approvals are not routed to it. Tick “push” for “Approval needed” in Settings → Notifications.'
      action = (
        <Button variant="ghost" size="sm" disabled={busy} disabledReason={BUSY_REASON}
          onClick={async () => {
            setBusy(true)
            const ok = await disableNativePush()
            setBusy(false)
            setNote(ok ? '' : 'Could not unregister — the gateway did not respond.')
            refresh()
          }}>Turn off</Button>
      )
    } else if (!nativeBridge()) {
      line = 'Pings go through the push relay to the store app. Open this page in the PersonalClaw app to register this device.'
    } else {
      line = 'Get woken up when a run needs your approval.'
      action = (
        <Button variant="primary" size="sm" disabled={busy} disabledReason={BUSY_REASON}
          onClick={async () => {
            setBusy(true)
            setNote('')
            const result = await enableNativePush()
            setBusy(false)
            if (result.ok) { refresh(); return }
            setNote(
              result.reason === 'denied'
                ? 'The OS refused notification permission. Allow notifications for PersonalClaw, then try again.'
                : `Could not register${result.detail ? ` — ${result.detail}` : ''}.`,
            )
          }}><BellRing size={15} /> Turn on push</Button>
      )
    }
  } else if (!data.vapid_ready) {
    line = <>No push keypair yet. Run <code>personalclaw push init</code> on the gateway.</>
  } else if (on) {
    // Checked BEFORE the capability probe on purpose: the gateway holding a subscription for
    // this profile is a FACT, and it outranks a feature test. Probing first would tell a
    // device the gateway is actively pushing to that it cannot receive push — and hide the
    // only control that can turn it off.
    //
    // Subscribed-but-not-ROUTED is a real state and it is named, not hidden. Turning push on
    // writes the `approval/requested` rule for a user who never set one, but it deliberately
    // does NOT override a rule they did set — so a user who once turned approval pushes off
    // has a subscribed device that stays quiet, and the only honest thing to do is say where
    // the switch is.
    line = data.approval_targeted
      ? 'Push is on for this device.'
      : 'Push is on for this device, but approvals are not routed to it. Tick “push” for “Approval needed” in Settings → Notifications.'
    action = <Button variant="ghost" size="sm" onClick={disable} disabled={busy} disabledReason={BUSY_REASON}>Turn off</Button>
  } else if (!supported) {
    // The honest iOS story: web push needs an INSTALLED PWA there, so a Safari tab
    // legitimately cannot subscribe and pretending otherwise wastes the user's time.
    line = 'This browser cannot hold a push subscription. Install to your home screen first.'
  } else {
    line = 'Get woken up when a run needs your approval.'
    action = <Button variant="primary" size="sm" onClick={enable} disabled={busy} disabledReason={BUSY_REASON}><BellRing size={15} /> Turn on push</Button>
  }

  return (
    <div className="flex flex-col gap-s rounded-lg border border-outline-variant/40 px-l py-l">
      <div className="flex items-center justify-between gap-m">
        <p data-type="body-m" className="min-w-0 text-on-surface-low">{line}</p>
        {action}
      </div>
      {note ? <p role="alert" data-type="body-m" className="text-warn">{note}</p> : null}
    </div>
  )
}

/** One item that is answered on its own page, with its text in its source's own words: its kind,
 *  where it came from, how long it has waited, and Open, which takes you to its card. Past
 *  answering it says so, and Open is off: there is nothing left to answer there. */
/** A waiting item's head, as the buddy says it (#3898): its orb, its work's glyph, its one line,
 *  and the exact phase it holds that work in. */
function WaitingHead({ item, rows, onOpen }: { item: WaitingItem; rows: ReadonlyMap<string, RunningRow>; onOpen: () => void }) {
  const state = waitingOrbState(item)
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      <WorkHead id={item.id} state={state} kind={workKindOf(item.work)} line={waitingLine(item, waitingWorkName(item, rows))} onOpen={onOpen} />
      <p data-type="caption" className="text-on-surface-low" data-phase={state}>{phaseLabel(state)}</p>
    </div>
  )
}

function WaitingRow({ item, rows, stale, navigate, onOpen }: {
  item: WaitingItem; rows: ReadonlyMap<string, RunningRow>; stale: boolean; navigate: RouteProps['navigate']; onOpen: () => void
}) {
  const said = waitingText(item)
  const from = waitingSource(item)
  const route = waitingRoute(item)
  const asked = item.asked_at ? Date.parse(item.asked_at) : NaN
  const waited = Number.isFinite(asked) ? waitedFor(Math.round(asked / 1000)) : ''
  return (
    <article
      aria-label={`${WAITING_KIND_LABEL[item.kind]}: ${said}`}
      className="flex flex-col gap-s rounded-lg border border-outline-variant bg-surface p-l"
    >
      <WaitingHead item={item} rows={rows} onOpen={onOpen} />
      <p data-type="caption" className="text-on-surface-low">
        {WAITING_KIND_LABEL[item.kind]}{from ? ` · ${from}` : ''}
      </p>
      <p data-type="body-l" className="break-words text-on-surface">{said}</p>
      <p data-type="caption" className="text-on-surface-low">
        {stale ? 'Expired — it can no longer be answered.' : waited ? `Waiting ${waited}` : 'Waiting'}
      </p>
      {route ? (
        <div>
          <Button
            variant="secondary"
            // A thumb's target on the phone: 44 px or more.
            size="lg"
            disabled={stale}
            disabledReason={stale ? 'It can no longer be answered.' : undefined}
            // A list paints one Open per item, so each is named for what it opens.
            ariaLabel={`Open ${WAITING_KIND_LABEL[item.kind].toLowerCase()}: ${said}`}
            onClick={() => { if (!stale) navigate(route) }}
          >
            Open <ArrowRight size={15} aria-hidden />
          </Button>
        </div>
      ) : null}
    </article>
  )
}
