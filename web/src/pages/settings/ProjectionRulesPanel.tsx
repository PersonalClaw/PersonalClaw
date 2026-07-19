import { useState } from 'react'
import { Scissors, Plus, X, AlertTriangle } from 'lucide-react'
import { api, type ProjectionRule, type ProjectionStrategy } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { PanelHeader, Section } from './settingsUI'

const STRATEGIES: { id: ProjectionStrategy; label: string; blurb: string }[] = [
  { id: 'log', label: 'Log', blurb: 'keep head + error/warning lines + tail' },
  { id: 'test', label: 'Test', blurb: 'keep failures + the summary line' },
  { id: 'diff', label: 'Diff', blurb: 'keep changed hunks + a +N/−M stat' },
  { id: 'json', label: 'JSON', blurb: 'keep the shape (keys/types) + a sample' },
  { id: 'csv', label: 'CSV', blurb: 'keep the header + first/last rows' },
]

/** User-teachable tool-output projection rules (TokenJuice, OP6).
 *
 *  When a tool returns a large output, PersonalClaw projects it to a token-cheap
 *  preview keyed to its content type (log error lines, diff hunks, test failures…)
 *  and retains the full raw for on-demand recall — instead of a blunt middle-cut.
 *  The builtin sniffer recognises the common formats; a rule here teaches the
 *  DISPATCH for a tool whose output it would otherwise treat as generic. Each rule
 *  maps a regex marker (matched against the output head) to a builtin strategy —
 *  declarative, so no user code runs, and a bad regex is rejected on save. */
export function ProjectionRulesPanel() {
  const { data: rules, refresh } = useCachedData(
    'settings:projection-rules', () => api.projectionRules().catch(() => [] as ProjectionRule[]),
    { persist: true },
  )
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const save = async (next: ProjectionRule[]) => {
    setBusy(true); setErr('')
    try { await api.setProjectionRules(next); refresh() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to save') }
    finally { setBusy(false) }
  }

  const list = rules ?? []

  return (
    <div>
      <PanelHeader title="Tool-output projection"
        hint="Teach PersonalClaw how to keep the salient slice of a large tool output — so a verbose result costs a preview, not the whole context window, while the full raw stays recoverable on demand." />

      <Section title="Custom rules"
        hint="A rule maps a content marker (regex, matched against the start of the output) to a projection strategy. Use it for a tool whose big output the builtin sniffer treats as generic (a blunt head/tail cut) — e.g. a domain-specific log or dump. Rules are checked before the builtin sniff.">
        <div className="flex flex-col gap-2">
          {list.map((r, i) => (
            <RuleRow key={i} rule={r} disabled={busy}
              onChange={(next) => save(list.map((x, j) => (j === i ? next : x)))}
              onRemove={() => save(list.filter((_, j) => j !== i))} />
          ))}
          {list.length === 0 && (
            <div className="rounded-lg bg-surface-container px-3 py-3 text-on-surface-low text-[0.8125rem]">
              No custom rules — the builtin projectors handle logs, diffs, JSON, test output, and CSV automatically. Add a rule only for a tool whose large output isn't recognised.
            </div>
          )}
          <AddRule disabled={busy} onAdd={(r) => save([...list, r])} />
          {err && <div className="flex items-center gap-1.5 text-error text-[0.8125rem]"><AlertTriangle size={13} /> {err}</div>}
        </div>
      </Section>
    </div>
  )
}

function StrategyPicker({ value, disabled, onChange }: {
  value: ProjectionStrategy; disabled?: boolean; onChange: (s: ProjectionStrategy) => void
}) {
  return (
    <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as ProjectionStrategy)}
      className="h-9 rounded-md bg-surface px-2 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
      {STRATEGIES.map((s) => <option key={s.id} value={s.id}>{s.label} — {s.blurb}</option>)}
    </select>
  )
}

function RuleRow({ rule, disabled, onChange, onRemove }: {
  rule: ProjectionRule; disabled?: boolean; onChange: (r: ProjectionRule) => void; onRemove: () => void
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-surface-container px-3 py-2.5">
      <div className="flex items-center gap-2">
        <Scissors size={13} className="shrink-0 text-on-surface-low" />
        <input value={rule.name} disabled={disabled} placeholder="rule name"
          onChange={(e) => onChange({ ...rule, name: e.target.value })}
          className="min-w-0 flex-1 h-9 rounded-md bg-surface px-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <StrategyPicker value={rule.strategy} disabled={disabled} onChange={(s) => onChange({ ...rule, strategy: s })} />
        <button type="button" disabled={disabled} onClick={onRemove} aria-label="Remove rule"
          className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface"><X size={15} /></button>
      </div>
      <input value={rule.match_regex} disabled={disabled} spellCheck={false} placeholder="match regex, e.g. ^\[MYAPP\]"
        onChange={(e) => onChange({ ...rule, match_regex: e.target.value })}
        className="h-9 rounded-md bg-surface px-2 font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
    </div>
  )
}

function AddRule({ disabled, onAdd }: { disabled?: boolean; onAdd: (r: ProjectionRule) => void }) {
  const [name, setName] = useState('')
  const [rx, setRx] = useState('')
  const [strat, setStrat] = useState<ProjectionStrategy>('log')
  const add = () => {
    if (!rx.trim()) return
    onAdd({ name: name.trim(), match_regex: rx.trim(), strategy: strat })
    setName(''); setRx(''); setStrat('log')
  }
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed border-outline-variant/50 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <Plus size={13} className="shrink-0 text-on-surface-low" />
        <input value={name} disabled={disabled} placeholder="new rule name"
          onChange={(e) => setName(e.target.value)}
          className="min-w-0 flex-1 h-9 rounded-md bg-surface px-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <StrategyPicker value={strat} disabled={disabled} onChange={setStrat} />
      </div>
      <div className="flex items-center gap-2">
        <input value={rx} disabled={disabled} spellCheck={false} placeholder="match regex, e.g. ^\[MYAPP\]"
          onChange={(e) => setRx(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') add() }}
          className="min-w-0 flex-1 h-9 rounded-md bg-surface px-2 font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <button type="button" disabled={disabled || !rx.trim()} onClick={add}
          className="shrink-0 h-9 rounded-md bg-primary px-3 text-on-primary text-[0.8125rem] disabled:opacity-40">Add rule</button>
      </div>
    </div>
  )
}
