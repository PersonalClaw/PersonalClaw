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
  // `inbox.enabled` and `inbox.engagement_ranking_enabled` live in config.json (InboxConfig),
  // NOT the inbox entity-settings store the rest of this panel uses — so they go through the
  // config PATCH, the ONE place the runtime reads them. Writing them to the entity store would
  // be a silent no-op toggle. (Same reasoning, same code, as the Inbox side panel: these two
  // controls existed ONLY there, so Settings → Inbox — the canonical home — could not reach
  // them at all.)
  const [engagementOn, setEngagementOn] = useState<boolean | null>(null)
  const [sourcesOn, setSourcesOn] = useState<boolean | null>(null)

  // Stale-while-revalidate + persist: paint instantly on revisit/reload. The
  // editable form state `s` is seeded/rehydrated from this read-only `data`;
  // the patch handler keeps mutating `s` optimistically + saving.
  const { data } = useCachedData('settings:inbox', () => api.inboxSettings().catch(() => null), { persist: true })
  useEffect(() => { if (data) setS(data) }, [data])

  useEffect(() => {
    api.personalclawConfig()
      .then((c) => {
        setEngagementOn(Boolean(c?.inbox?.engagement_ranking_enabled))
        setSourcesOn(Boolean(c?.inbox?.enabled))
      })
      .catch(() => { setEngagementOn(false); setSourcesOn(false) })
  }, [])

  const patch = (p: Partial<InboxSettings>) => {
    setS((prev) => prev && { ...prev, ...p })
    api.saveInboxSettings(p).then(() => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) }).catch(() => {})
  }

  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) }

  const setSources = (v: boolean) => {
    setSourcesOn(v)
    api.patchConfig('inbox.enabled', v)
      .then(() => api.restartInbox())  // re-attach/detach the poll provider live
      .then(flash)
      .catch(() => setSourcesOn(!v))   // revert the optimistic flip on failure
  }

  const setEngagement = (v: boolean) => {
    setEngagementOn(v)
    api.patchConfig('inbox.engagement_ranking_enabled', v)
      .then(flash)
      .catch(() => setEngagementOn(!v))
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

      {/* Collection + ordering. These two were reachable ONLY from the Inbox side panel, so a
          user who went to Settings → Inbox (the canonical home for every other inbox setting)
          could not turn poll sources on, and could not find the ranking switch at all. */}
      <Section title="Collection" hint="What the inbox gathers, and how it is ordered.">
        <Row label="Poll message sources"
          hint="Collect messages from connected poll sources (filesystem drops; channel apps). Agents can always post here directly.">
          <Toggle on={!!sourcesOn} onChange={setSources} label="Poll message sources" disabled={sourcesOn === null} />
        </Row>
        <Row label="Engagement ranking"
          hint="Rank the inbox by how much you engage with each channel/sender (favorites, opens, replies boost; dismisses lower) on top of recency. Off = pure newest-first.">
          <Toggle on={!!engagementOn} onChange={setEngagement} label="Engagement ranking" disabled={engagementOn === null} />
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
