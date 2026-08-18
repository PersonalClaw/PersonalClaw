import { useEffect, useState } from 'react'
import { epochSeconds } from '../../lib/epoch'
import { AlertTriangle, HardDriveDownload, Loader2, ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react'
import {
  api,
  type DurabilityArchive,
  type DurabilityArchives,
  type DurabilityConflict,
  type DurabilityConflictChoice,
  type DurabilityConflicts,
  type DurabilityStatus,
  type SettingsProvider,
} from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Toggle, SavedToast } from './settingsUI'
import { NumberField, Select } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { fvs } from '../../design/fontWeight'
import { confirm } from '../../ui/dialog'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'

/** Scheduled backups (DURABILITY-AND-SYNC §3).
 *
 *  The service, the retention tiers, the restore drill and three endpoints all
 *  shipped — with no frontend at all, so the schedule was invisible and its config
 *  was file-editable only. This panel closes both halves: the five `durability.*`
 *  fields get their control (the config contract's fifth leg), and the existing
 *  status/snapshots endpoints finally have a reader.
 *
 *  §6 (DAS-10) adds the ARCHIVE BROWSER below: each snapshot's per-domain counts read
 *  from its own manifest, the last drill's verdict, a plan-first preview and a
 *  merge-restore. `replace` restore is still command-line only — the server refuses one
 *  while the gateway runs and this panel IS the gateway, so a replace button here would
 *  always fail, and a control that reliably errors is worse than no control. */
export function DurabilityPanel() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)

  const { data, error: loadErr, refresh } = useCachedData('settings:durability', async () => {
    const [plaw, status, snaps, conflicts, transports] = await Promise.all([
      // The five `durability.*` controls come from here, so a fabricated `{}` would show a retention
      // schedule nobody configured. Status and snapshots keep their fallbacks: they DECORATE the panel
      // (a status strip and a snapshot list) rather than defining what its controls claim.
      api.personalclawConfig(),
      api.durabilityStatus().catch(() => null),
      api.durabilityArchive().catch(() => null),
      // The conflict queue and the transport list keep their REJECTION rather than collapsing
      // to null: a failed read of a review queue must not render as "nothing to review", and a
      // failed read of the installed transports must not render as "none installed". Both are
      // claims about the user's data, and both would be wrong.
      settle(api.durabilityConflicts(SURFACE_DURABILITY)),
      settle(api.settingsProviders().then((ps) => ps.filter((p) => p.provider?.type === 'sync'))),
    ])
    return {
      durability: (plaw.durability ?? {}) as Record<string, unknown>,
      status,
      snaps,
      conflicts,
      transports,
    }
  }, { persist: true })

  useEffect(() => { if (data) setCfg(data.durability) }, [data])

  // Error BEFORE the skeleton, or it is unreachable: `data` is undefined for the loading, failed AND
  // empty cases. Same one-line shape `AgentDefaultsPanel` ships for the same endpoint.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={2} what="settings" />

  return (
    <div>
      <PanelHeader
        title="Backups"
        hint="What gets backed up automatically, how long copies are kept, and whether a restore is ever actually rehearsed." />
      <ScheduleSection cfg={cfg} setCfg={setCfg} status={data.status} />
      <RetentionSection cfg={cfg} setCfg={setCfg} snaps={data.snaps} />
      <ArchiveSection snaps={data.snaps} onChanged={refresh} />
      <SyncSection cfg={cfg} setCfg={setCfg} status={data.status} transports={data.transports} />
      <ConflictsSection read={data.conflicts} onChanged={refresh} />
    </div>
  )
}

/** Keep a rejected read as a REJECTION. `Promise.all` with a `.catch(() => null)` turns
 *  "we could not ask" into "the answer is nothing", which for a review queue is the exact
 *  lie this panel must not tell. */
type Settled<T> = { ok: true; value: T } | { ok: false; error: string }

function settle<T>(p: Promise<T>): Promise<Settled<T>> {
  return p.then(
    (value) => ({ ok: true as const, value }),
    (e: unknown) => ({ ok: false as const, error: e instanceof Error ? e.message : String(e) }),
  )
}

// ── The schedule (durability.auto_backup / restore_drills) ───────────────────

function ScheduleSection({ cfg, setCfg, status }: {
  cfg: Record<string, unknown>
  setCfg: (c: Record<string, unknown>) => void
  status: DurabilityStatus | null
}) {
  const [saved, flash] = useSavedFlash()
  const [running, setRunning] = useState('')
  const patch = usePatch(cfg, setCfg, flash)
  const autoBackup = cfg.auto_backup !== false

  const run = async (job: 'export' | 'snapshot' | 'drill', label: string) => {
    setRunning(job)
    try {
      const r = await api.durabilityRun(job)
      // `skipped` is the REASON no work happened, not a flag — and a skip is not a
      // failure (a concurrent run held the lock, or there was nothing to do yet). Show
      // the server's own reason rather than inventing one, and don't call it an error.
      notify(
        r.skipped || r.detail || `${label} finished`,
        r.ok || r.skipped ? 'success' : 'error',
      )
    } catch (e) {
      notify(`${label} failed: ${String((e as Error)?.message || e)}`, 'error')
    } finally {
      setRunning('')
    }
  }

  return (
    <Section title="Schedule" hint="Backups run in the background so losing work never depends on remembering to run one.">
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <Row label="Automatic backups"
          hint="Take a nightly snapshot and an hourly incremental export in the background. Off means backups only happen when you run them by hand — below, or with `personalclaw backup export`.">
          <div className="flex items-center gap-2">
            <SavedToast show={saved} />
            <Toggle on={autoBackup} onChange={(v) => patch('auto_backup', v)} label="Automatic backups" />
          </div>
        </Row>
        <Row label="Monthly restore drill"
          hint="Once a month, restore the newest snapshot into a temporary directory and verify it — a backup nobody has restored is a hope, not a backup. Never touches live data.">
          <Toggle on={cfg.restore_drills !== false} onChange={(v) => patch('restore_drills', v)} label="Monthly restore drill" />
        </Row>

        {status && (
          <div className="border-t border-outline-var py-3">
            <div className="mb-2 text-on-surface-low text-[0.75rem]">
              {status.enabled
                ? 'Last run of each job:'
                : 'Automatic backups are off — these are the last runs from when they were on.'}
            </div>
            <div className="flex flex-col gap-1.5">
              <JobLine label="Incremental export" when={status.export.last_run} due={status.export.due} />
              <JobLine label="Nightly snapshot" when={status.snapshot.last_run} due={status.snapshot.due} />
              <JobLine label="Restore drill" when={status.drill.last_run} due={status.drill.due} />
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 border-t border-outline-var py-3">
          <span className="mr-1 text-on-surface-var text-[0.8125rem]">Run now:</span>
          <RunButton label="Export" icon={HardDriveDownload} busy={running === 'export'}
            disabled={!!running} onClick={() => run('export', 'Export')} />
          <RunButton label="Snapshot" icon={HardDriveDownload} busy={running === 'snapshot'}
            disabled={!!running} onClick={() => run('snapshot', 'Snapshot')} />
          <RunButton label="Verify a restore" icon={ShieldCheck} busy={running === 'drill'}
            disabled={!!running} onClick={() => run('drill', 'Restore drill')} />
        </div>
        <p className="pb-3 text-on-surface-low text-[0.75rem]">
          To restore, use the archive list below. A full <em>replace</em> restore stays a
          command-line action — <code>personalclaw restore --replace</code> — because it has to
          overwrite live state while the gateway is stopped.
        </p>
      </div>
    </Section>
  )
}

// ── Retention (durability.keep_daily / keep_weekly / keep_monthly) ───────────

function RetentionSection({ cfg, setCfg, snaps }: {
  cfg: Record<string, unknown>
  setCfg: (c: Record<string, unknown>) => void
  snaps: DurabilityArchives | null
}) {
  const [saved, flash] = useSavedFlash()
  const patch = usePatch(cfg, setCfg, flash)
  const pruneCount = snaps?.would_prune.length ?? 0

  return (
    <Section title="How long copies are kept"
      hint="Older snapshots thin out rather than piling up: dailies become weeklies, weeklies become monthlies. 0 disables a tier.">
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <NumberRow label="Daily snapshots" saved={saved}
          hint="How many days of nightly snapshots to keep before thinning to weeklies."
          value={num(cfg.keep_daily, 14)} min={0} max={365} suffix="days"
          onCommit={(n, l) => patch('keep_daily', n, undefined, l)} />
        <NumberRow label="Weekly snapshots" saved={saved}
          hint="How many weeks to keep one snapshot each."
          value={num(cfg.keep_weekly, 8)} min={0} max={260} suffix="weeks"
          onCommit={(n, l) => patch('keep_weekly', n, undefined, l)} />
        <NumberRow label="Monthly snapshots" saved={saved}
          hint="How many months to keep one snapshot each."
          value={num(cfg.keep_monthly, 12)} min={0} max={120} suffix="months"
          onCommit={(n, l) => patch('keep_monthly', n, undefined, l)} />

        {pruneCount > 0 && (
          <div className="border-t border-outline-var py-3 text-on-surface-low text-[0.8125rem]">
            {pruneCount} of {snaps?.archives.length ?? 0} snapshots would be removed by the
            settings above on the next pass. They are struck through in the archive below.
          </div>
        )}
      </div>
    </Section>
  )
}

// ── The archive browser (DURABILITY-AND-SYNC §6, DAS-10) ─────────────────────

/** The archive browser §6 asks for: date, size, per-domain counts read from each
 *  archive's own manifest, the last drill's verdict on the archive it exercised, and a
 *  restore that is plan-first.
 *
 *  Restore honesty: PREVIEW and MERGE work from here, REPLACE does not — the server
 *  refuses a replace while the gateway is running, and this panel IS the gateway. So
 *  replace is named as a command-line action rather than offered as a button that would
 *  always fail. A control that reliably errors is worse than no control. */
function ArchiveSection({ snaps, onChanged }: {
  snaps: DurabilityArchives | null
  onChanged: () => void
}) {
  const [busy, setBusy] = useState('')
  const [plan, setPlan] = useState<{ id: string; text: string } | null>(null)

  if (!snaps) {
    return (
      <Section title="Archive" hint="Every snapshot on disk, what is in it, and whether a restore from it has been verified.">
        <div className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low text-[0.8125rem]">
          The archive list could not be read. The backups above may still be running —
          reload to try again.
        </div>
      </Section>
    )
  }

  const preview = async (a: DurabilityArchive) => {
    setBusy(a.id); setPlan(null)
    try {
      // No `mode` — the server returns the plan and changes nothing.
      const r = await api.durabilityArchiveRestore(a.id)
      setPlan({ id: a.id, text: JSON.stringify(r, null, 1) })
    } catch (e) {
      notify(`Couldn't read the restore plan: ${String((e as Error)?.message || e)}`, 'error')
    }
    setBusy('')
  }

  const mergeRestore = async (a: DurabilityArchive) => {
    if (!(await confirm({
      title: 'Merge this snapshot in?',
      body: `Restore "${a.name}" in MERGE mode. Anything this instance already has is kept untouched; the snapshot only fills in what is missing.`,
      confirmLabel: 'Merge-restore',
    }))) return
    setBusy(a.id)
    try {
      const r = await api.durabilityArchiveRestore(a.id, { mode: 'merge', confirm: true })
      notify(r.ok === false ? `Restore refused: ${r.error?.message ?? 'unknown reason'}` : `Merged ${a.name}`, r.ok === false ? 'error' : 'success')
      onChanged()
    } catch (e) {
      notify(`Restore failed: ${String((e as Error)?.message || e)}`, 'error')
    }
    setBusy('')
  }

  return (
    <Section title="Archive" hint="Every snapshot on disk, what is in it, and whether a restore from it has been verified.">
      <div className="rounded-lg bg-surface-container px-4 py-3">
        <DrillLine drill={snaps.last_drill} />
        {snaps.archives.length === 0 ? (
          <div className="text-on-surface-low text-[0.8125rem]">
            No snapshots yet. One appears after the first nightly run, or as soon as you run
            a snapshot above.
          </div>
        ) : (
          <ul className="flex list-none flex-col gap-3 p-0">
            {snaps.archives.map((a) => (
              <li key={a.id} className="border-outline-var border-t pt-3 first:border-t-0 first:pt-0">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className={`min-w-0 flex-1 truncate text-[0.8125rem] ${a.retained ? 'text-on-surface' : 'text-on-surface-low line-through'}`} style={fvs(500)}>
                    {a.name}
                  </span>
                  <span className="shrink-0 text-on-surface-low text-[0.75rem]">{formatSize(a.size)}</span>
                  {a.validate && <ValidateBadge ok={a.validate.ok} detail={a.validate.detail} />}
                </div>
                <DomainCounts counts={a.domains} />
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Button variant="secondary" size="sm" onClick={() => preview(a)} disabled={busy !== ''}>
                    {busy === a.id ? <><Loader2 size={14} className="animate-spin" /> Working…</> : 'Preview restore'}
                  </Button>
                  <Button variant="secondary" size="sm" onClick={() => mergeRestore(a)} disabled={busy !== ''}>
                    Merge-restore
                  </Button>
                </div>
                {plan?.id === a.id && (
                  <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-surface px-3 py-2 text-on-surface-var text-[0.6875rem]">{plan.text}</pre>
                )}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-on-surface-low text-[0.75rem]">
          Stored in <code>{snaps.directory}</code>. A full <em>replace</em> restore is a
          command-line action — <code>personalclaw restore --replace</code> — because it has
          to overwrite live state while the gateway is stopped.
        </p>
      </div>
    </Section>
  )
}

// ── Sync (DURABILITY-AND-SYNC §4.3, DAS-10) ──────────────────────────────────

/** The review surface this panel owns. Memory- and knowledge-domain conflicts route to
 *  theirs (§4.2 item 3); their counts are reported here so a filtered view never reads as
 *  "nothing anywhere". */
const SURFACE_DURABILITY = 'durability'

const ENCRYPT_OPTIONS = [
  { value: 'auto', label: 'Automatic (per transport)' },
  { value: 'on', label: 'Always encrypt' },
  { value: 'off', label: 'Never encrypt' },
]

/** Which transport syncs this instance, and on what schedule (§4.3/§4.4).
 *
 *  Each transport is an installed `type: "sync"` provider app, so its OWN settings (repo
 *  URL, folder, host) live on its provider card under Settings → Providers — the standard
 *  `/api/providers` schema-driven form, which is what criterion 10 requires and what a
 *  third-party transport gets for free. What belongs HERE is the durability side of the
 *  decision: whether sync runs at all, which registered transport it uses, how stale is
 *  stale, and whether shards are encrypted before they leave.
 *
 *  Encryption is reported as the RESOLVED verdict rather than as the tri-state: "auto" does
 *  not tell a user whether their bytes are readable in someone else's storage, which is the
 *  only question the toggle exists to answer (§4.4). */
function SyncSection({ cfg, setCfg, status, transports }: {
  cfg: Record<string, unknown>
  setCfg: (c: Record<string, unknown>) => void
  status: DurabilityStatus | null
  transports: Settled<SettingsProvider[]>
}) {
  const [saved, flash] = useSavedFlash()
  const patch = usePatch(cfg, setCfg, flash)
  const syncOn = cfg.sync_enabled === true
  const chosen = String(cfg.sync_transport ?? '')
  const enabledTransports = transports.ok ? transports.value.filter((t) => t.enabled) : []

  return (
    <Section title="Sync"
      hint="Keep more than one machine in step through storage you own — a git repo, a synced folder, a bucket. There is no PersonalClaw server in the middle.">
      <div className="rounded-lg bg-surface-container px-4 py-1">
        <Row label="Sync this instance"
          hint="Push this machine's changes and pull the other machines' on the schedule below. Off means nothing leaves this machine.">
          <div className="flex items-center gap-2">
            <SavedToast show={saved} />
            <Toggle on={syncOn} onChange={(v) => patch('sync_enabled', v)} label="Sync this instance" />
          </div>
        </Row>

        {/* A failed provider read is NOT "none installed" — those two need different words,
            because one is a reason to install something and the other is a reason to retry. */}
        {!transports.ok ? (
          <div className="border-t border-outline-var py-3 text-[0.8125rem]" style={{ color: 'var(--color-error)' }}>
            The installed transports could not be read ({transports.error}). Reload to try
            again — this is not the same as having none installed.
          </div>
        ) : enabledTransports.length === 0 ? (
          <div className="border-t border-outline-var py-3 text-on-surface-low text-[0.8125rem]">
            No sync transport is installed and enabled yet. Install one from the Store (git-sync
            keeps a human-readable history in a repo you own; dir-sync uses any folder that
            already syncs itself), then enable it under Settings → Providers, where its own
            settings live.
          </div>
        ) : (
          <Row label="Transport"
            hint="Which installed transport carries the shards. Its own settings — repo, folder, host — live on its provider card under Settings → Providers.">
            <Select
              value={chosen}
              ariaLabel="Sync transport"
              onChange={(v) => patch('sync_transport', v)}
              options={[
                { value: '', label: 'None — sync is idle' },
                ...enabledTransports.map((t) => ({ value: t.name, label: t.displayName || t.name })),
              ]}
            />
          </Row>
        )}

        <NumberRow label="Pull no more often than" saved={saved}
          hint="The staleness window: how long this machine may go between checking the shared store. Sync is deliberately not continuous."
          value={num(cfg.sync_stale_after_secs, 900)} min={30} max={86400} suffix="seconds"
          onCommit={(n) => patch('sync_stale_after_secs', n)} />

        <Row label="Encrypt shards"
          hint="Encrypt each shard before it leaves this machine. Automatic encrypts for third-party storage (buckets, shared folders) and leaves a private git repo readable, because a diffable history is the point of using git.">
          <Select
            value={String(cfg.sync_encrypt ?? 'auto')}
            ariaLabel="Encrypt shards"
            onChange={(v) => patch('sync_encrypt', v)}
            options={ENCRYPT_OPTIONS}
          />
        </Row>

        {status?.sync && (
          <div className="border-t border-outline-var py-3">
            <div className="flex flex-col gap-1.5">
              <JobLine label="Last sync" when={status.sync.last_run} due={status.sync.due} />
              <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]">
                <span className="text-on-surface-var">Shards leaving this machine</span>
                <span className="shrink-0 text-on-surface-low text-[0.75rem]">
                  {!status.sync.transport
                    ? 'no transport chosen'
                    : status.sync.encrypted
                      ? 'encrypted'
                      : 'readable by anyone with access to that store'}
                </span>
              </div>
            </div>
            <p className="mt-2 text-on-surface-low text-[0.75rem]">
              Credentials never sync — API keys and this instance's secrets are excluded before
              any transport sees a byte, and re-enter per machine.
            </p>
          </div>
        )}
      </div>
    </Section>
  )
}

// ── The conflict review queue (DURABILITY-AND-SYNC §4.2, DAS-10) ──────────────

const CHOICE_LABELS: Record<DurabilityConflictChoice, string> = {
  keep_local: 'Keep this machine’s',
  take_remote: 'Take the other machine’s',
  accept_proposal: 'Accept the drafted merge',
}

/** Where a both-sides-edited divergence gets decided (§4.2 item 2).
 *
 *  The queue itself shipped with the sync engine — a detector, a durable JSONL, and the rule
 *  that the LOCAL row stays authoritative until a human chooses. What it never had was a way
 *  to see it or to answer it, so the hold was permanent and silent. This is that answer.
 *
 *  Three states that must not look alike, and don't:
 *   · the queue could not be READ → an error, naming it, with a retry;
 *   · sync has never been configured → nothing has ever compared two machines, so an empty
 *     queue is not evidence of anything;
 *   · sync runs and nothing diverged → the good empty state.
 *
 *  Nothing here auto-applies: each decision names the version it writes and takes a
 *  confirmation, because two of the three overwrite a row the other machine also edited. */
function ConflictsSection({ read, onChanged }: {
  read: Settled<DurabilityConflicts>
  onChanged: () => void
}) {
  const [busy, setBusy] = useState('')
  const [expanded, setExpanded] = useState('')

  const hint = 'When two machines edit the same thing while apart, PersonalClaw keeps both versions and waits for you instead of guessing.'

  if (!read.ok) {
    return (
      <Section title="Conflicts to review" hint={hint}>
        <div className="rounded-lg bg-surface-container px-4 py-3 text-[0.8125rem]">
          <div className="flex items-start gap-2" style={{ color: 'var(--color-error)' }}>
            <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
            <span>
              The review queue could not be read ({read.error}). This is <em>not</em> the same
              as having nothing to review — there may be conflicts waiting.
            </span>
          </div>
          <div className="mt-3">
            <Button variant="secondary" size="sm" onClick={onChanged}>Try again</Button>
          </div>
        </div>
      </Section>
    )
  }

  const { conflicts, counts, sync } = read.value
  const pending = conflicts.filter((c) => c.status === 'needs-review')
  const decided = conflicts.filter((c) => c.status !== 'needs-review')
  const elsewhere = Object.entries(counts.by_surface)
    .filter(([surface, n]) => surface !== SURFACE_DURABILITY && n > 0)

  const resolve = async (c: DurabilityConflict, choice: DurabilityConflictChoice) => {
    if (!(await confirm({
      title: 'Write this version?',
      body: `${CHOICE_LABELS[choice]} version of ${c.entity_id} will be written into ${c.entry_id} on this machine. The version you don't pick stays in the shared store, so this is reversible by resolving again from the other side.`,
      confirmLabel: 'Write it',
      // Danger tone, so the shell raises it as an alertdialog: two of the three choices
      // overwrite a row the other machine also edited.
      danger: true,
    }))) return
    setBusy(c.id)
    try {
      const r = await api.resolveDurabilityConflict(c.id, choice)
      notify(`Resolved ${c.entity_id}: ${CHOICE_LABELS[choice].toLowerCase()} version written (${r.written} written).`, 'success')
      onChanged()
    } catch (e) {
      // The server refuses a resolve it cannot apply — an undrafted merge, a record already
      // decided, a failed write. Each has its own reason and none of them is "done".
      notify(`Nothing was applied: ${String((e as Error)?.message || e)}`, 'error')
    }
    setBusy('')
  }

  return (
    <Section title="Conflicts to review" hint={hint}>
      <div className="rounded-lg bg-surface-container px-4 py-3">
        {pending.length === 0 ? (
          <div className="text-on-surface-low text-[0.8125rem]">
            {!sync.configured
              ? 'Nothing to review — but sync has never run on this instance, so no two versions have ever been compared. Choose a transport above first.'
              : 'Nothing to review. Every change either merged cleanly or only one machine had touched it.'}
          </div>
        ) : (
          <ul className="flex list-none flex-col gap-3 p-0">
            {pending.map((c) => (
              <li key={c.id} className="border-outline-var border-t pt-3 first:border-t-0 first:pt-0">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="min-w-0 flex-1 truncate text-on-surface text-[0.8125rem]" style={fvs(550)}>
                    {c.entity_id}
                  </span>
                  <span className="shrink-0 text-on-surface-low text-[0.75rem]">
                    in {c.entry_id}{c.detected_at ? ` · found ${relativeTime(c.detected_at)}` : ''}
                  </span>
                </div>
                <p className="mt-1 text-on-surface-low text-[0.75rem]">
                  Both machines changed this after they last agreed. This machine's version is
                  in place; nothing has been overwritten.
                </p>

                {c.proposal
                  ? (
                    <div className="mt-2 rounded-md bg-surface px-3 py-2 text-[0.75rem]">
                      <div className="text-on-surface" style={fvs(550)}>Drafted merge</div>
                      {c.rationale && <p className="mt-1 text-on-surface-low">{c.rationale}</p>}
                    </div>
                  )
                  : (
                    <p className="mt-2 text-on-surface-low text-[0.75rem]">
                      {c.proposal_error
                        ? `No merge was drafted — ${c.proposal_error}. Choose a version yourself.`
                        : 'No merge has been drafted yet. Choose a version yourself, or wait for the next background pass.'}
                    </p>
                  )}

                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Button variant="secondary" size="sm" disabled={busy !== ''} onClick={() => resolve(c, 'keep_local')}>
                    {busy === c.id ? <><Loader2 size={14} className="animate-spin" aria-hidden /> Writing…</> : CHOICE_LABELS.keep_local}
                  </Button>
                  <Button variant="secondary" size="sm" disabled={busy !== ''} onClick={() => resolve(c, 'take_remote')}>
                    {CHOICE_LABELS.take_remote}
                  </Button>
                  <Button variant="secondary" size="sm" disabled={busy !== '' || !c.proposal}
                    disabledReason={!c.proposal ? 'No merge has been drafted for this conflict' : undefined}
                    onClick={() => resolve(c, 'accept_proposal')}>
                    {CHOICE_LABELS.accept_proposal}
                  </Button>
                  <Button variant="secondary" size="xs" onClick={() => setExpanded(expanded === c.id ? '' : c.id)}>
                    {expanded === c.id ? 'Hide both versions' : 'Compare both versions'}
                  </Button>
                </div>

                {expanded === c.id && (
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    <RowVersion label="This machine" row={c.local_row} />
                    <RowVersion label="The other machine" row={c.remote_row} />
                    {c.proposal && <RowVersion label="Drafted merge" row={c.proposal} />}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {elsewhere.length > 0 && (
          <p className="mt-3 text-on-surface-low text-[0.75rem]">
            {elsewhere.map(([surface, n]) => `${n} ${surface}`).join(' and ')} conflict
            {elsewhere.reduce((t, [, n]) => t + n, 0) === 1 ? '' : 's'} are waiting on their own
            review surface — memory and knowledge divergences are reviewed where that data lives,
            not here.
          </p>
        )}
        {decided.length > 0 && (
          <p className="mt-3 text-on-surface-low text-[0.75rem]">
            {decided.length} already decided ({decided.map((c) => c.resolution).filter(Boolean).join(', ')}).
            Decisions are kept as the record of what needed one.
          </p>
        )}
      </div>
    </Section>
  )
}

/** One version of a conflicted row, verbatim. Verbatim on purpose: a summary of what
 *  changed is a second opinion, and the point of this control is to show the bytes the
 *  decision writes. */
function RowVersion({ label, row }: { label: string; row: Record<string, unknown> }) {
  return (
    <div>
      <div className="mb-1 text-on-surface-low text-[0.6875rem] uppercase tracking-wide">{label}</div>
      <pre className="max-h-48 overflow-auto rounded-md bg-surface px-3 py-2 text-on-surface-var text-[0.6875rem]">{JSON.stringify(row, null, 1)}</pre>
    </div>
  )
}

/** The last drill's verdict. An unrecorded outcome renders as UNKNOWN, never as a pass —
 *  a backup surface that shows green for "we don't know" is the worst possible lie. */
function DrillLine({ drill }: { drill: DurabilityArchives['last_drill'] }) {
  if (!drill.ran) {
    return (
      <div className="mb-3 flex items-start gap-2 text-on-surface-low text-[0.75rem]">
        <ShieldQuestion size={13} className="mt-0.5 shrink-0" />
        <span>No restore has been rehearsed yet. Run “Verify a restore” above to check that these snapshots can actually be restored.</span>
      </div>
    )
  }
  const Icon = drill.ok === true ? ShieldCheck : drill.ok === false ? ShieldAlert : ShieldQuestion
  const tone = drill.ok === true ? 'text-success' : drill.ok === false ? 'text-error' : 'text-on-surface-low'
  return (
    <div className={`mb-3 flex items-start gap-2 text-[0.75rem] ${tone}`}>
      <Icon size={13} className="mt-0.5 shrink-0" />
      <span>
        {drill.ok === true ? 'Last restore drill passed' : drill.ok === false ? 'Last restore drill FAILED' : 'Last restore drill ran; its result was not recorded'}
        {drill.detail && <> — {drill.detail}</>}
      </span>
    </div>
  )
}

function ValidateBadge({ ok, detail }: { ok: boolean | null; detail: string }) {
  const label = ok === true ? 'verified' : ok === false ? 'failed verification' : 'result unknown'
  const tone = ok === true ? 'text-success' : ok === false ? 'text-error' : 'text-on-surface-low'
  return <span className={`shrink-0 text-[0.6875rem] ${tone}`} title={detail || label}>{label}</span>
}

/** `null` counts mean the archive recorded none (taken before MANIFEST v3). That reads
 *  differently from an empty archive, so it says so rather than showing zeros. */
function DomainCounts({ counts }: { counts: DurabilityArchive['domains'] }) {
  if (counts === null) {
    return <div className="mt-1 text-on-surface-low text-[0.75rem]">Contents not recorded in this snapshot.</div>
  }
  const rows = Object.entries(counts).filter(([, v]) => v.files > 0)
  if (rows.length === 0) {
    return <div className="mt-1 text-on-surface-low text-[0.75rem]">This snapshot recorded no contents.</div>
  }
  return (
    <ul className="mt-1 flex list-none flex-wrap gap-x-4 gap-y-0.5 p-0 text-on-surface-low text-[0.75rem]">
      {rows.map(([domain, v]) => (
        <li key={domain}>
          {domain}: {v.rows > 0 ? `${v.rows} rows` : `${v.files} files`}
        </li>
      ))}
    </ul>
  )
}

// ── helpers ─────────────────────────────────────────────────────────────────

/** Optimistic patch of one `durability.*` field, rolled back on failure so the
 *  control never shows a value the server rejected. */
function usePatch(
  cfg: Record<string, unknown>,
  setCfg: (c: Record<string, unknown>) => void,
  flash: () => void,
) {
  return (key: string, value: unknown, _cb?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg({ ...cfg, [key]: value })
    api.patchConfig(`durability.${key}`, value).then(flash).catch((e) => {
      setCfg({ ...cfg, [key]: prev })
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
}

function useSavedFlash(): [boolean, () => void] {
  const [saved, setSaved] = useState(false)
  return [saved, () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }]
}

function num(v: unknown, fallback: number): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

function NumberRow({ label, hint, value, min, max, suffix, onCommit, saved }: {
  label: string; hint?: string; value: number; min: number; max: number
  suffix?: string
  /** `(value, label)` — see `ChatPanel`'s note: the label has to be SUPPLIED, not just accepted. */
  onCommit: (n: number, label?: string) => void; saved: boolean
}) {
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <NumberField value={value} min={min} max={max} step={1} onChange={(n) => onCommit(n, label)} ariaLabel={label} />
        {suffix && <span className="w-14 text-on-surface-low text-[0.75rem]">{suffix}</span>}
      </div>
    </Row>
  )
}

function RunButton({ label, icon: Icon, busy, disabled, onClick }: {
  label: string; icon: typeof ShieldCheck; busy: boolean; disabled: boolean; onClick: () => void
}) {
  return (
    <Button variant="secondary" size="xs" onClick={onClick} disabled={disabled} className="gap-1.5">
      {busy
        ? <Loader2 size={12} className="shrink-0 animate-spin" aria-hidden />
        : <Icon size={12} className="shrink-0" aria-hidden />}
      {label}
    </Button>
  )
}

function JobLine({ label, when, due }: { label: string; when: number; due: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]">
      <span className="text-on-surface-var">{label}</span>
      <span className="shrink-0 text-on-surface-low text-[0.75rem]">
        {when ? relativeTime(when) : 'never run'}{due && when ? ' · due' : ''}
      </span>
    </div>
  )
}

/** Epoch seconds → a coarse "3 hours ago". Coarse on purpose: the useful question
 *  is "recently or not", and a live-ticking timestamp in settings is noise. */
function relativeTime(ts: number | string | null | undefined): string {
  // The only formatter in the tree with no guard at all: a required `number` that nothing
  // validates, so an unreadable value printed "NaN days ago". Not observed live — its field
  // is numeric today — which is exactly why it is worth closing while the class is in hand.
  const epochSecs = epochSeconds(ts)
  if (epochSecs == null) return ''
  const secs = Math.max(0, Date.now() / 1000 - epochSecs)
  if (secs < 90) return 'just now'
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins} min ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} ${hours === 1 ? 'hour' : 'hours'} ago`
  const days = Math.round(hours / 24)
  if (days < 31) return `${days} ${days === 1 ? 'day' : 'days'} ago`
  const months = Math.round(days / 30)
  return `${months} ${months === 1 ? 'month' : 'months'} ago`
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = bytes / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`
}
