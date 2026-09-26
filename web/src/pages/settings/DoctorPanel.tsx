import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { RefreshCw, ChevronRight, CheckCircle2, AlertTriangle, XCircle, Wrench, FlaskConical } from 'lucide-react'
import {
  api, isSwitchedOff, type DoctorReport, type DoctorCapability, type DoctorProbe, type DoctorFix,
  type RemediationSnapshot, type SurfacingCandidate, type AutomationWouldExecute, type Trigger,
  type SwitchedOffView,
} from '../../lib/api'
import { notify } from '../../app/appSdk'
import { confirm } from '../../ui/dialog'
import { InvestigateButton } from '../../ui/InvestigateButton'
import { PanelHeader, Section } from './settingsUI'
// The shared epoch-seconds formatters, not a tenth local copy — `DevicesPanel` in this same
// directory already imports them, and `design/epoch` rails the family against re-implementation.
import { relPast } from '../schedule/scheduleMeta'
import { Select, TextInput } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { FormSkeleton, InlineLoadError } from '../../ui/ListScaffold'

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

/** Doctor — tiered health probes (PLATFORM-RESILIENCE §1). Runs every capability probe and groups
 *  the results into cards. The doctrine is honored in the copy: a failed CAPABILITY is a degraded
 *  row, never a "gateway broken" claim — only a core-tier failure says the gateway itself needs
 *  attention.
 *
 *  PROBING is read-only. The panel is not, and has not been since §2's fixes and §3's simulators
 *  landed: `FixButton` applies a confirm-gated, SEL-audited repair and `RemediationSection` runs
 *  the maintenance engine behind its own confirm. The header hint names both, because a promise
 *  that outlives the code it described is how issue 537 happened — this docstring's own closing
 *  clause ("Nothing here changes any state; fixes (§2) and simulators (§3) land in later
 *  sessions") was the same claim one layer up, still describing a panel that shipped two sessions
 *  ago. */
export function DoctorPanel() {
  const [report, setReport] = useState<DoctorReport | SwitchedOffView | null>(null)
  // `true` from the start: the mount effect below starts the first read. It was `false`, so the first
  // frame drew the whole panel — and mounted Simulators and Maintenance, which fire their own reads —
  // before the skeleton replaced it. With the Doctor switched off that was two requests whose answer
  // the report was about to give.
  const [busy, setBusy] = useState(true)
  // Bumped when a Fix lands, so Maintenance re-reads the score the Fix just changed — the two
  // sections read one health, and a Fix that turned a card green under a stale score would be
  // the page disagreeing with itself.
  const [rev, setRev] = useState(0)

  // `fresh` for the page's own Re-run and the re-read after a repair: the server caches the
  // report for 30s, so a cached read here showed the verdict from BEFORE the Fix or the run.
  const refresh = useCallback((fresh = false) => {
    setBusy(true)
    api.doctor(fresh).then(setReport).catch(() => setReport(null)).finally(() => setBusy(false))
  }, [])
  useEffect(() => { refresh() }, [refresh])
  const onFixed = useCallback(() => { refresh(true); setRev((n) => n + 1) }, [refresh])

  if (report === null && busy) return <FormSkeleton sections={2} />
  // `resilience.doctor_enabled` off: the report read answers `{"enabled": false}` (it used to 404,
  // which this page drew as "Couldn't load the doctor report"). Probes, simulators and maintenance
  // all address a surface that is off, so none of them is drawn — or asked for — and the page
  // offers the one thing that is possible: turning it back on.
  if (isSwitchedOff(report)) return <DoctorOff onTurnedOn={() => refresh(true)} />

  const caps = report ? Object.entries(report.capabilities) : []
  // Show a failed capability before the healthy ones (attention first).
  caps.sort(([, a], [, b]) => Number(a.ok) - Number(b.ok))

  return (
    <div>
      {/* 🔴 THIS SENTENCE DENIED MUTATIONS THE SAME PANEL EXPLAINS (issue 537). It opened
          "Read-only health probes" and closed with a blanket no-change promise, while the file
          rendered a failed probe's Fix (`api.doctorFixApply` — symlinks, stale locks, stale
          bindings) and Maintenance → Run now (`api.doctorRemediationRun` — embedding re-index,
          orphan prune, skill aging), with a dry-run description of what Run now WOULD do a few
          lines above its own button.

          CORRECTED, NOT MADE TRUE. The remediation engine is PLATFORM-RESILIENCE §2's shipped
          deliverable; deleting a working feature to rescue a sentence is the wrong direction.
          Scoped in `DiagnosticsPanel`'s shape — the read-only claim survives, attached to the
          PROBING it is actually true of, and each exception is named by the label on the control
          the reader will meet, so the sentence can be checked against the screen rather than
          believed.

          🪤 EACH CLAUSE IS RE-MEASURED AGAINST THIS FILE, NOT INHERITED. Both controls confirm
          today — `FixButton`'s "Apply this fix?" and `RemediationSection`'s "Run maintenance" —
          so the hint says so of both. An earlier draft of this fix asserted that Run now does
          NOT confirm, which was true of the panel when it was written and is false of the panel
          now; shipping it would have replaced one stale promise with another. */}
      <PanelHeader
        title="Doctor"
        hint="Health probes across every subsystem — memory, channels, local models, app backends, the SPA symlink, and model-provider breakers. A degraded capability never means the gateway is down; only a core failure does. Probing changes nothing on your machine; the two controls that do are a failed probe's Fix, which confirms first and is written to the security audit, and Maintenance → Run now, which also confirms and lists its exact plan above the button."
      />

      <div className="mb-l flex items-center justify-between gap-l">
        {report ? <StatusBanner report={report} /> : (
          // `role="alert"`: on a HEALTH surface, "we could not probe" is unrequested bad news that
          // changes what the screen means — the same reason `LoadError` announces. Measured before:
          // the sentence rendered with `[role="alert"]` count 0, so a screen-reader user reading the
          // panel top-down heard the Doctor's hint and then a Re-run button, with nothing between them.
          <div role="alert" data-type="body-s" className="text-on-surface-low">Couldn't load the doctor report.</div>
        )}
        <Button variant="secondary" size="sm" onClick={() => refresh(true)} loading={busy}>
          <RefreshCw size={15} /> Re-run
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
            {caps.map(([key, cap]) => <CapabilityCard key={key} name={key} cap={cap} onFixed={onFixed} />)}
          </div>
          {report.skipped_capabilities.length > 0 && (
            <div data-type="caption" className="mt-m text-on-surface-low">
              Skipped (core failed first): {report.skipped_capabilities.map(capLabel).join(', ')}
            </div>
          )}
        </Section>
      )}

      <SimulatorsSection />
      <RemediationSection rev={rev} onRan={() => refresh(true)} />
    </div>
  )
}

/** The Doctor page while `resilience.doctor_enabled` is off.
 *
 *  🔑 THE BUTTON IS THE WAY BACK ON, AND IT DID NOT EXIST. The switch is in the config allowlist
 *  but had no control anywhere in `web/`, so a Doctor switched off in `config.json` could only be
 *  switched back on there. The Doctor itself is the natural home for it: this page is where a user
 *  lands looking for health, and it is the page that has nothing else to show. No Retry beside it
 *  — a switch that is off does not flip when the read is repeated — and no `role="alert"`, because
 *  a decided answer is not unrequested bad news. */
function DoctorOff({ onTurnedOn }: { onTurnedOn: () => void }) {
  const [busy, setBusy] = useState(false)
  const turnOn = () => {
    setBusy(true)
    api.patchConfig('resilience.doctor_enabled', true)
      .then(onTurnedOn)
      .catch((e) => notify(`Couldn't turn the Doctor on: ${String((e as Error)?.message || e)}`, 'error'))
      .finally(() => setBusy(false))
  }
  return (
    <div>
      <PanelHeader title="Doctor" hint="Health probes across every subsystem — memory, channels, local models, app backends, the SPA symlink, and model-provider breakers." />
      <Section title="The Doctor is off">
        <p data-type="body-s" className="text-on-surface-low">
          Nothing is probing health, so no failed check is shown and no repair is offered. Turning it on runs the probes once now.
        </p>
        <div className="mt-m">
          <Button size="sm" onClick={turnOn} loading={busy} loadingLabel="Turning the Doctor on…">Turn the Doctor on</Button>
        </div>
      </Section>
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
      <div data-type="body-m" className="text-on-surface">Skill surfacing</div>
      <div data-type="caption" className="mt-0.5 text-on-surface-low">
        Which skills a message would surface, and — for the ones it wouldn't — why not.
      </div>
      {/* `TextInput`/`Select` from the shared form family, not raw elements: the primitive-adoption
          ratchet counts bespoke chrome, and these two carry the accessible-name plumbing (an
          explicit `ariaLabel` wins, which is what a control outside a `Field` needs). */}
      <form
        className="mt-s flex items-center gap-s"
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
        <div role="alert" data-type="caption" className="mt-s text-on-surface-low">
          Couldn't simulate surfacing: {String((err as Error)?.message || err)}
        </div>
      )}
      {rows !== null && rows.length === 0 && (
        <div data-type="caption" className="mt-s text-on-surface-low">No skill scored against that message.</div>
      )}
      {rows !== null && rows.length > 0 && (
        <div className="mt-s flex flex-col gap-xs border-t border-outline-variant/30 pt-s">
          {rows.map((c) => (
            <div key={c.key} data-type="caption" className="flex items-baseline justify-between gap-s">
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
  // #532: distinct from `err`, which is the SIMULATE failure. The list read used to
  // `.catch(() => setTriggers([]))`, and `empty` then drove two separate claims — the
  // "No automations yet. Create one on the Automations page" line and the Describe button's
  // `disabledReason` "You have no automations yet" — out of a failed read.
  const [listErr, setListErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.triggers().then((r) => setTriggers(r.triggers)).catch(setListErr)
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
      <div data-type="body-m" className="text-on-surface">Automation would-execute</div>
      <div data-type="caption" className="mt-0.5 text-on-surface-low">
        What one automation would do on its next fire — the resolved schedule, the rendered action,
        the session it targets, what it is allowed to do, and its observe-mode dry fire.
      </div>
      <div className="mt-s flex items-center gap-s">
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
          disabledReason={listErr !== null ? "Couldn't load your automations" : empty ? 'You have no automations yet' : 'Pick an automation first'}>
          <FlaskConical size={14} /> Describe
        </Button>
      </div>
      {listErr !== null && <InlineLoadError what="your automations" error={listErr} />}
      {/* 🪤 Deliberately NOT "…and it will appear here": this list is read once on mount, so that
          sentence would promise a live update the panel does not do — and the empty-state-promise
          census exists precisely to keep that shape out. */}
      {empty && (
        <div data-type="caption" className="mt-s text-on-surface-low">
          No automations yet. Create one on the Automations page, then reopen this panel.
        </div>
      )}
      {err !== null && (
        <div role="alert" data-type="caption" className="mt-s text-on-surface-low">
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
    <div className="mt-s flex flex-col gap-1.5 border-t border-outline-variant/30 pt-s">
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
            ? <pre data-type="caption" className="mt-xs overflow-x-auto rounded-md bg-surface px-2.5 py-s text-on-surface-low">{ac.rendered}</pre>
            : null}
        <pre data-type="caption" className="mt-xs overflow-x-auto rounded-md bg-surface px-2.5 py-s text-on-surface-low">
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
        <pre data-type="caption" className="mt-xs overflow-x-auto rounded-md bg-surface px-2.5 py-s text-on-surface-low">{om.detail}</pre>
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
    <div data-type="caption">
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
 *  snapshot — jsdom reports every box as 0, so nothing about them is measurable from layout.
 *
 *  `rev` re-reads the score when the page above applied a Fix; `onRan` tells the page to re-probe
 *  after Run now. The score counts the Doctor's failed checks, so either side changing without the
 *  other re-reading would leave one page showing two healths. */
export function RemediationSection({ rev = 0, onRan }: { rev?: number; onRan?: () => void } = {}) {
  const [snap, setSnap] = useState<RemediationSnapshot | null>(null)
  // The Doctor switched off between this read and the page's: the page above says so, and this
  // section has nothing to add to it.
  const [off, setOff] = useState(false)
  const [busy, setBusy] = useState(false)
  // 🔴 `setSnap(null)` on failure left this section rendering **"Loading…" forever** while **Run now
  // stayed enabled** — measured with `/api/doctor/remediation` at 500: no error text anywhere on the page
  // and the maintenance button still armed. Two defects in one line: the fabricated-pendency shape (a
  // dead end that looks like a slow network) and an action offered against state nobody could read.
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const load = useCallback(() => {
    api.doctorRemediation().then((v) => {
      setOff(isSwitchedOff(v)); setSnap(isSwitchedOff(v) ? null : v); setLoadErr(null)
    }).catch(setLoadErr)
  }, [])
  useEffect(() => { load() }, [load, rev])

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
      // 🔴 THE LEVEL WAS THE LITERAL 'success', WHATEVER CAME BACK. Measured against a live
      // gateway: POST /api/doctor/remediation/run on a home with 25 unembedded knowledge
      // items returned `{score_before: 100, score_after: 100, jobs: [], stopped_reason:
      // "target_score already met"}` and the panel raised a GREEN success toast reading
      // "score 100→100 (target_score already met)". Nothing ran, the one real deficit is
      // untouched, and the only feedback the user got was the colour that means "done".
      // A run whose jobs threw took the same green.
      //
      // So the level is derived from the result: a failed job is an error, a pass that ran no
      // work is information, and only a pass that did work without failing is a success.
      //
      // `=== 'error'`, NOT `!== 'ok'`: `run_remediation` appends four statuses and
      // `skipped_cooldown` is the storm guard doing its job, not a failure. Measured against the
      // live gateway — a second Run now inside the 6h window returned
      // `jobs: [{status: "skipped_cooldown"}]`, which a `!== 'ok'` count would have reported as
      // "1 not ok" in error red. It ran nothing, which is `info`.
      const failed = r.jobs.filter((j) => j.status === 'error').length
      const ran = r.jobs.filter((j) => j.status === 'ok').length
      const level = failed > 0 ? 'error' : ran === 0 ? 'info' : 'success'
      notify(
        ran === 0 && failed === 0
          // "no work happened" is the fact the old wording hid behind the score pair.
          ? `Maintenance changed nothing — ${r.stopped_reason}. Score ${Math.round(r.score_after)}.`
          : `Maintenance: score ${Math.round(r.score_before)}→${Math.round(r.score_after)} · ${ran} ok${failed > 0 ? ` · ${failed} failed` : ''} (${r.stopped_reason})`,
        level,
      )
      load()
      onRan?.()
    } catch (e) {
      notify(`Maintenance failed: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy(false) }
  }

  if (off) return null
  return (
    <Section title="Maintenance" hint="A health-scored engine keeps the stores tidy (embedding re-index, orphan prune, skill aging) on an adaptive schedule. Run it on demand here.">
      <div className="rounded-lg bg-surface-container px-4 py-3">
        <div className="flex items-center justify-between gap-l">
          <div data-type="body-s" className="text-on-surface">
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

            EVERY row counts. health_score() used to sum REACHABLE deficits only, so the number
            meant "what maintenance can still do" while reading as "how healthy this is": B16
            measured "Health score 100 · no deficits measured" directly under the Doctor's failed
            "faiss index desync" and "4 unclaimed paths" checks. Failed checks are now rows here
            (a `check:` key, labelled by their probe title), and an unreachable row subtracts like
            any other. `reachable` still decides what Run now touches: the rest are greyed and
            carry their own next step, so nobody presses Run now expecting them to clear.

            🔴 AND "not fixable yet" WAS NOT ENOUGH. Measured on a seeded home: a row reading
            `Knowledge missing embeddings ×25 · not fixable yet` — a dead end on screen, since
            "yet" promises a later pass that will never come when what is missing is an embedding
            model, not a maintenance tick. The engine knew that exactly where it computed
            `reachable`; `blocked_by` is that sentence, carried through instead of dropped, so the
            row names the prerequisite and the next step. */}
        {scored.length > 0 && (
          <div className="mt-s flex flex-col gap-xs border-t border-outline-variant/30 pt-s">
            {scored.map((d) => (
              <div key={d.key} data-type="caption" className="flex items-baseline justify-between gap-s">
                <span className={d.reachable ? 'text-on-surface-var' : 'text-on-surface-low'}>
                  {d.title || capLabel(d.key)}
                  <span className="ml-1.5 text-on-surface-low tabular-nums">×{d.count}</span>
                  {/* One reason string, produced once in `Deficit.blocked_by` and rendered
                      identically by `personalclaw doctor` — not a per-key map re-derived here,
                      which is the same sentence written twice in the surface least able to
                      know why the engine bailed. Non-empty exactly when `reachable` is false;
                      the truthiness guard keeps a bare "·" off screen if that ever slips. */}
                  {!d.reachable && d.blocked_by && (
                    <span className="ml-1.5 text-on-surface-low">· {d.blocked_by}</span>
                  )}
                </span>
                {/* Every row's penalty, because every row is subtracted: the column adds up to the
                    distance between the score above and 100. */}
                <span className="shrink-0 text-on-surface-low tabular-nums">
                  −{d.penalty.toFixed(1)}
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
          <div data-type="caption" className="mt-s border-t border-outline-variant/30 pt-s text-on-surface-low">
            {snap.plan.length > 0
              /* A job in its cooldown is IN the dry-run plan but will not run — naming it as
                 something Run now "would" do was a promise the run then broke. */
              ? <>Run now would: {snap.plan.map((j) => `${capLabel(j.id)}${j.status === 'skipped_cooldown' ? ' (cooling down — not yet)' : ''}`).join(' · ')}</>
              : scored.length === 0
                ? 'Nothing to do — no deficits measured.'
                : snap.score >= snap.target_score
                  ? 'Run now would do nothing — the score already meets its target, so the engine stops before touching the items above.'
                  /* SPLIT from the branch above, because they read opposite: here the score is
                     BELOW target and still nothing will run, which is exactly when a reader needs
                     to be told the rows are theirs to act on. */
                  : 'Run now would do nothing — nothing measured above is fixable by maintenance; each row names what it needs instead.'}
          </div>
        )}
        {/* The run ledger. It said `score 88→100 · 1 job · target_score reached` — dropping WHEN
            (`ts`) and WHAT (`jobs[].status`/`detail`/`error`), so a pass whose every job threw
            rendered identically to one that did the work. A silently failing maintenance job is
            the one thing this list exists to catch, so failures are counted on the summary line
            and the newest pass names each job's outcome underneath. */}
        {snap && snap.recent_runs.length > 0 && (
          <div className="mt-s flex flex-col gap-xs border-t border-outline-variant/30 pt-s">
            {snap.recent_runs.slice(0, 5).map((r, i) => {
              // `=== 'error'`: `skipped_cooldown` is the storm guard working, not a failure. See
              // the same discrimination on the toast level above.
              const failed = r.jobs.filter((j) => j.status === 'error').length
              return (
                <div key={i} data-type="caption" className="text-on-surface-low">
                  <div>
                    {relPast(r.ts)} · score {Math.round(r.score_before)}→{Math.round(r.score_after)} ·{' '}
                    {r.jobs.length === 1 ? '1 job' : `${r.jobs.length} jobs`}
                    {failed > 0 && <span style={{ color: 'var(--color-warning)' }}>{` · ${failed} failed`}</span>}
                    {' · '}{r.stopped_reason}
                  </div>
                  {/* Newest pass only — progressive disclosure. Five expanded ledger rows would
                      bury the score this section is about; the latest one is the pass a reader is
                      actually asking about, and older failures still show in its count above. */}
                  {i === 0 && r.jobs.map((j, k) => (
                    <div key={k} className="ml-m text-on-surface-low">
                      {capLabel(j.id)} — {j.status}{(j.error || j.detail) ? `: ${j.error || j.detail}` : ''}
                    </div>
                  ))}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </Section>
  )
}

// ── overall status line ──────────────────────────────────────────────────────
/** 🔴 WHEN THE VERDICT WAS TAKEN. `generated_at` had 0 readers anywhere in `web/src` — found by
 *  censusing the whole doctor payload, not just the deficits — so "All systems healthy" was a
 *  claim with no as-of. On a panel left open, or reopened from a cached report, that green line is
 *  a statement about a moment the reader cannot see, which is the same missing-evidence shape as
 *  the deficits below: the fact was measured, shipped, and dropped at the last step. */
function StatusBanner({ report }: { report: DoctorReport }) {
  const stamp = <span className="text-on-surface-low"> · checked {relPast(report.generated_at)}</span>
  if (report.core_ok && report.ok) {
    return (
      <div data-type="body-s" className="flex items-center gap-s" style={{ color: 'var(--color-success)' }}>
        <CheckCircle2 size={16} /> <span>All systems healthy{stamp}</span>
      </div>
    )
  }
  if (!report.core_ok) {
    return (
      <div data-type="body-s" className="flex items-center gap-s" style={{ color: 'var(--color-error)' }}>
        <XCircle size={16} />
        <span>Gateway core failing{report.restart_suggested ? ' — a restart may be required' : ''}{stamp}</span>
      </div>
    )
  }
  // core OK, but a capability degraded — the doctrine framing.
  return (
    <div data-type="body-s" className="flex items-center gap-s" style={{ color: 'var(--color-warning)' }}>
      <AlertTriangle size={16} />
      <span>Core healthy · {capLabel(report.worst)} degraded{stamp}</span>
    </div>
  )
}

// ── one capability card ────────────────────────────────────────────────────
function CapabilityCard({ name, cap, onFixed }: { name: string; cap: DoctorCapability; onFixed: () => void }) {
  const Icon = cap.ok ? CheckCircle2 : cap.tier <= 2 ? XCircle : AlertTriangle
  const color = cap.ok ? 'var(--color-success)' : cap.tier <= 2 ? 'var(--color-error)' : 'var(--color-warning)'
  return (
    <div className="rounded-lg bg-surface-container px-4 py-3">
      <div className="flex items-center gap-s">
        <Icon size={16} style={{ color }} />
        <span data-type="body-m" className="text-on-surface">{capLabel(name)}</span>
        {!cap.ok && (
          <span data-type="caption" className="text-on-surface-low">· failed at tier {cap.tier}</span>
        )}
        {/* Investigate (plan 60): re-runs this capability's read-only probes and
            opens a chat with the findings + any offered fix's dry-run preview —
            discussing a fix, never applying one. */}
        <span className="ml-auto">
          <InvestigateButton kind="doctor_finding" id={name} backLink="#/settings/doctor" size={28} />
        </span>
      </div>
      <div className="mt-s flex flex-col gap-1.5">
        {cap.probes.map((p) => <ProbeRow key={p.id} probe={p} onFixed={onFixed} />)}
      </div>
    </div>
  )
}

// ── a confirm-gated fix button (PLATFORM-RESILIENCE §2) ─────────────────────
// Nothing auto-applies: a two-step confirm (the armed-delete pattern) runs the fix,
// which is SEL-audited server-side. On success we re-run the doctor so the fixed
// capability turns green.
//
// The confirm names THIS fix: its title, its impact and its dry-run preview, read from
// `/api/doctor/fixes` when the button is pressed. It used to be one generic sentence enumerating
// "symlinks, stale locks, or stale bindings" for every fix — untrue the moment a fix did anything
// else (the memory index rebuild), and it never showed the preview the catalog already computes.
// A catalog read that fails is SAID in the dialog, with its reason, rather than papered over with
// a generic description that might not be true of this fix.
function FixButton({ fixId, onFixed }: { fixId: string; onFixed: () => void }) {
  const [busy, setBusy] = useState(false)
  const run = async () => {
    let fix: DoctorFix | undefined
    let unread = ''
    try {
      const catalog = await api.doctorFixes()
      fix = isSwitchedOff(catalog) ? undefined : catalog.fixes.find((f) => f.id === fixId)
      if (!fix) unread = isSwitchedOff(catalog) ? 'the Doctor has been switched off' : 'the server does not list it'
    } catch (e) { unread = e instanceof Error ? e.message : 'the request failed' }
    if (!(await confirm({
      title: fix ? `${fix.title}?` : 'Apply this fix?',
      body: fix
        ? `${fix.impact}\n\n${fix.preview}\n\nIt is logged to the security audit.`
        : `Couldn't read what this fix does (${unread}), so it can't be described here. It is logged to the security audit.`,
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
    <Button variant="secondary" size="xs" onClick={run} loading={busy} className="mt-xs shrink-0">
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
  // A failed probe offers its Fix, or says there is none and what to do. The page header promises
  // "a failed probe's Fix", and a failed row carrying neither was a dead end (settings B16: two
  // failed checks, no Fix, and nothing on either row saying so). The probe's own `remedy` names
  // the next step; a probe that has not written one still gets a true sentence, pointing at the one
  // control every card carries.
  const remedy = !probe.ok && !probe.fix_id
    ? (probe.remedy || 'No automatic fix for this check — use Investigate in chat on this card to find the next step.')
    : ''
  const head = (
    <>
      <span className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: dot }} />
      <span className="min-w-0 flex-1">
        <span data-type="body-s" className="text-on-surface">{probe.title}</span>
        <span data-type="caption" className="block text-on-surface-low">{probe.detail}</span>
        {remedy && <span data-type="caption" className="block text-on-surface-var">{remedy}</span>}
      </span>
      {probe.fix_id && !probe.ok && <FixButton fixId={probe.fix_id} onFixed={onFixed} />}
    </>
  )
  if (!hasEvidence) {
    return (
      <div className="flex items-start gap-s border-b border-outline-variant/30 pb-1.5 last:border-0 last:pb-0">
        {head}
      </div>
    )
  }
  return (
    <details className="group border-b border-outline-variant/30 pb-1.5 last:border-0 last:pb-0">
      <summary className="flex cursor-pointer list-none items-start gap-s">
        {head}
        <ChevronRight
          size={14}
          className="mt-xs shrink-0 text-on-surface-low transition-transform group-open:rotate-90"
        />
      </summary>
      <pre data-type="caption" className="mt-1.5 overflow-x-auto rounded-md bg-surface px-2.5 py-s text-on-surface-low">
        {JSON.stringify(probe.evidence, null, 2)}
      </pre>
    </details>
  )
}
