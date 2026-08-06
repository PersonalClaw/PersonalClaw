import { useEffect, useState } from 'react'
import { api, type InboxSettings } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Toggle, SavedToast } from './settingsUI'
import { NumberField } from '../../ui/forms'
import { FormSkeleton } from '../../ui/ListScaffold'

/** Inbox settings → /api/inbox/settings: auto-cleanup retention for the unified inbox.
 *  Alerting lives in the notification rules matrix now (plan 42 S3). */
export function InboxSettingsPanel() {
  const [s, setS] = useState<InboxSettings | null>(null)
  const [saved, setSaved] = useState(false)

  // Stale-while-revalidate + persist: paint instantly on revisit/reload. The
  // editable form state `s` is seeded/rehydrated from this read-only `data`;
  // the patch handler keeps mutating `s` optimistically + saving.
  const { data } = useCachedData('settings:inbox', () => api.inboxSettings().catch(() => null), { persist: true })
  useEffect(() => { if (data) setS(data) }, [data])

  const patch = (p: Partial<InboxSettings>) => {
    setS((prev) => prev && { ...prev, ...p })
    api.saveInboxSettings(p).then(() => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) }).catch(() => {})
  }

  if (!data || !s) return <FormSkeleton sections={2} />
  return (
    <div>
      <PanelHeader title="Inbox" hint="What gets flagged in the unified inbox, and how long items are kept." />
      <div className="mb-l flex justify-end"><SavedToast show={saved} /></div>

      {/* Alerting moved to Notifications → Per-kind delivery (plan 42 S3): keyword /
          name-mention escalation is a `conditions` block on ANY notification rule now, so
          the same rules cover loops and proposals, not just inbox messages. */}
      <Section title="Alerts" hint="Keyword and name-mention alerts are now per-notification-kind.">
        <Row label="Where to configure" hint="One place for every kind of notification, not just inbox items.">
          <a href="#/settings/notifications" className="text-primary text-[0.8125rem] hover:underline">Open notification rules</a>
        </Row>
      </Section>

      <Section title="Retention" hint="Automatically clean up old inbox items.">
        <Row label="Auto-cleanup" hint="Remove items past their retention window.">
          <Toggle on={s.auto_cleanup_enabled} onChange={(v) => patch({ auto_cleanup_enabled: v })} label="Auto-cleanup" />
        </Row>
        {s.auto_cleanup_enabled && (
          <Row label="Retention" hint="How long to keep inbox items (all sources).">
            <div className="flex items-center gap-2">
              <NumberField value={s.retention_days} min={1} max={3650} onChange={(v) => patch({ retention_days: v })} width="w-20" ariaLabel="Retention (days)" />
              <span className="text-on-surface-low text-[0.75rem]">days</span>
            </div>
          </Row>
        )}
      </Section>
    </div>
  )
}
