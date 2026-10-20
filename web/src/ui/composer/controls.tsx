import { Fragment, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { fvs } from '../../design/fontWeight'
import { motion } from 'framer-motion'
import { Bot, Cpu, ShieldCheck, Gauge, ChevronDown, Plus, Search, Paperclip, BookText, Feather, RotateCcw } from 'lucide-react'
import { Popover, MenuRow } from '../Popover'
import { Button } from '../Button'
import { physics } from '../../design/motion'
import { cx } from '../cx'
import type { ComposerData } from './types'
import type { ApprovalMode, ReasoningEffort } from '../../lib/api'

const APPROVAL: { id: ApprovalMode; label: string; hint: string }[] = [
  { id: 'normal', label: 'Normal', hint: 'Reads run; the rest ask when they need approval' },
  { id: 'trust_reads', label: 'Trust reads', hint: 'Read-only shell commands run too' },
  { id: 'trust', label: 'Trust', hint: 'Auto-approve in this chat' },
  { id: 'yolo', label: 'YOLO', hint: 'Auto-approve everywhere — auto-expires, re-enable to extend' },
]
// Shared trigger for every composer pill (agent-and-model, approval, reply style).
// An active tint while its popover is open and a spring chevron flip; a press shows by
// colour, at once, with no scale — these pills are pressed many times an hour. The
// popover itself opens via Popover.
/** The composer's value-selector trigger: an icon, the CURRENT VALUE, a chevron.
 *
 *  `dimension` is what the pill controls ("Model", "Agent"), and it exists because the visible
 *  label cannot carry it. These pills show a value and nothing else, so a screen-reader user
 *  tabbing the composer heard four bare values — "Agent", "Auto", "Default", "Normal" — with no
 *  indication that they were the agent, the model, the reasoning effort and the permission mode.
 *  Measured before this: every one of the four announced its value alone.
 *
 *  The name is composed exactly as `HeaderModePill` already composes its own
 *  (`aria-label={`${ariaLabel}: ${label}`}`, giving "Task mode: Agent"), so the two pill families
 *  in the app agree instead of one being precise and the other mute. Sighted users see no change —
 *  the dimension is already obvious from position and icon on screen, which is exactly why the
 *  visible label spends its width on the value. */
export function PillButton({ icon, label, dimension, open, toggle, wide = false, title }: {
  icon: React.ReactNode; label: string; dimension: string; open: boolean; toggle: () => void
  /** Room for a two-part value ("Codex · Careful coder", or one marked unavailable) that the
   *  standard width would cut to its first word. */
  wide?: boolean
  /** The whole sentence behind a value the pill can only show the start of. */
  title?: string
}) {
  return (
    <button
      type="button" onClick={toggle} aria-expanded={open} title={title}
      // A pill whose value IS its dimension ("Agent" when no specific agent is bound) would
      // otherwise announce "Agent: Agent".
      aria-label={label === dimension ? dimension : `${dimension}: ${label}`}
      data-type="label-s"
      className={cx('flex items-center gap-1.5 h-9 rounded-pill px-m transition-colors', wide ? 'max-w-[16rem]' : 'max-w-[160px]',
        open ? 'bg-surface-high text-on-surface' : 'text-on-surface-var hover:bg-surface-high active:bg-surface-high')}
      style={fvs(470)}
    >
      {icon}
      <span className="truncate">{label}</span>
      <motion.span className="shrink-0" animate={{ rotate: open ? 180 : 0 }} transition={physics.snappy}>
        <ChevronDown size={15} strokeWidth={2} />
      </motion.span>
    </button>
  )
}

/** Trim boilerplate governance suffixes that runtime-provided agents append to
 *  their descriptions (e.g. "DO NOT EDIT MANUALLY") so the picker hint shows
 *  only the meaningful part. */
export function cleanAgentHint(desc?: string): string {
  return (desc ?? '').replace(/\s*--\s*⚠️[^]*$/u, '').replace(/\s*\(?\[?DO NOT (UPDATE|EDIT)[^]*$/i, '').trim()
}

function ContextRing({ pct }: { pct: number }) {
  // A 16px ring that fills with context-window usage; warms to danger as it nears
  // full so a user notices an at-risk turn before it truncates.
  const r = 6, c = 2 * Math.PI * r
  const clamped = Math.max(0, Math.min(100, pct))
  const tone = clamped >= 90 ? 'var(--color-danger)' : clamped >= 70 ? 'var(--color-warn)' : 'var(--color-primary)'
  return (
    <span title={`Context: ${clamped.toFixed(0)}% used`} className="relative inline-flex shrink-0 items-center justify-center" style={{ width: 16, height: 16 }}>
      <svg width="16" height="16" viewBox="0 0 16 16" className="-rotate-90">
        <circle cx="8" cy="8" r={r} fill="none" stroke="var(--color-outline-variant)" strokeWidth="2" opacity={0.4} />
        <circle cx="8" cy="8" r={r} fill="none" stroke={tone} strokeWidth="2" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - clamped / 100)} />
      </svg>
    </span>
  )
}

/** Why the model pill shows no ring. Only one of the three causes is the user's to fix, so only
 *  that one (`null`: the gateway said nothing declares or serves a window) names the
 *  field that fixes it. Otherwise nothing has been measured since this page opened or the session
 *  restarted, which the next answer puts right. */
function unmeasuredTitle(windowTokens: number | null | undefined): string {
  if (windowTokens === null) return 'Context usage unknown — no context window is declared for this model. Set “Served context window” on the provider in Settings → Models.'
  const against = typeof windowTokens === 'number' ? `, against its ${windowTokens.toLocaleString('en-US')}-token window` : ''
  return `Context usage not measured yet — it's measured each time the model answers${against}.`
}

/** A reasoning-effort level as the pill lists it: the value the chat stores and its name. */
export type EffortLevel = { value: string; label: string }

/** A level whose value is no word, by the name the pill gives it. */
const LEVEL_NAMES: Record<string, string> = { xhigh: 'Extra high' }

/** A level a native model's record declares, as the pill names it: `high` → "High", `max` →
 *  "Max", `xhigh` → "Extra high". */
const levelName = (value: string) => LEVEL_NAMES[value] ?? value.charAt(0).toUpperCase() + value.slice(1)

/** The reasoning-effort levels the chosen agent and model declare; `[]` means none, and then the
 *  pill offers no effort row. An agent CLI declares them for its runtime (`supported_efforts`,
 *  verbatim, whichever of its models is chosen). A native model declares them through its
 *  provider's record of it (`efforts` on the composer's model list), and one whose record names
 *  none declares none. Auto names no model, so it declares none either. */
export function effortsFor(data: ComposerData | undefined, agent: string, model: string): EffortLevel[] {
  const acp = agent ? Object.values(data?.discovered ?? {}).flat().find((d) => d.name === agent) : undefined
  if (acp) return acp.supported_efforts ?? []
  if (!model || model === 'Auto') return []
  const declared = (data?.models ?? []).find((m) => m.name === model)?.efforts ?? []
  return declared.map((value) => ({ value, label: levelName(value) }))
}

/** What the agent-and-model pill can change. */
export type AgentModelPick = { agent?: string; model?: string; reasoning?: ReasoningEffort }

/** The composer's one pill for who answers: the agent, the model and, for a model that declares
 *  levels, the reasoning effort. It shows "<agent> · <model>" (the model half "Auto · <served
 *  model>" on Auto once a turn has named one) and opens one menu: the agents, the models, and an
 *  effort row under the chosen model listing exactly the levels it declares (`effortsFor`). A model
 *  that declares none has no effort row, and no other effort control exists.
 *
 *  A switch to an agent or a model that lacks the chat's level sets the effort back to Default in
 *  the same pick, and the pill says so beside itself until it is next opened. A chat already on one
 *  that lacks it (Auto with a level stored earlier) is set back to Default the same way, once the
 *  lists are read, so no turn runs at a level the pill does not show.
 *
 *  `parts` is the host's choice of halves (the composer's `controls.agent`, `.model` and
 *  `.reasoning`); a pill offering one half names only that dimension. */
export function AgentModelPill({ data, agent, model, effort, onSelect, parts, contextPct, contextWindow, served, autoChain, openSignal, effortSignal }: {
  data?: ComposerData
  agent: string
  model: string
  effort: ReasoningEffort
  onSelect: (pick: AgentModelPick) => void
  parts?: { agent?: boolean; model?: boolean; effort?: boolean }
  contextPct?: number; contextWindow?: number | null
  /** The model the chat's latest turn was answered by, and how it was chosen. */
  served?: { model: string; chosen: string }
  /** The Settings → Models chain the chat's turns run on while the model is Auto. */
  autoChain?: string
  /** Monotonic: each increment opens the pill (the `/agent`, `/model` and `/effort` commands). */
  openSignal?: number
  /** Monotonic: each increment opens the effort levels too (the `/effort` command). */
  effortSignal?: number
}) {
  const showAgent = parts?.agent ?? true
  const showModel = parts?.model ?? true
  const showEffort = parts?.effort ?? true
  const [q, setQ] = useState('')
  const [effortOpen, setEffortOpen] = useState(false)
  const [note, setNote] = useState('')
  const searchId = `agent-search-${useId()}`
  const agentHeading = useId()
  const modelHeading = useId()
  const lastEffortSignal = useRef(effortSignal ?? 0)
  useEffect(() => {
    if (effortSignal === undefined || effortSignal === lastEffortSignal.current) return
    lastEffortSignal.current = effortSignal
    if (effortSignal > 0) setEffortOpen(true)
  }, [effortSignal])

  // ── The agent half ──
  const nativeAgents = data?.agents ?? []
  const discoveredEntries = Object.entries(data?.discovered ?? {})
  const total = nativeAgents.length + discoveredEntries.reduce((n, [, a]) => n + a.length, 0)
  // 🪤 `ready` DEFAULTS TRUE when absent, not false. Callers that pass fixed lists
  // (the goal composer, tests) never set it, and defaulting to false would leave
  // them stuck on "Loading agents…" forever. Only a host that opted in by threading
  // `useComposerData` can report not-yet-read, which is exactly the host that knows.
  const ready = data?.ready ?? true
  const agentsErr = data?.agentsErr
  const retry = data?.retry
  // Long agent rosters (native + every discovered ACP agent) need a filter — 60+
  // entries is unscannable. Show the search box only once the list is big enough
  // to warrant it, so small setups stay clutter-free.
  const showSearch = showAgent && total > 8
  const nq = q.trim().toLowerCase()
  const native = nq ? nativeAgents.filter((a) => a.name.toLowerCase().includes(nq)) : nativeAgents
  const discovered = discoveredEntries
    .map(([rt, agents]) => [rt, nq ? agents.filter((d) => `${d.name} ${d.description}`.toLowerCase().includes(nq) || rt.toLowerCase().includes(nq)) : agents] as const)
    .filter(([, agents]) => agents.length > 0)
  const noMatches = nq && native.length === 0 && discovered.length === 0

  // ── The model half ──
  // If the selected agent is an ACP-discovered agent, scope the model list to
  // the models THAT agent provides (not the native global model list).
  const acp = agent ? Object.values(data?.discovered ?? {}).flat().find((d) => d.name === agent) : undefined
  const acpModels = acp?.models ?? []
  // `undefined` = the backend measured NO context usage → plain dot, no number.
  // A measured 0 still renders the ring (reading "0% used"): an empty context is a
  // real answer, and folding it into "unmeasured" is the inverse of the fabricated-
  // zero bug this guard replaced (it used to also require `> 0`, which hid it).
  // The dot SAYS it is unmeasured rather than just being quiet: a ring and a bare dot
  // are one pixel apart, so "unknown" was indistinguishable from "barely any context
  // used". And it says WHY (`unmeasuredTitle`), because only one of the reasons is a
  // user action.
  const dot = contextPct !== undefined
    ? <ContextRing pct={contextPct} />
    : <span title={unmeasuredTitle(contextWindow)} className="size-1.5 rounded-pill bg-primary" />
  const onAuto = !model || model === 'Auto'
  // The friendly model_name, not the raw "Provider:model_id" ref stored as the value. On Auto it
  // also names the model the latest turn was answered by: "Auto" alone read the same whichever
  // chain served the turn, so a binding passed over looked like one that was used.
  const modelName = (m: string) => (data?.models ?? []).find((x) => x.name === m)?.model_name || m
  const modelLabel = onAuto ? (served ? `Auto · ${served.model}` : 'Auto') : modelName(model)
  const pillTitle = showModel && onAuto && served
    ? `Auto — the latest turn ran on ${served.model}${served.chosen ? `, ${served.chosen}` : ''}.`
    : undefined
  // Which chain Auto runs on, before the turn: an agent CLI brings its own model, so it has none.
  // Short enough for the menu's width, which cut a longer one off before the chain's name ended.
  const autoHint = autoChain && !acp ? `Your ${autoChain} chain` : 'Use-case chain (Settings → Models)'

  // ── The effort ──
  const levels = showEffort ? effortsFor(data, agent, model) : []
  const rows: EffortLevel[] = [{ value: '', label: 'Default' }, ...levels]
  const current = rows.find((l) => l.value === effort)?.label ?? levelName(effort)
  // A chat already on an agent and model that lack its level (stored when every native model
  // offered one, a starter's, another tab's) has no effort row to show it, so it runs at Default:
  // the pill writes Default, as a switch to them would, and says so. Not before the lists are read,
  // when every model would look as if it declared none.
  const lacksLevel = showEffort && !!effort && !!data && (data.ready ?? true) && !levels.some((l) => l.value === effort)
  useEffect(() => {
    if (!lacksLevel) return
    onSelect({ reasoning: '' })
    setNote(`Effort set to Default: ${acp ? agent : onAuto ? 'Auto' : modelName(model)} has no ${current}`)
    // Once per binding that lacks its level: the host's `onSelect` and `data` are new each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lacksLevel, agent, model, effort])
  /** Apply an agent or model pick. One that lacks the chat's level takes the effort back to
   *  Default in the same write, and the pill says so. */
  const pick = (patch: { agent?: string; model?: string }, name: string) => {
    const next = effortsFor(data, patch.agent ?? agent, patch.model ?? model)
    if (showEffort && effort && !next.some((l) => l.value === effort)) {
      onSelect({ ...patch, reasoning: '' })
      setNote(`Effort set to Default: ${name} has no ${current}`)
    } else {
      onSelect(patch)
      setNote('')
    }
  }
  const effortBlock = (close: () => void) => levels.length > 0 && (
    <div className="pl-l">
      <button type="button" aria-expanded={effortOpen} onClick={() => setEffortOpen((v) => !v)}
        data-type="label-s"
        className="flex w-full items-center gap-s rounded-md px-m py-s text-left text-on-surface-var hover:bg-surface-high active:bg-surface-high transition-colors">
        <Gauge size={14} strokeWidth={2} className="shrink-0" />
        <span className="flex-1 min-w-0 truncate">Effort: {current}</span>
        <ChevronDown size={14} strokeWidth={2} className={cx('shrink-0 transition-transform', effortOpen && 'rotate-180')} />
      </button>
      {effortOpen && (
        <div role="group" aria-label="Effort">
          {rows.map((l) => (
            <MenuRow key={l.value || 'default'} label={l.label} selected={l.value === effort}
              onClick={() => { onSelect({ reasoning: l.value as ReasoningEffort }); setNote(''); close() }} />
          ))}
        </div>
      )}
    </div>
  )

  const label = [showAgent && (agent || 'Agent'), showModel && modelLabel].filter(Boolean).join(' · ')
  const dimension = showAgent && showModel ? 'Agent and model' : showAgent ? 'Agent' : 'Model'
  const heading = 'px-m pt-m pb-1 uppercase tracking-wide text-on-surface-low'
  return (
    <span className="inline-flex min-w-0 items-center gap-xs">
      <Popover portal width={300} openSignal={openSignal} trigger={(open, toggle) => (
        <PillButton icon={showModel ? dot : <Bot size={16} strokeWidth={2} />} label={label} dimension={dimension}
          open={open} wide={(showAgent && showModel) || (onAuto && !!served)} title={pillTitle}
          toggle={() => { if (!open) { setNote(''); setEffortOpen(false) } toggle() }} />
      )}>
        {(close) => {
          const modelRow = (key: string, rowLabel: string, value: string, hint: string, selected: boolean) => (
            <Fragment key={key}>
              <MenuRow icon={<Cpu size={16} />} label={rowLabel} hint={hint} selected={selected}
                onClick={() => { pick({ model: value }, rowLabel); close() }} />
              {selected && effortBlock(close)}
            </Fragment>
          )
          const anySelected = onAuto || (acp ? acpModels.includes(model) : (data?.models ?? []).some((m) => m.name === model))
          return (
            <div className="flex max-h-[420px] flex-col">
              {showSearch && (
                <div className="relative shrink-0 px-1 pb-1">
                  <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-low" />
                  <input id={searchId} autoComplete="off" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search agents" autoFocus
                    data-type="body-s"
                    className="h-8 w-full rounded-md bg-surface-high pl-8 pr-2 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
                </div>
              )}
              <div className="min-h-0 flex-1 overflow-y-auto">
                {showAgent && (
                  <div role="group" aria-labelledby={showModel ? agentHeading : undefined}>
                    {showModel && <div id={agentHeading} data-type="caption" className={heading}>Agent</div>}
                    {native.map((a) => (
                      <MenuRow key={a.name} label={a.name} selected={a.name === agent} onClick={() => { pick({ agent: a.name }, a.name); close() }} />
                    ))}
                    {discovered.map(([rt, agents]) => (
                      <div key={rt}>
                        <div data-type="caption" className={heading}>{rt}</div>
                        {agents.map((d) => (
                          <MenuRow key={d.id} label={d.name} hint={cleanAgentHint(d.description)} selected={d.name === agent} onClick={() => { pick({ agent: d.name }, d.name); close() }} />
                        ))}
                      </div>
                    ))}
                    {noMatches && <div data-type="body-s" className="px-m py-2 text-on-surface-low">No agents match “{q.trim()}”</div>}
                    {/* 🔑 THREE STATES, NOT ONE. `total === 0` used to print "No agents available" on all
                        three, and two of them are false. `useComposerData` seeds every list to `[]` and
                        readies via `Promise.allSettled`, so an unread roster and a REJECTED roster read are
                        both `[]` — indistinguishable from a user who genuinely has no agents installed.
                        "No agents available" is a statement about the user's own setup, the kind they
                        believe, so on an unreachable gateway it sent them looking for a problem in their
                        install. The order below is deliberate: not-yet-known, then could-not-be-read, then
                        the one real emptiness. */}
                    {total === 0 && (
                      !ready
                        ? <div data-type="body-s" className="px-m py-2 text-on-surface-low">Loading agents…</div>
                        : agentsErr
                          ? <div role="alert" className="px-m py-2">
                              <p data-type="body-s" className="text-on-surface-low">Couldn’t load your agents — this is a load error, nothing is missing from your setup.</p>
                              {retry && <Button variant="ghost-accent" size="xs" onClick={() => retry()} className="mt-1"><RotateCcw size={13} /> Try again</Button>}
                            </div>
                          : <div data-type="body-s" className="px-m py-2 text-on-surface-low">No agents available</div>
                    )}
                    {!showModel && effortBlock(close)}
                  </div>
                )}
                {showModel && (
                  <div role="group" aria-labelledby={showAgent ? modelHeading : undefined} className={cx(showAgent && 'mt-xs border-t border-outline-variant/30')}>
                    {showAgent && <div id={modelHeading} data-type="caption" className={heading}>Model</div>}
                    {modelRow('auto', 'Auto', 'Auto', autoHint, onAuto)}
                    {acp
                      ? acpModels.length > 0
                        ? acpModels.map((m) => modelRow(m, m, m, acp.runtime, m === model))
                        : <div data-type="body-s" className="px-m py-2 text-on-surface-low">No selectable models — this runtime uses its own default.</div>
                      : (data?.models ?? []).map((m) => modelRow(m.name, m.model_name || m.name, m.name, m.provider, m.name === model))}
                    {!anySelected && effortBlock(close)}
                    {/* Precedence explainer: the pick sits one level
                        above the use-case chain — it never replaces the chain, only fronts it. */}
                    <div data-type="caption" className="border-t border-outline-variant/30 px-m py-2 text-on-surface-low">
                      A picked model overrides this session's use-case chain; if it fails, the chain takes over.
                    </div>
                  </div>
                )}
              </div>
            </div>
          )
        }}
      </Popover>
      {note && <span role="status" data-type="caption" className="max-w-[16rem] truncate text-on-surface-low" title={note}>{note}</span>}
    </span>
  )
}

export function ApprovalPill({ value, onSelect }: { value: ApprovalMode; onSelect: (m: ApprovalMode) => void }) {
  const cur = APPROVAL.find((a) => a.id === value) ?? APPROVAL[0]
  return (
    <Popover portal width={240} trigger={(open, toggle) => <PillButton icon={<ShieldCheck size={16} strokeWidth={2} />} label={cur.label} dimension="Permission mode" open={open} toggle={toggle} />}>
      {(close) => APPROVAL.map((a) => (
        <MenuRow key={a.id} label={a.label} hint={a.hint} selected={a.id === value} onClick={() => { onSelect(a.id); close() }} />
      ))}
    </Popover>
  )
}

/** A reply style the picker lists: the backend's `GET /api/chat/reply-styles` rows. `by` names the
 *  app that adds it (an app's style only), which the picker shows beside its title. */
export type ReplyStyleOption = { id: string; title: string; description: string; by?: string }

/** A style's name as the picker says it: an app's carries the app, `Brief (Acme)`. */
export const replyStyleName = (s: ReplyStyleOption) => (s.by ? `${s.title} (${s.by})` : s.title)

/** What the next turn carries for its voice, as the backend previews it (`reply_style.preview`). */
export type ReplyStylePreviewView = {
  applies: 'style' | 'persona' | 'none'
  style: string; persona: string
  instruction: string; first_turn_only: boolean; refused: string
}

/** A persona theme's id as a name: `retro-terminal` → "Retro Terminal". */
const personaName = (id: string) => id.split('-').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')

/** The preview line under the picker: which voice the next turn carries and, on request, the exact
 *  text it appends. Rendered as the backend sent it; nothing here composes an instruction. */
function ReplyStylePreviewLine({ preview, title }: { preview: ReplyStylePreviewView; title: (id: string) => string }) {
  const [shown, setShown] = useState(false)
  const said = preview.refused
    ? preview.refused
    : preview.applies === 'style'
      ? `Next reply: the ${title(preview.style)} style, on every turn.`
      : preview.applies === 'persona'
        ? `Next reply: the ${personaName(preview.persona)} voice, on a new chat\u2019s first turn.`
        : 'Next reply: no style instruction is added.'
  return (
    <div className="border-t border-outline-variant/30 px-m py-2 flex flex-col gap-1">
      <span data-type="body-s">{said}</span>
      {preview.applies === 'style' && preview.persona && !preview.refused && (
        <span data-type="caption" className="text-on-surface-low">It replaces the {personaName(preview.persona)} voice while it is chosen.</span>
      )}
      {preview.instruction && (
        <>
          <button type="button" data-type="caption" className="self-start underline text-on-surface-low"
            aria-expanded={shown} onClick={() => setShown((v) => !v)}>
            {shown ? 'Hide what is sent' : 'Show what is sent'}
          </button>
          {shown && (
            <pre data-type="caption" className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-surface-container p-2 text-on-surface-low">{preview.instruction.trim()}</pre>
          )}
        </>
      )}
    </div>
  )
}

/** Who chose the style, as the pill says it; the conversation's own choice needs no word. */
const REPLY_STYLE_CHOSEN_BY: Record<string, string> = { agent: ' (agent)', platform: ' (default)' }

/** Reply-style picker: how the agent writes its replies in this conversation.
 *
 *  `""` (Agent default) follows the bound agent's style, then the platform default; each other row is a
 *  style this conversation chooses for itself, `default` included (no style, even when the agent
 *  asks for one). The rows and their one-line descriptions come from the backend.
 *
 *  This component RESOLVES NOTHING. `effective` and `source` arrive decided by the backend, which
 *  owns the one statement of the order (`reply_style.REPLY_STYLE_PRECEDENCE`); a label computed
 *  here from `choice` and the agent's style would be a second statement of it.
 *
 *  The pill shows the EFFECTIVE style and who chose it, so a change in how replies read can be
 *  traced to a control the owner can see. */
export function ReplyStylePill({ choice, effective, source, agentDefault, styles, preview, onSelect }: {
  /** What THIS conversation states: a style id, or `''` to follow the agent. */
  choice: string
  /** The style the next turn resolves to (a style id). */
  effective: string
  /** Which scope decided, a member of the backend's precedence tuple, or `''` before a
   *  conversation exists to resolve against. Then the pill shows the CHOICE, not an effect. */
  source: string
  /** What the bound agent's definition states, for the reset row's hint. */
  agentDefault: string
  styles: ReplyStyleOption[]
  /** What the next turn carries for its voice, from the backend; absent before the chat exists. */
  preview?: ReplyStylePreviewView | null
  onSelect: (style: string) => void
}) {
  const title = (id: string) => {
    const s = styles.find((x) => x.id === id)
    return s ? replyStyleName(s) : id
  }
  // A style this chat chose that the server no longer lists (its app disabled or without its grant,
  // its file deleted). The pick is kept and the turn uses the next level down; the row says so and
  // cannot be picked again until the style is back. Only once the list has arrived.
  const gone = source !== '' && choice !== '' && styles.length > 0 && !styles.some((s) => s.id === choice)
  const label = source === ''
    ? (choice ? title(choice) : 'Agent default')
    : `${title(effective)}${effective === 'default' ? '' : (REPLY_STYLE_CHOSEN_BY[source] ?? '')}`
  return (
    <Popover portal width={300} trigger={(open, toggle) => (
      <PillButton icon={<Feather size={16} strokeWidth={2} />} label={label} dimension="Reply style" open={open} toggle={toggle} />
    )}>
      {(close) => (
        <div className="flex flex-col">
          <MenuRow label="Agent default"
            hint={agentDefault ? `This agent asks for ${title(agentDefault)}` : 'Follows this agent, then the platform default'}
            selected={choice === ''} onClick={() => { onSelect(''); close() }} />
          {styles.map((s) => (
            <MenuRow key={s.id} label={s.id === 'default' ? 'No style' : replyStyleName(s)} hint={s.description}
              selected={choice === s.id} onClick={() => { onSelect(s.id); close() }} />
          ))}
          {gone && (
            <MenuRow label={`${choice} — no longer available`} disabled selected
              hint="This chat keeps the choice and uses the next level down until the style is back." />
          )}
          {preview && <ReplyStylePreviewLine preview={preview} title={title} />}
          <div data-type="caption" className="border-t border-outline-variant/30 px-m py-2 text-on-surface-low">
            Changes style only, never a fact, a caveat or a refusal. A choice here applies to this conversation and does not edit the agent.
          </div>
        </div>
      )}
    </Popover>
  )
}

/** The "+" toolbar menu — the single entry point for composer add-ons, the one
 *  place a composer keeps its extras. Always offers Attach; offers Saved prompts when the
 *  host wires `onOpenPrompts`; and renders any host `extra` items (e.g. Auto-nudge)
 *  at the bottom. Replaces the old attach-only button + the floating "Prompts" and
 *  auto-nudge chips that overlapped the composer's edge. Collapses to a plain
 *  attach button when nothing but attach is available (keeps the goal composer lean). */
export function PlusMenu({ onAttach, onOpenPrompts, extra }: {
  onAttach: () => void
  onOpenPrompts?: () => void
  extra?: (close: () => void) => ReactNode
}) {
  const hasMenu = !!onOpenPrompts || !!extra
  if (!hasMenu) {
    return (
      <button type="button" onClick={onAttach} aria-label="Attach files" title="Attach files"
        className="inline-flex items-center justify-center size-10 rounded-pill text-on-surface-var hover:bg-surface-high transition-colors">
        <Plus size={20} strokeWidth={2} />
      </button>
    )
  }
  return (
    <Popover portal width={240} trigger={(open, toggle) => (
      <button type="button" onClick={toggle} aria-label="Add to message" title="Add"
        aria-expanded={open}
        className={cx('inline-flex items-center justify-center size-10 rounded-pill transition-colors',
          open ? 'bg-surface-high text-on-surface' : 'text-on-surface-var hover:bg-surface-high')}>
        <Plus size={20} strokeWidth={2} className={cx('transition-transform', open && 'rotate-45')} />
      </button>
    )}>
      {(close) => (
        <div className="flex flex-col">
          <MenuRow icon={<Paperclip size={16} />} label="Attach files" onClick={() => { close(); onAttach() }} />
          {onOpenPrompts && <MenuRow icon={<BookText size={16} />} label="Saved prompts" hint="Insert a saved prompt" onClick={() => { close(); onOpenPrompts() }} />}
          {extra?.(close)}
        </div>
      )}
    </Popover>
  )
}

