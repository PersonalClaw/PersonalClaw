import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, ToggleRow, NumberRow } from './settingsUI'
import { FormSkeleton } from '../../ui/ListScaffold'

// The editable sources.* fields mirror the backend _EDITABLE_CONFIG allowlist
// (config/loader.py SourcesConfig). One master toggle + bounded integers, each PATCHed
// as a single allowlisted path via /api/config/personalclaw.
type SourcesCfg = Record<string, unknown>

/** Watched sources — the poll engine for feeds, pages and directories you add to your
 *  knowledge library. Master switch parks the whole loop; the intervals set how often a
 *  source is polled (with a network floor that keeps polls from being abusive to a third
 *  party); the caps bound how much one tick and one day can fetch/ingest. Each control
 *  PATCHes one allowlisted path. */
export function SourcesPanel() {
  const [cfg, setCfg] = useState<SourcesCfg | null>(null)

  const { data } = useCachedData('settings:sources', () =>
    api.personalclawConfig().then((c) => (c.sources ?? {}) as SourcesCfg).catch(() => ({} as SourcesCfg)),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data || !cfg) return <FormSkeleton sections={2} />

  // Optimistic single-field PATCH; a rejected save rolls back and surfaces the error
  // (a swallowed 400 would look exactly like a successful save).
  const patch = (key: string, value: unknown, onSaved?: () => void) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`sources.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Watched sources" hint="Poll feeds, pages and local directories into your knowledge library on a schedule. Off parks the loop — nothing is fetched." />

      <Section title="Polling" hint="How often sources are checked, and the floor that keeps polling polite.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <ToggleRow label="Watched sources" cfg={cfg} field="enabled" patch={patch}
            hint="Enable the poll engine. Off parks the loop; sources you add are not fetched until you turn it back on." />
          <NumberRow label="Default poll interval (seconds)" cfg={cfg} field="poll_interval_default_secs" min={300} max={604800} patch={patch}
            hint="How often a source is polled when it does not set its own interval. Clamped up to the network floor." />
          <NumberRow label="Network poll floor (seconds)" cfg={cfg} field="network_floor_secs" min={300} max={604800} patch={patch}
            hint="The fastest any network source is polled regardless of its own setting — the rate floor that keeps a poll from being abusive to the target server." />
        </div>
      </Section>

      <Section title="Limits" hint="Bounds so a busy feed or a runaway config cannot flood ingestion.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <NumberRow label="Max active sources" cfg={cfg} field="max_sources" min={1} max={1000} patch={patch}
            hint="Cap on how many enabled sources the engine arms per tick." />
          <NumberRow label="Max items per poll" cfg={cfg} field="max_items_per_poll" min={1} max={1000} patch={patch}
            hint="How many new items one poll may ingest before the rest wait for the next cycle." />
          <NumberRow label="Daily request budget per source" cfg={cfg} field="daily_request_budget" min={1} max={100000} patch={patch}
            hint="Upper bound on network requests one source may make in a rolling day (enforced by the fetching providers)." />
        </div>
      </Section>
    </div>
  )
}

// ── field renderers ─────────────────────────────────────────────────────────


