import { useCallback, useEffect, useRef, useState } from 'react'
import { Globe, Ban, ShieldAlert } from 'lucide-react'
import { api, type BrowseStatus, type BrowseExpiredSite, type BrowseStepFrame } from '../../../lib/api'
import { useChatSocket, type WsMessage } from '../../../lib/useChatSocket'
import { useVisiblePoll } from '../../../lib/useVisiblePoll'
import { reportingWrite } from '../../../app/reportingWrite'
import { ERROR_SURFACE_PAINT } from '../../../design/errorTreatments'
import { Button } from '../../../ui/Button'
import { StatusPill } from '../../../ui/StatusPill'
import { SlotEmptyState, WidgetRow } from './kit'

/** Live browse mirror + one-click kill switch + persistent auth-expired banner (BROWSE-AUTOMATION
 *  §(b)/(c), BA-5). The human-facing half of unattended browsing, the browser-side sibling of
 *  DesktopLiveView (DCU-7) — the two share one live-watch idiom so browser and desktop feel alike.
 *
 *  Three things, one panel, all of them OBSERVE-ONLY except the stop:
 *
 *   · **The live step feed.** Each `browse_step` WS frame the loop already emits — the SCREENED
 *     url, the rendered action line (a credential `TYPE` is already `[withheld]` upstream), the
 *     screenshot PATH and any note — renders as one row, newest first. A watching human sees the
 *     run advance. The panel exposes nothing the run did not already record: no debug port, no CDP,
 *     no new attack surface (browse/mirror.py's own words).
 *   · **The kill switch.** One click POSTs `/api/browse/kill` and unattended browsing stops within
 *     one step (a running loop parks, a new run refuses to start). This is the browse-only scalpel,
 *     distinct from the incident hammer that halts every automation — so stopping a bad browse
 *     never collaterally suspends cron/hooks/chat. Re-enabling is EXPLICIT (confirm), so a stop a
 *     human chose is never silently undone.
 *   · **The auth-expired banner.** When a saved session expires the backend sets
 *     `.meta.json auth_state=expired`; this surfaces it as a persistent `role="alert"` banner (one
 *     per expired site) that clears only when the site is re-authenticated. It carries no field a
 *     credential could occupy — the agent never handles credentials.
 *
 *  🔑 THE SOCKET IS AN ARGUED EXCEPTION to DashboardLive's "no widget opens its own socket". That
 *  doctrine also says WS envelopes are SIGNALS, never payloads — true for the drifting, many-
 *  producer envelopes (`chat_status`/`sessions`). `browse_step` is the opposite: ONE producer
 *  (browse/mirror.py), a fixed named shape, and NO GET slice to read the step stream back from — so
 *  the frame's fields ARE the data and must be read. It is also live only while an unattended browse
 *  runs (rare), so folding it into the shared feed would charge every dashboard visitor for a stream
 *  most sessions never see. Precedent: `useAgentActivity` opens its own socket for the same reason.
 *  `browse_kill` / `browse_auth_expired` stay SIGNALS here (they refetch `/api/browse/status`,
 *  which owns the kill + expired read); only `browse_step`, which has to be, is read as a payload. */

/** How many steps to keep. The feed is about the browse happening NOW, not an audit log — the SEL
 *  is the record. Older steps fall off the bottom. */
const KEPT_STEPS = 12
const DRAWN_STEPS = 8

/** Status changes rarely and the CLI can flip the kill switch out-of-band, so a slow safety-net
 *  poll (the `browse_kill`/`browse_auth_expired` frames land a change immediately). Same cadence
 *  and reason as IncidentBanner's incident poll. */
const STATUS_POLL_MS = 15000

interface LiveStep extends BrowseStepFrame {
  /** A stable client key — the run + step number can repeat across runs, so a monotonic counter
   *  keeps React keys unique and the list order intent-stable. */
  seq: number
}

/** Coerce the status read into the shape the panel renders, defensively. The GET contract
 *  guarantees `{kill, expired}`, but a producer/consumer version skew (or a partial answer) must
 *  degrade to "idle", never crash the dashboard this panel decorates — the same resilience the
 *  `browse_step` reader applies to a frame. */
function normalizeStatus(s: BrowseStatus | null | undefined): BrowseStatus {
  const kill = s?.kill
  const expiredIn = s?.expired
  const expired = (Array.isArray(expiredIn) ? expiredIn : [])
    .filter((e): e is BrowseExpiredSite => !!e && typeof (e as BrowseExpiredSite).site === 'string')
    .map((e) => ({ site: String(e.site), key_present: Boolean(e.key_present) }))
  return {
    kill: {
      active: Boolean(kill?.active),
      reason: String(kill?.reason ?? ''),
      started_at: String(kill?.started_at ?? ''),
    },
    expired,
  }
}

/** The panel's own live state: the accumulated step feed (from WS) plus the status read model
 *  (kill + expired sites, from one GET). Kept in a hook so the component body stays declarative and
 *  the test can drive the socket callback directly. */
function useBrowseMirror() {
  const [steps, setSteps] = useState<LiveStep[]>([])
  const [status, setStatus] = useState<BrowseStatus | null>(null)
  // A failed read must not render as a calm "nothing is browsing" — "the agent is idle" and "the
  // gateway didn't answer" are different facts. Distinct from `status === null` (not read yet).
  const [statusErr, setStatusErr] = useState<unknown>(null)
  const alive = useRef(true)
  const seq = useRef(0)
  useEffect(() => () => { alive.current = false }, [])

  const loadStatus = useCallback(() => {
    api.browseStatus()
      .then((s) => { if (alive.current) { setStatus(normalizeStatus(s)); setStatusErr(null) } })
      .catch((e) => { if (alive.current) setStatusErr(e) })
  }, [])

  const onMessage = useCallback((m: WsMessage) => {
    if (m.type === 'browse_step') {
      // The ONE payload read (see the header): the step stream has no GET to refetch from. Each
      // field is read off the untyped envelope and coerced — a producer/consumer version skew must
      // degrade a row, never crash the dashboard it decorates.
      const d = m.data
      const step: LiveStep = {
        seq: seq.current++,
        run_id: String(d.run_id ?? ''),
        step_n: Number(d.step_n ?? 0),
        url: String(d.url ?? ''),
        action: String(d.action ?? ''),
        screenshot: String(d.screenshot ?? ''),
        note: String(d.note ?? ''),
      }
      setSteps((prev) => [step, ...prev].slice(0, KEPT_STEPS))
    } else if (m.type === 'browse_kill' || m.type === 'browse_auth_expired') {
      // SIGNALS, not payloads — refetch the read model that owns kill + expired.
      loadStatus()
    }
  }, [loadStatus])

  useChatSocket(onMessage, loadStatus)  // reopened after a drop → re-sync the read model
  useVisiblePoll(loadStatus, STATUS_POLL_MS)

  const kill = useCallback(async () => {
    if (await reportingWrite('stop browsing', () => api.browseKill())) loadStatus()
  }, [loadStatus])
  const resume = useCallback(async () => {
    if (await reportingWrite('re-enable browsing', () => api.browseKillRelease())) loadStatus()
  }, [loadStatus])

  return { steps, status, statusErr, kill, resume }
}

export function BrowseMirror() {
  const { steps, status, statusErr, kill, resume } = useBrowseMirror()
  const [busy, setBusy] = useState(false)

  // Pre-load: nothing to say yet. A read failure with no prior data says so — it does not
  // impersonate a quiet, idle browser.
  if (!status && statusErr) {
    return <SlotEmptyState icon={Globe}>Couldn&rsquo;t read the browse mirror.</SlotEmptyState>
  }
  if (!status) return null

  const killed = status.kill.active
  const expired = status.expired
  const drawn = steps.slice(0, DRAWN_STEPS)

  const doKill = async () => { setBusy(true); try { await kill() } finally { setBusy(false) } }
  const doResume = async () => { setBusy(true); try { await resume() } finally { setBusy(false) } }

  return (
    <div className="flex min-w-0 flex-col gap-s pt-xs">
      {/* Persistent auth-expired banners — one per site, alert-role so a screen reader announces
          them. They clear only when the site is re-authenticated (the read drops it from `expired`),
          never on a timer or a dismiss. */}
      {expired.map((e) => (
        <div
          key={e.site}
          role="alert"
          className="flex items-start gap-s rounded-lg px-m py-s"
          style={ERROR_SURFACE_PAINT}
        >
          <ShieldAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          <p data-type="body-m" className="min-w-0 flex-1">
            <strong>Sign-in needed for {e.site}</strong> — the saved browse session expired. Open the
            site in the handoff window and sign in; PersonalClaw never sees what you type.
            {e.key_present
              ? ' Re-auth reuses the existing profile.'
              : ' A new profile will be created on sign-in.'}
          </p>
        </div>
      ))}

      {/* Posture + the one control that acts. The pill states a fact; the button is the scalpel. */}
      <div className="flex flex-wrap items-center gap-m">
        <StatusPill tone={killed ? 'danger' : steps.length > 0 ? 'primary' : 'neutral'}>
          {killed ? 'Browsing stopped' : steps.length > 0 ? 'Browsing' : 'Idle'}
        </StatusPill>
        {killed && status.kill.reason && (
          <span data-type="caption" className="min-w-0 truncate text-on-surface-low" title={status.kill.reason}>
            {status.kill.reason}
          </span>
        )}
        <span className="flex-1" />
        {killed ? (
          <Button variant="ghost" size="xs" onClick={doResume} loading={busy} ariaLabel="Re-enable browsing">
            Resume
          </Button>
        ) : (
          <Button variant="danger" size="xs" onClick={doKill} loading={busy} ariaLabel="Stop all browsing">
            <Ban size={13} aria-hidden="true" /> Stop
          </Button>
        )}
      </div>

      {/* The live step feed: the artifacts the loop already produced, newest first. Two static
          empty states rather than one interpolated child — the slot-empty-state ratchet reads the
          copy from source, and a `{ternary}` reads as no copy at all. */}
      {drawn.length === 0 && killed && (
        <SlotEmptyState icon={Globe}>
          Browsing is stopped. Resume to let unattended browse runs continue.
        </SlotEmptyState>
      )}
      {drawn.length === 0 && !killed && (
        <SlotEmptyState icon={Globe}>
          No unattended browse is running. Each step shows here live, with a one-click stop.
        </SlotEmptyState>
      )}
      {drawn.length > 0 && (
        <div className="flex flex-col gap-xs">
          {drawn.map((s) => (
            <WidgetRow key={s.seq}>
              <div className="flex min-w-0 flex-col gap-xs">
                <div className="flex min-w-0 items-center gap-s">
                  <StatusPill tone="neutral">{`#${s.step_n}`}</StatusPill>
                  <span data-type="label-m" className="min-w-0 truncate text-on-surface" title={s.url}>
                    {s.url || '(no url)'}
                  </span>
                </div>
                <span data-type="body-s" className="min-w-0 truncate text-on-surface-var" title={s.action}>
                  {s.action || '(no action)'}
                </span>
                {s.screenshot && (
                  // The screenshot enters as a PATH reference, never fetched as bytes — the §1
                  // screenshot-as-path discipline (browse/loop.py's `assert_no_base64`), and it
                  // keeps the panel from ever dialing a run-workspace file.
                  <span
                    data-type="caption"
                    className="min-w-0 truncate font-mono text-on-surface-low"
                    title={s.screenshot}
                  >
                    [SCREENSHOT: {s.screenshot}]
                  </span>
                )}
                {s.note && (
                  <span data-type="caption" className="min-w-0 truncate text-on-surface-low" title={s.note}>
                    {s.note}
                  </span>
                )}
              </div>
            </WidgetRow>
          ))}
        </div>
      )}
    </div>
  )
}
