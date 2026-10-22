import { useCallback, useEffect, useState } from 'react'
import { api, BUDDY_POLICY_CHANGED, type BuddyPolicy } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useQuery } from '../../lib/data'
import { useChatSocket } from '../../lib/useChatSocket'
import { prefersReducedMotion } from '../../design/motion'
import { TextLink } from '../../ui/TextLink'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { desktopBridge, type BuddyPlace, type DesktopBridge } from '../../lib/desktopBridge'
import { FormSkeleton, InlineLoadError, LoadError } from '../../ui/ListScaffold'
import { PanelHeader, Section, RowGroup, ToggleRow, Row, SegPills } from './settingsUI'

// The editable buddy.* fields mirror the backend _EDITABLE_CONFIG allowlist (config/loader.py
// BuddyConfig). Each control PATCHes one allowlisted path via /api/config/personalclaw, and the
// gateway rings `buddy_policy_changed`, so every window follows.
type BuddyCfg = Record<string, unknown>

/** How long you can be away before the buddy steps back, as offered. A value stored by hand that
 *  is not one of them is shown too. */
const AWAY: { secs: number; label: string }[] = [
  { secs: 60, label: '1 min' }, { secs: 180, label: '3 min' }, { secs: 300, label: '5 min' },
  { secs: 600, label: '10 min' }, { secs: 1800, label: '30 min' },
]

/** Whether the system asks for reduced motion, kept current as it changes. */
function useSystemReducedMotion(): boolean {
  const [on, setOn] = useState(prefersReducedMotion)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const q = window.matchMedia('(prefers-reduced-motion: reduce)')
    const follow = () => setOn(q.matches)
    q.addEventListener?.('change', follow)
    return () => q.removeEventListener?.('change', follow)
  }, [])
  return on
}

const timeOf = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })

/** The notification posture the buddy's alerts follow, in a sentence. */
function postureLine(n: BuddyPolicy['notifications']): string {
  if (n.muted) return 'Notifications are muted: the buddy’s alerts raise no notification until you unmute them.'
  if (n.quiet_now) {
    return `In quiet hours${n.changes_at ? ` until ${timeOf(n.changes_at)}` : ''}: alerts arrive as a quiet peek, with no sound.`
  }
  return `Alerts may interrupt you now${n.changes_at ? `; quiet hours begin at ${timeOf(n.changes_at)}` : ''}.`
}

type BuddyBridge = NonNullable<DesktopBridge['buddy']>

/** This computer's rows: where the buddy sits and whether screen capture sees it. They are the
 *  desktop app's, in its own store on this machine (`pclawDesktop.buddy`), so they show only in
 *  the desktop app, and each change moves the live window at once. */
function ThisComputer({ bridge }: { bridge: BuddyBridge }) {
  const [place, setPlace] = useState<BuddyPlace | null>(null)
  // Undefined while it is read; null where this computer cannot keep a window out of a capture.
  const [hidden, setHidden] = useState<boolean | null | undefined>(undefined)
  const failed = (what: string) => (e: unknown) => notify(`Couldn't ${what}: ${String((e as Error)?.message || e)}`, 'error')
  useEffect(() => {
    bridge.getPlace().then(setPlace, failed('read where the buddy sits'))
    bridge.getHideFromCapture().then(setHidden, failed('read whether the buddy is kept out of screen capture'))
  }, [bridge])
  const floating = place?.mode === 'floating'
  return (
    <Section title="This computer" hint="Kept by the desktop app on this computer only.">
      <RowGroup>
        <Row label="Where it sits"
          hint={floating ? 'Floating where you left it. Drag it to the top centre to dock it, or dock it from here.' : 'Docked at the top centre of the screen. Drag it down to float it.'}>
          <div className="flex items-center gap-s">
            <span data-type="body-s" className="text-on-surface-var">{place ? (floating ? 'Floating' : 'Docked') : 'Reading…'}</span>
            {floating && <Button size="sm" variant="secondary" onClick={() => bridge.resetPlace().then(setPlace, failed('dock the buddy'))}>Dock it</Button>}
          </div>
        </Row>
        {hidden === null ? (
          <Row label="Hide from screen sharing and screenshots"
            hint="This computer cannot keep a window out of a share or a screenshot, so the buddy shows in them.">
            <span data-type="body-s" className="text-on-surface-var">Not available on this computer</span>
          </Row>
        ) : (
          <Row label="Hide from screen sharing and screenshots" hint="Keeps what is waiting on you out of a share or a screenshot.">
            <Toggle on={hidden === true} disabled={hidden === undefined} disabledReason="Reading this computer’s setting" size="sm" label="Hide from screen sharing and screenshots"
              onChange={(v) => bridge.setHideFromCapture(v).then(setHidden, failed('change whether screen capture sees the buddy'))} />
          </Row>
        )}
      </RowGroup>
    </Section>
  )
}

/** Settings → Buddy (#3901): every buddy setting in one place. Buddy status is the opt-in, off
 *  until you turn it on; beside it, the surfaces it shows on and how long you are away before it
 *  steps back. What it follows from elsewhere is shown, not set here: the notification posture
 *  (Settings → Notifications) and the system's reduced motion. Where the buddy sits and whether
 *  screen sharing sees it are this machine's, in the desktop app (`ThisComputer`). */
export function BuddyPanel() {
  const [cfg, setCfg] = useState<BuddyCfg | null>(null)
  const { data, error: loadErr, refresh } = useQuery('settings:buddy', () =>
    api.personalclawConfig().then((c) => (c.buddy ?? {}) as BuddyCfg), { persist: true })
  const { data: policy, error: policyErr, status: policyStatus, refresh: refreshPolicy } = useQuery('settings:buddy:policy', () => api.buddyPolicy())
  const reduced = useSystemReducedMotion()
  const buddyBridge = desktopBridge()?.buddy

  useEffect(() => { if (data) setCfg(data) }, [data])
  // A change anywhere (another window, the menu bar, the notification settings) rings once.
  useChatSocket(useCallback((m) => { if (m.type === BUDDY_POLICY_CHANGED) { refreshPolicy(); refresh() } }, [refresh, refreshPolicy]), refreshPolicy)

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={2} what="settings" />

  // Optimistic single-field PATCH; a rejected save rolls back and says so.
  const write = (path: string, key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(path, value).then(() => { onSaved?.(); refreshPolicy() }).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  // One literal path per knob, so each control names the key it writes.
  const patchStatus = (_k: string, v: never, cb: () => void, label?: string) => write('buddy.show', 'show', v, cb, label)
  const patchWindow = (_k: string, v: never, cb: () => void, label?: string) => write('buddy.window', 'window', v, cb, label)
  const patchTray = (_k: string, v: never, cb: () => void, label?: string) => write('buddy.tray', 'tray', v, cb, label)
  const patchDock = (_k: string, v: never, cb: () => void, label?: string) => write('buddy.dock', 'dock', v, cb, label)
  const patchAlerts = (_k: string, v: never, cb: () => void, label?: string) => write('buddy.notifications', 'notifications', v, cb, label)
  const away = Number(cfg.away_after_secs ?? 180)
  const awayOptions = AWAY.some((a) => a.secs === away) ? AWAY : [...AWAY, { secs: away, label: `${Math.round(away / 60)} min` }]
  const on = Boolean(cfg.show)

  return (
    <>
      <PanelHeader title="Buddy" hint="The companion that shows what your agents are doing and asks you when one needs you." />
      <Section title="Buddy status">
        <RowGroup>
          <ToggleRow label="Buddy status" field="show" cfg={cfg} patch={patchStatus}
            hint="Show the buddy: its window, its tray and dock faces and its alerts’ notifications. Off takes every buddy surface away at once, in every window." />
        </RowGroup>
      </Section>
      <Section title="Where it shows" hint={on ? undefined : 'These apply once Buddy status is on.'}>
        <RowGroup>
          <ToggleRow label="Buddy window" field="window" cfg={cfg} patch={patchWindow} hint="Its own window, docked at the top of the screen or floating where you left it." />
          <ToggleRow label="Tray face" field="tray" cfg={cfg} patch={patchTray} hint="Its state on the menu bar or tray icon." />
          <ToggleRow label="Dock face" field="dock" cfg={cfg} patch={patchDock} hint="Its state on the app’s dock icon." />
          <ToggleRow label="Alert notifications" field="notifications" cfg={cfg} patch={patchAlerts}
            hint="Let its alerts raise system notifications, under your notification rules, mute and quiet hours." />
        </RowGroup>
      </Section>
      <Section title="When you’re away">
        <RowGroup>
          <Row label="Steps back after" hint="Docked it hides, floating it dozes. Alerts still appear.">
            <SegPills ariaLabel="When you’re away" value={String(away)}
              onChange={(v) => write('buddy.away_after_secs', 'away_after_secs', Number(v), undefined, 'When you’re away')}
              options={awayOptions.map((a) => ({ key: String(a.secs), label: a.label }))} />
          </Row>
        </RowGroup>
      </Section>
      {buddyBridge && <ThisComputer bridge={buddyBridge} />}
      <Section title="What it follows">
        <div role="group" aria-label="Notifications" className="mb-m flex flex-col gap-xs">
          {!policy && policyStatus === 'error'
            ? <InlineLoadError what="your notification posture" error={policyErr} onRetry={refreshPolicy} />
            : <p data-type="body-s" className="text-on-surface">{policy ? postureLine(policy.notifications) : 'Reading your notification settings…'}</p>}
          <p data-type="body-s" className="text-on-surface-low">
            Mute, quiet hours and which alerts reach you are your notification settings. Change them in{' '}
            <TextLink href="#/settings/notifications" size="sm">Settings → Notifications</TextLink>.
          </p>
        </div>
        <div role="group" aria-label="Reduced motion" className="flex flex-col gap-xs">
          <p data-type="body-s" className="text-on-surface">
            {reduced
              ? 'Your system asks for reduced motion: the buddy moves as little as it can.'
              : 'Your system does not ask for reduced motion.'}
          </p>
          <p data-type="body-s" className="text-on-surface-low">
            The buddy follows your system’s setting. Change it in its Accessibility settings: on a Mac, System Settings →
            Accessibility → Display → Reduce motion; on Windows, Settings → Accessibility → Visual effects → Animation effects.
          </p>
        </div>
      </Section>
    </>
  )
}
