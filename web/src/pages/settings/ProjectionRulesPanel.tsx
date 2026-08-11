import { useState } from 'react'
import { unavailableWhen } from '../../ui/unavailable'
import { Scissors, Plus, X, AlertTriangle, Gauge } from 'lucide-react'
import { api, type ProjectionRule, type ProjectionStrategy, type ToolsSavings } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { Button } from '../../ui/Button'
import { NumberField, TextInput } from '../../ui/forms'
import { PanelHeader, Section } from './settingsUI'

const STRATEGIES: { id: ProjectionStrategy; label: string; blurb: string }[] = [
  { id: 'log', label: 'Log', blurb: 'keep head + error/warning lines + tail' },
  { id: 'test', label: 'Test', blurb: 'keep failures + the summary line' },
  { id: 'diff', label: 'Diff', blurb: 'keep changed hunks + a +N/−M stat' },
  { id: 'json', label: 'JSON', blurb: 'keep field schema + first/last items' },
  { id: 'csv', label: 'CSV', blurb: 'keep the header + first/last rows' },
  { id: 'code', label: 'Code', blurb: 'keep signatures + docstrings + line map' },
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

      <SavingsCard />

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
              No custom rules — the builtin projectors handle logs, diffs, JSON, test output, CSV, and code automatically, and a builtin rule pack recognises common command output (git, pytest, npm, docker…). Add a rule only for a tool whose large output isn't recognised.
            </div>
          )}
          <AddRule disabled={busy} onAdd={(r) => save([...list, r])} />
          {err && <div className="flex items-center gap-1.5 text-danger text-[0.8125rem]"><AlertTriangle size={13} /> {err}</div>}
        </div>
      </Section>
    </div>
  )
}

/** Read-only TokenJuice savings card (§1.3) — estimated tokens saved by output
 *  projection this month + the top compressor. Renders nothing until there's a saving
 *  (a fresh install has no data; showing "0 saved" would be noise). Tokens are estimated
 *  (chars/4) — this is the counterfactual savings ledger, not authoritative spend. */
/** Exported for test: the breakdown's derivations (zero filter, savings ordering, the
 *  more-than-one gate, chars→tokens conversion) are only observable by rendering the card against a
 *  stubbed summary — jsdom reports every box as 0, so none of it is measurable from layout. */
export function SavingsCard() {
  const { data } = useCachedData<ToolsSavings>(
    'settings:tools-savings', () => api.toolsSavings(), { persist: true },
  )
  if (!data || data.saved_chars <= 0) return null
  const fmt = (n: number) => n.toLocaleString()
  // `by_compressor` is the per-compressor savings the summary already aggregates; the card named
  // only `top_compressor`, so "which of my compressors is actually earning its keep" had no answer
  // — and with one compressor dominating, the others were indistinguishable from unused.
  //
  // Sorted by savings and shown ONLY when more than one compressor contributed: with a single
  // entry the breakdown just restates `top_compressor` above it, which is noise rather than detail.
  const breakdown = Object.entries(data.by_compressor ?? {})
    .filter(([, chars]) => chars > 0)
    .sort((a, b) => b[1] - a[1])
  return (
    <div className="mb-4 flex items-start gap-3 rounded-lg bg-surface-container px-3 py-3">
      <Gauge size={16} className="mt-0.5 shrink-0 text-primary" />
      <div className="min-w-0 text-[0.8125rem]">
        <div className="text-on-surface">
          TokenJuice saved <span className="font-medium">~{fmt(data.saved_tokens_estimated)}</span> tokens
          {' '}across {fmt(data.projection_count)} projected result{data.projection_count === 1 ? '' : 's'}
          {data.top_compressor ? <> — top compressor: <span className="font-mono">{data.top_compressor}</span></> : null}.
        </div>
        {breakdown.length > 1 && (
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-on-surface-var">
            {breakdown.map(([name, chars]) => (
              // Tokens, not chars: the headline above is in tokens, and two units in one card
              // invites the reader to compare numbers that are not comparable. Same ~4 chars/token
              // estimate the backend uses for `saved_tokens_estimated`.
              <span key={name}>
                <span className="font-mono">{name}</span>{' '}
                <span className="tabular-nums">~{fmt(Math.round(chars / 4))}</span>
              </span>
            ))}
          </div>
        )}
        <div className="mt-0.5 text-on-surface-low">
          Estimated ({fmt(data.saved_chars)} chars, ~4 chars/token). The full raw of every projected result stays recoverable via <span className="font-mono">tool_result_get</span>.
        </div>
      </div>
    </div>
  )
}

function StrategyPicker({ value, disabled, onChange, forRule }: {
  value: ProjectionStrategy; disabled?: boolean; onChange: (s: ProjectionStrategy) => void
  /** Which rule this picker belongs to — the rule's name, or '' for the new-rule row. Two pickers
   *  render on this panel, so a constant name would announce both identically. */
  forRule?: string
}) {
  return (
    <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as ProjectionStrategy)}
      aria-label={forRule ? `Strategy for ${forRule}` : 'Strategy for the new rule'}
      className="h-9 rounded-md bg-surface px-2 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
      {STRATEGIES.map((s) => <option key={s.id} value={s.id}>{s.label} — {s.blurb}</option>)}
    </select>
  )
}

function RuleRow({ rule, disabled, onChange, onRemove }: {
  rule: ProjectionRule; disabled?: boolean; onChange: (r: ProjectionRule) => void; onRemove: () => void
}) {
  const hasOps = Boolean(rule.head || rule.tail || rule.keep || rule.skip || rule.count)
  const [showOps, setShowOps] = useState(hasOps)
  const inputCls = 'h-9 rounded-md bg-surface px-2 font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50'
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-surface-container px-3 py-2.5">
      <div className="flex items-center gap-2">
        <Scissors size={13} className="shrink-0 text-on-surface-low" />
        <input value={rule.name} disabled={disabled} placeholder="rule name"
          aria-label="Rule name"
          onChange={(e) => onChange({ ...rule, name: e.target.value })}
          className="min-w-0 flex-1 h-9 rounded-md bg-surface px-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <StrategyPicker value={rule.strategy} disabled={disabled} forRule={rule.name}
          onChange={(s) => onChange({ ...rule, strategy: s })} />
        {/* One button per rule row, so a constant "Remove rule" announces identically N times.
            Matches the sibling pattern in SecurityPanel (`Remove ${h}`). */}
        <button type="button" disabled={disabled} onClick={onRemove}
          aria-label={rule.name ? `Remove rule ${rule.name}` : 'Remove rule'}
          className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface"><X size={15} /></button>
      </div>
      <input value={rule.match_regex} disabled={disabled} spellCheck={false} placeholder="match regex, e.g. ^\[MYAPP\]"
        aria-label={rule.name ? `Match regex for ${rule.name}` : 'Match regex'}
        onChange={(e) => onChange({ ...rule, match_regex: e.target.value })}
        className={inputCls} />
      {/* Rule ops v2: declarative line operations. When any is set they replace the
          strategy projector; still pure data — no code runs. */}
      {showOps ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <div className="flex flex-col gap-1 text-on-surface-low text-[0.6875rem]">head lines
            <NumberField value={rule.head ?? 0} min={0} width="w-full" ariaLabel="Keep head lines"
              onChange={(n) => onChange({ ...rule, head: n })} />
          </div>
          <div className="flex flex-col gap-1 text-on-surface-low text-[0.6875rem]">tail lines
            <NumberField value={rule.tail ?? 0} min={0} width="w-full" ariaLabel="Keep tail lines"
              onChange={(n) => onChange({ ...rule, tail: n })} />
          </div>
          <div className="flex flex-col gap-1 text-on-surface-low text-[0.6875rem]">keep matching
            <TextInput value={rule.keep ?? ''} size="sm" mono placeholder="regex" ariaLabel="Keep lines matching regex"
              onChange={(v) => onChange({ ...rule, keep: v })} />
          </div>
          <div className="flex flex-col gap-1 text-on-surface-low text-[0.6875rem]">skip matching
            <TextInput value={rule.skip ?? ''} size="sm" mono placeholder="regex" ariaLabel="Skip lines matching regex"
              onChange={(v) => onChange({ ...rule, skip: v })} />
          </div>
          <div className="flex flex-col gap-1 text-on-surface-low text-[0.6875rem]">fold matching
            <TextInput value={rule.count ?? ''} size="sm" mono placeholder="regex" ariaLabel="Fold lines matching regex"
              onChange={(v) => onChange({ ...rule, count: v })} />
          </div>
        </div>
      ) : (
        <div className="self-start">
          <Button variant="ghost" size="xs" disabled={disabled} onClick={() => setShowOps(true)}>
            + line operations (head/tail window, keep/skip/fold filters)
          </Button>
        </div>
      )}
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
          aria-label="New rule name"
          onChange={(e) => setName(e.target.value)}
          className="min-w-0 flex-1 h-9 rounded-md bg-surface px-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <StrategyPicker value={strat} disabled={disabled} onChange={setStrat} />
      </div>
      <div className="flex items-center gap-2">
        <input value={rx} disabled={disabled} spellCheck={false} placeholder="match regex, e.g. ^\[MYAPP\]"
          aria-label="Match regex for the new rule"
          onChange={(e) => setRx(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') add() }}
          className="min-w-0 flex-1 h-9 rounded-md bg-surface px-2 font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <button type="button" onClick={add}
          {...unavailableWhen(!rx.trim(), 'Enter a pattern first', { busy: disabled })}
          className="shrink-0 h-9 rounded-md bg-primary px-3 text-on-primary text-[0.8125rem] disabled:opacity-40 aria-disabled:opacity-40 aria-disabled:cursor-not-allowed">Add rule</button>
      </div>
    </div>
  )
}
