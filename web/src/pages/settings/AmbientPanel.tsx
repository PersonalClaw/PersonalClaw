import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, RowGroup, ToggleRow, NumberRow } from './settingsUI'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'

// The editable ambient.* fields mirror the backend _EDITABLE_CONFIG allowlist
// (config/loader.py AmbientConfig). Booleans + a few bounded integers, each PATCHed
// as a single allowlisted path via /api/config/personalclaw.
type AmbientCfg = Record<string, unknown>

/** Ambient surfaces — the composable home + generative UI.
 *  Composable home turns saved artifacts into pinnable dashboard tiles (`views_store`
 *  reads it: off composes an empty overlay and refuses a new pin); max tiles caps a view;
 *  refresh cadence sets how often a TTL tile re-runs; generative UI gates agent-authored
 *  widgets (`visualize.py` reads it: off refuses the one primitive every producer goes
 *  through). Each control PATCHes one allowlisted path.
 *
 *  🔴 THE SURFACE-LAYERS AND MENU-BAR ROWS ARE GONE, and so are the config leaves they wrote:
 *  `ambient.surfaces_max_layer` and `ambient.tray_enabled` (issue #3490). Both PATCHed 200 and
 *  survived a reload with nothing reading either, which is the defect `RoutingPanel.tsx`
 *  states the rule for: an inert knob needs its reader wired or its allowlist row dropped.
 *  Neither was wireable here. The layer ceiling is a PROCESS latch by design —
 *  `surface_layers.py` argues persisting it would boot a recovered user into safe mode
 *  forever, and ships two levers (`?safe=1`, `--safe-surfaces`); the row's own hint also
 *  misdescribed the layers it claimed to set ("1 = + tiles" vs L1 = app surfaces). Menu-bar
 *  presence belongs to the Electron shell, which starts the tray unconditionally and reports
 *  it truthfully as the `tray` desktop capability under Security — so that row's default
 *  (off) contradicted shipped behaviour, and honouring it would have removed the menu-bar
 *  item from every desktop user. */
export function AmbientPanel() {
  const [cfg, setCfg] = useState<AmbientCfg | null>(null)

  const { data, error: loadErr, refresh } = useQuery('settings:ambient', () =>
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
  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`ambient.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Ambient surfaces" hint="Your composable home and agent-authored widgets. Nothing here is enabled behind your back — agent tiles are proposals you accept." />

      <Section title="Composable home" hint="Pin saved artifacts as self-refreshing dashboard tiles.">
        <RowGroup>
          <ToggleRow label="Composable home" cfg={cfg} field="tiles_enabled" patch={patch}
            hint="Turn saved artifacts into pinnable dashboard tiles. Off leaves the dashboard as its fixed default layout and refuses new pins — the tiles you already pinned are kept, and come back when you turn this on." />
          <NumberRow label="Max tiles per view" cfg={cfg} field="max_tiles" min={1} max={48} patch={patch}
            hint="Cap on how many artifact tiles a single view can hold — an unbounded home is an unreadable one." />
          <NumberRow label="Default tile refresh (seconds)" cfg={cfg} field="default_refresh_ttl_secs" min={30} max={86400} patch={patch}
            hint="How often a TTL-mode tile re-runs its bound data workflow. A view-trigger binding overrides this." />
        </RowGroup>
      </Section>

      <Section title="Generative UI" hint="Agent-authored widgets, rendered through the typed component registry.">
        <RowGroup>
          <ToggleRow label="Generative UI" cfg={cfg} field="genui_enabled" patch={patch}
            hint="Let agent-authored widgets render through the typed component registry alongside markdown. Off refuses every widget request — in chat, in a workflow node and on a tile — and says so instead of producing one." />
        </RowGroup>
      </Section>
    </div>
  )
}

// ── field renderers ─────────────────────────────────────────────────────────


