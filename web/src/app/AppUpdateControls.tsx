import { useState } from 'react'
import { AlertTriangle, CheckCircle2, DownloadCloud, Loader2, RefreshCw, RotateCw } from 'lucide-react'
import { api } from '../lib/api'
import type { AppUpdateState } from '../lib/desktopBridge'
import { RELEASES_PAGE_URL } from '../lib/repoDocs'
import type { AppUpdate } from './appUpdate'
import { Button } from '../ui/Button'
import { fvs } from '../design/fontWeight'

/** The phases in which an update is under way: nothing can be pressed until it ends. */
const UNDER_WAY: ReadonlySet<AppUpdateState['phase']> = new Set(['downloading', 'verifying', 'placing', 'restarting'])

/** Whether the System popover shows the app's row: while a release is on offer, one is ready to
 *  finish, an update runs, or a ready one the update settings dropped is still to be said. */
export function appUpdateOnOffer(state: AppUpdateState | null): boolean {
  return !!state && (!!state.available || state.phase === 'ready' || UNDER_WAY.has(state.phase) || !!state.dropped)
}

/** The one line that says where the app's update stands. */
export function appUpdateLine(state: AppUpdateState): string {
  const v = state.ready?.version ?? state.available?.version ?? ''
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
    case 'ready':
      return `PersonalClaw ${v} is ready. Restart to finish the update.`
    case 'placing':
      return `Putting PersonalClaw ${v} in place…`
    case 'restarting':
      return state.sentence || `PersonalClaw ${v} is in place. The app restarts now.`
    case 'current':
      return `This app is up to date (v${state.current}).`
    default:
      return v ? `PersonalClaw ${v} is available for this app.` : `This app is v${state.current}.`
  }
}

/** What the confirm asks: the restart, and whether the gateway goes with it. */
function confirmWords(version: string, restartsGateway: boolean, running: number, sessions: number, unread: string): string {
  const ask = `Restart PersonalClaw to finish updating to ${version}?`
  if (!restartsGateway) return ask
  if (unread) return `${ask} Your gateway restarts with it. ${unread}`
  if (running <= 0) return `${ask} Your gateway restarts with it.`
  const agents = `${running} running agent${running === 1 ? '' : 's'}`
  const live = sessions > 0 ? ` and ${sessions} live session${sessions === 1 ? '' : 's'}` : ''
  return `${ask} Your gateway restarts with it, which interrupts ${agents}${live}.`
}

/** The app's own update, in Settings › Updates › This app (`place="settings"`, with Check now and
 *  how the last update ended) and as the System popover's row (`place="popover"`). Two presses:
 *  Update downloads and checks the release in the background, and nothing beside the app changes;
 *  once it is ready, Restart to Finish the Update, confirmed, puts it in place and restarts. An
 *  update this copy cannot install here offers the releases page instead, as every failure does. */
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
  const readyVersion = state.phase === 'ready' ? state.ready?.version ?? '' : ''
  const underWay = UNDER_WAY.has(state.phase) || state.phase === 'checking'
  const canUpdate = !!state.available?.installable && !underWay && state.phase !== 'ready'
  const canFinish = !!readyVersion
  const offersPage = !underWay && (state.phase === 'failed' || (!!state.available && !state.available.installable))
  // The line already says a ready update, its placing and its restart. A copy the update settings
  // dropped is said until the next Update, whatever the check after it said.
  const detail = refused || state.dropped?.sentence || (['ready', 'placing', 'restarting'].includes(state.phase) ? '' : state.sentence)
  const popover = place === 'popover'

  const download = async () => {
    setRefused('')
    const r = await update.download(version)
    if (!r.ok) setRefused(r.sentence || 'The update did not start.')
  }
  const askToFinish = async () => {
    setRefused('')
    let running = 0
    let sessions = 0
    let unread = ''
    // The count of what the restart interrupts is asked first; a failed ask is said in the confirm,
    // which still offers the restart.
    if (state.restartsGateway) {
      try { const r = await api.restartProbe(); running = r.running_agents; sessions = r.sessions }
      catch { unread = "What it would interrupt couldn't be read." }
    }
    setAsking({ running, sessions, unread })
  }
  const finish = async () => {
    setAsking(null)
    const r = await update.finish(readyVersion)
    if (!r.ok) setRefused(r.sentence || 'The update could not be finished.')
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
          <span>{confirmWords(readyVersion, state.restartsGateway, asking.running, asking.sessions, asking.unread)}</span>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <Button size="xs" variant={warn ? 'danger' : 'primary'} onClick={finish}>Restart the App</Button>
          <Button size="xs" variant="ghost" onClick={() => setAsking(null)}>Cancel</Button>
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
        (canUpdate || canFinish) && (
          <Button size="xs" variant="secondary" shape="squircle" onClick={canFinish ? askToFinish : download}
            ariaLabel={canFinish ? 'Restart to finish the update of this app' : 'Update the app'}>
            {canFinish
              ? <><RotateCw size={12} aria-hidden /> Restart to Finish the Update</>
              : <><DownloadCloud size={12} aria-hidden /> Update</>}
          </Button>
        )
      ) : (
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="secondary" size="sm" loading={state.phase === 'checking'} disabled={underWay}
            disabledReason={state.phase === 'checking' ? undefined : 'An update of the app is under way'} onClick={() => { setRefused(''); void update.check() }}
            ariaLabel={state.phase === 'ready'
              // While a copy is ready, a check reads the update settings again and asks GitHub only once they no longer allow it.
              ? "Check now: whether this app's update settings still allow the ready update"
              : 'Check now: ask GitHub once, now, whether a newer version of this app exists'}><RefreshCw size={14} /> Check now</Button>
          {canUpdate && <Button size="sm" onClick={download} ariaLabel="Update the app"><DownloadCloud size={14} /> Update</Button>}
          {canFinish && <Button size="sm" onClick={askToFinish} ariaLabel="Restart to finish the update of this app"><RotateCw size={14} /> Restart to Finish the Update</Button>}
        </div>
      )}
    </div>
  )
}
