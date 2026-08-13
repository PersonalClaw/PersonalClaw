import { useEffect, useState } from 'react'
import { api, type InstalledPackRec } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Field, SavedToast, ToggleRow } from './settingsUI'
import { TextInput } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'

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

  const { data, error: loadErr, refresh } = useCachedData('settings:packs', () =>
    api.personalclawConfig().then((c) => (c.packs ?? {}) as PacksCfg),
    { persist: true },
  )
  const { data: installed } = useCachedData('settings:packs:installed', () =>
    api.packsInstalled().catch(() => [] as InstalledPackRec[]),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  // Error BEFORE the skeleton, or it is unreachable: `data` is undefined for the loading, failed AND
  // empty cases. Same one-line shape `AgentDefaultsPanel` ships for the same endpoint.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={2} what="settings" />

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
    return <div className="rounded-lg bg-surface-container px-4 py-3 text-[0.8125rem] text-on-surface-low">No packs installed yet.</div>
  }
  return (
    <div className="flex flex-col gap-2">
      {packs.map((p) => <PackRow key={p.name} pack={p} />)}
    </div>
  )
}

/** One resolved connector, as the ledger recorded it.
 *
 *  `mode` is `configure` | `substitute` | `skip`, and each mode means something different to a
 *  user reading "did my pack actually work": configure wrote an mcp.json server, substitute bound
 *  a different one, skip left a `connector_missing:<name>` marker. The row said only "Unavailable:
 *  <markers>", which reports the skips and stays silent about everything that succeeded — so a
 *  pack with three configured connectors and no skips looked identical to one with none at all. */
function ConnectorLine({ c }: { c: InstalledPackRec['connectors'][number] }) {
  const skipped = c.mode === 'skip'
  return (
    <div className="flex items-baseline gap-m text-[0.75rem]">
      <span className={`shrink-0 ${skipped ? 'text-warn' : 'text-on-surface-var'}`}>{c.name}</span>
      <div className="min-w-0 flex-1 text-on-surface-low">
        {c.mode}
        {/* Which mcp.json key was written (configure) or which substitute was bound. Empty on
            skip, where the marker below carries the story instead. */}
        {c.server_name && <span> → {c.server_name}</span>}
        {/* `error` is set only when a configure/substitute DEGRADED to skip — the difference
            between "the pack didn't ask for this" and "it asked and something went wrong". */}
        {c.error && <span className="text-warn"> · {c.error}</span>}
        {/* The audit fact the backend describes as "proving a credential reached the store, not
            the pack" — names only, never values (the schema bans value-bearing auth fields). */}
        {!!c.credentials_saved?.length && (
          <span> · saved {c.credentials_saved.join(', ')}</span>
        )}
      </div>
    </div>
  )
}

/** Exported for test: the gate and the per-mode connector rendering are only observable by
 *  rendering the row against a stubbed ledger record — jsdom reports every box as 0, so nothing
 *  about them is measurable from layout. */
export function PackRow({ pack }: { pack: InstalledPackRec }) {
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
  // What the pack actually put on this machine ("skill:cfo-report", "trigger:month-end", …).
  // The ledger exists to answer that without re-deriving it, and the row never showed it: an
  // installed pack was a name and a version, with no way to see what it brought.
  const components = pack.components ?? []
  const connectors = pack.connectors ?? []
  // The backend writes "%Y-%m-%dT%H:%M:%SZ". A malformed or empty value renders nothing rather
  // than "Invalid Date" — a ledger row predating the field is a real case, not an error to show.
  const parsed = pack.installed_at ? new Date(pack.installed_at) : null
  const installedOn = parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleDateString() : ''
  return (
    <div className="rounded-lg bg-surface-container px-4 py-3">
      <Row label={`${pack.name} ${pack.version}`.trim()}
        hint={pack.connector_markers.length > 0 ? `Unavailable: ${pack.connector_markers.join(', ')}` : undefined}>
        {pack.setup_pending && (
          <Button variant="primary" size="sm" disabled={busy} onClick={finishSetup}>Finish setup</Button>
        )}
      </Row>
      {/* Every fact this block can show joins its gate. Gating on components/connectors alone
          would hide a pack that has only a setup id and an install date — the same
          activity-vs-existence mistake the MCP pool tile made. */}
      {(components.length > 0 || connectors.length > 0 || installedOn || (pack.setup_skill && !pack.setup_pending)) && (
        <div className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2">
          {components.length > 0 && (
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[0.75rem]">
              <span className="text-on-surface-low">Installed</span>
              {components.map((c) => (
                <span key={c} className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low">{c}</span>
              ))}
            </div>
          )}
          {connectors.map((c) => <ConnectorLine key={c.name} c={c} />)}
          {/* `setup_skill` is the committed skill id behind the chip above. Shown only when the
              chip is NOT — a pending pack already has the affordance, so naming the id there
              would be redundant; a pack whose setup is done keeps a record of what ran. */}
          {pack.setup_skill && !pack.setup_pending && (
            <div className="text-on-surface-low text-[0.75rem]">Setup skill: {pack.setup_skill}</div>
          )}
          {/* When it landed. Unlike a report's `generated_at`, this is a durable record of a PAST
              event that nothing else on screen can re-derive — "has this pack been here since
              before the thing broke?" has no other answer. Formatted with `toLocaleDateString`
              rather than a new shared helper: one call site does not justify a date primitive. */}
          {installedOn && (
            <div className="text-on-surface-low text-[0.75rem]">Installed {installedOn}</div>
          )}
        </div>
      )}
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
