import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, ChevronRight, CheckCircle2, AlertTriangle, XCircle, Wrench } from 'lucide-react'
import { api, type DoctorReport, type DoctorCapability, type DoctorProbe, type RemediationSnapshot } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { confirm } from '../../ui/dialog'
import { InvestigateButton } from '../../ui/InvestigateButton'
import { PanelHeader, Section } from './settingsUI'
import { Button } from '../../ui/Button'
import { FormSkeleton } from '../../ui/ListScaffold'

// Prettify a capability key for a card title ("serving-fs" → "Serving / fs",
// "model-providers" → "Model providers"). The backend keys are URL-safe slugs;
// this is display only.
// Capability keys are kebab/slash-separated ("serving-fs", "model-providers"); deficit keys are
// snake_case ("knowledge_missing_embeddings"). One helper covers both rather than a second,
// near-identical one appearing beside it — `_` joins the same separator class.
function capLabel(key: string): string {
  const words = key.replace(/[-/_]/g, ' ').split(' ')
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
            {caps.map(([key, cap]) => <CapabilityCard key={key} name={key} cap={cap} onFixed={refresh} />)}
          </div>
          {report.skipped_capabilities.length > 0 && (
            <div className="mt-m text-on-surface-low text-[0.75rem]">
              Skipped (core failed first): {report.skipped_capabilities.map(capLabel).join(', ')}
            </div>
          )}
        </Section>
      )}

      <RemediationSection />
    </div>
  )
}

// ── remediation engine (PLATFORM-RESILIENCE §4) ─────────────────────────────
// A health score + a confirm-gated "run maintenance now" + the recent-run ledger.
// The engine also runs itself on an adaptive heartbeat cadence; this is the manual
// surface + visibility.
/** Exported for test: the deficit list's derivations (zero-count filter, reachable-first ordering,
 *  the penalty attribution) are only observable by rendering the section against a stubbed
 *  snapshot — jsdom reports every box as 0, so nothing about them is measurable from layout. */
export function RemediationSection() {
  const [snap, setSnap] = useState<RemediationSnapshot | null>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => { api.doctorRemediation().then(setSnap).catch(() => setSnap(null)) }, [])
  useEffect(() => { load() }, [load])

  // `measure_deficits()` returns EVERY source it can read, including the ones currently at zero
  // (a clean install reports skill_aging_due ×0). Those are measurements, not problems — listing
  // them would bury the real ones. Worst first: the biggest reachable penalty is the actionable row.
  const scored = (snap?.deficits ?? [])
    .filter((d) => d.count > 0)
    .sort((a, b) => Number(b.reachable) - Number(a.reachable) || b.penalty - a.penalty)

  const run = async () => {
    if (!(await confirm({
      title: 'Run maintenance now?',
      body: 'Runs the health-scored remediation engine (re-index, orphan prune, skill aging) once. Deterministic work only; nothing destructive.',
      confirmLabel: 'Run maintenance',
    }))) return
    setBusy(true)
    try {
      const r = await api.doctorRemediationRun()
      notify(`Maintenance: score ${Math.round(r.score_before)}→${Math.round(r.score_after)} (${r.stopped_reason})`, 'success')
      load()
    } catch (e) {
      notify(`Maintenance failed: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy(false) }
  }

  return (
    <Section title="Maintenance" hint="A health-scored engine keeps the stores tidy (embedding re-index, orphan prune, skill aging) on an adaptive schedule. Run it on demand here.">
      <div className="rounded-lg bg-surface-container px-4 py-3">
        <div className="flex items-center justify-between gap-l">
          <div className="text-on-surface text-[0.8125rem]">
            {snap ? <>Health score <span className="tabular-nums" style={{ color: snap.score >= snap.target_score ? 'var(--color-success)' : 'var(--color-warning)' }}>{Math.round(snap.score)}</span> / target {snap.target_score}</> : 'Loading…'}
          </div>
          <Button variant="secondary" size="sm" onClick={run} loading={busy}>
            <Wrench size={14} /> Run now
          </Button>
        </div>
        {/* WHY the score is what it is. `deficits` is the measured breakdown behind it — the
            engine's own input — and the panel showed only the total. On a real install this read
            "Health score 90 / target 90" in success green while carrying 26 orphan locks that are
            `reachable: true`, i.e. fixable by pressing Run now. A score with no breakdown cannot
            tell "nothing wrong" from "nothing the engine will act on".

            `reachable` is the load-bearing distinction: health_score() sums penalties over
            REACHABLE deficits only, because an unreachable one is at its floor and the engine
            cannot improve it (e.g. missing embeddings with no embedder bound). Those are shown
            greyed and marked, so a user does not press Run now expecting them to clear. */}
        {scored.length > 0 && (
          <div className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2">
            {scored.map((d) => (
              <div key={d.key} className="flex items-baseline justify-between gap-2 text-[0.75rem]">
                <span className={d.reachable ? 'text-on-surface-var' : 'text-on-surface-low'}>
                  {capLabel(d.key)}
                  <span className="ml-1.5 text-on-surface-low tabular-nums">×{d.count}</span>
                  {!d.reachable && <span className="ml-1.5 text-on-surface-low">· not fixable yet</span>}
                </span>
                {/* An unreachable deficit is NOT subtracted from the score, so showing its penalty
                    as if it counted would misattribute the number the row above reports. */}
                <span className="shrink-0 text-on-surface-low tabular-nums">
                  {d.reachable ? `−${d.penalty.toFixed(1)}` : '—'}
                </span>
              </div>
            ))}
          </div>
        )}
        {/* What Run now would actually DO. The dry-run plan was already fetched and discarded, so
            the button was unpreviewable. An empty plan is not silence: the engine stops with a
            reason (most often "target_score already met"), which is exactly the state that makes
            a nonzero deficit list look contradictory — so say it. */}
        {snap && (
          <div className="mt-2 border-t border-outline-variant/30 pt-2 text-on-surface-low text-[0.75rem]">
            {snap.plan.length > 0
              ? <>Run now would: {snap.plan.map((j) => capLabel(j.id)).join(' · ')}</>
              : scored.some((d) => d.reachable)
                ? 'Run now would do nothing — the score already meets its target, so the engine stops before touching the fixable items above.'
                : 'Nothing to do — no fixable deficits.'}
          </div>
        )}
        {snap && snap.recent_runs.length > 0 && (
          <div className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2">
            {snap.recent_runs.slice(0, 5).map((r, i) => (
              <div key={i} className="text-on-surface-low text-[0.75rem]">
                score {Math.round(r.score_before)}→{Math.round(r.score_after)} · {r.jobs.length} job(s) · {r.stopped_reason}
              </div>
            ))}
          </div>
        )}
      </div>
    </Section>
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
function CapabilityCard({ name, cap, onFixed }: { name: string; cap: DoctorCapability; onFixed: () => void }) {
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
        {/* Investigate (plan 60): re-runs this capability's read-only probes and
            opens a chat with the findings + any offered fix's dry-run preview —
            discussing a fix, never applying one. */}
        <span className="ml-auto">
          <InvestigateButton kind="doctor_finding" id={name} backLink="#/settings/doctor" size={28} />
        </span>
      </div>
      <div className="mt-2 flex flex-col gap-1.5">
        {cap.probes.map((p) => <ProbeRow key={p.id} probe={p} onFixed={onFixed} />)}
      </div>
    </div>
  )
}

// ── a confirm-gated fix button (PLATFORM-RESILIENCE §2) ─────────────────────
// Nothing auto-applies: a two-step confirm (the armed-delete pattern) runs the fix,
// which is SEL-audited server-side. On success we re-run the doctor so the fixed
// capability turns green.
function FixButton({ fixId, onFixed }: { fixId: string; onFixed: () => void }) {
  const [busy, setBusy] = useState(false)
  const run = async () => {
    if (!(await confirm({
      title: 'Apply this fix?',
      body: 'This repairs harness state (symlinks, stale locks, or stale bindings) — never your content. It is logged to the security audit.',
      confirmLabel: 'Apply fix',
    }))) return
    setBusy(true)
    try {
      const r = await api.doctorFixApply(fixId)
      notify(r.ok ? (r.result || 'Fix applied.') : `Fix failed: ${r.error || 'unknown error'}`,
        r.ok ? 'success' : 'error')
      if (r.ok) onFixed()
    } catch (e) {
      notify(`Fix failed: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy(false) }
  }
  return (
    <Button variant="secondary" size="xs" onClick={run} loading={busy} className="mt-1 shrink-0">
      <Wrench size={13} /> Fix
    </Button>
  )
}

// ── one probe row with expandable evidence ─────────────────────────────────
// Native details/summary disclosure: no JS state, keyboard-accessible by the
// platform, and not a bespoke button element (design-system primitive discipline).
function ProbeRow({ probe, onFixed }: { probe: DoctorProbe; onFixed: () => void }) {
  const hasEvidence = probe.evidence && Object.keys(probe.evidence).length > 0
  const dot = probe.ok ? 'var(--color-success)' : probe.tier <= 2 ? 'var(--color-error)' : 'var(--color-warning)'
  const head = (
    <>
      <span className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: dot }} />
      <span className="min-w-0 flex-1">
        <span className="text-on-surface text-[0.8125rem]">{probe.title}</span>
        <span className="block text-on-surface-low text-[0.75rem]">{probe.detail}</span>
      </span>
      {probe.fix_id && !probe.ok && <FixButton fixId={probe.fix_id} onFixed={onFixed} />}
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
