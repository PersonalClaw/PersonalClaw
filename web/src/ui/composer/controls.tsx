import { useState, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { Bot, Cpu, ShieldCheck, Gauge, ChevronDown, Plus, Search, Paperclip, BookText } from 'lucide-react'
import { Popover, MenuRow } from '../Popover'
import { spring, bounce, expr } from '../../design/motion'
import { cx } from '../cx'
import type { ComposerData } from './types'
import type { ApprovalMode, ReasoningEffort } from '../../lib/api'

const APPROVAL: { id: ApprovalMode; label: string; hint: string }[] = [
  { id: 'normal', label: 'Normal', hint: 'Ask before every tool' },
  { id: 'trust_reads', label: 'Trust reads', hint: 'Auto-approve read-only' },
  { id: 'trust', label: 'Trust', hint: 'Auto-approve in this chat' },
  { id: 'yolo', label: 'YOLO', hint: 'Auto-approve everywhere — auto-expires, re-enable to extend' },
]
// Native effort levels (map to Anthropic thinking budget / OpenAI reasoning_effort).
// ACP agents override this with their backend-declared supported_efforts.
const NATIVE_EFFORTS: { value: string; label: string }[] = [
  { value: 'low', label: 'Low' }, { value: 'medium', label: 'Medium' }, { value: 'high', label: 'High' }, { value: 'max', label: 'Max' },
]

// Shared trigger for every composer pill (Agent/Model/Approval/Reasoning/Plus).
// v2: a spring press (whileTap, expr-scaled), an active tint while its popover is
// open, and a spring chevron flip — one physical touch across all pills. The
// popover itself opens via the (already-v2) Popover.
function PillButton({ icon, label, open, toggle }: { icon: React.ReactNode; label: string; open: boolean; toggle: () => void }) {
  return (
    <motion.button
      type="button" onClick={toggle} aria-expanded={open}
      whileTap={{ scale: 1 - expr(0.04, 0.3) }} transition={spring.spatialFast}
      className={cx('flex items-center gap-1.5 h-9 rounded-pill px-m transition-colors text-[0.8125rem] max-w-[160px]',
        open ? 'bg-surface-high text-on-surface' : 'text-on-surface-var hover:bg-surface-high')}
      style={{ fontVariationSettings: '"wght" 470' }}
    >
      {icon}
      <span className="truncate">{label}</span>
      <motion.span className="shrink-0" animate={{ rotate: open ? 180 : 0 }} transition={bounce.subtle}>
        <ChevronDown size={15} strokeWidth={2} />
      </motion.span>
    </motion.button>
  )
}

/** Trim boilerplate governance suffixes that runtime-provided agents append to
 *  their descriptions (e.g. "DO NOT EDIT MANUALLY") so the picker hint shows
 *  only the meaningful part. */
function cleanAgentHint(desc?: string): string {
  return (desc ?? '').replace(/\s*--\s*⚠️[^]*$/u, '').replace(/\s*\(?\[?DO NOT (UPDATE|EDIT)[^]*$/i, '').trim()
}

export function AgentPill({ data, value, onSelect, openSignal }: { data?: ComposerData; value: string; onSelect: (agent: string) => void; openSignal?: number }) {
  const [q, setQ] = useState('')
  const nativeAgents = data?.agents ?? []
  const discoveredEntries = Object.entries(data?.discovered ?? {})
  const total = nativeAgents.length + discoveredEntries.reduce((n, [, a]) => n + a.length, 0)
  // Long agent rosters (native + every discovered ACP agent) need a filter — 60+
  // entries is unscannable. Show the search box only once the list is big enough
  // to warrant it, so small setups stay clutter-free.
  const showSearch = total > 8
  const nq = q.trim().toLowerCase()
  const native = nq ? nativeAgents.filter((a) => a.name.toLowerCase().includes(nq)) : nativeAgents
  const discovered = discoveredEntries
    .map(([rt, agents]) => [rt, nq ? agents.filter((d) => `${d.name} ${d.description}`.toLowerCase().includes(nq) || rt.toLowerCase().includes(nq)) : agents] as const)
    .filter(([, agents]) => agents.length > 0)
  const noMatches = nq && native.length === 0 && discovered.length === 0
  return (
    <Popover width={280} openSignal={openSignal} trigger={(open, toggle) => <PillButton icon={<Bot size={16} strokeWidth={2} />} label={value || 'Agent'} open={open} toggle={toggle} />}>
      {(close) => (
        <div className="flex max-h-[340px] flex-col">
          {showSearch && (
            <div className="relative shrink-0 px-1 pb-1">
              <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-low" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search agents" autoFocus
                className="h-8 w-full rounded-md bg-surface-high pl-8 pr-2 text-[0.8125rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {native.map((a) => (
              <MenuRow key={a.name} label={a.name} selected={a.name === value} onClick={() => { onSelect(a.name); close() }} />
            ))}
            {discovered.map(([rt, agents]) => (
              <div key={rt}>
                <div className="px-m pt-m pb-1 text-[0.7rem] uppercase tracking-wide text-on-surface-low">{rt}</div>
                {agents.map((d) => (
                  <MenuRow key={d.id} label={d.name} hint={cleanAgentHint(d.description)} selected={d.name === value} onClick={() => { onSelect(d.name); close() }} />
                ))}
              </div>
            ))}
            {noMatches && <div className="px-m py-2 text-[0.8125rem] text-on-surface-low">No agents match “{q.trim()}”</div>}
            {total === 0 && <div className="px-m py-2 text-[0.8125rem] text-on-surface-low">No agents available</div>}
          </div>
        </div>
      )}
    </Popover>
  )
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

export function ModelPill({ data, agent, value, onSelect, contextPct, openSignal }: { data?: ComposerData; agent?: string; value: string; onSelect: (m: string) => void; contextPct?: number; openSignal?: number }) {
  // If the selected agent is an ACP-discovered agent, scope the model list to
  // the models THAT agent provides (not the native global model list).
  const acp = agent
    ? Object.values(data?.discovered ?? {}).flat().find((d) => d.name === agent)
    : undefined
  const acpModels = acp?.models ?? []
  const dot = contextPct !== undefined && contextPct > 0
    ? <ContextRing pct={contextPct} />
    : <span className="size-1.5 rounded-pill bg-primary" />
  // The pill shows the friendly model_name, not the raw "Provider:model_id" ref
  // stored as the value (the dropdown rows already display model_name).
  const pillLabel = !value || value === 'Auto'
    ? 'Auto'
    : (data?.models ?? []).find((m) => m.name === value)?.model_name || value
  return (
    <Popover width={280} openSignal={openSignal} trigger={(open, toggle) => <PillButton icon={dot} label={pillLabel} open={open} toggle={toggle} />}>
      {(close) => (
        <div className="max-h-[320px] overflow-y-auto">
          <MenuRow icon={<Cpu size={16} />} label="Auto" hint="Agent's configured model" selected={!value || value === 'Auto'} onClick={() => { onSelect('Auto'); close() }} />
          {acp
            ? acpModels.length > 0
              ? acpModels.map((m) => <MenuRow key={m} icon={<Cpu size={16} />} label={m} hint={acp.runtime} selected={m === value} onClick={() => { onSelect(m); close() }} />)
              : <div className="px-m py-2 text-[0.8125rem] text-on-surface-low">No selectable models — this runtime uses its own default.</div>
            : (data?.models ?? []).map((m) => (
                <MenuRow key={m.name} icon={<Cpu size={16} />} label={m.model_name || m.name} hint={m.provider} selected={m.name === value} onClick={() => { onSelect(m.name); close() }} />
              ))}
        </div>
      )}
    </Popover>
  )
}

export function ApprovalPill({ value, onSelect }: { value: ApprovalMode; onSelect: (m: ApprovalMode) => void }) {
  const cur = APPROVAL.find((a) => a.id === value) ?? APPROVAL[0]
  return (
    <Popover width={240} trigger={(open, toggle) => <PillButton icon={<ShieldCheck size={16} strokeWidth={2} />} label={cur.label} open={open} toggle={toggle} />}>
      {(close) => APPROVAL.map((a) => (
        <MenuRow key={a.id} label={a.label} hint={a.hint} selected={a.id === value} onClick={() => { onSelect(a.id); close() }} />
      ))}
    </Popover>
  )
}

/** Reasoning-effort selector. `efforts` are the bound agent's advertised effort
 *  options (ACP: backend-declared; native: NATIVE_EFFORTS). Renders nothing when
 *  the agent has no effort axis, so the pill only appears for reasoning-capable
 *  agents. A "Default" row (value "") always leads, for the model's own default. */
export function ReasoningPill({ value, efforts, onSelect, openSignal }: {
  value: ReasoningEffort
  efforts: { value: string; label: string }[]
  onSelect: (e: ReasoningEffort) => void
  openSignal?: number
}) {
  if (!efforts.length) return null
  const rows = [{ value: '', label: 'Default' }, ...efforts]
  const cur = rows.find((e) => e.value === value) ?? rows[0]
  return (
    <Popover width={180} openSignal={openSignal} trigger={(open, toggle) => <PillButton icon={<Gauge size={16} strokeWidth={2} />} label={cur.label} open={open} toggle={toggle} />}>
      {(close) => rows.map((e) => (
        <MenuRow key={e.value || 'default'} label={e.label} selected={e.value === value} onClick={() => { onSelect(e.value as ReasoningEffort); close() }} />
      ))}
    </Popover>
  )
}

/** Resolve the effort options for the bound agent: an ACP discovered agent's
 *  backend-declared supported_efforts, else the native effort ladder. Empty when
 *  the ACP agent declares none (→ pill hidden). */
export function effortsForAgent(data: ComposerData | undefined, agent: string): { value: string; label: string }[] {
  const disc = agent ? Object.values(data?.discovered ?? {}).flat().find((d) => d.name === agent) : undefined
  if (disc) return disc.supported_efforts ?? []
  return NATIVE_EFFORTS
}

/** The "+" toolbar menu — the single entry point for composer add-ons, like the
 *  "+" in Claude.ai / ChatGPT. Always offers Attach; offers Saved prompts when the
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
    <Popover width={240} trigger={(open, toggle) => (
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

