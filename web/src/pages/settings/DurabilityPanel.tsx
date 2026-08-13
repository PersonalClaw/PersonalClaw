import { useEffect, useState } from 'react'
import { epochSeconds } from '../../lib/epoch'
import { HardDriveDownload, Loader2, ShieldCheck } from 'lucide-react'
import { api, type DurabilitySnapshots, type DurabilityStatus } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section, Row, Toggle, SavedToast } from './settingsUI'
import { NumberField } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'

/** Scheduled backups (DURABILITY-AND-SYNC §3).
 *
 *  The service, the retention tiers, the restore drill and three endpoints all
 *  shipped — with no frontend at all, so the schedule was invisible and its config
 *  was file-editable only. This panel closes both halves: the five `durability.*`
 *  fields get their control (the config contract's fifth leg), and the existing
 *  status/snapshots endpoints finally have a reader.
 *
 *  Deliberately NOT here: restore. Replace-restore refuses to run while the gateway
 *  is up, so a useful restore endpoint needs staged-swap-on-next-boot machinery that
 *  doesn't exist yet — and a button that appears to restore and doesn't is a trap.
 *  Restore stays `personalclaw restore`, and the panel says so rather than leaving a
 *  user hunting for it. */
export function DurabilityPanel() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)

  const { data, error: loadErr, refresh } = useCachedData('settings:durability', async () => {
    const [plaw, status, snaps] = await Promise.all([
      // The five `durability.*` controls come from here, so a fabricated `{}` would show a retention
      // schedule nobody configured. Status and snapshots keep their fallbacks: they DECORATE the panel
      // (a status strip and a snapshot list) rather than defining what its controls claim.
      api.personalclawConfig(),
      api.durabilityStatus().catch(() => null),
      api.durabilitySnapshots().catch(() => null),
    ])
    return {
      durability: (plaw.durability ?? {}) as Record<string, unknown>,
      status,
      snaps,
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
    </div>
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
          Restoring a backup is a command-line action — <code>personalclaw restore</code> — because
          it has to replace live state while the gateway is stopped.
        </p>
      </div>
    </Section>
  )
}

// ── Retention (durability.keep_daily / keep_weekly / keep_monthly) ───────────

function RetentionSection({ cfg, setCfg, snaps }: {
  cfg: Record<string, unknown>
  setCfg: (c: Record<string, unknown>) => void
  snaps: DurabilitySnapshots | null
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
          onCommit={(n) => patch('keep_daily', n)} />
        <NumberRow label="Weekly snapshots" saved={saved}
          hint="How many weeks to keep one snapshot each."
          value={num(cfg.keep_weekly, 8)} min={0} max={260} suffix="weeks"
          onCommit={(n) => patch('keep_weekly', n)} />
        <NumberRow label="Monthly snapshots" saved={saved}
          hint="How many months to keep one snapshot each."
          value={num(cfg.keep_monthly, 12)} min={0} max={120} suffix="months"
          onCommit={(n) => patch('keep_monthly', n)} />

        {snaps && (
          <div className="border-t border-outline-var py-3">
            {snaps.snapshots.length === 0 ? (
              <div className="text-on-surface-low text-[0.8125rem]">
                No snapshots yet. One appears after the first nightly run, or as soon as you
                run a snapshot above.
              </div>
            ) : (
              <>
                <div className="mb-2 text-on-surface-var text-[0.8125rem]">
                  {snaps.snapshots.length} {snaps.snapshots.length === 1 ? 'snapshot' : 'snapshots'}
                  {pruneCount > 0 && (
                    <> · <span className="text-on-surface-low">
                      {pruneCount} would be removed by the settings above on the next pass
                    </span></>
                  )}
                </div>
                <div className="flex flex-col gap-1">
                  {snaps.snapshots.slice(0, 12).map((s) => (
                    <div key={s.name} className="flex items-baseline justify-between gap-3 text-[0.75rem]">
                      <span className={`min-w-0 flex-1 truncate ${s.retained ? 'text-on-surface-var' : 'text-on-surface-low line-through'}`}>
                        {s.name}
                      </span>
                      <span className="shrink-0 text-on-surface-low">{formatSize(s.size)}</span>
                    </div>
                  ))}
                </div>
                {snaps.snapshots.length > 12 && (
                  <div className="mt-1.5 text-on-surface-low text-[0.75rem]">
                    …and {snaps.snapshots.length - 12} older
                  </div>
                )}
                <div className="mt-2 text-on-surface-low text-[0.75rem]">
                  Stored in <code>{snaps.directory}</code>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </Section>
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
  return (key: string, value: unknown) => {
    const prev = cfg[key]
    setCfg({ ...cfg, [key]: value })
    api.patchConfig(`durability.${key}`, value).then(flash).catch((e) => {
      setCfg({ ...cfg, [key]: prev })
      notify(`Couldn't save ${key}: ${String((e as Error)?.message || e)}`, 'error')
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
  suffix?: string; onCommit: (n: number) => void; saved: boolean
}) {
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <NumberField value={value} min={min} max={max} step={1} onChange={onCommit} ariaLabel={label} />
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
