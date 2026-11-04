import { useState } from 'react'
import { AlertTriangle, CheckCircle2, DownloadCloud, Loader2, RefreshCw } from 'lucide-react'
import { api } from '../lib/api'
import type { AppUpdateState } from '../lib/desktopBridge'
import { RELEASES_PAGE_URL } from '../lib/repoDocs'
import type { AppUpdate } from './appUpdate'
import { Button } from '../ui/Button'
import { fvs } from '../design/fontWeight'

/** The phases in which an update is under way: nothing can be pressed until it ends. */
const UNDER_WAY: ReadonlySet<AppUpdateState['phase']> = new Set(['downloading', 'verifying', 'restarting'])

/** Whether the System popover shows the app's row: while a release is on offer, or an update runs. */
export function appUpdateOnOffer(state: AppUpdateState | null): boolean {
  return !!state && (!!state.available || UNDER_WAY.has(state.phase))
}

/** The one line that says where the app's update stands. */
export function appUpdateLine(state: AppUpdateState): string {
  const v = state.available?.version ?? ''
  switch (state.phase) {
    case 'checking':
      return 'Looking for a new version of the app…'
    case 'downloading': {
      const p = state.progress
      const pct = p && p.total > 0 ? Math.min(100, Math.floor((p.received / p.total) * 100)) : null
      return `Downloading PersonalClaw ${v}…${pct === null ? '' : ` ${pct}%`}`
    }
    case 'verifying':
      return `Checking PersonalClaw ${v}…`
    case 'restarting':
      return state.sentence || `PersonalClaw ${v} is ready. The app restarts now.`
    case 'current':
      return `This app is up to date (v${state.current}).`
    default:
      return v ? `PersonalClaw ${v} is available for this app.` : `This app is v${state.current}.`
  }
}

/** What the confirm asks: the download, the restart, and whether the gateway goes with it. */
function confirmWords(version: string, restartsGateway: boolean, running: number, sessions: number, unread: string): string {
  const ask = `Download PersonalClaw ${version}, then restart the app on it?`
  if (!restartsGateway) return ask
  if (unread) return `${ask} Your gateway restarts with it. ${unread}`
  if (running <= 0) return `${ask} Your gateway restarts with it.`
  const agents = `${running} running agent${running === 1 ? '' : 's'}`
  const live = sessions > 0 ? ` and ${sessions} live session${sessions === 1 ? '' : 's'}` : ''
  return `${ask} Your gateway restarts with it, which interrupts ${agents}${live}.`
}

/** The app's own update, in Settings › Updates › This app (`place="settings"`, with Check now and
 *  how the last update ended) and as the System popover's row (`place="popover"`). Nothing
 *  downloads until Update & Restart is pressed and confirmed; an update this copy cannot install
 *  here offers the releases page instead, as every failure does. */
export function AppUpdateControls({ update, place }: { update: AppUpdate; place: 'settings' | 'popover' }) {
  const [asking, setAsking] = useState<{ running: number; sessions: number; unread: string } | null>(null)
  const [refused, setRefused] = useState('')
  const state = update.state
  if (!state) {
    return place === 'settings'
      ? <div role="status" data-type="caption" className="text-on-surface-low">Asking the app…</div>
      : null
  }
  const version = state.available?.version ?? ''
  const underWay = UNDER_WAY.has(state.phase) || state.phase === 'checking'
  const canPress = !!state.available?.installable && !underWay
  const offersPage = !underWay && (state.phase === 'failed' || (!!state.available && !state.available.installable))
  const detail = refused || (state.phase === 'restarting' ? '' : state.sentence)
  const popover = place === 'popover'

  const press = async () => {
    setRefused('')
    let running = 0
    let sessions = 0
    let unread = ''
    // The count of what the restart interrupts is asked first; a failed ask is said in the confirm,
    // which still offers the update.
    if (state.restartsGateway) {
      try { const r = await api.restartProbe(); running = r.running_agents; sessions = r.sessions }
      catch { unread = "What it would interrupt couldn't be read." }
    }
    setAsking({ running, sessions, unread })
  }
  const go = async () => {
    setAsking(null)
    const r = await update.install(version)
    if (!r.ok) setRefused(r.sentence || 'The update did not start.')
  }

  const icon = underWay
    ? <Loader2 size={popover ? 13 : 18} className="shrink-0 animate-spin text-on-surface-low" aria-hidden />
    : state.phase === 'failed'
      ? <AlertTriangle size={popover ? 13 : 18} className="shrink-0" style={{ color: 'var(--color-warning)' }} aria-hidden />
      : state.phase === 'current'
        ? <CheckCircle2 size={popover ? 13 : 18} className="shrink-0" style={{ color: 'var(--color-success)' }} aria-hidden />
        : <DownloadCloud size={popover ? 13 : 18} className="shrink-0 text-on-surface-low" aria-hidden />
  const releases = (
    <a href={RELEASES_PAGE_URL} target="_blank" rel="noreferrer noopener" className="text-primary underline">Open the releases page</a>
  )
  const last = !popover && state.last ? (
    <div data-type="caption" className="mt-1 flex items-start gap-1.5 text-on-surface-low">
      {state.last.ok
        ? <CheckCircle2 size={13} className="mt-0.5 shrink-0" style={{ color: 'var(--color-success)' }} aria-hidden />
        : <AlertTriangle size={13} className="mt-0.5 shrink-0" style={{ color: 'var(--color-warning)' }} aria-hidden />}
      <span>{state.last.sentence}{!state.last.ok && <> {releases}</>}</span>
    </div>
  ) : null

  if (asking) {
    const warn = state.restartsGateway && asking.running > 0
    return (
      <div className={popover ? '' : 'flex flex-col gap-2'}>
        <div data-type="caption" className="flex items-start gap-1.5 text-on-surface-var">
          {warn && <AlertTriangle size={13} className="mt-0.5 shrink-0" style={{ color: 'var(--color-warning)' }} aria-hidden />}
          <span>{confirmWords(version, state.restartsGateway, asking.running, asking.sessions, asking.unread)}</span>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <button type="button" onClick={go} data-type="caption" className="rounded-lg px-2 py-1 text-on-primary"
            style={{ background: warn ? 'var(--color-warning)' : 'var(--color-primary)', borderRadius: 'var(--radius-md)' }}>Update &amp; Restart</button>
          <button type="button" onClick={() => setAsking(null)} data-type="caption"
            className="rounded-lg px-2 py-1 text-on-surface-low hover:text-on-surface">Cancel</button>
        </div>
      </div>
    )
  }

  return (
    <div className={popover ? 'flex flex-col items-start gap-1.5' : 'flex items-start gap-3'}>
      <div className="flex min-w-0 flex-1 items-start gap-1.5">
        {icon}
        <div className="min-w-0">
          <div role="status" data-type={popover ? 'caption' : 'body-m'} className="text-on-surface" style={popover ? undefined : fvs(550)}>
            {appUpdateLine(state)}
          </div>
          {detail && <div data-type="caption" className="text-on-surface-low">{detail}</div>}
          {offersPage && <div data-type="caption">{releases}</div>}
          {last}
          {!popover && <div data-type="caption" className="mt-0.5 text-on-surface-low">This app: v{state.current}</div>}
        </div>
      </div>
      {popover ? (
        canPress && (
          <button type="button" onClick={press} data-type="caption"
            className="flex items-center gap-1.5 rounded-lg bg-surface-high px-2 py-1 text-on-surface-var hover:bg-surface-highest hover:text-on-surface"
            style={{ borderRadius: 'var(--radius-md)' }}>
            <DownloadCloud size={12} aria-hidden /> Update &amp; Restart
          </button>
        )
      ) : (
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="secondary" size="sm" loading={state.phase === 'checking'} disabled={underWay}
            disabledReason={state.phase === 'checking' ? undefined : 'An update of the app is under way'} onClick={() => { setRefused(''); void update.check() }}
            ariaLabel="Check now: ask GitHub once, now, whether a newer version of this app exists"><RefreshCw size={14} /> Check now</Button>
          {canPress && <Button size="sm" onClick={press}><DownloadCloud size={14} /> Update &amp; Restart</Button>}
        </div>
      )}
    </div>
  )
}
