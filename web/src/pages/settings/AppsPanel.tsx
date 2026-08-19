import { useEffect, useState } from 'react'
import { Check, ExternalLink, Loader2 } from 'lucide-react'
import { api, type AppSummary } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, ToggleRow } from './settingsUI'
import { Skeleton, LoadingStatus, LoadError, FormSkeleton } from '../../ui/ListScaffold'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { AppConfigFields, useAppConfig } from '../apps/appConfigForm'
import { AppIcon } from '../apps/appIcon'
import { fvs } from '../../design/fontWeight'

/** Settings > Apps — the home for non-provider app settings, mirroring how
 *  Settings > Providers hosts provider-app settings. Provider apps configure
 *  their pluggable-provider settings under their entity in Providers; everything
 *  a plain (non-provider) installed app exposes via `setup.configSchema` is
 *  aggregated here so a user reaches every app's settings in one place. */
export function AppsPanel({ navigate }: { navigate?: (p: string) => void }) {
  const { data: apps } = useCachedData<AppSummary[]>(
    // Shares the `'apps'` cache key with `#/apps` and the settings widget. A `.catch(() => [])` here
    // resolves with an empty list, which `useCachedData` persists — so every OTHER consumer reads `[]`
    // as a success and can never reach its own error branch. Measured: with all `/api/apps*` calls at
    // 500, `sessionStorage['cache:apps']` was `"[]"` and `#/apps` still said "No apps installed".
    'apps', () => api.apps(), { persist: true },
  )

  // Provider apps are configured in Settings > Providers; this panel lists only
  // NON-provider apps that actually expose configurable settings — an app with
  // nothing to configure has no reason to appear here.
  const configurable = (apps ?? []).filter((a) => !a.isProvider && a.hasConfig)

  return (
    <div>
      <PanelHeader
        title="Apps"
        hint="Where apps come from, plus the settings installed apps contribute — all in one place. Provider apps are configured under Settings › Providers; everything else lives here."
      />

      {/* Store sources is `apps.*` config, not per-app config, so it renders on its own read.
          Nesting it under the installed-apps read would blank a config control whenever an
          unrelated list was slow. */}
      <StoreSourcesSection />

      {/* 🔴 The panel header promises two things — "where apps come from, PLUS the settings installed
          apps contribute" — and only the first had a heading. This half rendered as a bare sibling of
          the "Store sources" section, so to assistive tech (and to anyone reading the outline) every
          app's settings appeared to live UNDER "Store sources": not merely unnamed, but attributed to
          the wrong group. The heading holds in the empty state too, because "nothing here" is an
          answer about THIS group and only reads as one if the group is named. */}
      <Section title="Installed app settings" hint="Settings contributed by non-provider apps you have installed. An app with nothing to configure does not appear.">
        {!apps ? <AppCardsSkeleton /> : configurable.length === 0 ? (
          <div className="rounded-lg bg-surface-container px-l py-xl text-center text-on-surface-low text-[0.8125rem]">
            No installed apps expose configurable settings. Browse the <TextLink onClick={() => navigate?.('apps')}>Store</TextLink> to add some.
          </div>
        ) : (
          configurable.map((app) => <AppSettingsCard key={app.name} app={app} navigate={navigate} />)
        )}
      </Section>
    </div>
  )
}

/** Store sources — the `apps.*` config section (one field today).
 *
 *  `registry_source_enabled` is a SEED switch: with it on, first start writes the curated
 *  registry into the Store's git-source list as a removable row. Turning it off here stops a
 *  future seed; it does not retract a source already seeded (remove that in the Store, where
 *  the removal persists). Said plainly in the hint, because a toggle that reads like a live
 *  on/off but only gates seeding is exactly the control users mis-trust. */
function StoreSourcesSection() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  const { data, error, refresh } = useCachedData('settings:apps-config', () =>
    api.personalclawConfig().then((c) => (c.apps ?? {}) as Record<string, unknown>),
    { persist: true },
  )
  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data && error) return <LoadError what="app store settings" error={error} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={1} rows={1} title={false} what="app store settings" />

  // Optimistic single-field PATCH against the allowlisted path; a rejection rolls back and says so.
  // The label is ToggleRow's fourth argument on purpose — the failure toast has to name the control
  // the way the screen does, not by its config key.
  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`apps.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <Section title="Store sources" hint="Where the Store looks for installable apps. A source only ever contributes listings — installing still runs the security scanner, and nothing is installed without your consent.">
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <ToggleRow label="Curated app registry" cfg={cfg} field="registry_source_enabled" patch={patch}
          hint="Ship the community app registry as a default Store source, so registry apps are discoverable out of the box. Added once as a removable source — turning this off stops it being added again, but does not remove one already there (do that in the Store)." />
      </div>
    </Section>
  )
}

/** One configurable app — header (icon + name + Open) and its schema-driven form
 *  rendered inline with a Save button (saves only that app's config). */
function AppSettingsCard({ app, navigate }: { app: AppSummary; navigate?: (p: string) => void }) {
  const cfg = useAppConfig(app.name)
  const justSaved = cfg.savedAt > 0 && !cfg.dirty

  return (
    <section className="mb-l rounded-lg bg-surface-container p-l">
      <div className="mb-m flex items-center gap-3">
        <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-high text-on-surface-low">
          <AppIcon name={app.icon} size={18} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-on-surface text-[0.9375rem]" style={fvs(600)}>{app.displayName}</span>
            <span className="text-on-surface-low text-[0.75rem] tabular-nums">v{app.version}</span>
            {!app.enabled && <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.75rem]">Disabled</span>}
          </div>
          {app.description && <div className="truncate text-on-surface-low text-[0.75rem]">{app.description}</div>}
        </div>
        {app.hasUI && (
          <Button variant="ghost" size="sm" onClick={() => navigate?.(`app/${app.name}`)}>
            <ExternalLink size={14} /> Open
          </Button>
        )}
      </div>

      {cfg.error ? (
        // Same defect as the Apps-page modal: the read's rejection was discarded, so this panel sat
        // on its skeleton forever. `useAppConfig` exposes the error now, so the row can say so.
        <LoadError what="app configuration" error={cfg.error} onRetry={cfg.reload} />
      ) : cfg.loading ? (
        <div className="flex flex-col gap-2"><Skeleton className="h-9 w-full" /><Skeleton className="h-9 w-2/3" /></div>
      ) : (
        <div className="flex flex-col gap-m pl-11">
          <AppConfigFields appName={app.name} props={cfg.props} cur={cfg.cur} set={cfg.set} secretSet={cfg.secretSet} />
          {cfg.err && <div data-type="body-s" className="text-negative">{cfg.err}</div>}
          <div className="flex items-center justify-end gap-2">
            {justSaved && <span className="flex items-center gap-1 text-ok text-[0.75rem]"><Check size={13} /> Saved</span>}
            <Button variant="primary" size="sm" disabled={cfg.busy || !cfg.dirty} disabledReason={!cfg.dirty && !cfg.busy ? 'No changes to save' : undefined} onClick={() => cfg.save()}>
              {cfg.busy ? <Loader2 size={14} className="animate-spin" /> : null} Save
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}

function AppCardsSkeleton() {
  return (
    <div role="status" aria-busy="true">
      {/* The panel header + Store sources now render above this while the app list loads, so the
          skeleton covers only the cards it stands in for. */}
      {/* 🔴 One region, not three: this skeleton carried `aria-busy` on each section with NO role and
          NO name, so it was neither announced nor findable. */}
      <LoadingStatus what="app settings" />
      {Array.from({ length: 3 }).map((_, i) => (
        <section key={i} className="mb-l rounded-lg bg-surface-container p-l" aria-busy="true">
          <div className="mb-m flex items-center gap-3">
            <Skeleton className="size-8 shrink-0 rounded-lg" />
            <div className="flex-1 space-y-2"><Skeleton className="h-3.5 w-1/4" /><Skeleton className="h-3 w-1/2" /></div>
          </div>
          <div className="flex flex-col gap-2 pl-11"><Skeleton className="h-9 w-full" /></div>
        </section>
      ))}
    </div>
  )
}
