import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, ToggleRow } from './settingsUI'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'

// The editable legibility.* fields mirror the backend _EDITABLE_CONFIG allowlist
// (config/loader.py LegibilityConfig). Both are runtime-editable booleans.
type LegibilityCfg = Record<string, unknown>

/** Legibility — how PersonalClaw describes its own capabilities. Two switches:
 *  Discover tips (the curated tour on the dashboard + the Discover hub that guides
 *  you to the parts of the system you haven't tried yet) and context files
 *  (marker-fenced PClaw blocks written into opted-in projects' CLAUDE.md / AGENTS.md
 *  so external agents inherit your rules). Each control PATCHes a single allowlisted
 *  path via /api/config/personalclaw. */
export function LegibilityPanel() {
  const [cfg, setCfg] = useState<LegibilityCfg | null>(null)

  // Stale-while-revalidate + persist: paint instantly on revisit, revalidate in
  // the background. Editable form state is seeded/rehydrated from this snapshot.
  const { data, error: loadErr, refresh } = useCachedData('settings:legibility', () =>
    api.personalclawConfig().then((c) => (c.legibility ?? {}) as LegibilityCfg),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  // 🔴 A settings panel must not present FABRICATED values as saved state. `.catch(() => ({}))` made a
  // failed config read resolve with an empty section, so every control below rendered at its fallback —
  // indistinguishable from "this is what you saved" — and the panel offered to edit values it had never
  // loaded. Measured on `#/settings/agent` with `/api/config` at 500: the form rendered in full with no
  // error anywhere. Now the rejection reaches the hook and the form is replaced by the failure.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={1} />

  // Optimistic single-field PATCH; a rejected save rolls back and surfaces the
  // error (a swallowed 400 here would look exactly like a successful save).
  const patch = (key: string, value: boolean, onSaved: () => void) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`legibility.${key}`, value).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Legibility" hint="How PersonalClaw describes its own capabilities — to you on the dashboard, and to the external agents you point at your projects. Both are proposals: nothing here is ever enabled on your behalf." />

      <Section title="Discover" hint="A curated tour of the parts of PersonalClaw you haven't tried yet, on the dashboard and the Discover hub.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <ToggleRow label="Discover tips" cfg={cfg} field="discover_tips" patch={patch}
            hint="Show the dashboard Discover section and the Discover hub — hand-picked tips that guide you to features like Chat, Tasks, Projects, Knowledge, and Automation, each a deep link to try it. A tip auto-hides once you've used that feature; dismiss hides one forever. Nothing is ever enabled on your behalf." />
        </div>
      </Section>

      <Section title="Context for external agents" hint="Write your rules and scored context into project files that other AI tools read.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <ToggleRow label="Context files" cfg={cfg} field="context_adapters" patch={patch}
            hint="Let PClaw render a marker-fenced block into an opted-in project's CLAUDE.md / AGENTS.md / .cursorrules. Only content inside the PCLAW markers is managed; the rest of the file is never touched. Regeneration is manual per project." />
        </div>
      </Section>
    </div>
  )
}

// ── field renderer ────────────────────────────────────────────────────────────
