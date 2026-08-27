import { useCallback, useEffect, useState } from 'react'
import { api, type BundledPackRec, type InstalledPackRec, type PackProposalRec, type PackUpdateRec } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { invalidateKeys, useQuery } from '../../lib/data'
import { PanelHeader, Section, RowGroup, Row, Field, SavedToast, ToggleRow } from './settingsUI'
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

  const { data, error: loadErr, refresh } = useQuery('settings:packs', () =>
    api.personalclawConfig().then((c) => (c.packs ?? {}) as PacksCfg),
    { persist: true },
  )
  const { data: installed, refresh: refreshInstalled } = useQuery('settings:packs:installed', () =>
    api.packsInstalled().catch(() => [] as InstalledPackRec[]),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  // ONE owner of the installed-ledger read, passed down to both surfaces that can install.
  // Two components each holding their own `useQuery('settings:packs:installed')` looked
  // fine and wasn't: installing from a proposal card refreshed only that component's copy, so
  // "Installed packs" kept saying "No packs installed yet" and the store row kept offering
  // Install until a full reload. A cached read is not shared state.
  const onInstalled = useCallback(() => {
    invalidateKeys('settings:packs:installed')
    refreshInstalled()
  }, [refreshInstalled])

  // Error BEFORE the skeleton, or it is unreachable: `data` is undefined for the loading, failed AND
  // empty cases. Same one-line shape `AgentDefaultsPanel` ships for the same endpoint.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={2} what="settings" />

  // Optimistic single-field PATCH; a rejected save rolls back and surfaces the error.
  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`packs.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Packs" hint="Importable capability bundles — skills, templates, agents and connector declarations one user can hand to another." />

      <Section title="Discovery" hint="How packs get proposed for a project. Fingerprinting only ever proposes — it never installs anything on its own.">
        <RowGroup>
          <ToggleRow label="Project fingerprinting" cfg={cfg} field="fingerprint_enabled" patch={patch}
            hint="Let the zero-LLM scanner propose matching packs for a project (e.g. a Terraform-shaped dir). Off stops scanning." />
        </RowGroup>
      </Section>

      <Section title="Connector catalog" hint="An optional published catalog the local connector set refreshes from. Fetched under the CONNECTOR egress profile; empty keeps the seeded bundled set only.">
        <RowGroup>
          <TextRow label="Connector catalog URL" cfg={cfg} field="connector_catalog_url" patch={patch}
            placeholder="https://example.com/connector_catalog.json"
            hint="Leave empty to use only the bundled starter catalog." />
        </RowGroup>
      </Section>

      <ProposalsSection onInstalled={onInstalled} />

      <PackStoreSection installed={installed ?? []} onInstalled={onInstalled} />

      <Section title="Installed packs" hint="Each imported pack, its skipped-connector markers, a re-runnable setup interview when it ships one, and an update that never overwrites a component you have edited.">
        <InstalledPacks packs={installed ?? []} />
      </Section>
    </div>
  )
}

// ── §7 propose-only fingerprint cards ────────────────────────────────────────

/** Suggested packs, per project, from the zero-LLM file-shape scanner.
 *
 *  Two properties are visible here on purpose. (1) Nothing installs itself: a card offers
 *  "Install" and "Not for this project", and the second one is remembered forever. (2) The
 *  confidence number arrives with its own derivation — an unexplained score is worse than
 *  none, so the card shows which file patterns and signals matched out of how many declared.
 *
 *  The GET performs the scan, so this section is the on-demand half of §7's "on project-create
 *  and on-demand only". It is loaded when the panel opens and re-run only when the user asks —
 *  nothing here is on a timer. */
export function ProposalsSection({ onInstalled }: { onInstalled: () => void }) {
  const [proposals, setProposals] = useState<PackProposalRec[] | null>(null)
  const [error, setError] = useState<string>('')
  const [busy, setBusy] = useState(false)

  const scan = useCallback(() => {
    setBusy(true)
    setError('')
    api.packProposals()
      .then(setProposals)
      .catch((e) => setError(String((e as Error)?.message || e)))
      .finally(() => setBusy(false))
  }, [])

  useEffect(() => { scan() }, [scan])

  const reject = (p: PackProposalRec) => {
    api.packRejectProposal(p.project_id, p.pack).then(() => {
      // Drop it locally too: the backend will never return it again, and leaving the card on
      // screen until a refetch would make a permanent decision look like it didn't take.
      setProposals((cur) => (cur ?? []).filter((x) => !(x.project_id === p.project_id && x.pack === p.pack)))
      notify(`${p.displayName} won't be suggested for this project again.`, 'info')
    }).catch((e) => notify(`Couldn't record that: ${String((e as Error)?.message || e)}`, 'error'))
  }

  const install = (p: PackProposalRec) => {
    setBusy(true)
    api.packBundledInstall(p.pack).then(() => {
      notify(`${p.displayName} installed. Its triggers are disabled and its roster is staged until you enable them.`, 'success')
      // Both: the proposal list (an installed pack is never proposed again) AND the installed
      // ledger the sections below read.
      onInstalled()
      scan()
    }).catch((e) => notify(`Couldn't install ${p.displayName}: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }

  return (
    <Section
      title="Suggested for your projects"
      hint="Matched by file shape only — no model reads your code. A suggestion never installs anything, and declining one is remembered for that project."
      right={<Button variant="ghost" size="sm" disabled={busy} onClick={scan}>{busy ? 'Scanning…' : 'Suggest packs'}</Button>}
    >
      {error && (
        <div className="rounded-lg bg-surface-container px-4 py-3 text-[0.8125rem] text-warn">Couldn't scan for suggestions: {error}</div>
      )}
      {!error && proposals !== null && proposals.length === 0 && (
        <div className="rounded-lg bg-surface-container px-4 py-3 text-[0.8125rem] text-on-surface-low">
          No pack matches any project's workspace. Bind a project to a codebase directory to get suggestions.
        </div>
      )}
      {!error && proposals === null && (
        <div className="rounded-lg bg-surface-container px-4 py-3 text-[0.8125rem] text-on-surface-low">Scanning your projects…</div>
      )}
      <div className="flex flex-col gap-2">
        {(proposals ?? []).map((p) => (
          <ProposalCard key={`${p.project_id}:${p.pack}`} proposal={p} busy={busy} onInstall={install} onReject={reject} />
        ))}
      </div>
    </Section>
  )
}

/** Exported for test: the confidence derivation and the propose-only affordances are only
 *  observable by rendering a card against a stubbed proposal. */
export function ProposalCard({ proposal, busy, onInstall, onReject }: {
  proposal: PackProposalRec
  busy: boolean
  onInstall: (p: PackProposalRec) => void
  onReject: (p: PackProposalRec) => void
}) {
  const top = proposal.matches[0]
  const pct = Math.round(proposal.confidence * 100)
  const would = proposal.inspect?.components ?? []
  return (
    <div className="rounded-lg bg-surface-container px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-m">
        <div className="min-w-0">
          <div className="text-on-surface text-[0.8125rem]">
            {proposal.displayName} {proposal.version}
            {/* The score, and immediately the reason for it. A bare percentage would be a
                number the user has to trust; the line under it is the arithmetic. */}
            <span className="ml-2 rounded-pill bg-surface-high px-2 py-0.5 text-[0.75rem] text-on-surface-var">{pct}% match</span>
          </div>
          {top && (
            <div className="mt-0.5 text-on-surface-low text-[0.75rem]">
              Looks like a {top.label.toLowerCase()} — {top.matched_globs.length} of {top.declared_globs.length} file patterns
              {top.declared_signals.length > 0 && <> and {top.matched_signals.length} of {top.declared_signals.length} content signals</>}
              {' '}matched, against a declared ceiling of {Math.round(top.declared_confidence * 100)}%.
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="primary" size="sm" disabled={busy} onClick={() => onInstall(proposal)}>Install</Button>
          <Button variant="ghost" size="sm" onClick={() => onReject(proposal)}>Not for this project</Button>
        </div>
      </div>
      <div className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2 text-[0.75rem]">
        <div className="text-on-surface-low">{proposal.description}</div>
        {/* Example matched paths. A score with no example path is unreviewable — this is how a
            user confirms the scanner looked at their project and not at a vendored copy. */}
        {top?.evidence.length ? (
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="text-on-surface-low">Matched</span>
            {top.evidence.map((e) => (
              <span key={e} className="rounded-pill bg-surface-high px-2 py-0.5 font-mono text-on-surface-low">{e}</span>
            ))}
            <span className="text-on-surface-low">of {proposal.files_scanned} files scanned</span>
          </div>
        ) : null}
        {/* The §3.1 inspect report: what installing WOULD put on this machine. Shown on the
            card because "here's what it would install" is the whole difference between a
            proposal and an ad. */}
        {would.length > 0 && (
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="text-on-surface-low">Would install</span>
            {would.map((c) => (
              <span key={`${c.kind}:${c.orig_id}`} className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low">{c.kind}:{c.target_id}</span>
            ))}
          </div>
        )}
        {proposal.inspect_error && (
          <div className="text-warn">Couldn't preview what this would install: {proposal.inspect_error}</div>
        )}
      </div>
    </div>
  )
}

// ── the pack store ───────────────────────────────────────────────────────────

/** The packs shipped in this build. Installing one runs the full import pipeline — every
 *  component scanned, triggers landing disabled, the roster staged until a human deploys it. */
export function PackStoreSection({ installed, onInstalled }: {
  installed: InstalledPackRec[]
  onInstalled: () => void
}) {
  const [busy, setBusy] = useState('')
  const { data: bundled, error, refresh } = useQuery('settings:packs:bundled', () =>
    api.packsBundled().catch(() => [] as BundledPackRec[]),
    { persist: true },
  )
  // The installed set is the PARENT's read, passed down — not a second copy of the same query.
  const have = new Set(installed.map((p) => p.name))

  const install = (name: string, label: string) => {
    setBusy(name)
    api.packBundledInstall(name).then(() => {
      notify(`${label} installed. Its triggers are disabled and its roster is staged until you enable them.`, 'success')
      onInstalled()
    }).catch((e) => notify(`Couldn't install ${label}: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(''))
  }

  return (
    <Section title="Pack store" hint="The packs shipped in this build. Installing one scans every component, lands its triggers disabled, and stages its roster until you deploy it.">
      {error ? <LoadError what="pack catalog" error={error} onRetry={refresh} /> : null}
      <div className="flex flex-col gap-2">
        {(bundled ?? []).map((p) => (
          <RowGroup key={p.name}>
            <Row label={`${p.displayName} ${p.version}`.trim()} hint={p.description}>
              {have.has(p.name)
                ? <span className="text-[0.75rem] text-on-surface-low">Installed</span>
                : <Button variant="primary" size="sm" disabled={busy === p.name} onClick={() => install(p.name, p.displayName)}>Install</Button>}
            </Row>
          </RowGroup>
        ))}
      </div>
    </Section>
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

/** The pack row's connector warning, in words.
 *
 *  🔴 IT RENDERED THE MACHINE CODE. `packs/connectors.py` is explicit about what these strings are:
 *  "the machine-readable degraded-completion marker for a skipped connector … a stable code, NEVER
 *  PROSE, so a UI can branch on it". The row branched on it by printing it — measured on
 *  `#/settings/packs` with `health-os` installed, the hint read
 *  **"Unavailable: connector_missing:health-records"**.
 *
 *  Two things wrong, not one. The code leaked, and "Unavailable" overstates it: the pack installed
 *  fine and all eight of its components are on the machine — one connector was skipped, which the
 *  backend itself calls *degraded* completion. `MISSING_PREFIX` (`connector_missing:`) is the only
 *  marker shape there is, so this parse is bounded; an unrecognised marker is still SHOWN verbatim
 *  rather than swallowed, because a code nobody planned for is better read than hidden.
 *
 *  Exported for test — the branches are the finding. */
export function connectorWarning(markers: string[]): string | undefined {
  if (!markers.length) return undefined
  const PREFIX = 'connector_missing:'
  const named = markers.filter((m) => m.startsWith(PREFIX)).map((m) => m.slice(PREFIX.length)).filter(Boolean)
  const other = markers.filter((m) => !m.startsWith(PREFIX))
  const parts: string[] = []
  if (named.length) parts.push(`Needs ${named.length === 1 ? 'a connector' : 'connectors'}: ${named.join(', ')}`)
  if (other.length) parts.push(other.join(', '))
  return parts.join(' · ')
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
  const [update, setUpdate] = useState<PackUpdateRec | null>(null)
  // Dry-run FIRST, always. The interesting output of an update is the skip list — which of
  // your edited copies it would leave alone — and applying before seeing that is exactly the
  // mistake the pack_owned rule exists to prevent.
  const checkUpdate = () => {
    setBusy(true)
    api.packUpdate(pack.name, false).then((r) => {
      setUpdate(r.update)
      if (r.update.components.length === 0) notify(`${pack.name} has nothing to update.`, 'info')
    }).catch((e) => notify(`Couldn't check for an update: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }
  const applyUpdate = () => {
    setBusy(true)
    api.packUpdate(pack.name, true).then((r) => {
      setUpdate(r.update)
      const kept = r.update.drift_notes.length
      notify(
        kept > 0
          ? `${pack.name} updated. ${r.update.overwritten.length} replaced; ${kept} of your edited copies kept.`
          : `${pack.name} updated — ${r.update.overwritten.length} component(s) replaced.`,
        'success',
      )
    }).catch((e) => notify(`Couldn't update ${pack.name}: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }
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
    <RowGroup>
      <Row label={`${pack.name} ${pack.version}`.trim()}
        hint={connectorWarning(pack.connector_markers)}>
        <div className="flex items-center gap-2">
          {pack.setup_pending && (
            <Button variant="primary" size="sm" disabled={busy} onClick={finishSetup}>Finish setup</Button>
          )}
          <Button variant="ghost" size="sm" disabled={busy} onClick={checkUpdate}>
            {busy ? 'Checking…' : 'Check for update'}
          </Button>
        </div>
      </Row>
      {update && <UpdatePreview update={update} busy={busy} onApply={applyUpdate} />}
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
    </RowGroup>
  )
}

/** The §1 update preview: what would be replaced, and — the load-bearing half — which of your
 *  edited copies would be kept, with the reason for each.
 *
 *  Exported for test. A silent skip is indistinguishable from a clobber to anyone reading the
 *  result, so the drift note is part of the contract rather than a nicety, and it renders
 *  BEFORE the apply button so the decision is informed. */
export function UpdatePreview({ update, busy, onApply }: {
  update: PackUpdateRec
  busy: boolean
  onApply: () => void
}) {
  const kept = update.components.filter((c) => c.action === 'skip_drift' || c.action === 'skip_unverifiable')
  const notOwned = update.components.filter((c) => c.action === 'skip_not_pack_owned')
  return (
    <div className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2 text-[0.75rem]">
      <div className="flex flex-wrap items-baseline justify-between gap-m">
        <span className="text-on-surface-var">
          {update.applied ? 'Updated' : 'Update available'}: {update.from_version} → {update.to_version}
          {' · '}{update.overwritten.length} to replace, {update.skipped.length} to keep
        </span>
        {!update.applied && update.overwritten.length > 0 && (
          <Button variant="primary" size="sm" disabled={busy} onClick={onApply}>Apply update</Button>
        )}
      </div>
      {/* Every kept copy, named, with the reason. This is the "visible drift note" the §1
          contract requires — an update that skipped silently would look identical to one that
          quietly overwrote the user's work. */}
      {kept.map((c) => (
        <div key={c.ref} className="flex items-baseline gap-m">
          <span className="shrink-0 text-warn">{c.ref}</span>
          <span className="min-w-0 flex-1 text-on-surface-low">{c.reason}</span>
        </div>
      ))}
      {notOwned.length > 0 && (
        <div className="text-on-surface-low">
          Not owned by this pack, so untouched: {notOwned.map((c) => c.ref).join(', ')}
        </div>
      )}
      {update.overwritten.length > 0 && (
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-on-surface-low">{update.applied ? 'Replaced' : 'Would replace'}</span>
          {update.overwritten.map((ref) => (
            <span key={ref} className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low">{ref}</span>
          ))}
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
        {/* `surface="high"`: every caller of this row sits inside a `bg-surface-container` block, which
            is also TextInput's DEFAULT surface, so a default field painted exactly its own backdrop and
            — with no at-rest border or shadow — had no visible edge (measured 1.00:1, both themes). */}
        <TextInput value={draft} onChange={setDraft} placeholder={placeholder} ariaLabel={label} mono surface="high"
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
