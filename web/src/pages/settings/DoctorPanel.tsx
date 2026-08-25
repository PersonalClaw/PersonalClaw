import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { RefreshCw, ChevronRight, CheckCircle2, AlertTriangle, XCircle, Wrench, FlaskConical } from 'lucide-react'
import {
  api, type DoctorReport, type DoctorCapability, type DoctorProbe, type RemediationSnapshot,
  type SurfacingCandidate, type AutomationWouldExecute, type Trigger,
} from '../../lib/api'
import { notify } from '../../app/appSdk'
import { confirm } from '../../ui/dialog'
import { InvestigateButton } from '../../ui/InvestigateButton'
import { PanelHeader, Section } from './settingsUI'
import { Select, TextInput } from '../../ui/forms'
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
          // `role="alert"`: on a HEALTH surface, "we could not probe" is unrequested bad news that
          // changes what the screen means — the same reason `LoadError` announces. Measured before:
          // the sentence rendered with `[role="alert"]` count 0, so a screen-reader user reading the
          // panel top-down heard the Doctor's hint and then a Re-run button, with nothing between them.
          <div role="alert" className="text-on-surface-low text-[0.8125rem]">Couldn't load the doctor report.</div>
        )}
        <Button variant="secondary" size="sm" onClick={refresh} disabled={busy}>
          <RefreshCw size={15} className={busy ? 'animate-spin' : undefined} /> Re-run
        </Button>
      </div>

      {/* 🔴 Titled, because this panel's PRIMARY content was the one group with no heading while
          "Maintenance" below it had one. `settingsUI`'s Section renders its `h2` only when given a
          `title`, so an untitled one is a card, not a named group: measured on `#/settings/doctor`,
          14 controls (Re-run + every "Investigate in chat") sat under the `h1` with no section of
          their own, and a screen-reader user walking the headings met "Maintenance" first. */}
      {report && (
        <Section title="Subsystem probes">
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

      <SimulatorsSection />
      <RemediationSection />
    </div>
  )
}

// ── the two trust simulators (PLATFORM-RESILIENCE §3.1 + §3.3) ───────────────
//
// §3.3: "so 'simulate a query' and 'simulate a trigger' live side by side before the user grants
// unattended operation". Both are read-only by construction — the surfacing one re-runs the same
// deterministic scorer a real turn runs, and the automation one walks AUTOMATION-SUBSTRATE's dry
// fire. Neither executes anything, spends a token, or resolves a credential.
/** Exported for test: the five-fact rendering is only observable by rendering this against a
 *  stubbed response, and the whole point of §3.3 is that a user can READ the description. */
export function SimulatorsSection() {
  return (
    <Section
      title="Simulators"
      hint="Ask the system what it WOULD do, before it does it. Nothing here runs an action, spends a token, or changes any state."
    >
      <div className="flex flex-col gap-m">
        <SurfacingSimulator />
        <AutomationSimulator />
      </div>
    </Section>
  )
}

// ── §3.1: simulate a query ───────────────────────────────────────────────────
function SurfacingSimulator() {
  const [text, setText] = useState('')
  const [rows, setRows] = useState<SurfacingCandidate[] | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    try {
      const r = await api.doctorSimulateSurfacing(text)
      setRows(r.candidates)
      setErr(null)
    } catch (e) { setErr(e); setRows(null) } finally { setBusy(false) }
  }

  return (
    <div className="rounded-lg bg-surface-container px-4 py-3">
      <div className="text-on-surface text-[0.875rem]">Skill surfacing</div>
      <div className="mt-0.5 text-on-surface-low text-[0.75rem]">
        Which skills a message would surface, and — for the ones it wouldn't — why not.
      </div>
      {/* `TextInput`/`Select` from the shared form family, not raw elements: the primitive-adoption
          ratchet counts bespoke chrome, and these two carry the accessible-name plumbing (an
          explicit `ariaLabel` wins, which is what a control outside a `Field` needs). */}
      <form
        className="mt-2 flex items-center gap-2"
        onSubmit={(e) => { e.preventDefault(); if (text.trim() && !busy) void run() }}
      >
        <TextInput
          value={text}
          onChange={setText}
          placeholder="e.g. deploy the gateway"
          ariaLabel="A message to simulate skill surfacing for"
          size="sm"
        />
        {/* `type="submit"` so Enter in the box does what the button does — an input whose Enter
            key went nowhere is the "enter target unstated" shape. */}
        <Button type="submit" variant="secondary" size="sm" loading={busy} disabled={!text.trim()}
          disabledReason="Type a message to simulate">
          <FlaskConical size={14} /> Simulate
        </Button>
      </form>
      {err !== null && (
        <div role="alert" className="mt-2 text-on-surface-low text-[0.75rem]">
          Couldn't simulate surfacing: {String((err as Error)?.message || err)}
        </div>
      )}
      {rows !== null && rows.length === 0 && (
        <div className="mt-2 text-on-surface-low text-[0.75rem]">No skill scored against that message.</div>
      )}
      {rows !== null && rows.length > 0 && (
        <div className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2">
          {rows.map((c) => (
            <div key={c.key} className="flex items-baseline justify-between gap-2 text-[0.75rem]">
              <span className={c.included ? 'text-on-surface-var' : 'text-on-surface-low'}>
                {c.key} <span className="text-on-surface-low">· {c.reason}</span>
              </span>
              <span className="shrink-0 text-on-surface-low tabular-nums">
                kw {c.kw_score.toFixed(2)} / sem {c.sem_score.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── §3.3: simulate a trigger — the would-execute description ─────────────────
function AutomationSimulator() {
  const [triggers, setTriggers] = useState<Trigger[] | null>(null)
  const [pick, setPick] = useState('')
  const [desc, setDesc] = useState<AutomationWouldExecute | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.triggers().then((r) => setTriggers(r.triggers)).catch(() => setTriggers([]))
  }, [])

  const run = async () => {
    setBusy(true)
    try {
      const r = await api.doctorSimulateAutomation(pick)
      setDesc(r)
      setErr(null)
    } catch (e) { setErr(e); setDesc(null) } finally { setBusy(false) }
  }

  // The store id, not the namespaced wire id: the endpoint reads the unified TriggerStore, whose
  // rows are keyed by `raw_id` (`/api/triggers` prefixes `schedule:`/`event:` as its migration map).
  const rows = (triggers ?? []).map((t) => ({ value: t.raw_id, label: `${t.name || t.raw_id} · ${t.kind}` }))
  const empty = triggers !== null && rows.length === 0
  const options = [{ value: '', label: 'Pick an automation…' }, ...rows]

  return (
    <div className="rounded-lg bg-surface-container px-4 py-3">
      <div className="text-on-surface text-[0.875rem]">Automation would-execute</div>
      <div className="mt-0.5 text-on-surface-low text-[0.75rem]">
        What one automation would do on its next fire — the resolved schedule, the rendered action,
        the session it targets, what it is allowed to do, and its observe-mode dry fire.
      </div>
      <div className="mt-2 flex items-center gap-2">
        <Select
          value={pick}
          onChange={(v) => { setPick(v); setDesc(null); setErr(null) }}
          options={options}
          ariaLabel="An automation to describe"
        />
        {/* Two different reasons for one disabled state. A constant would read "you have no
            automations" on a machine that has three — non-null and still wrong, the ambiguous-name
            failure the settings-panel census turned up. */}
        <Button variant="secondary" size="sm" onClick={run} loading={busy} disabled={!pick}
          disabledReason={empty ? 'You have no automations yet' : 'Pick an automation first'}>
          <FlaskConical size={14} /> Describe
        </Button>
      </div>
      {/* 🪤 Deliberately NOT "…and it will appear here": this list is read once on mount, so that
          sentence would promise a live update the panel does not do — and the empty-state-promise
          census exists precisely to keep that shape out. */}
      {empty && (
        <div className="mt-2 text-on-surface-low text-[0.75rem]">
          No automations yet. Create one on the Automations page, then reopen this panel.
        </div>
      )}
      {err !== null && (
        <div role="alert" className="mt-2 text-on-surface-low text-[0.75rem]">
          Couldn't describe that automation: {String((err as Error)?.message || err)}
        </div>
      )}
      {desc && <WouldExecute d={desc} />}
    </div>
  )
}

/** The five facts §3.3 names, in one block. Exported for test. */
export function WouldExecute({ d }: { d: AutomationWouldExecute }) {
  const nf = d.next_fire
  const ac = d.action_config
  const cg = d.capability_grants
  const om = d.observe_mode
  return (
    <div className="mt-2 flex flex-col gap-1.5 border-t border-outline-variant/30 pt-2">
      {/* 1 — resolved next fire. `source` is rendered, not just the instant: an "armed" row is
          one the tick will act on, a "computed" one is enabled-but-inert (it has no
          `next_fire_at` yet), and conflating them hides exactly the automations a user comes
          to this panel to ask about. */}
      <Fact label="Next fire">
        {nf.source === 'none'
          ? <span className="text-on-surface-low">Never — this automation has no scheduled fire.</span>
          : <>
            {nf.cadence}{nf.at ? ` · ${new Date(nf.at).toLocaleString()}` : ''}
            <span className="ml-1.5 text-on-surface-low">
              {nf.armed ? '· armed' : '· not armed yet (this is a preview, not a scheduled time)'}
            </span>
          </>}
      </Fact>

      {/* 2 — the rendered action config. A secret is NAMED, never resolved. */}
      <Fact label="Action">
        <span className="font-mono">{ac.provider || '(none)'}</span>
        {ac.secret_refs.length > 0 && (
          <span className="ml-1.5 text-on-surface-low">· uses {ac.secret_refs.join(', ')}</span>
        )}
        {ac.render_error
          ? <div role="alert" style={{ color: 'var(--color-warning)' }}>Would fail to render: {ac.render_error}</div>
          : ac.rendered
            ? <pre className="mt-1 overflow-x-auto rounded-md bg-surface px-2.5 py-2 text-on-surface-low text-[0.6875rem]">{ac.rendered}</pre>
            : null}
        <pre className="mt-1 overflow-x-auto rounded-md bg-surface px-2.5 py-2 text-on-surface-low text-[0.6875rem]">
          {JSON.stringify(ac.config, null, 2)}
        </pre>
      </Fact>

      {/* 3 — the session the fire targets. */}
      <Fact label="Session">
        <span className="font-mono">{d.session_key.key}</span>
        <span className="ml-1.5 text-on-surface-low">· {d.session_key.mode}</span>
      </Fact>

      {/* 4 — capability grants. Three distinct renderings (fenced / granted by the read-only
          default / granted by an explicit opt-in), because a refusal a user cannot explain is
          one they work around by widening the allowlist far past what the automation needed. */}
      <Fact label="Allowed to">
        {cg.granted
          ? <span style={{ color: 'var(--color-success)' }}>
            {Object.keys(cg.needs_fence).length === 0
              ? 'Yes — a read-only action needs no opt-in.'
              : `Yes — the frozen set grants ${Object.values(cg.needs_fence).flat().join(', ')}.`}
          </span>
          : <span style={{ color: 'var(--color-warning)' }}>
            Refused: {cg.refused.map((r) => `${r.value} (${r.reason})`).join(' · ')}
          </span>}
      </Fact>

      {/* 5 — the observe-mode result, from AUTOMATION-SUBSTRATE's dry fire. The T9 rule is in
          the copy: only the spawn-based providers have a real observe mode, so for everything
          else this says PREVIEW rather than promising a safety property it does not have. */}
      <Fact label={om.mode === 'observe' ? 'Observe-mode dry fire' : 'Preview (no observe mode)'}>
        {!om.provider_known && (
          <div style={{ color: 'var(--color-warning)' }}>
            No provider named {om.provider || '(none)'} is registered — this automation cannot run.
          </div>
        )}
        {om.mode === 'preview' && om.provider_known && (
          <div className="text-on-surface-low">
            {om.provider} executes its config directly and has no observe mode, so this describes
            what would run instead of running it.
          </div>
        )}
        <pre className="mt-1 overflow-x-auto rounded-md bg-surface px-2.5 py-2 text-on-surface-low text-[0.6875rem]">{om.detail}</pre>
        {om.gate_plan.enforced && om.gate_plan.enforced.length > 0 && (
          <div className="text-on-surface-low">Gates enforced: {om.gate_plan.enforced.join(', ')}</div>
        )}
      </Fact>

      {/* AUTO-R15's `closest` suggestion. "Invalid" with no next step is how a near-miss becomes
          a dead row nobody diagnoses, so the suggested key is surfaced with the issue. */}
      {d.trigger.issues.length > 0 && (
        <Fact label="Problems with this row">
          <ul className="list-none">
            {d.trigger.issues.map((i, n) => (
              <li key={n} className="text-on-surface-low">
                {i.path}: {i.message}{i.closest ? ` — did you mean ${i.closest}?` : ''}
              </li>
            ))}
          </ul>
        </Fact>
      )}
    </div>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="text-[0.75rem]">
      <div className="text-on-surface-low">{label}</div>
      <div className="text-on-surface-var">{children}</div>
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
  // 🔴 `setSnap(null)` on failure left this section rendering **"Loading…" forever** while **Run now
  // stayed enabled** — measured with `/api/doctor/remediation` at 500: no error text anywhere on the page
  // and the maintenance button still armed. Two defects in one line: the fabricated-pendency shape (a
  // dead end that looks like a slow network) and an action offered against state nobody could read.
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const load = useCallback(() => {
    api.doctorRemediation().then((v) => { setSnap(v); setLoadErr(null) }).catch(setLoadErr)
  }, [])
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
            {snap
              ? <>Health score <span className="tabular-nums" style={{ color: snap.score >= snap.target_score ? 'var(--color-success)' : 'var(--color-warning)' }}>{Math.round(snap.score)}</span> / target {snap.target_score}</>
              : loadErr
                ? <span role="alert">Couldn't load the health score: {String((loadErr as Error)?.message || loadErr)}</span>
                : 'Loading…'}
          </div>
          {/* Disabled only on a READ FAILURE, not during the initial load: running maintenance whose
              current score cannot be read means the result is unverifiable. Same reasoning as the
              incident kill switch staying disabled while its state is unknown. */}
          <Button variant="secondary" size="sm" onClick={run} loading={busy} disabled={Boolean(loadErr)}
            disabledReason={loadErr ? 'The health score could not be read' : undefined}>
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
