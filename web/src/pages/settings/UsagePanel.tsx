import { useMemo } from 'react'
import { Coins } from 'lucide-react'
import { api, type UsageAgg } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { Segmented } from '../../ui/Segmented'
import { PanelHeader, Section } from './settingsUI'
import { BigStat, KVList } from './bento'

/** Account-level cost/token usage (COST-AND-TOKEN-OBSERVABILITY S2c).
 *
 *  Reads the per-turn ledger (never SpendMeter's enforcement store — this is
 *  observation only): period totals, a by-model + by-source table, the cache
 *  savings line, and a read-only "spent $X of your $Y cap" from the guardrails
 *  config. Honest-partial: a period mixing a model with no price row shows a
 *  "partial — N unpriced" marker, never a confidently-complete dollar figure. */
const PERIODS = [
  { id: 'today', label: 'Today', days: 1 },
  { id: '7d', label: '7 days', days: 7 },
  { id: '30d', label: '30 days', days: 30 },
] as const

function _sinceIso(days: number): string {
  // Start-of-window in ISO-UTC. "Today" = midnight UTC today; N days = N*24h back.
  const now = Date.now()
  if (days === 1) {
    const d = new Date(now)
    return `${d.toISOString().slice(0, 10)}T00:00:00+00:00`
  }
  return new Date(now - days * 24 * 60 * 60 * 1000).toISOString()
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

function fmtUsd(n: number): string {
  return n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(4)}`
}

/** Cumulative model wall-clock. Kept panel-local beside its sibling formatters: this is the only
 *  duration on this surface, and one call site does not justify a shared primitive. */
function fmtDuration(ms: number): string {
  const secs = Math.round(ms / 1000)
  if (secs < 60) return `${secs}s`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ${secs % 60}s`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}

export function UsagePanel({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [period, setPeriod] = useQueryParam(query, setQuery, 'period', 'today', { replace: true })
  const days = (PERIODS.find((p) => p.id === period) ?? PERIODS[0]).days
  const since = useMemo(() => _sinceIso(days), [days])

  const { data: totals } = useCachedData(
    `settings:usage-totals:${period}`,
    () => api.usageTotals({ since }).then((d) => d.totals).catch(() => null),
    { persist: false },
  )
  const { data: byModel } = useCachedData(
    `settings:usage-rollup:model:${period}`,
    () => api.usageRollup({ group_by: 'model', since }).then((d) => d.rows).catch(() => []),
    { persist: false },
  )
  const { data: bySource } = useCachedData(
    `settings:usage-rollup:source:${period}`,
    () => api.usageRollup({ group_by: 'source', since }).then((d) => d.rows).catch(() => []),
    { persist: false },
  )
  // The configured daily $ cap (read-only; SpendMeter owns enforcement). 0 = unlimited.
  const { data: cfg } = useCachedData(
    'settings:guardrails-config',
    () => api.personalclawConfig().then((c) => c?.guardrails?.budgets ?? null).catch(() => null),
    { persist: false },
  )
  const { data: todayTotals } = useCachedData(
    'settings:usage-totals:today',
    () => api.usageTotals({ since: _sinceIso(1) }).then((d) => d.totals).catch(() => null),
    { persist: false },
  )
  // Wire the in-memory SystemAgentStats token counters (SystemInfo.stats) — process-
  // lifetime totals, distinct from the durable ledger above, rendered so the typed-
  // but-orphaned field finally has a reader (no parallel type added).
  const { data: sys } = useCachedData(
    'settings:usage-system-stats',
    () => api.system().then((s) => s.stats ?? null).catch(() => null),
    { persist: false },
  )

  const t: UsageAgg | null = totals ?? null
  const cacheTokens = (t?.cache_read_tokens ?? 0) + (t?.cache_creation_tokens ?? 0)
  // Prompt-cache row is conditional: an install whose provider never reports cached tokens should
  // not carry a permanent "0 read / 0 written" line.
  const cacheLive = (sys?.cache_read_tokens ?? 0) + (sys?.cache_creation_tokens ?? 0)
  // The section used to appear only when tokens were non-zero, so a gateway that had created
  // sessions or spawned subagents WITHOUT a chat turn hid all of it. Any counter moving is enough.
  const hasActivity = !!sys && (
    sys.input_tokens > 0 || sys.output_tokens > 0 || sys.total_turns > 0
    || sys.sessions_created > 0 || sys.subagents_spawned > 0 || cacheLive > 0
  )
  const unpricedModels = (byModel ?? []).filter((r) => !r.priced)
  const dayCap = Number(cfg?.max_dollars_per_day ?? 0) || 0

  return (
    <div className="flex flex-col" style={{ minHeight: 0 }}>
      <PanelHeader title="Usage"
        hint="What you've spent — real provider-reported tokens and real USD, from a per-turn ledger over every turn (chat, subagents, loops, automations). Observation only: this never caps or throttles a turn (that's Guardrails). A model with no price row is shown honestly as 'unpriced', never $0.00." />

      <div className="mb-l">
        <Segmented
          ariaLabel="Usage period"
          options={PERIODS.map((p) => ({ key: p.id, label: p.label }))}
          value={period}
          onChange={setPeriod}
        />
      </div>

      {t && (
        <div className="mb-l grid grid-cols-2 gap-2 sm:grid-cols-3">
          <BigStat value={t.priced ? fmtUsd(t.cost_usd) : 'unpriced'} caption="cost" />
          <BigStat value={fmtTokens(t.input_tokens + t.output_tokens)} caption="tokens" />
          <BigStat value={t.turns.toLocaleString()} caption="turns" />
        </div>
      )}

      {/* Honest-partial marker: a period that includes any unpriced model can't
          present a complete dollar figure. */}
      {unpricedModels.length > 0 && (
        <div className="mb-l rounded-lg bg-surface-container px-3 py-2 text-on-surface-var text-[0.8125rem]"
          role="status">
          <span className="text-warning">Partial</span> — {unpricedModels.length} unpriced{' '}
          {unpricedModels.length === 1 ? 'model' : 'models'} (no price row); their tokens count but
          their cost is not included in the total.
        </div>
      )}

      {/* Cap context — the first time the Guardrails cap input has a corresponding actual. */}
      {dayCap > 0 && (
        <Section title="Daily budget">
          <div className="rounded-lg bg-surface-container px-3 py-2.5 text-[0.8125rem] text-on-surface-var">
            <Coins size={13} className="mr-1.5 inline text-primary" />
            Spent <span className="tabular-nums text-on-surface">{fmtUsd(todayTotals?.cost_usd ?? 0)}</span>{' '}
            of your <span className="tabular-nums text-on-surface">${dayCap.toFixed(2)}</span> daily cap
            <span className="ml-1 text-on-surface-low">(automations only — interactive chat is uncapped)</span>
          </div>
        </Section>
      )}

      <Section title="By model" hint="Which models this period's cost went to.">
        <UsageTable rows={byModel ?? []} keyField="model" empty="No model usage recorded this period." />
      </Section>

      <Section title="By source" hint="Which subsystem spent — chat, subagents, loops, automations.">
        <UsageTable rows={bySource ?? []} keyField="source" empty="No usage recorded this period." />
      </Section>

      <Section title="Cache savings">
        <div className="rounded-lg bg-surface-container px-3 py-2.5 text-[0.8125rem] text-on-surface-var">
          {cacheTokens > 0
            ? <>Reused <span className="tabular-nums text-on-surface">{fmtTokens(cacheTokens)}</span> cached tokens this period.</>
            : 'No prompt-cache activity yet — cached tokens appear here once a provider reports them.'}
        </div>
      </Section>

      {/* In-memory counters since the gateway started (SystemInfo.stats) — a live cross-check on
          the durable ledger, reset on restart unlike the ledger above.

          This block used to read 3 of the 14 typed fields (tokens in/out + turns), leaving 8
          counters that the backend increments on real runtime paths with no reader anywhere: the
          session and subagent lifecycles, prompt-cache tokens, and cumulative duration. They are
          surfaced here rather than in a new panel because this is already the "what has this
          gateway done" surface, and a second one would split the answer. */}
      {sys && hasActivity && (
        <Section title="Since gateway start" hint="Live in-memory counters — reset on restart, unlike the ledger above.">
          <div className="rounded-lg bg-surface-container px-3 py-2.5">
            <KVList rows={[
              { k: 'Tokens', v: `${fmtTokens(sys.input_tokens)} in / ${fmtTokens(sys.output_tokens)} out` },
              // Prompt-cache tokens are counted separately from input/output by every provider that
              // reports them, so folding them into "in" would double-count the cached prefix.
              ...(cacheLive > 0
                ? [{ k: 'Prompt cache', v: `${fmtTokens(sys.cache_read_tokens)} read / ${fmtTokens(sys.cache_creation_tokens)} written` }]
                : []),
              { k: 'Turns', v: sys.total_turns.toLocaleString() },
              // Cumulative wall-clock across turns — the one counter that is a duration, not a
              // count, so it gets the same humanized format the rest of the app uses for spans.
              ...(sys.total_duration_ms > 0 ? [{ k: 'Model time', v: fmtDuration(sys.total_duration_ms) }] : []),
              { k: 'Sessions', v: `${sys.sessions_created.toLocaleString()} created / ${sys.sessions_cleaned.toLocaleString()} cleaned` },
              // Subagent failures are the only counter here that can indicate a problem, so it
              // reads as plain text when zero and takes the warn ink only when it is not.
              {
                k: 'Subagents',
                v: (
                  <>
                    {sys.subagents_spawned.toLocaleString()} spawned / {sys.subagents_completed.toLocaleString()} completed
                    {sys.subagents_failed > 0 && (
                      <> / <span className="text-warn">{sys.subagents_failed.toLocaleString()} failed</span></>
                    )}
                  </>
                ),
              },
            ]} />
          </div>
        </Section>
      )}
    </div>
  )
}

function UsageTable({ rows, keyField, empty }: {
  rows: Array<UsageAgg & Record<string, string>>
  keyField: 'model' | 'source'
  empty: string
}) {
  const total = rows.reduce((s, r) => s + (r.cost_usd || 0), 0)
  if (rows.length === 0) {
    return <div className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-low text-[0.8125rem]">{empty}</div>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[0.8125rem]">
        <thead>
          <tr className="text-on-surface-low">
            <th className="border-b border-outline-variant/40 px-2 py-1.5 text-left font-normal">{keyField === 'model' ? 'Model' : 'Source'}</th>
            <th className="border-b border-outline-variant/40 px-2 py-1.5 text-right font-normal">Tokens</th>
            <th className="border-b border-outline-variant/40 px-2 py-1.5 text-right font-normal">Cost</th>
            <th className="border-b border-outline-variant/40 px-2 py-1.5 text-right font-normal">Share</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const label = r[keyField] || '(none)'
            const share = total > 0 && r.priced ? Math.round((r.cost_usd / total) * 100) : 0
            return (
              <tr key={label} className="text-on-surface-var">
                <td className="border-b border-outline-variant/25 px-2 py-1.5 font-mono">{label}</td>
                <td className="border-b border-outline-variant/25 px-2 py-1.5 text-right tabular-nums">{fmtTokens((r.input_tokens || 0) + (r.output_tokens || 0))}</td>
                <td className="border-b border-outline-variant/25 px-2 py-1.5 text-right tabular-nums">{r.priced ? fmtUsd(r.cost_usd) : 'unpriced'}</td>
                <td className="border-b border-outline-variant/25 px-2 py-1.5 text-right tabular-nums text-on-surface-low">{r.priced ? `${share}%` : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
