import { useEffect, useState } from 'react'
import { api, type DashboardConfig, type SessionTemplate } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useAgentCatalog, ensureBindableAgentName } from '../../lib/agents'
import { useQuery, invalidateKeys } from '../../lib/data'
import { reportingWrite } from '../../app/reportingWrite'
import { PanelHeader, Section, RowGroup, Row, Field, Toggle, SegPills, SavedToast, ToggleRow,
  // Aliased: this file already declares a LOCAL `NumberRow` on the other settings-row
  // contract (`{value, onCommit}`, flash owned by the panel). settingsUI's is the
  // `cfg`/`field`/`patch` one, which is what a plain config key wants. Both shapes are
  // shipped and the choice between them is an open question the family records; this
  // section takes the by-key one because that is all a `rooms.*` knob needs.
  NumberRow as ConfigNumberRow } from './settingsUI'
import { Combobox } from '../../ui/Combobox'
import { NumberField } from '../../ui/forms'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { IconButton } from '../../ui/IconButton'
import { confirmDelete } from '../../ui/dialog'
import { Trash2, VolumeX } from 'lucide-react'
import { FormSkeleton, InlineLoadError, LoadError } from '../../ui/ListScaffold'

const RESTORE_WINDOWS = [
  { key: '15', label: '15 min' }, { key: '30', label: '30 min' },
  { key: '60', label: '1 hour' }, { key: '240', label: '4 hours' }, { key: '0', label: 'All' },
]

/** Chat settings — session restore + message display (server-stored so behavior
 *  is identical across browsers) + context lifecycle (auto-compact, idle timeout,
 *  warm pool). Dashboard prefs persist via /api/dashboard/config; the session.*
 *  lifecycle knobs via the config PATCH allowlist. */
export function ChatPanel() {
  const [cfg, setCfg] = useState<DashboardConfig | null>(null)
  const [session, setSession] = useState<Record<string, unknown> | null>(null)
  const [routing, setRouting] = useState<Record<string, unknown> | null>(null)
  const [resilience, setResilience] = useState<Record<string, unknown> | null>(null)
  const [checkpoints, setCheckpoints] = useState<Record<string, unknown> | null>(null)
  const [tools, setTools] = useState<Record<string, unknown> | null>(null)
  const [rooms, setRooms] = useState<Record<string, unknown> | null>(null)
  const { options: agentOptions, discovered } = useAgentCatalog()

  // Stale-while-revalidate + persist: paint instantly on revisit/reload from a
  // single cached snapshot of both fetches, revalidating in the background. The
  // editable form state below is seeded/rehydrated from this read-only `data`.
  const { data, error: loadErr, refresh } = useQuery('settings:chat', async () => {
    const [dash, plaw] = await Promise.all([
      // 🔴 NO FALLBACK ON THIS ONE EITHER. It kept `.catch(() => null)` on the belief that it only
      // fed the starter list — but it is `cfg`, the Sessions and Messages sections' whole state, and
      // the gate below waits for `cfg`. So a failed read resolved the query with `cfg: null`, the
      // LoadError branch never fired (`data` was defined), and the panel spun its skeleton forever.
      // The starter list has its own read and its own failure (`StartersSection`).
      api.dashboardConfig(),
      // 🔴 NOT `.catch(() => ({}))`. This read IS the panel: session, routing and resilience all come
      // from it, so an empty object rendered every control at its fallback — indistinguishable from
      // "this is what you saved". Measured on `#/settings/chat` with `/api/config/personalclaw` at 500:
      // **10 switches and 6 inputs rendered, with no error anywhere**, and each one PATCHes on change.
      // Its sibling `AgentDefaultsPanel` already reads the same endpoint and shows the failure.
      api.personalclawConfig(),
    ])
    return {
      cfg: dash,
      session: (plaw.session ?? {}) as Record<string, unknown>,
      routing: (plaw.agents_routing ?? {}) as Record<string, unknown>,
      resilience: (plaw.resilience ?? {}) as Record<string, unknown>,
      checkpoints: (plaw.checkpoints ?? {}) as Record<string, unknown>,
      tools: (plaw.tools ?? {}) as Record<string, unknown>,
      rooms: (plaw.rooms ?? {}) as Record<string, unknown>,
    }
  }, { persist: true })

  useEffect(() => {
    if (data) {
      setCfg(data.cfg); setSession(data.session); setRouting(data.routing)
      setResilience(data.resilience); setCheckpoints(data.checkpoints); setTools(data.tools)
      setRooms(data.rooms)
    }
  }, [data])

  // Error BEFORE the skeleton, or it is unreachable: `data` is undefined for the loading, failed AND
  // empty cases. Same one-line shape `AgentDefaultsPanel` ships for the same endpoint.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg || !session || !routing || !resilience || !checkpoints || !tools || !rooms) return <FormSkeleton sections={3} what="settings" />

  return (
    <div>
      <PanelHeader title="Chat" hint="How sessions restore, how messages display, and how long context lives. These follow you across browsers." />

      <SessionsSection cfg={cfg} setCfg={setCfg} />
      <MessagesSection cfg={cfg} setCfg={setCfg} />
      <MidTurnSection resilience={resilience} setResilience={setResilience} />
      <RoutingSection routing={routing} setRouting={setRouting} />
      <LifecycleSection session={session} setSession={setSession} agentOptions={agentOptions} discovered={discovered} />
      <BackgroundCompressionSection tools={tools} setTools={setTools} />
      <CheckpointsSection checkpoints={checkpoints} setCheckpoints={setCheckpoints} />
      <RoomsSection rooms={rooms} setRooms={setRooms} />
      <StartersSection />
    </div>
  )
}

/** Saved chat starters (SESSION-MANAGEMENT S3 T3.2) — the management surface.
 *
 *  Starters are CREATED from a chat's header ("Save as starter"), because that's where
 *  the setup being saved actually exists. This section is where they're reviewed and
 *  removed: without it a starter would be creatable and never deletable, which is how
 *  a picker fills up with stale entries nobody can clear. */
function StartersSection() {
  const [items, setItems] = useState<SessionTemplate[] | null>(null)
  // 🔴 A failed read used to set `[]`, and the section said "No starters yet" — with instructions
  // for making one — to a user whose starters simply could not be read. Said, with a Retry.
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let live = true
    setLoadErr(null)
    api.sessionTemplates()
      .then((t) => { if (live) setItems(t) })
      .catch((e) => { if (live) setLoadErr(e) })
    return () => { live = false }
  }, [attempt])

  async function remove(t: SessionTemplate) {
    if (!(await confirmDelete('starter', t.name))) return
    try {
      await api.deleteSessionTemplate(t.id)
    } catch (e) {
      notify(`Couldn't delete this starter: ${String((e as Error)?.message || e)}`, 'error')
      return
    }
    setItems((prev) => (prev ?? []).filter((x) => x.id !== t.id))
    // The chat page caches the starter list for instant paint; drop it so the picker
    // doesn't keep offering something that no longer exists.
    invalidateKeys('chat:starters')
  }

  return (
    <Section title="Chat starters" hint="Reusable setups — agent, model and reasoning effort. Save one from a chat's header; they appear on the new-chat screen.">
      <RowGroup>
        {items === null && loadErr ? (
          <div className="py-m"><InlineLoadError what="chat starters" error={loadErr} onRetry={() => setAttempt((n) => n + 1)} /></div>
        ) : items === null ? (
          <p data-type="body-s" className="py-m text-on-surface-low">Loading…</p>
        ) : items.length === 0 ? (
          <p data-type="body-s" className="py-m text-on-surface-low">
            No starters yet. Open a chat, set it up how you like, then use “Save as starter” in its header.
          </p>
        ) : items.map((t) => (
          <Row key={t.id} label={t.name} hint={[t.agent, t.model, t.reasoning_effort].filter(Boolean).join(' · ') || 'Uses your defaults'}>
            <IconButton icon={Trash2} label={`Delete ${t.name}`} iconSize={16} size={32} tone="danger" onClick={() => remove(t)} />
          </Row>
        ))}
      </RowGroup>
    </Section>
  )
}

const MID_TURN_POLICIES = [
  { key: 'queue', label: 'Queue' },
  { key: 'steer', label: 'Steer' },
  { key: 'cancel_and_replace', label: 'Replace' },
] as const

// ── Mid-turn messages (personalclaw config: resilience.mid_turn_policy) ──────
// The field shipped with PLATFORM-RESILIENCE S3 but had no frontend control, so
// the config round-trip contract's fifth point was unmet — file-editable only.
function MidTurnSection({ resilience, setResilience }: {
  resilience: Record<string, unknown>; setResilience: (r: Record<string, unknown>) => void
}) {
  const [saved, flash] = useSavedFlash()
  const policy = String(resilience.mid_turn_policy ?? 'queue')
  const patch = (value: string) => {
    const prev = resilience.mid_turn_policy
    setResilience({ ...resilience, mid_turn_policy: value })
    api.patchConfig('resilience.mid_turn_policy', value).then(flash).catch((e) => {
      setResilience({ ...resilience, mid_turn_policy: prev })
      notify(`Couldn't save mid-turn policy: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  return (
    <Section title="Mid-turn messages" hint="What happens when you send something while an answer is still being written.">
      <RowGroup>
        <Row label="Default handling"
          hint="Queue: deliver it as the next turn. Steer: fold it into the answer being written, where the running agent supports that — otherwise it queues. Replace: stop the current answer and start over with the new message. Unattended work (loops, cron, subagents) always queues.">
          <div className="flex items-center gap-s">
            <SavedToast show={saved} />
            <SegPills ariaLabel="Default handling" value={policy} onChange={patch} options={[...MID_TURN_POLICIES]} />
          </div>
        </Row>
        {policy === 'steer' && (
          <p data-type="caption" className="pb-m text-on-surface-low">
            Steering reaches the running answer on the built-in agent. Connected CLI
            agents (ACP) don't expose a mid-turn seam yet, so a message there queues
            instead — either way it appears above the composer, never dropped.
          </p>
        )}
      </RowGroup>
    </Section>
  )
}

// ── Agent Rooms (personalclaw config: rooms.*) ───────────────────────────────
/** The three `rooms.*` knobs, and the switch that makes the feature exist at all.
 *
 *  🔑 THIS SECTION CLOSES AN INERT SURFACE. All three keys completed their config round-trip
 *  when the room store shipped — dataclass, `_meta`, `load()`, `to_dict()` and the
 *  `_EDITABLE_CONFIG` PATCH allowlist — and had NO control anywhere in `web/src`. So the
 *  feature was off by default and there was no way to turn it on short of hand-editing
 *  `config.json`, which is the shape of a shipped-but-unreachable setting.
 *
 *  It lives in Chat settings rather than its own panel because a room IS a chat surface: it is
 *  reached from the chat page's Rooms scope, and a separate settings route would put the switch
 *  somewhere a user looking for "how do I get my agents to talk to each other" would not look.
 *
 *  `round_budget` here is the INSTALL-wide default. A room may override it for itself, and that
 *  control lives on the room (its Members panel) — two controls because they are two different
 *  facts, and the room's own copy says which one it is using.
 */
function RoomsSection({ rooms, setRooms }: {
  rooms: Record<string, unknown>; setRooms: (r: Record<string, unknown>) => void
}) {
  const on = Boolean(rooms.enabled)
  const patch = (key: string, value: unknown, done?: () => void, label?: string) => {
    const prev = rooms[key]
    setRooms({ ...rooms, [key]: value })
    api.patchConfig(`rooms.${key}`, value).then(() => done?.()).catch((e) => {
      setRooms({ ...rooms, [key]: prev })
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  return (
    <Section title="Agent Rooms" hint="A standing conversation where several of your agents deliberate with you refereeing — each with its own role, its own provider session and its own tool reach.">
      <RowGroup>
        <ToggleRow label="Agent Rooms" cfg={rooms} field="enabled" patch={patch}
          hint="Off by default. While off, no room can be opened or started — including one you already made, which stays on disk untouched." />
        {/* The two numbers only mean anything once the feature is on, so they appear with it
            rather than sitting greyed out. A disabled stepper for a feature you have not enabled
            is a control that owes an explanation nobody reads. */}
        {on && (
          <>
            <ConfigNumberRow label="Round budget" cfg={rooms} field="round_budget" min={1} max={100} patch={patch}
              hint="How many turns the members may take among themselves before a room pauses and asks you. Your reply resets it and lets any parked turns run. A room can override this for itself." />
            <ConfigNumberRow label="Members per room" cfg={rooms} field="max_members" min={1} max={32} patch={patch}
              hint="The ceiling on how many agents one room may hold. Each member holds its own provider session, so this is also a ceiling on how much one message can cost." />
          </>
        )}
      </RowGroup>
    </Section>
  )
}

// ── Agent routing (personalclaw config: agents_routing.*) ────────────────────
function RoutingSection({ routing, setRouting }: { routing: Record<string, unknown>; setRouting: (r: Record<string, unknown>) => void }) {
  const [saved, flash] = useSavedFlash()
  const patch = (key: string, value: unknown, _cb?: () => void, label?: string) => {
    const prev = routing[key]
    setRouting({ ...routing, [key]: value })
    api.patchConfig(`agents_routing.${key}`, value).then(flash).catch(() => {
      setRouting({ ...routing, [key]: prev })
      notify(`Couldn't save ${label ?? key}`, 'error')
    })
  }
  const enabled = routing.enabled !== false
  return (
    <Section title="Agent routing" hint="Suggest a better-fit specialist agent when a message matches one — you always confirm before it re-targets the chat.">
      <RowGroup>
        <Row label="Suggest specialists" hint="When a message in a default-agent chat fits an installed specialist, show a one-click 'route to <agent>?' chip. Never routes silently.">
          <div className="flex items-center gap-s"><SavedToast show={saved} /><Toggle on={enabled} onChange={(v) => patch('enabled', v)} label="Suggest specialists" /></div>
        </Row>
        {enabled && (
          <NumberRow label="Confidence threshold" hint="Minimum match confidence before a routing chip appears. Higher = fewer, surer suggestions." value={Number(routing.min_confidence ?? 0.62)} min={0.3} max={0.95} step={0.01} onCommit={(n, l) => patch('min_confidence', n, undefined, l)} saved={saved} />
        )}
        {enabled && (
          <NumberRow label="Dismiss cooldown" hint="After you dismiss a suggestion for an agent, suppress it for this long. A third dismissal mutes the agent for good — Muted agents below is where you undo that; this field can't, and neither can the switch above." value={Number(routing.cooldown_hours ?? 24)} min={0} max={720} step={1} suffix="h" onCommit={(n, l) => patch('cooldown_hours', n, undefined, l)} saved={saved} />
        )}
        {/* NOT gated on `enabled`, deliberately: a mute outlives the master switch. Measured on a
            live gateway — with three dismissals recorded, PATCHing agents_routing.enabled false then
            true left muted unchanged, and so did dragging cooldown_hours to 0, because is_suppressed
            returns on the mute BEFORE it reads the cooldown. Hiding the only working undo behind the
            switch would recreate the trap this row exists to close (issue 414). */}
        <MutedAgentsField />
      </RowGroup>
    </Section>
  )
}

/** Every agent the auto-router has stopped suggesting, with the undo.
 *
 *  🔑 THIS IS THE PROMISE THE PANEL ABOVE MAKES. "Dismiss cooldown" told the user that three
 *  dismissals "mute it until you re-enable" and no re-enable existed anywhere in the frontend:
 *  `api.routingUnmute` and `api.routingStatus` were defined in `lib/api.ts` and called by nothing,
 *  so a muted agent was invisible AND permanent. The per-agent Unmute on the agent detail page
 *  (AR2-8) closed half of it; this closes the half a per-agent control structurally cannot.
 *
 *  🪤 THE LIST IS THE STORE, NOT THE AGENT CATALOG. `record_dismiss` writes a key without checking
 *  that an agent by that name exists, and an agent can be deleted while muted, so the store
 *  legitimately holds keys with no detail page to visit — measured live: three dismissals of
 *  `zz414-phantom-agent` produced a durable mute with no page anywhere in the app to clear it from.
 *  Reserved built-ins are a second such class (their detail panel renders no Advanced section at
 *  all). Rendering the store's own keys is what makes every one of them reachable; filtering to
 *  known agents would silently re-orphan exactly the entries that need this row most. */
function MutedAgentsField() {
  // `error` is BOUND, and the failed branch is distinct from the loading one. Read only `data`
  // and "Checking…" is terminal: the read is propagating (no `.catch` to swallow it), so a
  // failure leaves `data` undefined forever and the row sits on a progress word that never
  // resolves — the same shape as the first-run spinner, one field deep in Settings → Chat.
  const { data, error: mutesErr, refresh } = useQuery('agents:routing-mutes', () => api.routingStatus())
  const [busy, setBusy] = useState('')
  const muted = data?.muted ?? []
  const unmute = async (agent: string) => {
    setBusy(agent)
    // Gated: a refused unmute must not drop the row, or the click reads as having worked.
    if (await reportingWrite(`unmute ${agent}`, () => api.routingUnmute(agent))) {
      invalidateKeys('agents:routing-mutes')
      refresh()
    }
    setBusy('')
  }
  return (
    <Field label="Muted agents" hint="Agents the router has stopped suggesting because you dismissed them three times. Unmuting clears the mute and the dismissal count, so the agent can be suggested again.">
      {data === undefined && mutesErr ? (
        <p data-type="body-s" role="alert" className="text-on-surface-low">
          Couldn&apos;t check which agents are muted. <TextLink size="sm" onClick={refresh}>Retry</TextLink>
        </p>
      ) : data === undefined ? (
        <p data-type="body-s" className="text-on-surface-low">Checking…</p>
      ) : muted.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">None — no agent is muted.</p>
      ) : (
        // One grid, not per-row flex: agent names vary in width, so a button placed after the
        // name landed at a different x on every row and the actions read as scattered rather
        // than as one column you can run down.
        <ul className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-s gap-y-s">
          {muted.map((agent) => {
            const count = data.dismissals?.[agent]?.count
            return (
              <li key={agent} data-type="label-s" className="col-span-3 grid grid-cols-subgrid items-center">
                <VolumeX size={14} className="shrink-0 text-on-surface-low" />
                <span className="truncate">
                  <span className="text-on-surface">{agent}</span>
                  {count ? <span className="text-on-surface-low"> · {count} dismissals</span> : null}
                </span>
                {/* `loading` + `loadingLabel`, never a hand-rolled `disabled={busy} + {busy ? 'Unmuting…'}`
                    ternary. `aria-busy` is published from `loading` alone, so the hand-rolled shape trades
                    the announcement for the word and a screen-reader user gets neither — see `ui/Button`'s
                    note on the eight sites that had already made that trade. These rows sit in a column
                    where several unmutes can be in flight, which is the case the progress verb exists for.
                    Measured: the hand-rolled form raised `busyIsNotAnnounced`'s ceiling from 79 to 80.

                    `ariaLabel` carries the subject because this is a COLUMN of identical buttons: the
                    visible text is "Unmute" on every row, so a screen reader reading the actions list
                    announces the same name N times with nothing to choose between. `design/rowActionNames`
                    measures exactly this and holds the unnamed population at a ceiling of 5 — a sixth bare
                    row action reds it by file and text, which is how this one was caught. */}
                <Button size="sm" variant="secondary" onClick={() => unmute(agent)} ariaLabel={`Unmute ${agent}`}
                  loading={busy === agent} loadingLabel="Unmuting…">Unmute</Button>
              </li>
            )
          })}
        </ul>
      )}
    </Field>
  )
}

// ── Sessions (dashboard config) ──────────────────────────────────────────────
function SessionsSection({ cfg, setCfg }: { cfg: DashboardConfig; setCfg: (c: DashboardConfig) => void }) {
  const [saved, flash] = useSavedFlash()
  // 🔴 `stream_reveal` is set here and consumed by ChatPage under its own `chat:stream-reveal` key,
  // which nothing invalidated — and that key is `persist: true`, so Chat's first paint used the
  // pre-change value and a hard reload rehydrated it and used it again. Unlike the rest of this
  // panel that is not a displayed value: it decides how streamed text reveals, so a stale read means
  // the chat keeps behaving the way you just told it to stop.
  const save = (patch: Partial<DashboardConfig>) => {
    setCfg({ ...cfg, ...patch })
    // A settings toggle updates locally FIRST, so a failed save leaves the switch showing a value the
    // server rejected — and this said nothing: measured with the PUT at 500, the toggle went
    // `aria-pressed` false to true and STAYED true, with no toast, no live-region text, and the "Saved"
    // confirmation simply never appearing. Reported the way the eight sibling panels already report it
    // (`AccountPanel`, `AmbientPanel`, `AgentDefaultsPanel`, ...).
    api.saveDashboardConfig(patch).then(() => { invalidateKeys('chat:stream-reveal'); flash() }).catch((e) => {
      notify(`Couldn't save this chat setting: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  return (
    <Section title="Sessions" hint="What happens to your chats on restart, and while the agent is busy.">
      <RowGroup>
        {/* Same WCAG 2.5.3 fix as `NotificationsPanel`: the name was a truncation of the visible label. */}
        <Row label="Restore sessions on startup" hint="Re-open recently active sessions when the app starts.">
          <div className="flex items-center gap-s"><SavedToast show={saved} /><Toggle on={cfg.restore_sessions} onChange={(v) => save({ restore_sessions: v })} label="Restore sessions on startup" /></div>
        </Row>
        {cfg.restore_sessions && (
          <Row label="Restore window" hint="How recently active a session must be to re-open.">
            <SegPills ariaLabel="Restore window" value={String(cfg.restore_window_minutes)} onChange={(v) => save({ restore_window_minutes: Number(v) })} options={RESTORE_WINDOWS} />
          </Row>
        )}
        <Row label="Merge queued messages" hint="While the agent is busy, combine follow-ups into one labeled prompt instead of queueing separately.">
          <Toggle on={cfg.merge_queued_messages} onChange={(v) => save({ merge_queued_messages: v })} label="Merge queued messages" />
        </Row>
        <Row label="Auto-tag new chats" hint="When a chat's title is generated, also propose and assign tags in the same pass. Never touches chats you've tagged yourself, or incognito/temporary chats.">
          <Toggle on={cfg.auto_tag_sessions} onChange={(v) => save({ auto_tag_sessions: v })} label="Auto-tag new chats" />
        </Row>
      </RowGroup>
    </Section>
  )
}

// ── Messages (dashboard config display prefs) ────────────────────────────────
function MessagesSection({ cfg, setCfg }: { cfg: DashboardConfig; setCfg: (c: DashboardConfig) => void }) {
  const [saved, flash] = useSavedFlash()
  // 🔴 `stream_reveal` is set here and consumed by ChatPage under its own `chat:stream-reveal` key,
  // which nothing invalidated — and that key is `persist: true`, so Chat's first paint used the
  // pre-change value and a hard reload rehydrated it and used it again. Unlike the rest of this
  // panel that is not a displayed value: it decides how streamed text reveals, so a stale read means
  // the chat keeps behaving the way you just told it to stop.
  const save = (patch: Partial<DashboardConfig>) => {
    setCfg({ ...cfg, ...patch })
    // A settings toggle updates locally FIRST, so a failed save leaves the switch showing a value the
    // server rejected — and this said nothing: measured with the PUT at 500, the toggle went
    // `aria-pressed` false to true and STAYED true, with no toast, no live-region text, and the "Saved"
    // confirmation simply never appearing. Reported the way the eight sibling panels already report it
    // (`AccountPanel`, `AmbientPanel`, `AgentDefaultsPanel`, ...).
    api.saveDashboardConfig(patch).then(() => {
      // Every reader of this section's writes gets its own persisted key, so each needs busting here
      // or the chat keeps the value you just changed until that key happens to go stale. The
      // composer's Enter binding is the third reader in that same shape.
      invalidateKeys('chat:stream-reveal')
      invalidateKeys('chat:show-timestamps')
      invalidateKeys('chat:send-on-enter')
      flash()
    }).catch((e) => {
      notify(`Couldn't save this chat setting: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  return (
    <Section title="Messages" hint="How messages and tool activity render in the chat.">
      <RowGroup>
        {/* Both halves of the OFF hint used to be false: it promised a newline that Enter did not
            insert, and a chord that optimizes the prompt rather than sending. The newline half is
            real as of this change; the send route is the composer's button, so that is what it
            names. */}
        <Row label="Send on Enter" hint={cfg.send_on_enter ? 'Enter sends · Shift+Enter for a newline.' : 'Enter inserts a newline · sending is button-only.'}>
          <div className="flex items-center gap-s"><SavedToast show={saved} /><Toggle on={cfg.send_on_enter} onChange={(v) => save({ send_on_enter: v })} label="Send on Enter" /></div>
        </Row>
        <Row label="Show timestamps" hint="Display a time on each message.">
          <Toggle on={cfg.show_timestamps} onChange={(v) => save({ show_timestamps: v })} label="Show timestamps" />
        </Row>
        <Row label="Show thinking inline" hint="Show intermediate reasoning between tool calls instead of collapsing it.">
          <Toggle on={cfg.show_thinking_inline} onChange={(v) => save({ show_thinking_inline: v })} label="Show thinking inline" />
        </Row>
        <Row label="Simplified tool names" hint="Tool pills show a simplified purpose instead of the exact command.">
          <Toggle on={cfg.simplified_tool_names} onChange={(v) => save({ simplified_tool_names: v })} label="Simplified tool names" />
        </Row>
        <Row label="Follow-up suggestions" hint="After each reply, show 2-3 suggested next messages (one small background call; never blocks the turn). Skipped for temporary/incognito chats; silent with no model bound.">
          <Toggle on={cfg.followup_chips} onChange={(v) => save({ followup_chips: v })} label="Follow-up suggestions" />
        </Row>
        {/* Its own Row: a `Row` renders ONE label + hint on the left and its children together on
            the right, so a second switch here shared the "Follow-up suggestions" caption and that
            row's hint — which describes suggested next messages, not verification. Both switches
            then rendered with no visible text of their own, so only a screen reader could tell them
            apart. 1 of 39 switch-bearing settings rows held two controls; this restores the form the
            other 38 use. */}
        <Row label="Offer “Check this work”" hint="After a turn that did real multi-step work, offer a chip that re-derives and re-runs the checks against what the turn claimed. Only ever an offer — the verification cost is spent on your click, never automatically.">
          <Toggle on={cfg.offer_check_work} onChange={(v) => save({ offer_check_work: v })} label="Offer 'Check this work'" />
        </Row>
        {/* MI-4. Off by default and its own Row, because the hint IS the consent text:
            a privacy switch whose scope the user has to infer is not consent. */}
        <Row label="Share screen in chat" hint="Adds a “Share screen” control to the composer. With it on, a message can carry ONE frame of a screen or window you pick in your browser's own share dialog — held in memory for that single turn, never written to disk. Your browser shows its own capture indicator the whole time. Off means the control is hidden and the server refuses any frame.">
          <Toggle on={cfg.screen_share_enabled} onChange={(v) => save({ screen_share_enabled: v })} label="Share screen in chat" />
        </Row>
        <Row label="Streaming text reveal" hint="Smooth: steady word-by-word reveal decoupled from network chunks (never lags). Immediate: render each chunk the instant it arrives.">
          <SegPills ariaLabel="Streaming text reveal" value={cfg.stream_reveal} onChange={(v) => save({ stream_reveal: v as 'smooth' | 'immediate' })}
            options={[{ key: 'smooth', label: 'Smooth' }, { key: 'immediate', label: 'Immediate' }]} />
        </Row>
        <Row label="Widget density" hint="How aggressively the agent uses inline widgets for visual content.">
          <SegPills ariaLabel="Widget density" value={cfg.widget_density} onChange={(v) => save({ widget_density: v as 'more' | 'less' })}
            options={[{ key: 'more', label: 'More' }, { key: 'less', label: 'Less' }]} />
        </Row>
      </RowGroup>
    </Section>
  )
}

// ── File checkpoints (checkpoints.* config) ──────────────────────────────────
/** The bounds on `/rewind-to-turn`'s backing store (EXECUTION-ISOLATION §6).
 *
 *  Only the BOUNDS are here. Which files are never copied is a code-level floor with no
 *  config field, deliberately — so this panel cannot be used to widen what the store may
 *  hold. The hints say what turning each knob down actually costs, because a cap the user
 *  lowers silently makes older rewinds impossible. */
function CheckpointsSection({ checkpoints, setCheckpoints }: {
  checkpoints: Record<string, unknown>; setCheckpoints: (c: Record<string, unknown>) => void
}) {
  const [saved, flash] = useSavedFlash()
  const patch = (key: string, value: unknown, _cb?: () => void, label?: string) => {
    const prev = checkpoints[key]
    setCheckpoints({ ...checkpoints, [key]: value })
    api.patchConfig(`checkpoints.${key}`, value).then(flash).catch((e) => {
      setCheckpoints({ ...checkpoints, [key]: prev })
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  const on = checkpoints.enabled !== false
  return (
    <Section title="File checkpoints" hint="Before the agent's first write to a file in a turn, its current bytes are saved so /rewind-to-turn can restore them. Files only — never the conversation. Credential files (.env, keys) are never copied, so they are never restored either.">
      <RowGroup>
        <Row label="Back up files before an edit" hint={on ? 'A wrong edit is recoverable with /rewind-to-turn N.' : 'Off — a wrong edit is gone. Nothing is being recorded.'}>
          <div className="flex items-center gap-s"><SavedToast show={saved} /><Toggle on={on} onChange={(v) => patch('enabled', v)} label="Back up files before an edit" /></div>
        </Row>
        {on && (
          <>
            <NumberRow label="Store cap per chat" hint="Megabytes of saved file contents kept per chat. When a new backup would exceed this, the oldest turns are dropped — rewinding past them stops being possible. 0 = no byte cap." value={Number(checkpoints.max_mb ?? 200)} min={0} max={100000} step={10} suffix="MB" onCommit={(n, l) => patch('max_mb', n, undefined, l)} saved={saved} />
            <NumberRow label="Turns kept" hint="How many recent turns you can rewind to. Older turns are dropped." value={Number(checkpoints.max_turns ?? 50)} min={1} max={1000} step={1} onCommit={(n, l) => patch('max_turns', n, undefined, l)} saved={saved} />
            <NumberRow label="Largest file backed up" hint="A file bigger than this is noted but not copied, so a rewind reports it as not captured instead of restoring it. Keeps one big write from filling the whole store. 0 = no limit." value={Number(checkpoints.max_file_mb ?? 8)} min={0} max={10000} step={1} suffix="MB" onCommit={(n, l) => patch('max_file_mb', n, undefined, l)} saved={saved} />
          </>
        )}
      </RowGroup>
    </Section>
  )
}

// ── Context & lifecycle (session.* config) ───────────────────────────────────
function LifecycleSection({ session, setSession, agentOptions, discovered }: {
  session: Record<string, unknown>; setSession: (s: Record<string, unknown>) => void
  agentOptions: import('../../lib/agents').AgentOption[]; discovered: Record<string, import('../../lib/api').DiscoveredAgent[]>
}) {
  const [saved, flash] = useSavedFlash()
  const patch = (key: string, value: unknown, _cb?: () => void, label?: string) => {
    const prev = session[key]
    setSession({ ...session, [key]: value })
    api.patchConfig(`session.${key}`, value).then(flash).catch((e) => {
      setSession({ ...session, [key]: prev })
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  const poolSize = Number(session.pool_size ?? 0)
  return (
    <Section title="Context & lifecycle" hint="Keep long sessions productive and control how warm sessions are kept ready.">
      <RowGroup>
        <NumberRow label="Auto-compact threshold" hint="Context-usage % that triggers compaction. Lower = more frequent." value={Number(session.autocompact_pct ?? 90)} min={5} max={90} step={1} suffix="%" onCommit={(n, l) => patch('autocompact_pct', n, undefined, l)} saved={saved} />
        <NumberRow label="Idle timeout" hint="Auto-close an idle session after this long. 0 = never." value={Number(session.timeout_secs ?? 0)} min={0} max={86400} step={60} suffix="s" onCommit={(n, l) => patch('timeout_secs', n, undefined, l)} saved={saved} />
        <AutoArchiveRow days={Number(session.auto_archive_days ?? 30)} onCommit={(n, l) => patch('auto_archive_days', n, undefined, l)} saved={saved} />

        <Row label="Warm pool size" hint="Pre-started sessions kept ready for an instant first turn. 0 = off.">
          <NumberField value={poolSize} min={0} max={10} step={1} onChange={(n) => patch('pool_size', n)} ariaLabel="Warm pool size" />
        </Row>
        {poolSize > 0 && (
          <>
            <Row label="Warm pool agent" hint="Which agent the warm sessions pre-start as (native or a connected ACP-runtime agent). Empty uses the default agent.">
              <div className="w-56">
                <Combobox
                  value={String(session.pool_agent ?? '')}
                  options={[{ value: '', label: '— default —' }, ...agentOptions,
                    ...(session.pool_agent && !agentOptions.some((o) => o.value === session.pool_agent) ? [{ value: String(session.pool_agent), label: String(session.pool_agent), group: 'Current' }] : [])]}
                  placeholder="— default —" emptyText="No agents"
                  onChange={async (v) => { const name = v ? await ensureBindableAgentName(v, discovered) : ''; patch('pool_agent', name) }} />
              </div>
            </Row>
            <NumberRow label="Warm pool TTL" hint="Recycle a warm session after this long unused." value={Number(session.pool_ttl_secs ?? 1800)} min={0} max={7200} step={60} suffix="s" onCommit={(n, l) => patch('pool_ttl_secs', n, undefined, l)} saved={saved} />
          </>
        )}
      </RowGroup>
    </Section>
  )
}

// ── Background compression (tools.bg_compress_* config) ──────────────────────
/** The always-on complement to the auto-compact threshold above: an old, idle, at-rest chat is
 *  topic-segmented and summarized on the maintenance cadence, with no manual trigger. What it
 *  shortens is the history the MODEL is handed when the chat resumes — a record kept beside the
 *  chat (`bg_compress.py`). It never writes the chat itself, so the copy below must not suggest it
 *  trims, archives or recovers anything.
 *
 *  Its two allowlisted paths (`tools.bg_compress_enabled`, `tools.bg_compress_idle_days`) were
 *  PATCH-editable and read by `bg_compress.py` with NO control anywhere in `web/` — one of the
 *  sections issue #2801 counted. They sit here, beside the compaction threshold they complement,
 *  rather than on the Tool-output page: that panel is about PROJECTING a single tool result, and
 *  this is about a session's stored history.
 *
 *  🪤 `tools.*` IS A DIFFERENT SECTION FROM `session.*`, so this owns its own state and its own
 *  patch. One setter reaching into both would roll a failed save back into the wrong object — the
 *  reason `SourcesPanel` declares `patchKnowledge` separately from `patch`. */
function BackgroundCompressionSection({ tools, setTools }: {
  tools: Record<string, unknown>; setTools: (t: Record<string, unknown>) => void
}) {
  const [saved, flash] = useSavedFlash()
  const patch = (key: string, value: unknown, _cb?: () => void, label?: string) => {
    const prev = tools[key]
    setTools({ ...tools, [key]: value })
    api.patchConfig(`tools.${key}`, value).then(flash).catch((e) => {
      setTools({ ...tools, [key]: prev })
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  const on = tools.bg_compress_enabled !== false
  return (
    <Section title="Background compression" hint="When a long, idle chat is picked up again, the history handed to the model opens with a summary of its older part instead of every message.">
      <RowGroup>
        <Row label="Background compression"
          hint="Summarizes the older part of idle chats, using the background model. Your chats are never changed: every message stays as you left it. A summary stops being used the moment a message it covers changes, and is deleted with its chat. Incognito and temporary chats are never summarized.">
          <div className="flex items-center gap-s">
            <SavedToast show={saved} />
            <Toggle on={on} onChange={(v) => patch('bg_compress_enabled', v, undefined, 'Background compression')} label="Background compression" />
          </div>
        </Row>
        {on && (
          <NumberRow label="Idle window before summarizing" hint="Only summarize chats untouched for at least this long. An active chat is never summarized."
            value={Number(tools.bg_compress_idle_days ?? 7)} min={0} max={365} step={1} suffix="d"
            onCommit={(n, l) => patch('bg_compress_idle_days', n, undefined, l)} saved={saved} />
        )}
      </RowGroup>
    </Section>
  )
}

/** The auto-archive threshold, plus what it would actually do right now.
 *
 *  The rule has been running on the heartbeat since S2 with no way to see or change
 *  it: chats silently left the list after 30 days and the only evidence was a shorter
 *  list. A retention rule the user can't inspect is indistinguishable from data loss,
 *  so the count is fetched from the existing dry-run preview — the same call the sweep
 *  makes, so the number shown IS the number that would move, not an estimate. */
export function AutoArchiveRow({ days, onCommit, saved }: {
  days: number; /** `(value, label)` — this row has no `label` prop (its name lives in `ariaLabel`), so it supplies
   *  the literal. Accepting the argument without supplying one is the gap this fixes. */
  onCommit: (n: number, label?: string) => void; saved: boolean
}) {
  const [pending, setPending] = useState<number | null>(null)
  const [preview, setPreview] = useState<{ count: number; enabled: boolean } | null>(null)
  const shown = pending ?? days

  useEffect(() => {
    // Only meaningful while the rule is on; 0 = off has nothing to preview.
    if (shown <= 0) { setPreview(null); return }
    let live = true
    api.autoArchiveSessions({ dry_run: true })
      .then((r) => { if (live) setPreview({ count: r.count, enabled: r.enabled }) })
      .catch(() => { if (live) setPreview(null) })
    return () => { live = false }
  }, [shown])

  return (
    <Row
      label="Auto-archive after"
      hint="Archive chats with no activity for this long. Archived chats stay searchable and restore in one click — nothing is deleted. 0 = off."
    >
      <div className="flex items-center gap-s">
        <SavedToast show={saved} />
        {shown > 0 && preview?.enabled && (
          <span data-type="caption" className="text-on-surface-var tabular-nums">
            {preview.count === 0 ? 'none stale now' : `${preview.count} stale now`}
          </span>
        )}
        <NumberField
          value={shown} min={0} max={3650} step={1} ariaLabel="Auto-archive after (days)"
          onChange={(n) => { setPending(n); onCommit(n, 'Auto-archive after (days)') }}
        />
        <span data-type="caption" className="text-on-surface-var">{shown > 0 ? 'days' : 'off'}</span>
      </div>
    </Row>
  )
}

// ── helpers ──────────────────────────────────────────────────────────────────
function useSavedFlash(): [boolean, () => void] {
  const [saved, setSaved] = useState(false)
  return [saved, () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }]
}

function NumberRow({ label, hint, value, min, max, step, suffix, onCommit, saved }: {
  label: string; hint?: string; value: number; min: number; max: number; step?: number; suffix?: string
  /** 🪤 `(value, label)` — the label travels so a rejected save can name this control instead of its
   *  config key. Declaring the parameter on the panel's `patch` is not enough: nothing was PASSING one
   *  from here, so the toast fell through to `?? key` and still read "Couldn't save autocompact_pct". */
  onCommit: (n: number, label?: string) => void; saved: boolean
}) {
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-s">
        <SavedToast show={saved} />
        <NumberField value={value} min={min} max={max} step={step} onChange={(n) => onCommit(n, label)} ariaLabel={label} />
        {suffix && <span data-type="caption" className="w-6 text-on-surface-low">{suffix}</span>}
      </div>
    </Row>
  )
}
