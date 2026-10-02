import { useEffect, useMemo, useRef, useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { Pencil, Trash2, Check, X, Star, Lock, Cpu, ShieldCheck, ChevronDown, VolumeX, RefreshCw, FileText } from 'lucide-react'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { TextArea, FieldError, useSyncedDraft } from '../../ui/forms'
import { FormFooter } from '../../ui/FormFooter'
import { Combobox } from '../../ui/Combobox'
import { Markdown } from '../../ui/Markdown'
import { confirmDelete } from '../../ui/dialog'
import { Skeleton } from '../../ui/ListScaffold'
import { HeldChange, StaleWriteNotice } from '../../ui/StaleWriteNotice'
import { useQuery, invalidateKeys } from '../../lib/data'
import { HELD_CHANGE_REASON, rebaseRecord, rebaseText, sameDocument, type Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { api, type SavedAgent, type DiscoveredAgent, type McpActiveServer, type WaitingAgentHook } from '../../lib/api'
import { useActiveChatModelOptions, canonicalAgentKey } from '../../lib/agents'
import { providerMeta, isReservedAgent, isBuiltinDefaultAgent } from './agentMeta'
import { AgentForm, toDraft, draftToPayload, type AgentDraft } from './AgentForm'
import { ModelUnavailableNote, unavailableModelOption } from './agentModelStatus'
import { accentChip, toneChipSkin } from '../../design/accent'
import { repoDocUrl } from '../../lib/repoDocs'

/** An agent as the editor starts from it: the draft of its profile, with the revision the same read
 *  reported — the base the save names. */
function baseOf(agent: SavedAgent): Revisioned<AgentDraft> {
  return { value: toDraft(agent), revision: agent.revision }
}

/** Native agent inspector: view ↔ in-panel edit (full builder), set-as-default,
 *  delete. */
export function NativeAgentDetail({ agent, isDefault, onSaved, onDeleted, onSetDefault, editing: editingProp, onEditingChange, providerLabel }: {
  agent: SavedAgent; isDefault: boolean; onSaved: () => void; onDeleted: () => void; onSetDefault: () => void; editing: boolean; onEditingChange: (v: boolean) => void
  /** The name of the agent CLI the profile runs on, as the gateway sends it (`RuntimeGroup.label`). */
  providerLabel?: string
}) {
  const reserved = isReservedAgent(agent)
  // Edit mode is owned by the URL (?edit=1), threaded in fully controlled; a
  // reserved built-in is model-only so it can never enter the edit form.
  const editing = editingProp && !reserved
  const setEditing = onEditingChange
  // 🔴 THE PROFILE IS SAVED WHOLE, OVER THE COPY THE DRAFT WAS SEEDED FROM. The editor sends every
  // field it shows, the untouched ones as it read them — so a change made since (the chat's "Always
  // allow for this agent", a skill ticked in another tab) was reverted by the next save here without a
  // word. `base` is that copy with the revision the same read reported, seeded together with the draft
  // and never refreshed under it; a stale save is refused and offered back (`ui/StaleWriteNotice`).
  const [base, setBase] = useState<Revisioned<AgentDraft>>(() => baseOf(agent))
  const [draft, setDraft] = useState<AgentDraft>(() => base.value)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const guard = useStaleWriteGuard<AgentDraft>({
    read: async () => {
      const stored = (await api.agents()).agents.find((a) => a.name === agent.name)
      if (!stored) throw new Error(`the agent “${agent.name}” is no longer configured`)
      return baseOf(stored)
    },
    write: (next, revision) => api.updateAgent(agent.name, draftToPayload(next), revision),
    onSaved: () => { onSaved(); setEditing(false) },
    // Dropping the change leaves the editor on what is stored: the list re-reads, and the next Edit
    // seeds from it.
    onDiscard: () => { onSaved(); setEditing(false); setErr('') },
  })
  // Trigger bindings store the hook's raw id (unlike skills/tools, whose stored
  // values ARE their labels), so the panel resolves ids through the same
  // endpoint the picker built its options from (#629). null = not loaded (no
  // bindings, or the fetch failed) — then Caps renders the raw id and CLAIMS
  // nothing; a loaded map that lacks an id marks a genuinely dangling binding.
  const [triggerNames, setTriggerNames] = useState<Map<string, string> | null>(null)
  useEffect(() => {
    if (!agent.triggers?.length) { setTriggerNames(null); return }
    let alive = true
    api.hooks()
      .then((hs) => { if (alive) setTriggerNames(new Map(hs.map((h) => [h.id, h.event ? `${h.name} · ${h.event}` : h.name]))) })
      .catch(() => { if (alive) setTriggerNames(null) })
    return () => { alive = false }
  }, [agent.triggers])
  // The agent-scoped-SOP list was here (workflows whose scope_ref matched this
  // agent's binding id, via the used-by reverse index). Both the endpoint and the
  // scope_ref model are gone with the old feature (WORKFLOWS-V2 Phase 1); v2
  // definitions are not agent-scoped, so there is nothing equivalent to show.

  // Seeded from the agent as the list shows it when the editor opens (or another agent is picked) —
  // never over an edit, where a revalidation landing mid-edit must not replace what is typed, and
  // never on mount: the state was just seeded from this same agent, so that run could only overwrite
  // an edit that raced the first flush (`ui/forms.useSyncedDraft`).
  const reseed = () => { const b = baseOf(agent); setBase(b); setDraft(b.value) }
  const seededFor = useRef(`${agent.name}:${editing}`)
  useEffect(() => {
    const now = `${agent.name}:${editing}`
    if (seededFor.current === now) return
    seededFor.current = now
    reseed()
  }, [agent.name, editing])
  // A FRESHER COPY REPLACES AN UNTOUCHED ONE. A reload straight into the editor (`?edit=1`) paints the
  // tab's cached list first, and the fresh read that lands a moment later never reached the form — so
  // it showed values older than what was stored, and its first save was refused. Nothing was lost,
  // but a page that is only stale because it has not caught up should catch up.
  useEffect(() => {
    if (agent.revision !== base.revision && guard.conflict === null && sameDocument(draft, base.value)) reseed()
  }, [agent.revision])  // eslint-disable-line

  async function save() {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    setSaving(true); setErr('')
    // A refusal keeps the draft and the editor open, with the notice below offering the way back.
    try { await guard.save(base, draft, rebaseRecord(base.value, draft)) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (isDefault) { setErr('Can’t delete the default agent — set another default first.'); return }
    if (!(await confirmDelete('agent', agent.name))) return
    try { await api.deleteAgent(agent.name); onDeleted() } catch { setErr('Delete failed') }
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-l">
        <HeldChange guard={guard}>
          <AgentForm draft={draft} onChange={setDraft} nameLocked compact
            unavailable={agent.model_unavailable && agent.model ? { model: agent.model, reason: agent.model_unavailable } : undefined} />
        </HeldChange>
        <StaleWriteNotice guard={guard} what={`The agent “${agent.name}”`} />
        <FormFooter error={err}>
          {/* Cancelling a refused save drops the kept change, exactly as "Discard my change" does. */}
          <Button variant="ghost" size="sm" onClick={() => { if (guard.conflict) guard.discard(); else { setEditing(false); setErr('') } }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} loading={saving} disabled={guard.conflict !== null}
            disabledReason={HELD_CHANGE_REASON}><Check size={15} /> Save</Button>
        </FormFooter>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-wrap items-center gap-s">
        {reserved ? (
          <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]"><Lock size={13} /> Reserved built-in — model only</span>
        ) : (
          <>
            <Button size="sm" variant="secondary" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</Button>
            {!isDefault && <Button size="sm" variant="ghost" onClick={onSetDefault}><Star size={14} /> Set default</Button>}
            <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
          </>
        )}
        {isDefault && <span className="ml-auto inline-flex items-center gap-1 text-primary text-[0.75rem]"><Star size={12} fill="currentColor" /> Default</span>}
      </div>
      {err && <FieldError>{err}</FieldError>}

      {reserved && <p className="text-on-surface-low text-[0.8125rem] leading-relaxed">This is a built-in system agent (the background-chore worker, the goal-loop worker, or the goal-planner). Its definition is fixed, but you can swap which model it runs on.</p>}

      {reserved && <ReservedModelEditor agent={agent} onSaved={onSaved} />}

      <div className="flex flex-wrap items-center gap-s text-[0.8125rem]">
        {/* 🔴 THIS CHIP WAS THE LITERAL STRING 'Native' for every non-reserved agent, while
            `providerMeta` sat imported and unused by this component — so a profile stored as
            `acp:<cli>` was labelled Native, and the one surface that could have shown the mismatch
            was the surface hiding it. That was the missing cue for the provider clobber the sibling
            `AgentForm.draftToPayload` used to perform; now that the edit PRESERVES the provider, a
            hardcoded label would be a permanent false statement rather than a transient one.
            🪤 `providerMeta('')` still answers 'Native', which is today's string for every agent that
            never set a provider — so this cannot read WORSE than before for anybody. It is also not
            strictly true: empty means *inherit the global* `agent.provider`, which the frontend has no
            way to resolve here. Naming that honestly needs the global on the wire; out of scope. */}
        <span className="inline-flex items-center gap-1 rounded-pill px-m h-7" style={accentChip}>{reserved && <ShieldCheck size={12} />}{reserved ? 'Built-in' : providerMeta(agent.provider, providerLabel).label}</span>
        {!reserved && agent.model && (
          <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center gap-1 font-mono text-on-surface-var text-[0.75rem]">
            {agent.model}
            {agent.model_unavailable && <span className="font-sans text-[0.6875rem]" style={{ color: 'var(--color-warning)' }}>· unavailable</span>}
          </span>
        )}
        {agent.approval_mode && <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center text-on-surface-var">{agent.approval_mode}</span>}
      </div>

      {agent.model_unavailable && agent.model && (
        <ModelUnavailableNote model={agent.model} unavailable={agent.model_unavailable} fixHere={reserved ? 'above' : 'with Edit'} />
      )}

      {/* The CLI hooks merge into THIS agent's config (`agent.py`), and one waiting for the owner's
          yes is a decision, so it is shown here, not behind Advanced. */}
      {isBuiltinDefaultAgent(agent) && <AgentHooksWaiting />}

      {agent.description && <p className="text-on-surface text-[0.9375rem] leading-relaxed">{agent.description}</p>}

      {agent.system_prompt ? (
        <Section label="System prompt"><div tabIndex={0} role="group" aria-label="System prompt" className="rounded-md bg-surface-container px-m py-2 max-h-72 overflow-y-auto text-on-surface-var text-[0.8125rem] leading-relaxed"><Markdown>{agent.system_prompt}</Markdown></div></Section>
      ) : isBuiltinDefaultAgent(agent) && (
        // The default agent's prompt is NOT empty in effect — it is the bound one. Hiding the
        // section made it look like this agent ran with no instructions at all.
        <Section label="System prompt">
          <p className="text-on-surface-var text-[0.8125rem] leading-relaxed">
            None of its own — it answers with the prompt bound in{' '}
            <TextLink href="#/settings/prompts" size="sm">Settings → Prompts</TextLink>
            {' '}(Chat, or Background for unattended runs), using the names in Settings → Account. A prompt written here replaces that binding for this agent.
          </p>
        </Section>
      )}

      <Caps label="Skills" items={agent.skills} />
      <Caps label="Tools" items={agent.tools} />
      <Caps label="Triggers" items={agent.triggers} resolve={triggerNames} />

      {!reserved && <AgentAdvanced agentName={agent.name} />}
    </div>
  )
}

/** Advanced per-agent config, collapsed by default: routing notes (editable — the
 *  "when to use this agent" hint included in the orchestrator's generated delegation
 *  roster), the MCP servers this agent gets (read-only), and the lifecycle hooks in
 *  effect (read-only). Each block loads its data lazily only when the section is expanded. */
function AgentAdvanced({ agentName }: { agentName: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-t border-outline-variant/40 pt-l">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex items-center gap-1.5 text-on-surface-var text-[0.8125rem] hover:text-on-surface">
        <ChevronDown size={15} className={`transition-transform ${open ? 'rotate-180' : ''}`} /> Advanced
      </button>
      {open && (
        <div className="mt-m flex flex-col gap-l">
          <RoutingNotesEditor agentName={agentName} />
          <RoutingStatusView agentName={agentName} />
          <AgentMcpView agentName={agentName} />
          <AgentHooksView />
        </div>
      )}
    </div>
  )
}

/** "When to use this agent" routing notes — populate the orchestrator's generated delegation roster. */
function RoutingNotesEditor({ agentName }: { agentName: string }) {
  // The note as read, with the revision the same read reported: the editor saves the WHOLE note over
  // it, so a note changed since — in another tab, or seeded by the orchestrator from the agent's
  // description — is refused instead of overwritten (`lib/staleWrite.ts`).
  const [stored, setStored] = useState<Revisioned<string> | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState('')
  const [loadErr, setLoadErr] = useState('')
  const [reloads, setReloads] = useState(0)
  // 🔴 A FAILED READ MUST NOT LOOK LIKE AN EMPTY NOTE, BECAUSE THIS EDITOR OVERWRITES THE FILE.
  // This used to `.catch(() => { setContent(''); setDraft('') })`, which conflates "the GET
  // failed" with "there is no note yet": the field rendered blank with no error, so a user whose
  // read had failed typed into what looked like an empty note and Save PUT it over the real one.
  // `dirty` is `content !== null && draft !== content`, so the empty-string content is precisely
  // what armed the button — leaving `content` null makes `dirty` unreachable and the overwrite
  // impossible until a read has actually succeeded. This is the same defect the save path below
  // already fixed one state earlier: two situations told apart only by a missing signal.
  useEffect(() => {
    let alive = true
    setStored(null)
    setLoadErr('')
    api.agentMetadata(agentName)
      .then((note) => { if (alive) { setStored(note); setDraft(note.value) } })
      .catch((e) => { if (alive) setLoadErr(e instanceof Error ? e.message : 'Could not load the routing note') })
    return () => { alive = false }
  }, [agentName, reloads])
  // What the write answered — the note as stored after it, and its new revision — which is what the
  // next save is based on. Taken from the write, never from a later read: a later read can carry
  // another tab's save, and a draft measured against THAT would overwrite it unrefused.
  const written = useRef<Revisioned<string> | null>(null)
  const flash = () => { setSaved(true); setTimeout(() => setSaved(false), 1800) }
  const guard = useStaleWriteGuard<string>({
    read: () => api.agentMetadata(agentName),
    write: async (next, revision) => {
      const res = await api.saveAgentMetadata(agentName, next, revision)
      written.current = { value: res.content, revision: res.revision }
    },
    onSaved: () => {
      const note = written.current
      written.current = null
      // No write result: the re-read already held this exact note, so nothing was sent — read it again.
      if (note) { setStored(note); setDraft(note.value) } else setReloads((n) => n + 1)
      flash()
    },
    onDiscard: () => setReloads((n) => n + 1),
  })
  const content = stored ? stored.value : null
  const dirty = content !== null && draft !== content
  const save = async () => {
    if (!stored) return
    setBusy(true)
    setErr('')
    // Keeping the draft on failure is right; being silent about it was not. `Saved ✓` appears only on
    // success, so without this a refused save was indistinguishable from a click that never landed.
    try { await guard.save(stored, draft, rebaseText(stored.value, draft)) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') }
    setBusy(false)
  }
  return (
    <Section label="Routing notes">
      <p className="mb-1.5 text-on-surface-low text-[0.75rem]">Used by the orchestrator's generated delegation roster. Automatic suggestions use Specialty and Routing hints instead.</p>
      {loadErr ? (
        <div className="flex flex-col items-start gap-2">
          <p role="alert" data-type="caption" className="text-danger">Couldn’t load this note, so it isn’t safe to edit — saving now could overwrite what’s on disk. {loadErr}</p>
          <Button size="sm" onClick={() => setReloads((n) => n + 1)}><RefreshCw size={14} /> Try again</Button>
        </div>
      ) : content === null ? <Skeleton className="h-16 w-full rounded-md" /> : (
        <div className="flex flex-col gap-2">
          <HeldChange guard={guard}>
            <TextArea value={draft} onChange={setDraft} rows={3} size="sm" ariaLabel="Routing notes"
              placeholder="e.g. Use for deep code reviews and multi-file refactors; prefers a thorough, direct style." />
          </HeldChange>
          <StaleWriteNotice guard={guard} what="This routing note" />
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={save} loading={busy} loadingLabel="Saving…" disabled={!dirty || busy || guard.conflict !== null}
              disabledReason={guard.conflict !== null ? HELD_CHANGE_REASON : !dirty && !busy ? 'No changes to save' : undefined}><Check size={14} /> Save notes</Button>
            {saved && <span className="text-ok text-[0.75rem]">Saved ✓</span>}
            {err && <span role="alert" className="text-danger text-[0.75rem]">{err}</span>}
          </div>
        </div>
      )}
    </Section>
  )
}

/** Routing status: whether the auto-router has this agent MUTED (the user dismissed its
 *  suggestion chip enough times that it stopped being suggested), with an Unmute control.
 *  A muted agent is otherwise invisible on this page — the mute is a routing preference the
 *  user set implicitly, so this is where they see it while looking at the agent itself.
 *  It is not the ONLY place any more, and must not become one again: this view can only ask
 *  "is THIS agent muted", and the store legitimately holds keys with no agent page to visit
 *  (a name that was never an agent, an agent deleted while muted, a reserved built-in whose
 *  panel renders no Advanced section at all). Settings › Chat › Agent routing › Muted agents
 *  lists the store's own keys and reaches those; the two read one declared cache key. */
export function RoutingStatusView({ agentName }: { agentName: string }) {
  // 🪤 ONE DECLARED KEY, SHARED WITH SETTINGS › CHAT › AGENT ROUTING, and `canonicalAgentKey` is
  // not cosmetic. The suppression store's agent identity is CASE-INSENSITIVE (`agents/routing.py`
  // `canonical_agent`), so `routing_status()` returns canonical keys — and this view used to test
  // `s.muted.includes(agentName)` with the RAW route name. Three
  // dismissals of the shipped default agent `PersonalClaw` produced `muted: ["personalclaw"]` and
  // `is_suppressed("PersonalClaw") == True`, while this panel rendered "Active — eligible for
  // auto-routing suggestions" and no Unmute button. A wrong claim plus no undo, from one `includes`.
  const { data: status, refresh } = useQuery('agents:routing-mutes', () => api.routingStatus())
  const [muted, setMuted] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => {
    if (status === undefined) return
    setMuted((status.muted || []).some((m) => canonicalAgentKey(m) === canonicalAgentKey(agentName)))
  }, [status, agentName])
  const unmute = async () => {
    setBusy(true)
    setErr('')
    // Staying muted is right — `setMuted(false)` must never claim a state the backend refused. But
    // "the row stays so the user can retry" was the whole failure path, and the row staying is also
    // exactly what a click that never landed looks like. Retryable is not the same as legible.
    try { await api.routingUnmute(agentName); setMuted(false); invalidateKeys('agents:routing-mutes'); refresh() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Unmute failed') }
    setBusy(false)
  }
  if (muted === null) return <Section label="Routing status"><Skeleton className="h-6 w-40 rounded-md" /></Section>
  return (
    <Section label="Routing status">
      {muted ? (
        <div className="flex items-center gap-2 text-[0.8125rem]">
          <span className="inline-flex items-center gap-1.5 text-on-surface-var">
            <VolumeX size={14} /> Muted — the auto-router stopped suggesting this agent.
          </span>
          <Button size="sm" onClick={unmute} loading={busy}>Unmute</Button>
          {err && <span role="alert" className="text-danger text-[0.75rem]">{err}</span>}
        </div>
      ) : (
        <p className="text-on-surface-low text-[0.75rem]">Active — eligible for auto-routing suggestions.</p>
      )}
    </Section>
  )
}

/** Read-only view of the MCP servers this agent is scoped to. */
function AgentMcpView({ agentName }: { agentName: string }) {
  const { data: servers } = useQuery<McpActiveServer[]>(`agent:mcp:${agentName}`, () => api.mcpActive(agentName).catch(() => [] as McpActiveServer[]), { persist: false })
  if (servers === undefined) return <Section label="MCP servers"><Skeleton className="h-6 w-40 rounded-pill" /></Section>
  return (
    <Section label={`MCP servers · ${servers.length}`}>
      {servers.length === 0 ? <p className="text-on-surface-low text-[0.8125rem] italic">No MCP servers scoped to this agent.</p> : (
        <div className="flex flex-wrap gap-1.5">
          {servers.map((s) => (
            <span key={s.name} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-6 text-[0.75rem]" style={{ color: s.enabled ? 'var(--color-on-surface-var)' : 'var(--color-on-surface-low)' }}>
              <span className="size-1.5 rounded-pill" style={{ background: s.enabled ? 'var(--color-ok)' : 'var(--color-outline)' }} />{s.name}
            </span>
          ))}
        </div>
      )}
    </Section>
  )
}

/** Scripts the agent CLI's own hooks would run that it leaves out until the owner allows them
 *  (`waiting`, `agent.user_hooks_waiting`): a new script in the hooks folder or `agent_hooks`, or
 *  one whose file changed since the owner's yes (`agent_hook_grants.py`). Each has Allow, which the
 *  gateway asks about first and which names the file as this page read it (`seal`). Renders nothing
 *  when nothing waits. */
function AgentHooksWaiting() {
  const { data, error, refresh } = useQuery('agent:hooks', () => api.agentHooks(), { persist: false })
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  if (error) return <FieldError>Could not read the agent's hooks.</FieldError>
  if (!data || data.waiting.length === 0) return null
  async function allow(hook: WaitingAgentHook) {
    setBusy(`${hook.event} ${hook.command} ${hook.matcher}`); setErr('')
    try { await api.allowAgentHook(hook); invalidateKeys('agent:hooks'); refresh() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not allow this hook') }
    finally { setBusy('') }
  }
  return (
    <div role="note" className="flex flex-col gap-s rounded-md bg-warn/10 px-m py-s">
      <p data-type="body-s" className="text-warn">
        Not allowed to run yet: your agent's CLI leaves {data.waiting.length === 1 ? 'this hook' : 'these hooks'} out
        until you allow {data.waiting.length === 1 ? 'it' : 'them'}. Allowing one asks you first, and a change to its
        file does not run until you allow it again.
      </p>
      {data.waiting.map((w) => {
        const id = `${w.event} ${w.command} ${w.matcher}`
        return (
          <div key={id} className="flex items-center gap-s rounded-md bg-surface-container px-2.5 py-1.5">
            <div data-type="caption" className="min-w-0 flex-1 font-mono text-on-surface-low overflow-x-auto" style={{ fontFamily: '"JetBrains Mono", ui-monospace, monospace' }}>
              <span className="text-on-surface-var">{w.event}</span>{w.matcher && <span className="text-primary"> [{w.matcher}]</span>} {w.command}
            </div>
            <Button size="sm" variant="secondary" onClick={() => allow(w)} loading={busy === id} disabled={busy !== ''}
              ariaLabel={`Allow the ${w.event} hook: ${w.command}`}>Allow</Button>
          </div>
        )
      })}
      {err && <FieldError>{err}</FieldError>}
    </div>
  )
}

/** The agent CLI's lifecycle hooks in effect (redacted commands), grouped by event. Read-only:
 *  one that waits for the owner's yes is `AgentHooksWaiting`'s. */
function AgentHooksView() {
  const { data, error } = useQuery('agent:hooks', () => api.agentHooks(), { persist: false })
  if (data === undefined && !error) return <Section label="Lifecycle hooks"><Skeleton className="h-6 w-40 rounded-md" /></Section>
  if (data === undefined) return <Section label="Lifecycle hooks"><FieldError>Could not read the agent's hooks.</FieldError></Section>
  const events = Object.entries(data.hooks).filter(([, hs]) => hs.length > 0)
  return (
    <Section label="Lifecycle hooks">
      {events.length === 0 ? <p className="text-on-surface-low text-[0.8125rem] italic">No lifecycle hooks configured.</p> : (
        <div className="flex flex-col gap-2">
          {events.map(([event, hs]) => (
            <div key={event}>
              <div className="mb-0.5 text-on-surface-var text-[0.75rem]" style={fvs(600)}>{event}</div>
              {hs.map((h, i) => (
                <div key={i} className="rounded-md bg-surface-container px-2.5 py-1.5 font-mono text-[0.75rem] text-on-surface-low overflow-x-auto" style={{ fontFamily: '"JetBrains Mono", ui-monospace, monospace' }}>
                  {h.matcher && <span className="text-primary">[{h.matcher}] </span>}{h.command}{h.source && <span className="text-on-surface-low/60"> · {h.source}</span>}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </Section>
  )
}

/** Model-only editor for a reserved built-in agent: swap its model (constrained
 *  to active chat models + Auto) without touching its locked persona/tools. */
function ReservedModelEditor({ agent, onSaved }: { agent: SavedAgent; onSaved: () => void }) {
  // `catalogErr` is bound so an unreachable active-model list cannot render as "No active chat
  // models" — on a box with three bound models that sentence is a false claim about a setting, and
  // it is the one fact this editor exists to show.
  const { options, loading, error: catalogErr } = useActiveChatModelOptions()
  // Re-seeded when the editor is showing another agent, never on mount (`useSyncedDraft`).
  const [model, setModel] = useSyncedDraft(agent.model ?? '', agent.name)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  // A saved pin the list does not offer is listed as unavailable, never shown as "Auto".
  const opts = useMemo(
    () => [{ value: '', label: 'Auto — use chat binding' }, ...options, ...(loading || catalogErr ? [] : unavailableModelOption(agent.model ?? '', options))],
    [options, loading, catalogErr, agent.model],
  )
  const dirty = model !== (agent.model ?? '')
  const save = async () => {
    setSaving(true); setErr('')
    // The one field this editor owns, alone: no copy of the rest of the profile rides along to go
    // stale, so it names no base (`api.setAgentModel`).
    try { await api.setAgentModel(agent.name, model); onSaved() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Model</div>
      <div className="flex items-center gap-s">
        <div className="min-w-0 flex-1"><Combobox options={opts} value={model} onChange={setModel} placeholder="Auto — use chat binding" emptyText="No active chat models" /></div>
        {dirty && <Button size="sm" onClick={save} loading={saving}><Check size={14} /> Save</Button>}
      </div>
      {err && <FieldError>{err}</FieldError>}
      {catalogErr ? <FieldError>Couldn't load your active chat models — {(catalogErr as Error)?.message || 'the server did not respond'}. The list above is incomplete; reload before changing it.</FieldError> : null}
    </div>
  )
}

/** Read-only inspector for an ACP-runtime-discovered agent. *label* is the runtime's name as the
 *  gateway sends it (`RuntimeGroup.label`). */
export function DiscoveredAgentDetail({ agent, providerId, label }: { agent: DiscoveredAgent; providerId: string; label?: string }) {
  const pm = providerMeta(providerId, label)
  return (
    <div className="flex flex-col gap-l">
      <div className="inline-flex items-center gap-1.5 self-start rounded-pill px-m h-7 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-on-surface-low) 14%, transparent)', color: 'var(--color-on-surface-var)' }}>
        <Lock size={13} /> {pm.label} — read-only
      </div>
      <p className="text-on-surface-low text-[0.8125rem]">This agent is defined and run by the {pm.label} runtime. It can't be edited here, but you can use it from the chat agent picker.</p>

      <Section label="Capability parity">
        <TextLink href={repoDocUrl('docs/agents/acp-parity.md')} external size="sm" icon={FileText}>
          What’s at parity, host-compensated, or constrained for {pm.label}
        </TextLink>
      </Section>

      <div className="flex flex-wrap items-center gap-s text-[0.8125rem]">
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={toneChipSkin(pm.tone, 16)}><pm.icon size={13} /> {pm.label}</span>
        {agent.reasoning_effort && <span className="rounded-pill bg-surface-high px-m h-7 inline-flex items-center text-on-surface-var">{agent.reasoning_effort} effort</span>}
      </div>

      {agent.description && <p className="text-on-surface text-[0.9375rem] leading-relaxed">{agent.description}</p>}

      {agent.provider_agent && <Section label="Runtime agent id"><span className="font-mono text-on-surface-var text-[0.8125rem]">{agent.provider_agent}</span></Section>}
      {(agent.models?.length ?? 0) > 0 && (
        <Section label="Models">
          <div className="flex flex-wrap gap-1.5">{agent.models.map((m) => <span key={m} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 h-6 font-mono text-on-surface-var text-[0.75rem]"><Cpu size={11} /> {m}</span>)}</div>
        </Section>
      )}
    </div>
  )
}

function Caps({ label, items, resolve }: { label: string; items?: string[]; resolve?: Map<string, string> | null }) {
  if (!items || items.length === 0) return null
  return (
    <Section label={`${label} · ${items.length}`}>
      <div className="flex flex-wrap gap-1.5">{items.map((i) => {
        const name = resolve?.get(i)
        // Only a LOADED map may call an id dangling — a failed/absent fetch
        // renders the raw value and claims nothing (it cannot know).
        const dangling = resolve != null && !name
        return (
          <span key={i} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center gap-1 text-on-surface-var text-[0.75rem]">
            {name ?? i}
            {dangling && <span className="text-[0.6875rem]" style={{ color: 'var(--color-warning)' }}>· trigger no longer exists</span>}
          </span>
        )
      })}</div>
    </Section>
  )
}
function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
