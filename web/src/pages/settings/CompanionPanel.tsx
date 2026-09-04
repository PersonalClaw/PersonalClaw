import { Fragment, useEffect, useState } from 'react'
import { Check, Radio, RadioTower, ShieldCheck, ShieldAlert } from 'lucide-react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, RowGroup, ToggleRow, Field, Row, SegPills } from './settingsUI'
import { TextInput } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { serviceWorkerBlockedReason } from '../../app/registerServiceWorker'

// The editable companion.* fields mirror the backend _EDITABLE_CONFIG allowlist
// (config/loader.py CompanionConfig, COMPANION-APPS CA-4). Each control PATCHes one
// allowlisted path via /api/config/personalclaw.
type CompanionCfg = Record<string, unknown>

/** Companion apps — native clients (phone/desktop) over the local or remote gateway.
 *  LAN discovery advertises this gateway on the local network so a client can find it
 *  without a typed URL; instance name is the friendly label a client shows for it. */
export function CompanionPanel() {
  const [cfg, setCfg] = useState<CompanionCfg | null>(null)
  // BA-7's connector toggle lives in the `browse.*` section, not `companion.*`, so it needs its
  // own optimistic copy — one state object holding two config sections would PATCH the wrong path.
  const [browseCfg, setBrowseCfg] = useState<CompanionCfg | null>(null)
  const [nameDraft, setNameDraft] = useState('')
  const [nameSaved, setNameSaved] = useState(false)
  // `mobile.*` (MOBILE-COMPANION MC-5) rides this panel rather than a new tab: it is the
  // same question ("how does my phone talk to this gateway"), and a second Settings tab
  // for two fields is how a settings surface becomes unnavigable.
  const [mobileCfg, setMobileCfg] = useState<CompanionCfg | null>(null)
  const [topicDraft, setTopicDraft] = useState('')
  const [topicSaved, setTopicSaved] = useState(false)

  const { data, error: loadErr, refresh } = useQuery('settings:companion', () =>
    api.personalclawConfig().then((c) => ({
      companion: (c.companion ?? {}) as CompanionCfg,
      browse: (c.browse ?? {}) as CompanionCfg,
    })),
    { persist: true },
  )
  // Same key as #/companion's reader: one collection, one namespace (splitCollectionBusts).
  const { data: pushStatus } = useQuery('companion:push', () => api.pushStatus())
  const { data: mobileData } = useQuery('settings:companion:mobile', () =>
    api.personalclawConfig().then((c) => (c.mobile ?? {}) as CompanionCfg),
    { persist: true },
  )

  // The LIVE advertiser, read separately from the flag that requests it. These two
  // legitimately disagree — a loopback-only gateway advertises nothing by design — and a
  // panel that showed only the toggle would render that disagreement as success.
  const { data: discovery, refresh: refreshDiscovery } = useQuery(
    'settings:companion:discovery', () => api.companionDiscovery(),
  )

  useEffect(() => {
    if (data) {
      setCfg(data.companion)
      setBrowseCfg(data.browse)
      setNameDraft(String(data.companion.instance_name ?? ''))
    }
  }, [data])
  useEffect(() => {
    if (mobileData) { setMobileCfg(mobileData); setTopicDraft(String(mobileData.ntfy_topic_url ?? '')) }
  }, [mobileData])

  // A settings panel must not present fabricated values as saved state — a failed read
  // renders the failure, not controls at their fallback (mirrors AmbientPanel).
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg || !browseCfg) return <FormSkeleton sections={1} what="settings" />

  // Optimistic single-field PATCH; a rejected save rolls back and surfaces the error.
  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`companion.${key}`, value).then(() => {
      onSaved?.()
      // The backend starts or stops the advertiser on this PATCH, so re-read the live
      // state rather than assuming the toggle got what it asked for.
      refreshDiscovery()
    }).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  // The same optimistic shape against the `browse.*` section (BA-7). A separate function rather
  // than a section parameter on the one above: that one also re-reads the mDNS advertiser, which
  // this toggle has nothing to do with.
  const patchBrowse = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = browseCfg[key]
    setBrowseCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`browse.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setBrowseCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  // `mobile.*` writes go to their own dotted prefix. Same optimistic-then-roll-back shape as
  // `patch` above; separate because a shared helper would have to take the prefix as an
  // argument at every call site, which is how the wrong prefix gets written.
  const patchMobile = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = (mobileCfg ?? {})[key]
    setMobileCfg((c) => ({ ...(c ?? {}), [key]: value }))
    api.patchConfig(`mobile.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setMobileCfg((c) => ({ ...(c ?? {}), [key]: prev }))
      // The failure names the CONTROL the user touched, never the config path: "ntfy topic
      // URL" is a thing they just read on screen, `mobile.ntfy_topic_url` is not. The
      // `?? key` fallback stays so a caller that forgets a label prints an identifier
      // instead of "undefined" (`saveFailureNamesTheControl.test.ts` ratchets both halves).
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  // Read at render: the answer depends on how the user reached this page (localhost vs LAN).
  const swBlocked = serviceWorkerBlockedReason()
  const nameDirty = nameDraft !== String(cfg.instance_name ?? '')
  const saveName = () => patch('instance_name', nameDraft, () => {
    setNameSaved(true)
    setTimeout(() => setNameSaved(false), 1500)
  })
  const topicDirty = topicDraft !== String(mobileCfg?.ntfy_topic_url ?? '')
  const saveTopic = () => patchMobile('ntfy_topic_url', topicDraft, () => {
    setTopicSaved(true)
    setTimeout(() => setTopicSaved(false), 1500)
  }, 'ntfy topic URL')

  return (
    <div>
      <PanelHeader title="Companion apps" hint="Native clients — phone or desktop — that connect to this gateway. Nothing is announced on your network unless you turn discovery on." />

      <Section title="Local network" hint="How companion apps find this gateway on your LAN.">
        <RowGroup>
          <ToggleRow label="LAN discovery" cfg={cfg} field="discovery_enabled" patch={patch}
            hint="Advertise this gateway on your local network so companion apps can find it without a typed URL. Off by default — announcing a service on your LAN is an opt-in." />
          <Field label="Instance name" hint="Friendly name companion apps show for this gateway. Empty falls back to the machine hostname.">
            <div className="flex items-center gap-s">
              <div className="flex-1" style={{ maxWidth: 280 }}>
                {/* `surface="high"` because the wrapper above is `bg-surface-container`, which is also
                    TextInput's DEFAULT surface — a default field here painted its own backdrop exactly
                    (measured 1.00:1 in both themes) and, with no at-rest border or shadow, had no edge. */}
                <TextInput value={nameDraft} onChange={setNameDraft} surface="high" placeholder="e.g. Living room Mac" />
              </div>
              <Button size="sm" variant={nameDirty ? 'primary' : 'secondary'}
                disabled={!nameDirty} disabledReason={!nameDirty ? 'No changes to save' : undefined} onClick={saveName}>
                {nameSaved ? <Check size={14} /> : null} {nameSaved ? 'Saved' : 'Save'}
              </Button>
            </div>
          </Field>
          {/* The advertiser's LIVE state, in words as well as tone (a colour-only status
              would fail 1.4.1). `detail` is the backend's sentence for the reason code —
              one vocabulary, so "on but inert" can never read here as "on". */}
          {discovery ? (
            <Row label="Status" hint={discovery.detail}>
              <span className={`inline-flex items-center gap-1.5 text-[0.8125rem] ${discovery.advertising ? 'text-ok' : 'text-on-surface-low'}`}>
                {discovery.advertising ? <Radio size={14} /> : <RadioTower size={14} />}
                {discovery.advertising ? 'Advertising' : 'Not advertising'}
              </span>
            </Row>
          ) : null}
        </RowGroup>
      </Section>

      {/* What the network is actually told. A discovery record is a broadcast — unauthenticated
          and readable by every device on the network — so the owner gets to READ it rather than
          take our word for it. Only rendered while advertising: an empty table beside "not
          advertising" would suggest the record exists and is blank. */}
      {discovery?.advertising ? (
        <Section title="What your network is told" hint="The exact record this gateway broadcasts. It carries no token, no session and no content.">
          <div className="rounded-lg bg-surface-container px-4 py-3">
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-[0.8125rem]">
              <dt className="text-on-surface-low">service</dt>
              <dd className="font-mono break-all">{discovery.service_type}</dd>
              {discovery.addresses.map((addr) => (
                <Fragment key={addr}>
                  <dt className="text-on-surface-low">address</dt>
                  <dd className="font-mono break-all">{addr}:{discovery.port}</dd>
                </Fragment>
              ))}
              {Object.entries(discovery.txt).map(([k, v]) => (
                <Fragment key={k}>
                  <dt className="text-on-surface-low">{k}</dt>
                  <dd className="font-mono break-all">{v}</dd>
                </Fragment>
              ))}
            </dl>
          </div>
        </Section>
      ) : null}

      {/* BROWSER CONTROL (BROWSE-AUTOMATION BA-7). This panel rather than a new surface: the
          connector is a companion client — BA-8 pairs it through the same device-session
          machinery the section above advertises for — so the switch belongs beside the other
          "what may attach to this gateway" decisions.
          The limits are stated in the hint, not left to be discovered: a scheduled run can never
          use this target, and with the switch off a browse task that asks for your browser is
          SKIPPED rather than quietly run on the gateway's own profile. */}
      <Section title="Browser control" hint="Whether a browse task may drive your own browser, with the sites you are already signed in to.">
        <RowGroup>
          <ToggleRow label="Let tasks drive my browser" cfg={browseCfg} field="user_browser_enabled" patch={patchBrowse}
            hint="Off by default. When off, a task that asks for your browser is skipped with a reason — it is never switched to this machine's own browser profile, which has different logins. Scheduled and unattended runs can never use your browser at all." />
        </RowGroup>
      </Section>

      {/* PHONE PUSH (MOBILE-COMPANION MC-5 §C3). Only the TRANSPORT lives here — one
          gateway-wide choice. Whether a given notification pushes at all is Settings →
          Notifications (plan 42's per-kind rules), and turning push on for a specific
          device can only happen ON that device, from `#/companion`. Three surfaces, three
          different questions; collapsing them would put a phone-only control on a desktop
          page that cannot honour it. */}
      <Section title="Phone push" hint="How a wake-up reaches your phone. Every push carries ids only — never the tool, its arguments or any message text.">
        <RowGroup>
          <Field label="Push backend" hint="'Web push' uses your browser's own subscription and needs a keypair from `personalclaw push init`. 'ntfy' publishes to a self-hosted topic. 'Off' sends nothing.">
            <SegPills
              ariaLabel="Push backend"
              value={String(mobileCfg?.push_backend ?? 'webpush')}
              onChange={(v) => patchMobile('push_backend', v, undefined, 'Push backend')}
              options={[
                { key: 'webpush', label: 'Web push' },
                { key: 'ntfy', label: 'ntfy' },
                { key: 'none', label: 'Off' },
              ]}
            />
          </Field>
          {/* Only shown for the backend that reads it — same rule as the ntfy field
              below. Web push cannot deliver anything until the gateway holds a VAPID
              keypair, and a selected backend that silently sends nothing is the
              worst state: this row says the readiness OUT LOUD (words + tone, like
              Install & offline), with the one command that fixes it. */}
          {String(mobileCfg?.push_backend ?? 'webpush') === 'webpush' && pushStatus ? (
            <Row label="Keypair"
              hint={pushStatus.vapid_ready
                ? 'Ready — subscribed devices can receive pushes.'
                : 'Missing — run `personalclaw push init` on the gateway host, then reload. Until then, Web push sends nothing.'}>
              <span className={`inline-flex items-center gap-1.5 text-[0.8125rem] ${pushStatus.vapid_ready ? 'text-ok' : 'text-warn'}`}>
                {pushStatus.vapid_ready ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
                {pushStatus.vapid_ready ? 'Ready' : 'Not set up'}
              </span>
            </Row>
          ) : null}
          {/* Only shown for the backend that reads it. A URL field rendered beside "Web
              push" would look like a setting that does something, and it does not. */}
          {String(mobileCfg?.push_backend ?? '') === 'ntfy' ? (
            <Field label="ntfy topic URL" hint="Full https URL of your topic, e.g. https://ntfy.example/personalclaw. http is refused — a ping must not travel in the clear.">
              <div className="flex items-center gap-s">
                <div className="flex-1" style={{ maxWidth: 320 }}>
                  <TextInput value={topicDraft} onChange={setTopicDraft} surface="high" placeholder="https://ntfy.example/personalclaw" />
                </div>
                <Button size="sm" variant={topicDirty ? 'primary' : 'secondary'}
                  disabled={!topicDirty} disabledReason={!topicDirty ? 'No changes to save' : undefined}
                  onClick={saveTopic}>
                  {topicSaved ? <Check size={14} /> : null} {topicSaved ? 'Saved' : 'Save'}
                </Button>
              </div>
            </Field>
          ) : null}
        </RowGroup>
      </Section>

      {/* INSTALL & OFFLINE (MOBILE-COMPANION T3.1). `serviceWorkerBlockedReason` was written to be
          said out loud — its own docstring argues that "saying so out loud beats an install button
          that silently never appears" — but its only consumer was `console.info`, so the sentence
          reached nobody. A user who opens the dashboard over a LAN address
          (`http://192.168.1.5:10000`) gets no install affordance and no explanation, because a
          service worker needs a secure context and plain http on a LAN is not one.
          This panel is the right home rather than a new surface: it is the MOBILE-COMPANION settings
          surface, and installing the PWA is how the dashboard gets onto a phone.
          The state is reported in WORDS as well as tone — a colour-only status would fail 1.4.1. */}
      <Section title="Install & offline" hint="Whether this browser can install the dashboard as an app and keep its shell available offline.">
        <RowGroup>
          <Row label="Install &amp; offline support"
            hint={swBlocked
              ? `Unavailable — ${swBlocked}.`
              : 'Available — the app shell is cached, and your browser can install this page as an app.'}>
            <span className={`inline-flex items-center gap-1.5 text-[0.8125rem] ${swBlocked ? 'text-warn' : 'text-ok'}`}>
              {swBlocked ? <ShieldAlert size={14} /> : <ShieldCheck size={14} />}
              {swBlocked ? 'Unavailable' : 'Available'}
            </span>
          </Row>
        </RowGroup>
      </Section>
    </div>
  )
}
