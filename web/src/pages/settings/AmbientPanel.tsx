import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, ToggleRow, NumberRow } from './settingsUI'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'

// The editable ambient.* fields mirror the backend _EDITABLE_CONFIG allowlist
// (config/loader.py AmbientConfig). Booleans + a few bounded integers, each PATCHed
// as a single allowlisted path via /api/config/personalclaw.
type AmbientCfg = Record<string, unknown>

/** Ambient surfaces — the composable home + generative UI + the menu-bar companion.
 *  Composable home turns saved artifacts into pinnable dashboard tiles; max tiles caps
 *  a view; refresh cadence sets how often a TTL tile re-runs; generative UI gates
 *  agent-authored widgets; surface layers is the safe-mode ceiling; menu-bar companion
 *  gates the macOS tray. Each control PATCHes one allowlisted path. */
export function AmbientPanel() {
  const [cfg, setCfg] = useState<AmbientCfg | null>(null)

  const { data, error: loadErr, refresh } = useCachedData('settings:ambient', () =>
    api.personalclawConfig().then((c) => (c.ambient ?? {}) as AmbientCfg),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  // 🔴 A settings panel must not present FABRICATED values as saved state. `.catch(() => ({}))` made a
  // failed config read resolve with an empty section, so every control below rendered at its fallback —
  // indistinguishable from "this is what you saved" — and the panel offered to edit values it had never
  // loaded. Measured on `#/settings/agent` with `/api/config` at 500: the form rendered in full with no
  // error anywhere. Now the rejection reaches the hook and the form is replaced by the failure.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={2} what="settings" />

  // Optimistic single-field PATCH; a rejected save rolls back and surfaces the error
  // (a swallowed 400 would look exactly like a successful save).
  const patch = (key: string, value: unknown, onSaved?: () => void) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`ambient.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Ambient surfaces" hint="Your composable home, agent-authored widgets, and the menu-bar companion. Nothing here is enabled behind your back — agent tiles are proposals you accept." />

      <Section title="Composable home" hint="Pin saved artifacts as self-refreshing dashboard tiles.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <ToggleRow label="Composable home" cfg={cfg} field="tiles_enabled" patch={patch}
            hint="Turn saved artifacts into pinnable dashboard tiles. Off leaves the dashboard as its fixed default layout." />
          <NumberRow label="Max tiles per view" cfg={cfg} field="max_tiles" min={1} max={48} patch={patch}
            hint="Cap on how many artifact tiles a single view can hold — an unbounded home is an unreadable one." />
          <NumberRow label="Default tile refresh (seconds)" cfg={cfg} field="default_refresh_ttl_secs" min={30} max={86400} patch={patch}
            hint="How often a TTL-mode tile re-runs its bound data workflow. A view-trigger binding overrides this." />
        </div>
      </Section>

      <Section title="Generative UI & surfaces" hint="Agent-authored widgets and the layered surface overlay.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <ToggleRow label="Generative UI" cfg={cfg} field="genui_enabled" patch={patch}
            hint="Let agent-authored widgets render through the typed component registry alongside markdown." />
          <NumberRow label="Surface layers" cfg={cfg} field="surfaces_max_layer" min={0} max={2} patch={patch}
            hint="The layered-surface ceiling (0 = pure launcher, 1 = + tiles, 2 = full). The safe-mode knob — force 0 to disable the overlay." />
        </div>
      </Section>

      <Section title="Companion" hint="A thin macOS menu-bar app over the existing gateway APIs.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <ToggleRow label="Menu-bar companion" cfg={cfg} field="tray_enabled" patch={patch}
            hint="Enable the macOS menu-bar tray companion. Off by default; macOS only." />
        </div>
      </Section>
    </div>
  )
}

// ── field renderers ─────────────────────────────────────────────────────────


