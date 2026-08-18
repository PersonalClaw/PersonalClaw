import { useMemo } from 'react'
import { Coins } from 'lucide-react'
import { api, type UsageAgg, type UsageFold } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { Segmented } from '../../ui/Segmented'
import { Meter } from '../../ui/Meter'
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

/** The same period control drives both lenses, so the page never shows two different windows.
 *  `GET /api/usage` speaks day|week|month; the ledger routes speak an ISO `since`. */
const FOLD_WINDOW: Record<string, 'day' | 'week' | 'month'> = {
  today: 'day', '7d': 'week', '30d': 'month',
}

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
  // The durable per-day fold of the same ledger (MRT-3): purpose grouping, daily shape, and the
  // size of the unattended spend that is deliberately excluded from every figure on this page.
  const { data: fold } = useCachedData(
    `settings:usage-fold:${period}`,
    () => api.usageFold({ window: FOLD_WINDOW[period] ?? 'day', group: 'purpose' }).catch(() => null),
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
        hint="What you've spent — real tokens and real USD from a per-turn ledger over every streamed turn (chat, subagents, loops, automations). Unattended model calls are recorded in a separate log that cannot be merged with these without double-counting; the 'By day and purpose' section states how much is excluded. Observation only: nothing here caps or throttles a turn (that's Guardrails). A model with no price row is shown honestly as 'unpriced', never $0.00." />

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

      {/* The per-day fold (MRT-3): the SAME ledger money as the tiles above, grouped into the five
          purposes and shaped per day, plus a statement of the unattended spend that is excluded.
          Placed directly under the tiles so the shape and the exclusion read together. */}
      <ByDayAndPurposeSection fold={fold ?? null} days={days} />

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

/** Human labels for the purpose vocabulary the backend fold uses (`interactive|background|loop|
 *  eval|app`). Only purposes the fold actually returned are rendered, so a bucket no writer can
 *  fill yet (`eval`) never shows as a permanent 0 row. */
const PURPOSE_LABEL: Record<string, string> = {
  interactive: 'Interactive — turns you watched',
  background: 'Background — automations and subagents',
  loop: 'Loops',
  eval: 'Evaluations',
  app: 'Apps',
}

/** Daily spend bars, mirroring the house bar-chart idiom (percentage heights, one primary token,
 *  per the dataviz conventions). `role="img"` + a summary label because a row of divs otherwise
 *  announces nothing; the same numbers are also in the meters below, so no data is chart-only. */
function DailySpendChart({ series }: { series: UsageFold['series'] }) {
  const max = series.reduce((m, s) => Math.max(m, s.dollars_est), 0)
  const peak = series.reduce((a, b) => (b.dollars_est > a.dollars_est ? b : a), series[0])
  const label = max > 0
    ? `Spend per day over ${series.length} days; highest ~${fmtUsd(peak.dollars_est)} on ${peak.date}`
    : `Spend per day over ${series.length} days; no cost recorded`
  return (
    <div>
      <div className="flex h-24 items-end gap-px" role="img" aria-label={label}>
        {series.map((s) => (
          <div key={s.date} className="flex min-w-0 flex-1 items-end self-stretch">
            <div
              className={`w-full rounded-t ${s.calls > 0 ? 'bg-primary' : 'bg-surface-high'}`}
              style={{ height: max > 0 ? `${Math.max(2, (s.dollars_est / max) * 100)}%` : '2px' }}
              title={`${s.date}: ~${fmtUsd(s.dollars_est)} over ${s.calls} ${s.calls === 1 ? 'turn' : 'turns'}`}
            />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-on-surface-low text-[0.75rem] tabular-nums">
        <span>{series[0]?.date}</span>
        <span>{series[series.length - 1]?.date}</span>
      </div>
    </div>
  )
}

/** "By day and purpose" — the durable per-day fold (MRT-3) of the same per-turn ledger the tiles
 *  above read, grouped into the fixed purpose vocabulary and shaped per day.
 *
 *  Its honesty markers are load-bearing, because this is a money surface:
 *  · a "~" on every figure (each dollar is computed from the price table, not reported by a provider)
 *  · an explicit FLOOR when some model has no price row, instead of a confident total
 *  · the unattended spend that is NOT included, stated with its size — a loop's inner inference is
 *    recorded in a second log with no shared id, so merging the two would double-count. Saying
 *    "excluded, ~$X" is honest; silently omitting it would claim a completeness the data lacks. */
function ByDayAndPurposeSection({ fold, days }: { fold: UsageFold | null; days: number }) {
  if (!fold) return null
  const total = fold.total
  const uncounted = fold.uncounted
  const apps = Object.keys(fold.app_sources ?? {})
  const window = days === 1 ? 'today' : `the last ${days} days`
  const excluded = uncounted?.calls > 0 && (
    <div className="text-on-surface-var text-[0.8125rem]" role="status">
      <span className="text-on-surface">Not included:</span>{' '}
      {uncounted.calls.toLocaleString()} unattended model{' '}
      {uncounted.calls === 1 ? 'call' : 'calls'} (~{fmtUsd(uncounted.total_dollars_est)} across the
      whole log). They are recorded separately and cannot be merged with turns without
      double-counting loops.
    </div>
  )
  if (total.calls === 0) {
    return (
      <Section title="By day and purpose" hint={`How ${window} broke down.`}>
        <div className="flex flex-col gap-s rounded-lg bg-surface-container px-3 py-2.5 text-[0.8125rem] text-on-surface-low">
          <span>No turns recorded {window}.</span>
          {excluded}
        </div>
      </Section>
    )
  }
  return (
    <Section title="By day and purpose"
      hint={`The same money as above, grouped into the five purposes and shaped across ${window}.`}>
      <div className="flex flex-col gap-l rounded-lg bg-surface-container px-3 py-3">
        {/* Deliberately NOT restating cost/tokens/turns: the BigStat row sits ~100px above with
            the same three numbers for the same window (driving the page is what made the
            duplication obvious). The local share is the one headline figure the tiles omit. */}
        {total.local_calls > 0 && (
          <div className="text-[0.8125rem] text-on-surface-var">
            <span className="text-on-surface tabular-nums">
              {Math.round((total.local_calls / total.calls) * 100)}%
            </span>{' '}
            of these turns ran locally at $0.
          </div>
        )}

        {fold.series.length > 1 && <DailySpendChart series={fold.series} />}

        <div className="flex flex-col gap-s">
          {fold.rows.map((r) => (
            <Meter
              key={r.key}
              label={`${PURPOSE_LABEL[r.key] ?? r.key} — share of spend`}
              pct={total.dollars_est > 0 ? (r.dollars_est / total.dollars_est) * 100 : 0}
              detail={`${PURPOSE_LABEL[r.key] ?? r.key} · ~${fmtUsd(r.dollars_est)} · ${r.calls.toLocaleString()} ${r.calls === 1 ? 'turn' : 'turns'}`}
            />
          ))}
        </div>

        {apps.length > 0 && (
          <div className="text-on-surface-var text-[0.8125rem]">
            App spend came from {apps.join(', ')}.
          </div>
        )}

        {!total.priced && (
          <div className="text-on-surface-var text-[0.8125rem]" role="status">
            <span className="text-warning">Floor</span> — {total.unpriced_calls.toLocaleString()}{' '}
            {total.unpriced_calls === 1 ? 'turn ran' : 'turns ran'} on a model with no price row, so
            their tokens count but their cost does not. Real spend is higher than the figure above.
          </div>
        )}
        {excluded}
        <p className="text-on-surface-low text-[0.75rem]">
          Every figure here is prefixed “~” because it is computed from the price table, not
          reported by the provider{fold.estimated_share < 1
            ? ` (${Math.round(fold.estimated_share * 100)}% of this total is estimated)`
            : ''}.
        </p>
      </div>
    </Section>
  )
}
