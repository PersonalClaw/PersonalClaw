import { useEffect, useState } from 'react'
import { api, type InboxSettings } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, Row, Toggle, SavedToast, StrListField } from './settingsUI'
import { storedChannels, watchedChannelReaders, watchedChannelsHint } from '../inbox/watchedChannels'
import { TriageRulesCard } from './TriageRulesCard'
import { FieldError, NumberField } from '../../ui/forms'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { notify } from '../../app/appSdk'
import { reportActionFailure } from '../../app/reportingWrite'
import { TextLink } from '../../ui/TextLink'

/** Inbox settings → /api/inbox/settings: auto-cleanup retention for the unified inbox.
 *  Alerting lives in the notification rules matrix now. */
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
  // `inbox.sort_messages`: whether each new message goes to the background model to be sorted.
  const [sortOn, setSortOn] = useState<boolean | null>(null)
  // `ProactiveConfig` was wired end to end —
  // dataclass, loader, `to_dict`, the `_EDITABLE_CONFIG` PATCH allowlist — and had NO frontend
  // control, so the round-trip contract's fourth point was open and `triage_enabled` was
  // unreachable from the UI. `null` = not read yet, which is why every toggle below is disabled
  // until it resolves rather than rendering `false` (an unread switch is not an off switch).
  const [triageOn, setTriageOn] = useState<boolean | null>(null)
  const [autoExecOn, setAutoExecOn] = useState<boolean | null>(null)
  // `inbox.watched_channels`, as stored. `null` until the config is read, and the list is not shown
  // until then: an unread list is not an empty one.
  const [channels, setChannels] = useState<string[] | null>(null)
  const [cfgErr, setCfgErr] = useState('')
  // Which polled sources read those channels: the list is shown, named by them, only while one is.
  const { data: sourceList, error: sourcesErr } = useQuery('inbox:providers', () => api.inboxProviders(), { persist: false })
  const channelReaders = watchedChannelReaders(sourceList)

  // Stale-while-revalidate + persist: paint instantly on revisit/reload. The
  // editable form state `s` is seeded/rehydrated from this read-only `data`;
  // the patch handler keeps mutating `s` optimistically + saving.
  // 🔴 `.catch(() => null)` made a failed read RESOLVE with null — so `!data` below fired and the panel
  // sat in its skeleton forever. Measured with `GET /api/inbox/settings` at 500: 0 editable controls, 22
  // shimmering skeleton nodes, no error text, no retry — a dead end that looks like a slow network. Worse,
  // the resolved `null` was PERSISTED: `sessionStorage['cache:settings:inbox'] === "null"`, so the next
  // mount seeded null from cache and could not tell "failed" from "loaded". Same cache-key poisoning as
  // the `'apps'` key, with `null` instead of `[]` — and this key has THREE consumers.
  const { data, error: loadErr, refresh } = useQuery('settings:inbox', () => api.inboxSettings(), { persist: true })
  useEffect(() => { if (data) setS(data) }, [data])

  // 🔴 #532 row 19: this catch used to `setEngagementOn(false); setSourcesOn(false)`, under a comment
  // calling that fallback deliberate ("the two inbox switches keep their historical `false`
  // fallback"). It was not defensible — `false` is this component's value for "read, and the setting
  // is off", so a failed `GET /api/config` rendered two OFF switches as saved configuration on the
  // two flags that decide whether the inbox collects anything at all. The triage pair next to them
  // already did the honest thing for the same rejection, which is what made the inconsistency
  // visible: ONE read, four switches, two of them lying about it. All four stay `null` now — every
  // switch below is disabled until the read succeeds — and the band at the top of the panel reports
  // the rejection with a retry.
  const loadConfig = () => api.personalclawConfig()
    .then((c) => {
      setEngagementOn(Boolean(c?.inbox?.engagement_ranking_enabled))
      setSourcesOn(Boolean(c?.inbox?.enabled))
      setSortOn(Boolean(c?.inbox?.sort_messages))
      setTriageOn(Boolean(c?.proactive?.triage_enabled))
      setAutoExecOn(Boolean(c?.proactive?.auto_execute_enabled))
      setChannels(storedChannels(c?.inbox?.watched_channels))
      setCfgErr('')
    })
    .catch((e) => setCfgErr(String((e as Error)?.message || e)))
  useEffect(() => { loadConfig() }, [])

  const patch = (p: Partial<InboxSettings>) => {
    // Roll back on refusal (#624): the notify below reports the failure, but the
    // optimistic merge stayed put — the field showed a value the server had
    // REFUSED until the next reload. Pre-patch rollback, same as `setTriage`/
    // `setAutoExec` below and `VoicePanel.saveSettings` (`settingsWriteReported`
    // doctrine: keeping a refused value is the one unsanctioned shape).
    const prev = s
    setS((cur) => cur && { ...cur, ...p })
    api.saveInboxSettings(p)
      .then(() => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) })
      .catch((e) => {
        setS(prev)
        notify(`Couldn't save your inbox settings: ${String((e as Error)?.message || e)}`, 'error')
      })
  }

  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1600) }

  // Why a switch with no value is unavailable — and the two cases are different facts. "Still
  // reading" self-heals; a rejection does not, so saying "still reading" after the read FAILED is
  // the hover-text version of the same defect #532 row 19 named in the switch itself.
  const cfgUnread = cfgErr
    ? "Couldn't read your configuration, so this switch has no saved value to show — retry at the top of the panel."
    : 'Still reading your configuration — this switch appears once it loads.'

  // The inbox reads this switch at every poll, so the next one follows it: nothing to restart.
  // A refused save reverts the switch AND says why, like the triage pair below.
  const setSources = (v: boolean) => {
    setSourcesOn(v)
    api.patchConfig('inbox.enabled', v)
      .then(flash)
      .catch((e) => { setSourcesOn(!v); notify(`Couldn't change that: ${String((e as Error)?.message || e)}`, 'error') })
  }

  // Two writes, in this order, and the order matters. The config PATCH is the one source
  // of truth for whether the digest fires; `proactiveInstall` then reconciles the schedule row
  // against it — retiring it on off, re-arming it on on. Patching without reconciling would leave
  // a cron firing for a disabled digest; reconciling without patching would leave the switch and
  // the schedule disagreeing, which the digest card reports as drift.
  const setTriage = (v: boolean) => {
    setTriageOn(v)
    api.patchConfig('proactive.triage_enabled', v)
      // 🔑 THE RECONCILE'S FAILURE IS REPORTED, AND IT IS NOT THE PATCH'S FAILURE. This read
      // `.then(() => api.proactiveInstall().catch(() => undefined))`, so a refused reconcile fell
      // through to `flash` — a SUCCESS confirmation over exactly the state the paragraph above
      // calls wrong: a cron still firing for a disabled digest. Two failures, two truths, so they
      // do not share a handler. The patch landing is what the switch shows, so a failed reconcile
      // must NOT revert it (the setting really did change); and `flash` must not fire either,
      // because the pair did not complete. The outer catch below still owns the patch itself.
      .then(() => api.proactiveInstall()
        .then(flash)
        .catch(reportActionFailure(`${v ? 're-arm' : 'retire'} the digest's schedule`)))
      .catch((e) => { setTriageOn(!v); notify(`Couldn't change that: ${String((e as Error)?.message || e)}`, 'error') })
  }

  const setAutoExec = (v: boolean) => {
    setAutoExecOn(v)
    api.patchConfig('proactive.auto_execute_enabled', v)
      .then(flash)
      .catch((e) => { setAutoExecOn(!v); notify(`Couldn't change that: ${String((e as Error)?.message || e)}`, 'error') })
  }

  // Read before every sorting call, so the next one follows it. A refused save reverts and says why.
  const setSort = (v: boolean) => {
    setSortOn(v)
    api.patchConfig('inbox.sort_messages', v)
      .then(flash)
      .catch((e) => { setSortOn(!v); notify(`Couldn't change that: ${String((e as Error)?.message || e)}`, 'error') })
  }

  const setEngagement = (v: boolean) => {
    setEngagementOn(v)
    api.patchConfig('inbox.engagement_ranking_enabled', v)
      .then(flash)
      .catch(() => setEngagementOn(!v))
  }

  // One channel in or out per write, never this panel's copy of the list (`StrListField`); the
  // chips then show the list as stored. A refused id keeps the list as it was and says why.
  const editChannels = (_key: string, next: string[], onSaved: () => void) => {
    api.saveListEdits('inbox.watched_channels', channels ?? [], next)
      .then((stored) => { setChannels(stored); onSaved() })
      .catch((e) => notify(`Couldn't change the channels to read: ${String((e as Error)?.message || e)}`, 'error'))
  }

  if (!data && loadErr) return <LoadError what="inbox settings" error={loadErr} onRetry={refresh} />
  if (!data || !s) return <FormSkeleton sections={2} what="inbox settings" />
  return (
    <div>
      <PanelHeader title="Inbox" hint="What gets flagged in the unified inbox, and how long items are kept." />
      <div className="mb-l flex justify-end"><SavedToast show={saved} /></div>

      {/* ONE read backs the Collection and Proactive-triage switches, so its failure is reported
          once, here, above both of them — not inside the section that happened to notice first. */}
      {cfgErr && (
        <div className="mb-l">
          <InlineError icon onRetry={loadConfig}>Couldn't read your inbox configuration: {cfgErr}</InlineError>
        </div>
      )}

      {/* Alerting moved to Notifications → Per-kind delivery: keyword /
          name-mention escalation is a `conditions` block on ANY notification rule now, so
          the same rules cover loops and proposals, not just inbox messages. */}
      <Section title="Alerts" hint="Keyword and name-mention alerts are now per-notification-kind.">
        <Row label="Where to configure" hint="One place for every kind of notification, not just inbox items.">
          <TextLink href="#/settings/notifications" ink="emphasis" size="sm">Open notification rules</TextLink>
        </Row>
      </Section>

      {/* Collection + ordering. These two were reachable ONLY from the Inbox side panel, so a
          user who went to Settings → Inbox (the canonical home for every other inbox setting)
          could not turn the drop folder on, and could not find the ranking switch at all. */}
      <Section title="Collection" hint="What the inbox gathers, and how it is ordered.">
        <Row label="Poll the drop folder"
          hint="Collect the messages a program on this machine drops as JSON files in the inbox's incoming folder. Off unless you use one: anything that can write to this machine can drop a file there. Inbox apps you install (Mail Inbox, Slack) are collected while they are enabled, and agents can always post here directly.">
          <Toggle on={!!sourcesOn} onChange={setSources} label="Poll the drop folder" disabled={sourcesOn === null} />
        </Row>
        <Row label="Sort new messages"
          hint="The background model reads each new message and sorts it into Needs reply, FYI or Noise, a few messages per call, inside your daily budget. Off: messages arrive unsorted, nothing is sent to a model for them, and you sort them yourself.">
          <Toggle on={!!sortOn} onChange={setSort} label="Sort new messages" disabled={sortOn === null} disabledReason={cfgUnread} />
        </Row>
        <Row label="Engagement ranking"
          hint="Rank the inbox by how much you engage with each channel/sender (favorites, opens, replies boost; dismisses lower) on top of recency. Off = pure newest-first.">
          <Toggle on={!!engagementOn} onChange={setEngagement} label="Engagement ranking" disabled={engagementOn === null} />
        </Row>
        {sourcesErr ? (
          <FieldError>Couldn't read the inbox sources, so the channels they read can't be shown: {String((sourcesErr as Error)?.message || sourcesErr)}</FieldError>
        ) : channels !== null && channelReaders.length > 0 && (
          <StrListField label="Channels to read" hint={watchedChannelsHint(channelReaders)}
            cfg={{ watched_channels: channels }} field="watched_channels" editList={editChannels}
            placeholder="Add a channel id…" />
        )}
      </Section>

      {/* The digest's own switches, then the rules it taught itself.
          Kept together and in this order because the rules are meaningless without the switch:
          a rules list under a disabled digest reads as dormant-but-kept only when
          the switch that made it dormant is directly above it. */}
      <Section title="Proactive triage" hint="One scheduled digest of what accumulated, with proposals you answer. Off by default; nothing is collected or spent while it is off.">
        <Row label="Morning triage digest" hint="Collect, filter and propose on a schedule. Turning this off retires the schedule and keeps every rule you taught — turning it back on is lossless.">
          <Toggle on={!!triageOn} onChange={setTriage} label="Morning triage digest"
            disabled={triageOn === null}
            disabledReason={cfgUnread} />
        </Row>
        <Row label="Auto-execute the trivial tier" hint="Let the digest perform reversible inbox actions (archive, mark read, mute) on its own, inside your daily budget and per-run cap. Every one is a ledger row with a one-click undo.">
          <Toggle on={!!autoExecOn} onChange={setAutoExec} label="Auto-execute the trivial tier"
            disabled={autoExecOn === null || !triageOn}
            disabledReason={autoExecOn === null
              ? cfgUnread
              : 'Turn the Morning triage digest on first — there is nothing to auto-execute without it.'} />
        </Row>
      </Section>

      <TriageRulesCard />

      <Section title="Retention" hint="Automatically clean up old inbox items.">
        <Row label="Auto-cleanup" hint="Remove items past their retention window.">
          <Toggle on={s.auto_cleanup_enabled} onChange={(v) => patch({ auto_cleanup_enabled: v })} label="Auto-cleanup" />
        </Row>
        {s.auto_cleanup_enabled && (
          <Row label="Retention" hint="How long to keep inbox items (all sources).">
            <div className="flex items-center gap-s">
              <NumberField value={s.retention_days} min={1} max={3650} onChange={(v) => patch({ retention_days: v })} width="w-20" ariaLabel="Retention (days)" />
              <span data-type="caption" className="text-on-surface-low">days</span>
            </div>
          </Row>
        )}
      </Section>
    </div>
  )
}
