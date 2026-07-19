import { Check, ExternalLink, Loader2 } from 'lucide-react'
import { api, type AppSummary } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader } from './settingsUI'
import { Skeleton } from '../../ui/ListScaffold'
import { Button } from '../../ui/Button'
import { AppConfigFields, useAppConfig } from '../apps/appConfigForm'
import { AppIcon } from '../apps/appIcon'

/** Settings > Apps — the home for non-provider app settings, mirroring how
 *  Settings > Providers hosts provider-app settings. Provider apps configure
 *  their pluggable-provider settings under their entity in Providers; everything
 *  a plain (non-provider) installed app exposes via `setup.configSchema` is
 *  aggregated here so a user reaches every app's settings in one place. */
export function AppsPanel({ navigate }: { navigate?: (p: string) => void }) {
  const { data: apps } = useCachedData<AppSummary[]>(
    'apps', () => api.apps().catch(() => []), { persist: true },
  )

  if (!apps) return <AppsSkeleton />

  // Provider apps are configured in Settings > Providers; this panel lists only
  // NON-provider apps that actually expose configurable settings — an app with
  // nothing to configure has no reason to appear here.
  const configurable = apps.filter((a) => !a.isProvider && a.hasConfig)

  return (
    <div>
      <PanelHeader
        title="Apps"
        hint="Settings contributed by installed apps, all in one place. Provider apps are configured under Settings › Providers; everything else lives here."
      />

      {configurable.length === 0 ? (
        <div className="rounded-lg bg-surface-container px-l py-xl text-center text-on-surface-low text-[0.875rem]">
          No installed apps expose configurable settings. Browse the <button type="button"
            className="text-primary hover:underline" onClick={() => navigate?.('apps')}>Store</button> to add some.
        </div>
      ) : (
        configurable.map((app) => <AppSettingsCard key={app.name} app={app} navigate={navigate} />)
      )}
    </div>
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
            <span className="truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 600' }}>{app.displayName}</span>
            <span className="text-on-surface-low text-[0.7rem] tabular-nums">v{app.version}</span>
            {!app.enabled && <span className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.65rem]">disabled</span>}
          </div>
          {app.description && <div className="truncate text-on-surface-low text-[0.75rem]">{app.description}</div>}
        </div>
        {app.hasUI && (
          <Button variant="ghost" size="sm" onClick={() => navigate?.(`app/${app.name}`)}>
            <ExternalLink size={14} /> Open
          </Button>
        )}
      </div>

      {cfg.loading ? (
        <div className="flex flex-col gap-2"><Skeleton className="h-9 w-full" /><Skeleton className="h-9 w-2/3" /></div>
      ) : (
        <div className="flex flex-col gap-m pl-11">
          <AppConfigFields appName={app.name} props={cfg.props} cur={cfg.cur} set={cfg.set} secretSet={cfg.secretSet} />
          {cfg.err && <div data-type="body-s" className="text-negative">{cfg.err}</div>}
          <div className="flex items-center justify-end gap-2">
            {justSaved && <span className="flex items-center gap-1 text-ok text-[0.75rem]"><Check size={13} /> Saved</span>}
            <Button variant="primary" size="sm" disabled={cfg.busy || !cfg.dirty} onClick={() => cfg.save()}>
              {cfg.busy ? <Loader2 size={14} className="animate-spin" /> : null} Save
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}

function AppsSkeleton() {
  return (
    <div>
      <PanelHeader title="Apps" hint="Settings contributed by installed apps, all in one place." />
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
