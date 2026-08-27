import { Fragment, useEffect, useState } from 'react'
import { Check, Radio, RadioTower, ShieldCheck, ShieldAlert } from 'lucide-react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, RowGroup, ToggleRow, Field, Row } from './settingsUI'
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
  const [nameDraft, setNameDraft] = useState('')
  const [nameSaved, setNameSaved] = useState(false)

  const { data, error: loadErr, refresh } = useQuery('settings:companion', () =>
    api.personalclawConfig().then((c) => (c.companion ?? {}) as CompanionCfg),
    { persist: true },
  )

  // The LIVE advertiser, read separately from the flag that requests it. These two
  // legitimately disagree — a loopback-only gateway advertises nothing by design — and a
  // panel that showed only the toggle would render that disagreement as success.
  const { data: discovery, refresh: refreshDiscovery } = useQuery(
    'settings:companion:discovery', () => api.companionDiscovery(),
  )

  useEffect(() => {
    if (data) { setCfg(data); setNameDraft(String(data.instance_name ?? '')) }
  }, [data])

  // A settings panel must not present fabricated values as saved state — a failed read
  // renders the failure, not controls at their fallback (mirrors AmbientPanel).
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={1} what="settings" />

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

  // Read at render: the answer depends on how the user reached this page (localhost vs LAN).
  const swBlocked = serviceWorkerBlockedReason()
  const nameDirty = nameDraft !== String(cfg.instance_name ?? '')
  const saveName = () => patch('instance_name', nameDraft, () => {
    setNameSaved(true)
    setTimeout(() => setNameSaved(false), 1500)
  })

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
