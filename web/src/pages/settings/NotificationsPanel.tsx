import { useEffect, useState } from 'react'
import { api, type NotificationSettings } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Field, Toggle, SegPills, SavedToast } from './settingsUI'
import { FormSkeleton } from '../../ui/ListScaffold'

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
  const { data: settingsData } = useCachedData(
    'settings:notification-settings', () => api.notificationSettings().catch(() => null), { persist: true },
  )
  useEffect(() => { if (settingsData) setS(settingsData) }, [settingsData])

  const patch = (p: Partial<NotificationSettings>) => {
    setS((prev) => prev && { ...prev, ...p })
    api.saveNotificationSettings(p).then(() => { setSaved(true); setTimeout(() => setSaved(false), 1600) }).catch(() => {})
  }

  if (!s) return <FormSkeleton sections={2} />
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
    </div>
  )
}

function TimeInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  // Only propagate a complete HH:MM — clearing the field emits '' which the
  // backend rejects (an unparseable time silently disabled quiet hours at the
  // delivery gate). The controlled value snaps back, so a clear is a no-op.
  return (
    <input type="time" value={value} onChange={(e) => { if (e.target.value) onChange(e.target.value) }}
      className="h-9 rounded-md bg-surface-container px-2.5 text-on-surface text-[0.875rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]" />
  )
}
