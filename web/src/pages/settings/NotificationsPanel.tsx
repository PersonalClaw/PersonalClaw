import { useEffect, useState } from 'react'
import { api, type NotificationSettings, type NotificationRulesDoc } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Field, Toggle, SegPills, SavedToast } from './settingsUI'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { NotificationRulesMatrix, DigestSchedule } from './NotificationRulesMatrix'
import { notify } from '../../app/appSdk'

const SEVERITIES = [
  { key: 'info', label: 'All' },
  { key: 'warning', label: 'Warnings+' },
  { key: 'error', label: 'Errors only' },
]

/** Notification preferences → GET/PUT /api/notifications/settings, enforced by
 *  the DashboardState.notify() delivery gate (mute / severity / quiet hours). */
export function NotificationsPanel() {
  const [s, setS] = useState<NotificationSettings | null>(null)
  const [saved, setSaved] = useState(false)

  // Settings: stale-while-revalidate + persist; seeded into the optimistic local
  // `s` so saves keep their existing behavior.
  // 🔴 NOT `.catch(() => null)`. A substituted null is indistinguishable from "still loading" to the gate
  // below, so a failed read left this panel shimmering FOREVER with nothing said — measured on
  // `#/settings/notifications` with the GET at 500: 0 controls, one `aria-busy` skeleton, no alert. Same
  // shape cycle 117 found on the inbox panel and cycle 124 on three config panels.
  const { data: settingsData, error: loadErr, refresh } = useCachedData(
    'settings:notification-settings', () => api.notificationSettings(), { persist: true },
  )
  useEffect(() => { if (settingsData) setS(settingsData) }, [settingsData])

  // The per-kind rules matrix. Not persisted to the cache: it's the authoritative view of
  // policy, and serving a stale copy after an edit would show the user a rule they just
  // changed back to its old value.
  // The rules matrix keeps its own fallback: it DECORATES this panel (a per-kind policy table below the
  // settings), so losing it degrades one section rather than fabricating the switches above.
  const { data: rules, refresh: refreshRules } = useCachedData<NotificationRulesDoc | null>(
    'settings:notification-rules', () => api.notificationRules().catch(() => null), { persist: false },
  )
  const reloadRules = () => { invalidateCache('settings:notification-rules'); refreshRules() }

  const patch = (p: Partial<NotificationSettings>) => {
    setS((prev) => prev && { ...prev, ...p })
    // Optimistic locally, silent on failure. A notification setting that did not save while the UI says
    // it did is the worst member of this family: the next missed alert has no explanation.
    api.saveNotificationSettings(p)
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 1600) })
      .catch((e) => notify(`Couldn't save your notification settings: ${String((e as Error)?.message || e)}`, 'error'))
  }

  // Error BEFORE the skeleton, or it is unreachable: `s` is null for loading AND for failure.
  if (!s && loadErr) return <LoadError what="notification settings" error={loadErr} onRetry={refresh} />
  if (!s) return <FormSkeleton sections={2} what="notification settings" />
  return (
    <div>
      <PanelHeader title="Notifications" hint="Control how and when PersonalClaw notifies you." />
      <div className="mb-l flex justify-end"><SavedToast show={saved} /></div>

      <Section title="Delivery">
        <Row label="Mute all notifications" hint="Pause every notification regardless of severity.">
          <Toggle on={s.mute_all} onChange={(v) => patch({ mute_all: v })} label="Mute all" />
        </Row>
        <Field label="Minimum severity" hint="Only notify at or above this level.">
          <SegPills value={s.min_severity} onChange={(v) => patch({ min_severity: v })} options={SEVERITIES} />
        </Field>
      </Section>

      <Section title="Quiet hours" hint="Suppress non-critical notifications during these hours.">
        <Row label="Enable quiet hours">
          <Toggle on={s.quiet_hours_enabled} onChange={(v) => patch({ quiet_hours_enabled: v })} label="Quiet hours" />
        </Row>
        {s.quiet_hours_enabled && (
          <Row label="Window" hint="Start and end (24-hour, server time).">
            <div className="flex items-center gap-2">
              <TimeInput value={s.quiet_hours_start} onChange={(v) => patch({ quiet_hours_start: v })} />
              <span className="text-on-surface-low text-[0.8125rem]">to</span>
              <TimeInput value={s.quiet_hours_end} onChange={(v) => patch({ quiet_hours_end: v })} />
            </div>
          </Row>
        )}
      </Section>

      {/* Per-kind rules sit BELOW the global controls because that's the order they apply
          in: the gate above decides whether anything is delivered at all, and these decide
          how. Rendered only once loaded — an empty matrix would read as "no kinds exist". */}
      {rules && <NotificationRulesMatrix doc={rules} onSaved={reloadRules} />}
      {rules && <DigestSchedule schedule={rules.digest.schedule} onSaved={reloadRules} />}
    </div>
  )
}

function TimeInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  // Only propagate a complete HH:MM — clearing the field emits '' which the
  // backend rejects (an unparseable time silently disabled quiet hours at the
  // delivery gate). The controlled value snaps back, so a clear is a no-op.
  return (
    <input type="time" value={value} onChange={(e) => { if (e.target.value) onChange(e.target.value) }}
      className="h-9 rounded-md bg-surface-container px-2.5 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
  )
}
