import { useEffect, useState } from 'react'
import { api, type InboxSettings } from '../../lib/api'
import { Loading } from '../../ui/ListScaffold'
import { Row, Field, Toggle, SavedToast } from '../settings/settingsUI'
import { NumberField } from '../../ui/forms'
import { notify } from '../../app/appSdk'

/** Inbox settings → GET/PUT /api/inbox/settings (alert keywords, name-mention
 *  alerts, auto-cleanup, retention). Lives in the Inbox SidePanel. */
export function InboxSettingsPanel() {
  const [s, setS] = useState<InboxSettings | null>(null)
  const [saved, setSaved] = useState(false)
  // The engagement-ranking + poll-sources flags live in config.json (InboxConfig),
  // NOT the inbox entity-settings store the rest of this panel uses — so they're
  // read/written via the config PATCH (the ONE place the runtime reads them),
  // never the entity store (which would be a silent no-op toggle).
  const [engagementOn, setEngagementOn] = useState<boolean | null>(null)
  const [sourcesOn, setSourcesOn] = useState<boolean | null>(null)

  useEffect(() => { api.inboxSettings().then(setS).catch(() => setS(null)) }, [])
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
    // The drawer copy of the settings panel — same optimistic-then-silent shape, same fix, so the two
    // copies stay in parity (`settings/inboxSettingsParity.test.ts` guards their fields).
    api.saveInboxSettings(p)
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 1600) })
      .catch((e) => notify(`Couldn't save your inbox settings: ${String((e as Error)?.message || e)}`, 'error'))
  }

  const setEngagement = (v: boolean) => {
    setEngagementOn(v)
    api.patchConfig('inbox.engagement_ranking_enabled', v)
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 1600) })
      .catch(() => setEngagementOn(!v))  // revert the optimistic flip on failure
  }

  const setSources = (v: boolean) => {
    setSourcesOn(v)
    api.patchConfig('inbox.enabled', v)
      .then(() => api.restartInbox())  // re-attach/detach the poll provider live
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 1600) })
      .catch(() => setSourcesOn(!v))
  }

  if (!s) return <Loading />
  return (
    <div className="flex flex-col gap-l">
      <div className="flex justify-end"><SavedToast show={saved} /></div>

      <Row label="Poll message sources"
        hint="Collect messages from connected poll sources (filesystem drops; channel apps). Agents can always post here directly.">
        <Toggle on={!!sourcesOn} onChange={setSources} label="Poll message sources"
          disabled={sourcesOn === null} />
      </Row>

      <Row label="Engagement ranking"
        hint="Rank the inbox by how much you engage with each channel/sender (favorites, opens, replies boost; dismisses lower) on top of recency. Off = pure newest-first.">
        <Toggle on={!!engagementOn} onChange={setEngagement} label="Engagement ranking"
          disabled={engagementOn === null} />
      </Row>

      {/* Alerting moved to Settings → Notifications → Per-kind delivery (plan 42 S3):
          keyword / name-mention escalation is now a `conditions` block on ANY notification
          rule, not two inbox-only fields. Pointing there beats leaving controls that write
          to a store nothing reads. */}
      <Row label="Alerts" hint="Keyword and name-mention alerts are now per-notification-kind, so the same rules cover loops, proposals and messages alike.">
        <a href="#/settings/notifications" className="text-primary text-[0.8125rem] hover:underline">Open notification rules</a>
      </Row>

      <Row label="Auto-cleanup" hint="Automatically prune items past the retention window.">
        <Toggle on={s.auto_cleanup_enabled} onChange={(v) => patch({ auto_cleanup_enabled: v })} label="Auto cleanup" />
      </Row>

      {s.auto_cleanup_enabled && (
        <Field label="Retention (days)" hint="How long to keep inbox items (all sources).">
          <NumberField value={s.retention_days} min={1} max={3650} step={1} onChange={(v) => patch({ retention_days: v })} width="w-28" ariaLabel="Retention (days)" />
        </Field>
      )}
    </div>
  )
}
