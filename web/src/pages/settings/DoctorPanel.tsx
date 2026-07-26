import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, ChevronRight, CheckCircle2, AlertTriangle, XCircle } from 'lucide-react'
import { api, type DoctorReport, type DoctorCapability, type DoctorProbe } from '../../lib/api'
import { PanelHeader, Section } from './settingsUI'
import { Button } from '../../ui/Button'
import { FormSkeleton } from '../../ui/ListScaffold'

// Prettify a capability key for a card title ("serving-fs" → "Serving / fs",
// "model-providers" → "Model providers"). The backend keys are URL-safe slugs;
// this is display only.
function capLabel(key: string): string {
  const words = key.replace(/[-/]/g, ' ').split(' ')
  return words.map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w)).join(' ')
}

/** Doctor — tiered, read-only health probes (PLATFORM-RESILIENCE §1). Runs every
 *  capability probe and groups the results into cards. The doctrine is honored in
 *  the copy: a failed CAPABILITY is a degraded row, never a "gateway broken" claim —
 *  only a core-tier failure says the gateway itself needs attention. Nothing here
 *  changes any state; fixes (§2) and simulators (§3) land in later sessions. */
export function DoctorPanel() {
  const [report, setReport] = useState<DoctorReport | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => {
    setBusy(true)
    api.doctor().then(setReport).catch(() => setReport(null)).finally(() => setBusy(false))
  }, [])
  useEffect(() => { refresh() }, [refresh])

  if (report === null && busy) return <FormSkeleton sections={2} />

  const caps = report ? Object.entries(report.capabilities) : []
  // Show a failed capability before the healthy ones (attention first).
  caps.sort(([, a], [, b]) => Number(a.ok) - Number(b.ok))

  return (
    <div>
      <PanelHeader
        title="Doctor"
        hint="Read-only health probes across every subsystem — memory, channels, local models, app backends, the SPA symlink, and model-provider breakers. A degraded capability never means the gateway is down; only a core failure does. Nothing here changes anything on your machine."
      />

      <div className="mb-l flex items-center justify-between gap-l">
        {report ? <StatusBanner report={report} /> : (
          <div className="text-on-surface-low text-[0.8125rem]">Couldn't load the doctor report.</div>
        )}
        <Button variant="secondary" size="sm" onClick={refresh} disabled={busy}>
          <RefreshCw size={15} className={busy ? 'animate-spin' : undefined} /> Re-run
        </Button>
      </div>

      {report && (
        <Section>
          <div className="flex flex-col gap-m">
            {caps.map(([key, cap]) => <CapabilityCard key={key} name={key} cap={cap} />)}
          </div>
          {report.skipped_capabilities.length > 0 && (
            <div className="mt-m text-on-surface-low text-[0.75rem]">
              Skipped (core failed first): {report.skipped_capabilities.map(capLabel).join(', ')}
            </div>
          )}
        </Section>
      )}
    </div>
  )
}

// ── overall status line ──────────────────────────────────────────────────────
function StatusBanner({ report }: { report: DoctorReport }) {
  if (report.core_ok && report.ok) {
    return (
      <div className="flex items-center gap-2 text-[0.8125rem]" style={{ color: 'var(--color-success)' }}>
        <CheckCircle2 size={16} /> All systems healthy
      </div>
    )
  }
  if (!report.core_ok) {
    return (
      <div className="flex items-center gap-2 text-[0.8125rem]" style={{ color: 'var(--color-error)' }}>
        <XCircle size={16} />
        Gateway core failing{report.restart_suggested ? ' — a restart may be required' : ''}
      </div>
    )
  }
  // core OK, but a capability degraded — the doctrine framing.
  return (
    <div className="flex items-center gap-2 text-[0.8125rem]" style={{ color: 'var(--color-warning)' }}>
      <AlertTriangle size={16} />
      Core healthy · {capLabel(report.worst)} degraded
    </div>
  )
}

// ── one capability card ────────────────────────────────────────────────────
function CapabilityCard({ name, cap }: { name: string; cap: DoctorCapability }) {
  const Icon = cap.ok ? CheckCircle2 : cap.tier <= 2 ? XCircle : AlertTriangle
  const color = cap.ok ? 'var(--color-success)' : cap.tier <= 2 ? 'var(--color-error)' : 'var(--color-warning)'
  return (
    <div className="rounded-lg bg-surface-container px-4 py-3">
      <div className="flex items-center gap-2">
        <Icon size={16} style={{ color }} />
        <span className="text-on-surface text-[0.875rem]">{capLabel(name)}</span>
        {!cap.ok && (
          <span className="text-on-surface-low text-[0.75rem]">· failed at tier {cap.tier}</span>
        )}
      </div>
      <div className="mt-2 flex flex-col gap-1.5">
        {cap.probes.map((p) => <ProbeRow key={p.id} probe={p} />)}
      </div>
    </div>
  )
}

// ── one probe row with expandable evidence ─────────────────────────────────
// Native details/summary disclosure: no JS state, keyboard-accessible by the
// platform, and not a bespoke button element (design-system primitive discipline).
function ProbeRow({ probe }: { probe: DoctorProbe }) {
  const hasEvidence = probe.evidence && Object.keys(probe.evidence).length > 0
  const dot = probe.ok ? 'var(--color-success)' : probe.tier <= 2 ? 'var(--color-error)' : 'var(--color-warning)'
  const head = (
    <>
      <span className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: dot }} />
      <span className="min-w-0 flex-1">
        <span className="text-on-surface text-[0.8125rem]">{probe.title}</span>
        <span className="block text-on-surface-low text-[0.75rem]">{probe.detail}</span>
      </span>
    </>
  )
  if (!hasEvidence) {
    return (
      <div className="flex items-start gap-2 border-b border-outline-variant/30 pb-1.5 last:border-0 last:pb-0">
        {head}
      </div>
    )
  }
  return (
    <details className="group border-b border-outline-variant/30 pb-1.5 last:border-0 last:pb-0">
      <summary className="flex cursor-pointer list-none items-start gap-2">
        {head}
        <ChevronRight
          size={14}
          className="mt-1 shrink-0 text-on-surface-low transition-transform group-open:rotate-90"
        />
      </summary>
      <pre className="mt-1.5 overflow-x-auto rounded-md bg-surface px-2.5 py-2 text-on-surface-low text-[0.6875rem]">
        {JSON.stringify(probe.evidence, null, 2)}
      </pre>
    </details>
  )
}
