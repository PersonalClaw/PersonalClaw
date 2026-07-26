import { useEffect, useRef } from 'react'
import { Clock, Tag, Cpu, Zap, Users, ArrowUpCircle, ShieldAlert, Activity, MemoryStick, Network, HardDrive, Stethoscope } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api } from '../../../lib/api'
import { confirm } from '../../../ui/dialog'
import { useDashboardLive } from '../DashboardLive'
import { RowAction } from './kit'
import type { RouteProps } from '../../../app/useQueryState'

/** One rail metric — icon + value, with the word-label shed responsively. The
 *  rail is a `@container` (DashboardPage), so the label hides below a container
 *  width where the full-label strip would wrap (icon + value keep carrying the
 *  reading; the full "value label" stays on the title tooltip so nothing is
 *  lost). `shrink-0` keeps a metric from being squeezed mid-word before the
 *  strip decides to wrap. */
function Metric({ icon: Icon, value, label, tone }: { icon: LucideIcon; value: string | number; label: string; tone?: string }) {
  return (
    <div className="flex shrink-0 items-center gap-s" title={`${value} ${label}`}>
      <Icon size={15} className="shrink-0" style={{ color: tone ?? 'var(--color-on-surface-low)' }} />
      <span data-type="title-m" className="tabular-nums text-on-surface">{value}</span>
      <span data-type="body-m" className="hidden text-on-surface-low @min-[1520px]:inline">{label}</span>
    </div>
  )
}

/** A tiny inline sparkline over a rolling sample buffer — a minimal SVG polyline,
 *  theme-token stroked, no deps. Flat until ≥2 samples. */
function Spark({ samples, tone = 'var(--color-primary)', width = 56, height = 16 }: {
  samples: number[]; tone?: string; width?: number; height?: number
}) {
  if (samples.length < 2) return null
  const max = Math.max(...samples, 1)
  const step = width / (samples.length - 1)
  const pts = samples.map((v, i) => `${(i * step).toFixed(1)},${(height - (v / max) * height).toFixed(1)}`).join(' ')
  return (
    <svg width={width} height={height} className="shrink-0" aria-hidden viewBox={`0 0 ${width} ${height}`}>
      <polyline points={pts} fill="none" stroke={tone} strokeWidth={1.5}
        strokeLinejoin="round" strokeLinecap="round" opacity={0.8} />
    </svg>
  )
}

const _SPARK_MAX = 30  // rolling window of samples (~30 × fast-poll ≈ a few minutes)

/** Prettify a doctor capability slug for the rollup chip ("serving-fs" →
 *  "Serving fs", "model-providers" → "Model providers"). Display only. */
function capLabel(key: string): string {
  if (!key) return ''
  const s = key.replace(/[-/]/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Format a kb/s rate compactly: MB/s over 1024, else KB/s (integer). */
function fmtRate(kbs: number | undefined): string {
  const v = kbs ?? 0
  return v >= 1024 ? `${(v / 1024).toFixed(1)}MB/s` : `${Math.round(v)}KB/s`
}

/** System & Capability Health — a wide strip of LIVE health metrics: CPU%, memory
 *  used/total, network rx/tx, disk, load, plus uptime/version/crons/subagents. The
 *  live rates come from /api/system (P27 — already computed server-side, surfaced
 *  here) with a rolling CPU sparkline; an inline Update action + a YOLO indicator. */
export function SystemHealth({ navigate }: RouteProps) {
  const { status, system, doctor } = useDashboardLive()
  // Client-side rolling buffer of CPU% samples for the sparkline (the backend
  // computes the instantaneous rate; history is cheap to keep here).
  const cpuHist = useRef<number[]>([])
  useEffect(() => {
    if (system && typeof system.cpu_pct === 'number') {
      const h = cpuHist.current
      h.push(system.cpu_pct)
      if (h.length > _SPARK_MAX) h.shift()
    }
  }, [system])

  if (!status) {
    return <div className="skeleton h-8 w-full rounded-lg" />
  }

  const runUpdate = async () => {
    // Same confirm gate as the Updates settings panel — a one-click header action
    // must not kick off a pull/rebuild/restart pipeline without an explicit yes.
    if (!(await confirm({ title: 'Apply the available update?', body: 'The backend will update and may restart.', confirmLabel: 'Apply update' }))) return
    // Fire — the shell-level UpdateProgressOverlay owns the progress feedback
    // (step stepper + cancel). No inline busy state here; it would never clear
    // on success since the gateway re-execs and the page reloads. A rejected
    // START (409 dirty tree / update already in progress) pushes NO progress
    // events — the overlay never opens — so toast the backend's error text.
    api.applyUpdate().catch((e) => {
      const msg = e instanceof Error && e.message ? e.message : 'Request failed'
      window.dispatchEvent(new CustomEvent('ne:toast', { detail: { level: 'error', message: `Update blocked: ${msg}` } }))
    })
  }

  const cpuTone = system && system.cpu_pct >= 85 ? 'var(--color-warn)' : 'var(--color-primary)'
  const memPct = system && system.mem_total_gb ? (system.mem_used_gb / system.mem_total_gb) * 100 : 0
  const memTone = memPct >= 90 ? 'var(--color-warn)' : 'var(--color-info)'

  return (
    // `w-full` so the trailing `ml-auto` group can push "Details" to the rail's
    // right edge (without it the strip is content-width and ml-auto has no slack).
    // Gaps tighten as the container narrows, and word-labels/sparkline shed
    // (see Metric + the Spark wrapper) so the strip stays one line as long as it
    // fits — the container is the rail island, tracking --content-width. The
    // island owns symmetric vertical padding now, so the strip just centers.
    <div className="flex h-full w-full flex-wrap items-center gap-x-l gap-y-s @6xl:gap-x-xl">
      <Metric icon={Clock} value={status.uptime ?? '—'} label="uptime" />
      <Metric icon={Tag} value={`v${status.version ?? '?'}`} label={status.platform ?? ''} />
      {/* Live metrics from /api/system (P27) — render only when present. */}
      {system && (
        <>
          <div className="flex shrink-0 items-center gap-s">
            <Metric icon={Activity} value={`${Math.round(system.cpu_pct)}%`} label="cpu" tone={cpuTone} />
            {/* Sparkline is decorative — first to go when the rail is tight. */}
            <span className="hidden @3xl:inline-flex"><Spark samples={cpuHist.current} tone={cpuTone} /></span>
          </div>
          {system.mem_total_gb > 0 && (
            <Metric icon={MemoryStick} value={`${system.mem_used_gb.toFixed(1)}/${system.mem_total_gb}GB`} label="mem" tone={memTone} />
          )}
          {(system.net_rx_kbs != null || system.net_tx_kbs != null) && (
            <Metric icon={Network} value={`↓${fmtRate(system.net_rx_kbs)} ↑${fmtRate(system.net_tx_kbs)}`} label="net" />
          )}
          {system.disk_total_gb != null && system.disk_free_gb != null && system.disk_total_gb > 0 && (
            <Metric icon={HardDrive}
              value={`${(system.disk_total_gb - system.disk_free_gb).toFixed(0)}/${system.disk_total_gb.toFixed(0)}GB`}
              label="disk" />
          )}
          {system.load_1m != null && <Metric icon={Cpu} value={system.load_1m.toFixed(2)} label={`load · ${system.cpu_count}cpu`} />}
        </>
      )}
      <Metric icon={Zap} value={status.cron_jobs ?? 0} label="triggers" tone="var(--color-secondary)" />
      <Metric icon={Users} value={status.subagents ?? 0} label="subagents" tone="var(--color-info)" />
      {status.yolo && (
        <span className="flex items-center gap-xs rounded-pill px-m py-xs" style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)' }}>
          <ShieldAlert size={13} className="text-warn" />
          <span data-type="label-m" className="text-warn">YOLO active</span>
        </span>
      )}
      <div className="ml-auto flex items-center gap-s">
        {/* Doctor rollup — surfaces only when something needs attention; a healthy
            system stays quiet (the strip is already dense). Links to the Doctor tab. */}
        {doctor && !doctor.ok && (
          <RowAction tone="danger" onClick={() => navigate('settings/doctor')}
            title={doctor.core_ok ? `${capLabel(doctor.worst)} degraded — open Doctor` : 'Gateway core failing — open Doctor'}>
            <Stethoscope size={14} /> {doctor.core_ok ? `${capLabel(doctor.worst)} degraded` : 'Core failing'}
          </RowAction>
        )}
        {status.update_available && (
          <RowAction tone="primary" onClick={runUpdate} title="Apply the available update">
            <ArrowUpCircle size={14} /> Update available
          </RowAction>
        )}
        <button type="button" onClick={() => navigate('settings/updates')} className="rounded-pill px-m py-xs text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface" data-type="label-m">
          Details →
        </button>
      </div>
    </div>
  )
}
