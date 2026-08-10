import { useEffect, useState } from 'react'
import { api, type InstalledPackRec } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Field, SavedToast, ToggleRow } from './settingsUI'
import { TextInput } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { FormSkeleton } from '../../ui/ListScaffold'

// The editable packs.* fields mirror the backend _EDITABLE_CONFIG allowlist
// (config/loader.py PacksConfig). A fingerprint toggle + a catalog-refresh URL, each
// PATCHed as a single allowlisted path via /api/config/personalclaw. Skill-catalog LIST
// editing is AP-6's Skills-store surface; this panel wires the two scalars + the installed-
// pack ledger with its re-runnable "Finish setup" chip (AP-3 §3.4).
type PacksCfg = Record<string, unknown>

/** Packs — importable capability bundles. Fingerprinting lets the scanner PROPOSE matching
 *  packs for a project (it never auto-installs); the connector-catalog URL is an optional
 *  published catalog the local set refreshes from. Below, each installed pack shows its
 *  skipped-connector markers and a re-runnable "Finish setup" chip when it ships a setup
 *  interview. Each control PATCHes one allowlisted path. */
export function PacksPanel() {
  const [cfg, setCfg] = useState<PacksCfg | null>(null)

  const { data } = useCachedData('settings:packs', () =>
    api.personalclawConfig().then((c) => (c.packs ?? {}) as PacksCfg).catch(() => ({} as PacksCfg)),
    { persist: true },
  )
  const { data: installed } = useCachedData('settings:packs:installed', () =>
    api.packsInstalled().catch(() => [] as InstalledPackRec[]),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data || !cfg) return <FormSkeleton sections={2} />

  // Optimistic single-field PATCH; a rejected save rolls back and surfaces the error.
  const patch = (key: string, value: unknown, onSaved?: () => void) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`packs.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Packs" hint="Importable capability bundles — skills, templates, agents and connector declarations one user can hand to another." />

      <Section title="Discovery" hint="How packs get proposed for a project. Fingerprinting only ever proposes — it never installs anything on its own.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <ToggleRow label="Project fingerprinting" cfg={cfg} field="fingerprint_enabled" patch={patch}
            hint="Let the zero-LLM scanner propose matching packs for a project (e.g. a Terraform-shaped dir). Off stops scanning." />
        </div>
      </Section>

      <Section title="Connector catalog" hint="An optional published catalog the local connector set refreshes from. Fetched under the CONNECTOR egress profile; empty keeps the seeded bundled set only.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <TextRow label="Connector catalog URL" cfg={cfg} field="connector_catalog_url" patch={patch}
            placeholder="https://example.com/connector_catalog.json"
            hint="Leave empty to use only the bundled starter catalog." />
        </div>
      </Section>

      <Section title="Installed packs" hint="Each imported pack, its skipped-connector markers, and a re-runnable setup interview when it ships one.">
        <InstalledPacks packs={installed ?? []} />
      </Section>
    </div>
  )
}

// ── installed packs + finish-setup chip ──────────────────────────────────────
function InstalledPacks({ packs }: { packs: InstalledPackRec[] }) {
  if (packs.length === 0) {
    return <div className="rounded-lg bg-surface-container px-4 py-3 text-sm text-on-surface-low">No packs installed yet.</div>
  }
  return (
    <div className="flex flex-col gap-2">
      {packs.map((p) => <PackRow key={p.name} pack={p} />)}
    </div>
  )
}

function PackRow({ pack }: { pack: InstalledPackRec }) {
  const [busy, setBusy] = useState(false)
  const finishSetup = () => {
    setBusy(true)
    api.packFinishSetup(pack.name).then((r) => {
      // The interview runs in chat under normal tool approval — surface the slash-command
      // the user runs. Re-runnable: setup_pending stays true, so the chip persists.
      notify(`Run ${r.command} in chat to finish setting up ${pack.name}.`, 'info')
    }).catch((e) => {
      notify(`Couldn't start setup: ${String((e as Error)?.message || e)}`, 'error')
    }).finally(() => setBusy(false))
  }
  return (
    <div className="rounded-lg bg-surface-container px-4 py-3">
      <Row label={`${pack.name} ${pack.version}`.trim()}
        hint={pack.connector_markers.length > 0 ? `Unavailable: ${pack.connector_markers.join(', ')}` : undefined}>
        {pack.setup_pending && (
          <Button variant="primary" size="sm" disabled={busy} onClick={finishSetup}>Finish setup</Button>
        )}
      </Row>
    </div>
  )
}

// ── field renderers ─────────────────────────────────────────────────────────

function TextRow({ label, hint, cfg, field, patch, placeholder }: {
  label: string; hint?: string; cfg: PacksCfg; field: string; placeholder?: string
  patch: (k: string, v: unknown, cb?: () => void) => void
}) {
  const [saved, setSaved] = useState(false)
  const [draft, setDraft] = useState(str(cfg[field]))
  useEffect(() => { setDraft(str(cfg[field])) }, [cfg, field])
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  const commit = () => { if (draft !== str(cfg[field])) patch(field, draft, flash) }
  return (
    <Field label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <TextInput value={draft} onChange={setDraft} placeholder={placeholder} ariaLabel={label} mono
          onKeyDown={(e) => { if (e.key === 'Enter') commit() }} />
        <Button variant="ghost" size="sm" onClick={commit}>Save</Button>
        <SavedToast show={saved} />
      </div>
    </Field>
  )
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}
