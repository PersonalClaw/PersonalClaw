import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { api, type RunnerRow } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { reportingWrite } from '../../app/reportingWrite'
import { useAgentCatalog, ensureBindableAgentName, type AgentOption } from '../../lib/agents'
import { useQuery } from '../../lib/data'
import { durableWorkersHint, usePersistAvailable } from '../../lib/persistClaim'
import { PanelHeader, Section, RowGroup, Row, SegPills, SavedToast, StrListField, ToggleRow } from './settingsUI'
import { Combobox } from '../../ui/Combobox'
import { FieldError, NumberField, TextInput, useSyncedDraft } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { InlineError } from '../../ui/InlineError'
import { accentChip } from '../../design/accent'
import { setAgentYolo } from './agentYolo'
import { APPROVAL_MODE_HINT, APPROVAL_MODES, approvalModeMeans } from './approvalModes'

// The editable agent.* fields mirror the backend _EDITABLE_CONFIG allowlist
// (types + ranges are the server's truth; we surface the same bounds).
type AgentCfg = Record<string, unknown>

/** Agent defaults — the default agent for new chats + the agent-execution
 *  config (approval, subagents, advanced). Each control PATCHes a single
 *  allowlisted path via /api/config/personalclaw. Session/warm-pool settings
 *  live under Chat. */
export function AgentDefaultsPanel() {
  const [cfg, setCfg] = useState<AgentCfg | null>(null)
  const { options: agentOptions, loading: agentsLoading, discovered } = useAgentCatalog()
  /** `null` = the default agent could not be read — never '' (which renders as "no default"). */
  const [defaultAgent, setDefaultAgent] = useState<string | null>('')
  // Whether THIS host can keep a worker's shell alive past a restart, for the durable-workers row
  // below. Called up here, above the early returns — a hook after a conditional `return` is a
  // different hook order on the loading pass.
  const persistAvailable = usePersistAvailable()

  // Stale-while-revalidate + persist: paint instantly on revisit/reload from one
  // cached snapshot. The editable form state (cfg/defaultAgent) is seeded and
  // rehydrated from this read-only `data`; mutations stay optimistic below.
  const { data, error: loadErr, refresh } = useQuery('settings:agent-defaults', async () => {
    const [plaw, agents] = await Promise.all([
      api.personalclawConfig().then((c) => (c.agent ?? {}) as AgentCfg),
      // Tolerated so the rest of the panel still renders, but as `null`, never `''`: `''` showed
      // the picker at "Select an agent…", i.e. no default, when the default had simply not been
      // read — and invited a pick against that. The row says it could not read it instead.
      api.agents().then((d) => d.default_agent).catch(() => null),
    ])
    return { cfg: plaw, defaultAgent: agents }
  }, { persist: true })

  useEffect(() => {
    if (data) { setCfg(data.cfg); setDefaultAgent(data.defaultAgent) }
  }, [data])

  // 🔴 A settings panel must not present FABRICATED values as saved state. `.catch(() => ({}))` made a
  // failed config read resolve with an empty section, so every control below rendered at its fallback —
  // indistinguishable from "this is what you saved" — and the panel offered to edit values it had never
  // loaded. Measured on `#/settings/agent` with `/api/config` at 500: the form rendered in full with no
  // error anywhere. Now the rejection reaches the hook and the form is replaced by the failure.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg || agentsLoading) return <FormSkeleton sections={3} what="settings" />

  // Selecting a discovered ACP agent materializes a saved profile for it first,
  // then sets default_agent to that profile name (persistent bindings resolve a
  // saved profile, not an ephemeral discovered agent).
  const onPickDefault = async (value: string) => {
    let name: string
    try { name = await ensureBindableAgentName(value, discovered) }
    catch (e) { notify(`Couldn't save that agent as a binding: ${String((e as Error)?.message || e)}`, 'error'); return }
    const prev = defaultAgent
    setDefaultAgent(name)
    api.setDefaultAgent(name).catch((e) => {
      setDefaultAgent(prev)
      notify(`Couldn't set the default agent: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  // local optimistic patch helper — updates state, fires the single-field PATCH.
  // A rejected PATCH (e.g. allowlist drift) rolls the optimistic value back and
  // surfaces the server error — a swallowed 400 here looks exactly like a save.
  const patch = (key: string, value: unknown, onSaved: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`agent.${key}`, value).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  // A list field sends the names added and removed, never this panel's copy of the list; the chips
  // then show the list as stored, which includes anything another tab added meanwhile.
  const editList = (key: string, next: string[], onSaved: () => void, label?: string) => {
    const prev = Array.isArray(cfg[key]) ? (cfg[key] as string[]) : []
    api.saveListEdits(`agent.${key}`, prev, next).then((stored) => {
      setCfg((c) => ({ ...c, [key]: stored }))
      onSaved()
    }).catch((e) => notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error'))
  }

  // YOLO goes through the ONE writer (`agentYolo.setAgentYolo`), which asks before turning it on,
  // carries the consent the server requires, and reports a failed write itself — the hub tile calls
  // the same function, so the two surfaces cannot ask differently, or one of them not at all. Not
  // optimistic, unlike `patch` above: the switch moves once the server has accepted, so it never
  // shows a bypass that is not live, and a declined dialog or a refused write leaves it where it was.
  const patchYolo = (_key: string, value: boolean, onSaved: () => void) => {
    void setAgentYolo(value).then((wrote) => {
      if (!wrote) return
      setCfg((c) => ({ ...c, yolo: value }))
      onSaved()
    })
  }

  // `agent.self_qa.*` is a nested section, so its rows need a patch that prefixes the sub-path
  // AND rolls back into the nested object. Reusing the flat `patch` above would PATCH
  // `agent.enabled` — a path the server's allowlist rejects, so the control would appear to work
  // and then quietly revert.
  const selfQa = (cfg.self_qa ?? {}) as Record<string, unknown>
  // `confirmed` is `ToggleRow`'s own "Let the companion propose fix branches?" dialog, forwarded so
  // the gateway, which asks consent for that loosening itself, does not ask a second time.
  const patchSelfQa = (key: string, value: unknown, onSaved: () => void, label?: string, confirmed?: boolean) => {
    const prev = selfQa[key]
    setCfg((c) => ({ ...c, self_qa: { ...((c?.self_qa ?? {}) as object), [key]: value } }))
    api.patchConfig(`agent.self_qa.${key}`, value, confirmed === true).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, self_qa: { ...((c?.self_qa ?? {}) as object), [key]: prev } }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Agent defaults" hint="The default agent for new chats and how agents execute — approval, subagents, and advanced safety knobs. New sessions inherit these unless overridden." />

      {/* A chat's default only. A loop runs on what its own composer's "Runs on" picks (PersonalClaw
          unless an agent CLI is chosen there): moving every new loop onto another program because a
          chat default changed would hand her unattended work to a CLI she never picked for it. */}
      <Section title="Default agent" hint="Which agent definition serves a new chat when none is chosen. Native agents and connected ACP-runtime agents are both selectable. Loops don't use it: a loop runs on what you pick under Runs on when you create it.">
        <DefaultAgentRow options={agentOptions} value={defaultAgent} onChange={onPickDefault} onRetry={refresh} />
      </Section>

      <Section title="Defaults" hint="Baseline behavior for every session.">
        <RowGroup>
          <ApprovalModeRow cfg={cfg} patch={patch} />
          <ToggleRow label="YOLO mode" cfg={cfg} field="yolo" patch={patchYolo}
            hint="Skip every tool-approval confirmation — overrides approval mode, applies immediately, and stays on until turned off (no expiry, unlike the chat YOLO pill). Only inside a sandbox or for trusted automation." danger />
          {/* Was a fixed two hours, so an approval asked at night was denied before anyone woke.
              Still fails closed: past the wait the call is denied, and the Inbox says so. */}
          <NumberRow label="Approval wait" cfg={cfg} field="approval_timeout_minutes" patch={patch} min={1} max={10080} suffix="min"
            hint="How long a tool approval waits for your answer before it is denied — the Inbox then says what was denied. An approval a running subagent or workflow step asks for also ends with that work's own time limit; asking to start a subagent waits this long. A workflow's approval gates wait this long too, except in a run started unattended, where a gate gives up after 45 seconds and the run says so. An automation or a loop allowed to run on its own never waits: a call its grant does not cover is declined at once, since no one is there to ask." />
        </RowGroup>
      </Section>

      <RunnersSection />

      <Section title="Subagents" hint="Limits for helper agents the main agent spawns.">
        <RowGroup>
          <NumberRow label="Max concurrent subagents" hint="0 = auto-size from this host's CPU and memory." cfg={cfg} field="max_subagents" patch={patch} min={0} max={16} />
          <NumberRow label="Max turns per subagent" cfg={cfg} field="subagent_max_turns" patch={patch} min={1} max={200} />
          <NumberRow label="Subagent timeout" hint="Counted from when a subagent starts running, not from when it was asked for." cfg={cfg} field="subagent_timeout_secs" patch={patch} min={60} max={7200} suffix="s" />
          <NumberRow label="Min free memory to spawn" cfg={cfg} field="spawn_min_memory_gb" patch={patch} min={0} max={64} step={0.5} suffix="GB" />
          <StrListField label="Allowed working directories" hint="Folders besides the workspace where the agent's file tools read files without asking and change them under the same approval as the workspace's files (a room member as its own tier allows), and where a subagent may run. Empty: the workspace only." cfg={cfg} field="subagent_cwd_allowed_roots" editList={editList} placeholder="Add path…" />
        </RowGroup>
      </Section>

      <Section title="Advanced" hint="Delegation, safety enforcement, and diagnostics.">
        <RowGroup>
          <ToggleRow label="Orchestrator skill" cfg={cfg} field="orchestrator_skill" patch={patch}
            hint="Enable agent delegation — loads the orchestrator skill with the agent roster." />
          <ToggleRow label="Concurrent ACP sessions" cfg={cfg} field="acp_concurrent_sessions" patch={patch}
            hint="Run multiple ACP chats on ONE backend process (multiplexing) instead of one process per session. Only takes effect for backends that support session interleaving." />
          <ToggleRow label="Unattended runs need a verified adapter" cfg={cfg} field="unattended_requires_verified_adapter" patch={patch}
            hint="Refuse an unattended spawn — a cron fire, a loop-cycle worker, a subagent, the background session, an inbox or side sweep, a channel delivery, or a trigger dispatch — onto a runner whose ACP adapter has no verified provenance: an npx fetch-at-launch, an adapter that changed since it was provisioned, or a runner with no catalog row. Interactive chat is never gated. See each runner's adapter state under Runners." />
          <EnumRow label="Log level" hint="Backend logger level (overridden by --verbose)." cfg={cfg} field="log_level" patch={patch}
            options={[{ key: 'DEBUG', label: 'Debug' }, { key: 'INFO', label: 'Info' }, { key: 'WARNING', label: 'Warning' }, { key: 'ERROR', label: 'Error' }]} />
          <NumberRow label="Soft-stop budget" cfg={cfg} field="soft_stop_budget_secs" patch={patch} min={0.5} max={60} step={0.5} suffix="s"
            hint="Seconds to wait for a cooperative cancel before hard-killing a session." />
          <NumberRow label="Runner health check interval" cfg={cfg} field="runner_health_check_secs" patch={patch} min={60} max={86400} step={60} suffix="s"
            hint="How long a runner's measured health stays current. Past this, its row under Runners is marked check overdue rather than presenting an old reading as the present state. Nothing is checked automatically — use a runner's Check version." />
          <NumberRow label="Runner idle release" cfg={cfg} field="runner_idle_release_secs" patch={patch} min={60} max={86400} step={60} suffix="s"
            hint="How long a session may hold an agent runner without using it. Past this the hold is released and the runner reads as free under Runners — so a session that went quiet, or a gateway that was killed, cannot leave a runner looking permanently taken. The session itself is untouched." />
          {/* The hint used to END with "Requires the tmux binary; without it this has no effect" —
              naming a precondition it never consulted, while the Terminal page already had the
              host's answer on the wire. It now comes from `lib/persistClaim`, the one owner of
              this promise, so this row and the terminal toggle cannot state one fact two ways. */}
          <ToggleRow label="Durable worker sessions" cfg={cfg} field="durable_sessions" patch={patch}
            hint={durableWorkersHint(persistAvailable)} />
        </RowGroup>
        {/* multi-agent space concurrency (max_spaces / max_space_agents) lives in
            Settings → Spaces, not here. */}
      </Section>

      <Section title="Self-QA companion" hint="Watch a repository and QA each user-impacting commit as a user would — driving the real UI, then filing an Inbox item and a Task when a scenario fails. Off by default: it spends model calls and drives your browser unattended.">
        <RowGroup>
          <ToggleRow label="Enable the companion" cfg={selfQa} field="enabled" patch={patchSelfQa}
            hint="Off means the commit watcher stays idle. A commit only gets a scenario when the change could actually be noticed by a user — test-only and docs-only commits are recorded as skips, with the reason, and cost nothing." />
          <TextRow label="Watched repository" cfg={selfQa} field="watched_repo" patch={patchSelfQa}
            placeholder="/path/to/your/repo"
            hint="Absolute path to the git repository whose new commits trigger a QA run. Empty leaves the watcher idle even when the companion is enabled." />
          <NumberRow label="Max scenarios per run" cfg={selfQa} field="max_scenarios_per_fire" patch={patchSelfQa} min={1} max={20}
            hint="Ceiling on scenarios generated from one push. Every commit still gets a verdict; this bounds how many browser sessions one push can start." />
          <ToggleRow label="Propose fix branches" cfg={selfQa} field="fix_branch_enabled" patch={patchSelfQa} danger
            hint="On a confirmed failure, open a pclaw/selfqa-<sha> branch carrying a proposed diff. Never merged and never pushed — the branch name lands in the Task for you to review."
            confirmOn={{
              title: 'Let the companion propose fix branches?',
              body: 'On every confirmed failure, the companion will open a pclaw/selfqa-<sha> branch in your watched repository carrying a proposed diff — with no further confirmation. It is never merged or pushed automatically.',
              confirmLabel: 'Allow fix branches',
            }} />
        </RowGroup>
      </Section>
    </div>
  )
}

function DefaultAgentRow({ options, value, onChange, onRetry }: { options: AgentOption[]; value: string | null; onChange: (v: string) => void; onRetry: () => void }) {
  const [saved, setSaved] = useState(false)
  if (value === null) {
    return (
      <RowGroup>
        <Row label="Default agent" hint="Used for every new chat.">
          <InlineError icon onRetry={onRetry}>Couldn't read the default agent, so it can't be changed until a retry succeeds.</InlineError>
        </Row>
      </RowGroup>
    )
  }
  // The stored default is a profile NAME; surface it as a selectable option even
  // if it isn't (yet) in the catalog (e.g. a freshly-materialized ACP profile).
  const opts = options.some((o) => o.value === value) || !value
    ? options
    : [{ value, label: value, group: 'Current' }, ...options]
  return (
    <RowGroup>
      <Row label="Default agent" hint="Used for every new chat.">
        <div className="flex items-center gap-2">
          <SavedToast show={saved} />
          <div className="w-56">
            <Combobox options={opts} value={value} placeholder="Select an agent…" emptyText="No agents"
              onChange={(v) => { onChange(v); setSaved(true); window.setTimeout(() => setSaved(false), 1500) }} />
          </div>
        </div>
      </Row>
    </RowGroup>
  )
}

// ── runner catalog ──────────────────────────────────────────────────────────

/** The runner catalog: one row per external agent CLI, with the health evidence
 *  actually measured for it. Every value shown here is a reading or an explicit
 *  "unknown" — an unchecked runner says so, an unparseable version says so, and a
 *  failed check carries the CLI's own error text verbatim rather than a house
 *  paraphrase, because the verbatim text is what tells you WHICH thing is missing.
 *
 *  Reading the catalog runs nothing. A runner's CLI runs only when you press ITS Check,
 *  which runs only its version command, and only for a runner an installed agent app set
 *  up here — the row says which, before anything is pressed. */
function RunnersSection() {
  const [rows, setRows] = useState<RunnerRow[] | null>(null)
  const [err, setErr] = useState<unknown>(null)

  const load = useCallback(() => {
    api.agentRunners()
      .then((r) => { setRows(r); setErr(null) })
      .catch((e) => setErr(e))
  }, [])

  useEffect(() => { load() }, [load])

  // A Check answers with the one row it measured, and an Allow with the row it allowed; each
  // replaces that row and no other.
  const onRow = useCallback((row: RunnerRow) => {
    setRows((cur) => (cur ?? []).map((r) => (r.id === row.id ? row : r)))
  }, [])

  return (
    <Section title="Runners"
      hint="External agent CLIs this install can drive. Nothing here runs one until you press its Check version, which runs only that CLI's version command — and only for a runner an installed agent app set up.">
      {!rows && err ? <LoadError what="runners" error={err} onRetry={load} />
        : !rows ? <FormSkeleton sections={1} rows={4} title={false} what="runners" />
        : rows.length === 0 ? (
          <p data-type="body-s" className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low">
            No runners in the catalog. Drop a definition into <code className="font-mono">runners/&lt;id&gt;.json</code> under your PersonalClaw home to add one.
          </p>
        ) : (
          <ul className="divide-y divide-outline-variant overflow-hidden rounded-lg bg-surface-container">
            {rows.map((r) => <RunnerRowItem key={r.id} row={r} onRow={onRow} />)}
          </ul>
        )}
    </Section>
  )
}

function Chip({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'ok' | 'bad' }) {
  // The healthy chip takes the SHARED accent-chip skin from design/accent.ts (the
  // container pair) rather than an accent ink over an accent tint, which fails AA in
  // light mode at every tint strength. The unhealthy chip keeps danger INK on the
  // neutral chip ground: the semantic tones have no `<tone>-container` sibling, so
  // inventing a red fill here would be a redesign, not a contrast fix.
  // Metrics + chrome only — the type size rides `data-type="caption"` on each consumer.
  const base = 'inline-flex items-center rounded-pill px-2.5 py-1'
  if (tone === 'ok') return <span data-type="caption" className={base} style={accentChip}>{children}</span>
  const toneCx = tone === 'bad' ? 'text-danger' : 'text-on-surface'
  return <span data-type="caption" className={`${base} bg-surface-high ${toneCx}`}>{children}</span>
}

function RunnerRowItem({ row, onRow }: { row: RunnerRow; onRow: (row: RunnerRow) => void }) {
  const [checking, setChecking] = useState(false)
  const [allowing, setAllowing] = useState(false)
  const h = row.health
  const caps = row.capabilities
  // What a Check runs, said before anyone presses it: the CLI's name and its version flag.
  const command = [row.bin_names[0], ...(row.version_args ?? [])].join(' ')
  const check = async () => {
    setChecking(true)
    try {
      await reportingWrite(`check ${row.display_name}`, async () => onRow(await api.checkAgentRunner(row.id)))
    } finally { setChecking(false) }
  }
  // The gateway asks first, in its own words, with what the definition runs; a declined question
  // writes nothing.
  const allow = async () => {
    setAllowing(true)
    try {
      await reportingWrite(`allow ${row.display_name}`, async () => onRow(await api.allowAgentRunner(row.id, row.allow_revision)))
    } finally { setAllowing(false) }
  }
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span data-type="body-m" className="text-on-surface">{row.display_name}</span>
        <span data-type="caption" className="font-mono text-on-surface-low">{row.runtime_id}</span>
        {row.source === 'user' && <Chip>your definition</Chip>}
        {h === null ? <Chip>never checked</Chip> : h.ok ? <Chip tone="ok">healthy</Chip> : <Chip tone="bad">unhealthy</Chip>}
        {/* An overdue check is not a verdict on the runner — it says the reading you are
            looking at is older than the health-check interval, so "healthy" describes
            then, not now. Only shown for `true`: `null` means we do not know the age. */}
        {row.health_stale === true && <Chip>check overdue</Chip>}
        {/* Who holds this runner. Shown beside the health verdict because it answers
            a different question — a healthy runner someone else is using is not one you can
            start a chat on right now. The backend has already dropped an expired lease, so
            a chip here always names a CURRENT holder. */}
        {row.lease !== null && <Chip>held by {row.lease.holder}</Chip>}
        {/* A definition of yours that came from another machine, or changed since you allowed it:
            nothing runs its CLI, a Check included, until you allow what it runs here. */}
        {row.waiting && <Chip tone="bad">waiting for you</Chip>}
        {row.waiting ? (
          <Button size="xs" variant="secondary" className="ml-auto" loading={allowing} loadingLabel="Allowing…"
            ariaLabel={`Allow: ${row.display_name}`} onClick={allow}>
            Allow
          </Button>
        ) : row.set_up && (
          <Button size="xs" variant="secondary" className="ml-auto" loading={checking} loadingLabel="Checking…"
            ariaLabel={`Check version: ${row.display_name}`} title={`Runs ${command}`} onClick={check}>
            Check version
          </Button>
        )}
      </div>
      {/* What runs, and when — on every row, so a runner's CLI never runs without the row having
          said so first. A runner nothing set up here is never run, not even for its version. */}
      <div data-type="caption" className="mt-1.5 text-on-surface-low">
        {row.waiting
          ? 'Your definition of this runner came from another machine, or changed since you allowed it, so PersonalClaw runs its CLI only after you allow what it runs here.'
          : row.set_up
            ? <>Check version runs <code className="font-mono">{command}</code> and nothing else, only when you press it.</>
            : 'Not set up here — no installed agent app runs this CLI, so PersonalClaw never runs it.'}
      </div>
      {/* The lease detail, only when there is a lease. "for Ns" is the age of the hold and
          "released in Ns" is when idle-release takes it back — together they tell a user
          whether to wait or to go look at that session. */}
      {row.lease !== null && (
        <div data-type="caption" className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-on-surface-low">
          <span>held for {row.lease.age_secs}s</span>
          <span>released in {row.lease.expires_in_secs}s if idle</span>
        </div>
      )}

      {/* Health evidence. `version`/`latency_ms` can be null even on a healthy probe —
          the CLI answered but its output carried no version we could parse — so each is
          labelled unknown rather than shown as a zero or an em dash that reads like a
          measurement. */}
      {h !== null && (
        <div data-type="caption" className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-on-surface-low">
          <span>{h.version ? `v${h.version}` : 'version unknown'}</span>
          <span>{h.latency_ms === null ? 'latency unknown' : `${h.latency_ms} ms`}</span>
          <span>last {h.probe} probe {new Date(h.checked_at).toLocaleString()}</span>
        </div>
      )}
      {/* FieldError, not a hand-rolled danger line: an unhealthy runner is a failure the
          user did not ask for, so it has to announce (ui/fieldErrorAnnounced.test.tsx).
          The extra classes keep the CLI's text verbatim — wrapped, not truncated. */}
      {h !== null && !h.ok && h.error && (
        <FieldError className="mt-1.5 whitespace-pre-wrap break-words font-mono">{h.error}</FieldError>
      )}

      {/* Capability chips come ONLY from a recorded handshake. No handshake yet means
          the matrix is unknown — printing an assumed set would tell you this runner
          supports things nobody has ever seen it do. */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {caps === null ? (
          <span data-type="caption" className="text-on-surface-low">Capabilities unknown — no handshake recorded yet.</span>
        ) : (
          <>
            {caps.permission_modes.map((m) => <Chip key={`m-${m}`}>{m}</Chip>)}
            {caps.efforts.map((e) => <Chip key={`e-${e}`}>effort: {e}</Chip>)}
            {caps.models.length > 0 && <Chip>{caps.models.length} model{caps.models.length === 1 ? '' : 's'}</Chip>}
            {caps.permission_modes.length === 0 && caps.efforts.length === 0 && caps.models.length === 0 && (
              <span data-type="caption" className="text-on-surface-low">Handshake recorded no capabilities.</span>
            )}
          </>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Chip tone={row.adapter.verified ? 'ok' : 'bad'}>adapter {row.adapter.state}</Chip>
        <span data-type="caption" className="text-on-surface-low">{row.adapter.detail}</span>
      </div>
    </li>
  )
}

// ── field renderers ─────────────────────────────────────────────────────────
function useSavedFlash(): [boolean, () => void] {
  const [saved, setSaved] = useState(false)
  return [saved, () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }]
}

/** A free-text config row with an explicit Save.
 *
 *  Explicit rather than save-on-change because the value is a filesystem path: a PATCH per
 *  keystroke would persist a dozen half-typed paths, and the last one saved before a pause would
 *  be a directory that does not exist. Dirty state gates the button, so "did that save?" is
 *  answered on screen instead of by re-opening the panel. */
function TextRow({ label, hint, cfg, field, patch, placeholder }: {
  label: string; hint?: string; cfg: AgentCfg; field: string
  patch: (k: string, v: unknown, cb: () => void, label?: string) => void
  placeholder?: string
}) {
  const stored = String(cfg[field] ?? '')
  // Re-seeded when the loaded config changes under us (a revalidate, or another tab's write) —
  // never on mount, where it could only clobber an edit that raced the first flush
  // (`useSyncedDraft`).
  const [draft, setDraft] = useSyncedDraft(stored)
  const [saved, flash] = useSavedFlash()
  const dirty = draft !== stored
  const save = () => patch(field, draft.trim(), flash, label)
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-s">
        <SavedToast show={saved} />
        <div className="flex-1" style={{ maxWidth: 280 }}>
          <TextInput value={draft} onChange={setDraft} placeholder={placeholder} ariaLabel={label} size="sm"
            onKeyDown={(e) => { if (e.key === 'Enter' && dirty) save() }} />
        </div>
        <Button size="sm" variant={dirty ? 'primary' : 'secondary'} ariaLabel={`Save: ${label}`}
          disabled={!dirty} disabledReason={!dirty ? 'No changes to save' : undefined} onClick={save}>
          Save
        </Button>
      </div>
    </Row>
  )
}

/** Approval mode, saying what the chosen value does for a chat and for an agent no chat started
 *  (`approvalModes.ts`). "Auto" carries the glyph every switch that relaxes a safety default does:
 *  it lets such an agent approve all of its own calls, and a config from before the mode shipped
 *  asking may still hold it without anyone having chosen it. A loosening asks the gateway's own
 *  consent question before it is saved (`api.patchConfig`). */
function ApprovalModeRow({ cfg, patch }: {
  cfg: AgentCfg; patch: (k: string, v: unknown, cb: () => void) => void
}) {
  const [saved, flash] = useSavedFlash()
  const value = String(cfg.approval_mode ?? APPROVAL_MODES[0].key)
  const known = APPROVAL_MODES.some((m) => m.key === value)
  const options = known ? APPROVAL_MODES : [{ key: value, label: value, means: '' }, ...APPROVAL_MODES]
  return (
    <Row label="Approval mode" hint={`${APPROVAL_MODE_HINT} ${approvalModeMeans(value)}`}>
      <div className="flex items-center gap-s">
        <SavedToast show={saved} />
        {value === 'auto' && <AlertTriangle size={14} className="text-warn" role="img" aria-label="Relaxes a safety default" />}
        <SegPills ariaLabel="Approval mode" value={value} onChange={(v) => patch('approval_mode', v, flash)}
          options={options.map(({ key, label }) => ({ key, label }))} />
      </div>
    </Row>
  )
}

function EnumRow({ label, hint, cfg, field, patch, options }: {
  label: string; hint?: string; cfg: AgentCfg; field: string; patch: (k: string, v: unknown, cb: () => void) => void
  options: { key: string; label: string }[]
}) {
  const [saved, flash] = useSavedFlash()
  const value = String(cfg[field] ?? options[0].key)
  // if the stored value isn't among options (legacy), show it but keep options.
  const opts = options.some((o) => o.key === value) ? options : [{ key: value, label: value }, ...options]
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <SegPills ariaLabel={label} value={value} onChange={(v) => patch(field, v, flash)} options={opts} />
      </div>
    </Row>
  )
}

function NumberRow({ label, hint, cfg, field, patch, min, max, step, suffix }: {
  label: string; hint?: string; cfg: AgentCfg; field: string; patch: (k: string, v: unknown, cb: () => void) => void
  min?: number; max?: number; step?: number; suffix?: string
}) {
  const [saved, flash] = useSavedFlash()
  // Every agent.* field is always serialized by the backend (asdict), so cfg[field]
  // is present — the `?? min ?? 0` fallback only guards a transient empty snapshot.
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <NumberField value={Number(cfg[field] ?? min ?? 0)} min={min} max={max} step={step}
          onChange={(n) => patch(field, n, flash)} ariaLabel={label} />
        {suffix && <span data-type="caption" className="w-6 text-on-surface-low">{suffix}</span>}
      </div>
    </Row>
  )
}

