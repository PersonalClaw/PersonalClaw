import { useEffect, useState } from 'react'
import { api, type InboxSettings } from '../../lib/api'
import { Loading } from '../../ui/ListScaffold'
import { ChipInput } from '../tasks/formControls'
import { Row, Field, Toggle, SavedToast } from '../settings/settingsUI'

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
    api.saveInboxSettings(p).then(() => { setSaved(true); setTimeout(() => setSaved(false), 1600) }).catch(() => {})
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

      <Field label="Alert keywords" hint="Messages containing these words are flagged for attention.">
        <ChipInput values={s.alert_keywords} onChange={(v) => patch({ alert_keywords: v })} placeholder="add a keyword, Enter" />
      </Field>

      <Row label="Alert on name mention" hint="Flag messages that mention you by name.">
        <Toggle on={s.alert_on_name_mention} onChange={(v) => patch({ alert_on_name_mention: v })} label="Name-mention alerts" />
      </Row>

      <Row label="Auto-cleanup" hint="Automatically prune items past the retention window.">
        <Toggle on={s.auto_cleanup_enabled} onChange={(v) => patch({ auto_cleanup_enabled: v })} label="Auto cleanup" />
      </Row>

      {s.auto_cleanup_enabled && (
        <Field label="Retention (days)" hint="How long to keep inbox items (all sources).">
          <NumInput value={s.retention_days} onChange={(v) => patch({ retention_days: v })} />
        </Field>
      )}
    </div>
  )
}

function NumInput({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <input type="number" min={1} value={value} onChange={(e) => onChange(Number(e.target.value))}
      className="h-9 w-28 rounded-md bg-surface-container px-2.5 text-on-surface text-[0.875rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]" />
  )
}
