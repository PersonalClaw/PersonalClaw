import { Coins } from 'lucide-react'
import { api, type UsageAgg, type UsageBudget, type UsageFold, type UsageWindow } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { Segmented } from '../../ui/Segmented'
import { Table, THead, Th, Td } from '../../ui/Table'
import { Meter } from '../../ui/Meter'
import { PanelHeader, Section } from './settingsUI'
import { InlineLoadError, ListSkeleton } from '../../ui/ListScaffold'
import { BigStat, KVList } from './bento'
import { ModelPricesSection } from './ModelPricesSection'

/** Account-level cost/token usage.
 *
 *  Reads the usage ledger, one row per model call: period totals, by-model, by-provider and
 *  by-source tables, and the cache savings line. The one exception is the "Daily budget" line,
 *  which sets the spend meter's day total beside the cap, because that total is what the cap is
 *  held to (`GET /api/usage/budget`); it is read-only. Honest-partial: a period mixing a model
 *  with no price row shows a "partial — N unpriced" marker, never a confidently-complete dollar
 *  figure. */
/** The same period control drives every read on the page, as one `window` of the gateway's local
 *  days, so the tiles, the tables and the chart never count two different spans. The gateway
 *  decides where each day starts, because the daily cap beside them counts its days: a page that
 *  worked out "today" from UTC said a turn cost money today while the cap said nothing had. */
const PERIODS: ReadonlyArray<{ id: string; label: string; days: number; window: UsageWindow }> = [
  { id: 'today', label: 'Today', days: 1, window: 'day' },
  { id: '7d', label: '7 days', days: 7, window: 'week' },
  { id: '30d', label: '30 days', days: 30, window: 'month' },
]

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

function fmtUsd(n: number): string {
  return n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(4)}`
}

/** The headline cost figure. Exported for test — the three branches ARE the finding.
 *
 *  🔴 ONE UNPRICED MODEL USED TO ERASE THE WHOLE NUMBER. Measured with a seeded ledger: four
 *  models, three of them priced, `cost_usd` **11.3496**, `priced: false` — and this stat rendered
 *  the word "unpriced", so a spend surface showed no spend while its own table listed
 *  $6.02 + $4.59 + $0.7398 immediately below.
 *
 *  The backend's rule is right — "a single unpriced constituent taints the total … it can never
 *  present as complete" (`usage_ledger`) — but **"not complete" is not "not knowable"**: the ledger
 *  had computed a real floor. This panel already uses exactly that concept 250 lines down: "Floor —
 *  … Real spend is higher than **the figure above**", copy that presupposes a figure there.
 *
 *  So: the exact number when everything is priced, an explicit floor when some of it is, and the
 *  bare word only when there is no floor to state — "≥$0.00" would be true and useless. The Partial
 *  marker under the stat carries the incompleteness either way. */
export function headlineCost(t: { cost_usd: number; priced: boolean }): string {
  if (t.priced) return fmtUsd(t.cost_usd)
  return t.cost_usd > 0 ? `≥${fmtUsd(t.cost_usd)}` : 'unpriced'
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
  const { days, window } = PERIODS.find((p) => p.id === period) ?? PERIODS[0]

  const { data: totals } = useQuery(
    `settings:usage-totals:${period}`,
    () => api.usageTotals({ window }).then((d) => d.totals).catch(() => null),
    { persist: false },
  )
  // 🔴 BOTH ROLLUPS SWALLOWED THEIR REJECTION AND THE TABLES ANSWERED FOR THE LEDGER (#532). `[]`
  // fell straight through `UsageTable`'s `rows.length === 0` branch, so a 500 on
  // /api/usage/rollup printed "No model usage recorded this period." and "No usage recorded this
  // period." on a spend page — the one surface whose entire job is to say what was spent. Worse
  // than a blank: the headline tiles above read from a DIFFERENT endpoint, so a user could see
  // $11.35 and 412 turns over two tables both swearing nothing had run.
  //
  // The two rollups are separate reads of the same ledger and fail independently, so each table
  // carries its own error rather than one banner speaking for both.
  const { data: byModel, error: byModelErr, refresh: refreshByModel } = useQuery(
    `settings:usage-rollup:model:${period}`,
    () => api.usageRollup({ group_by: 'model', window }).then((d) => d.rows),
    { persist: false },
  )
  const { data: bySource, error: bySourceErr, refresh: refreshBySource } = useQuery(
    `settings:usage-rollup:source:${period}`,
    () => api.usageRollup({ group_by: 'source', window }).then((d) => d.rows),
    { persist: false },
  )
  // Which provider entry answered: the `FakeUp` of `FakeUp:gpt-4o`, or an ACP runtime. The same
  // model id can come from two entries at two prices, and "By model" folds them together.
  const { data: byProvider, error: byProviderErr, refresh: refreshByProvider } = useQuery(
    `settings:usage-rollup:provider:${period}`,
    () => api.usageRollup({ group_by: 'provider', window }).then((d) => d.rows),
    { persist: false },
  )
  // Today's spend as the daily cap counts it, beside the cap (read-only; SpendMeter owns
  // enforcement). The ledger totals on this page include chat turns, which no cap covers, so they
  // are never the number set beside the cap, though both count the same day.
  const { data: budget } = useQuery(
    'settings:usage-budget',
    () => api.usageBudget().catch(() => null),
    { persist: false },
  )
  // Wire the in-memory SystemAgentStats token counters (SystemInfo.stats) — process-
  // lifetime totals, distinct from the durable ledger above, rendered so the typed-
  // but-orphaned field finally has a reader (no parallel type added).
  const { data: sys } = useQuery(
    'settings:usage-system-stats',
    () => api.system().then((s) => s.stats ?? null).catch(() => null),
    { persist: false },
  )
  // The durable per-day fold of the same ledger: purpose grouping, daily shape, and the
  // size of the unattended spend that is deliberately excluded from every figure on this page.
  const { data: fold } = useQuery(
    `settings:usage-fold:${period}`,
    () => api.usageFold({ window, group: 'purpose' }).catch(() => null),
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
  // Left on `?? []` deliberately: with the rollup unread this marker simply does not render, which
  // is an ABSENCE (no claim about pricing) rather than a fabricated one. The `Partial` banner only
  // ever adds a caveat, so withholding it withholds nothing the user could act on — and the table
  // that DID fail says so on its own line below.
  const unpricedModels = (byModel ?? []).filter((r) => !r.priced)

  return (
    <div className="flex flex-col" style={{ minHeight: 0 }}>
      <PanelHeader title="Usage"
        hint="What you've spent — real tokens and real USD from a per-call ledger: every turn (chat, rooms, subagents, loops, automations) and every call PersonalClaw makes around them, from a chat's title and follow-ups to judges and digests. A call that does not finish writes no row and is recorded only in a separate log; the 'By day and purpose' section states how much that is. Observation only: nothing here caps or throttles a turn (that's Guardrails). A model with no price is shown honestly as 'unpriced', never $0.00, until you give it one under Model prices." />

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
          <BigStat caption="cost" value={headlineCost(t)} />
          <BigStat value={fmtTokens(t.input_tokens + t.output_tokens)} caption="tokens" />
          <BigStat value={t.turns.toLocaleString()} caption="turns" />
        </div>
      )}

      {/* Honest-partial marker: a period that includes any unpriced model can't
          present a complete dollar figure. */}
      {unpricedModels.length > 0 && (
        <div data-type="body-s" className="mb-l rounded-lg bg-surface-container px-3 py-2 text-on-surface-var"
          role="status">
          <span className="text-warning">Partial</span> — {unpricedModels.length} unpriced{' '}
          {unpricedModels.length === 1 ? 'model' : 'models'} (no price row); their tokens count but
          their cost is not included in the total. Give {unpricedModels.length === 1 ? 'it' : 'them'} a
          price under Model prices.
        </div>
      )}

      {/* The per-day fold: the SAME ledger money as the tiles above, grouped into the five
          purposes and shaped per day, plus a statement of the unattended spend that is excluded.
          Placed directly under the tiles so the shape and the exclusion read together. */}
      <ByDayAndPurposeSection fold={fold ?? null} days={days} />

      {/* Cap context — the Guardrails cap beside the spend it is actually held to. */}
      {budget && <DailyBudgetSection budget={budget} />}

      {/* What each model's calls are counted at, and where a price is set: the place every
          "no price" line on this page sends the owner. */}
      <ModelPricesSection />

      {/* `rows` travels undefaulted — `?? []` here would put the swallow back one layer down, where
          it reads as the empty state again. */}
      <Section title="By model" hint="Which models this period's cost went to.">
        <UsageTable rows={byModel} error={byModelErr} onRetry={refreshByModel} keyField="model" empty="No model usage recorded this period." />
      </Section>

      <Section title="By provider" hint="Which provider each answer came from, as your model settings name it.">
        <UsageTable rows={byProvider} error={byProviderErr} onRetry={refreshByProvider} keyField="provider" empty="No provider usage recorded this period." />
      </Section>

      <Section title="By source" hint="Which subsystem spent — chat, rooms, subagents, loops, automations.">
        <UsageTable rows={bySource} error={bySourceErr} onRetry={refreshBySource} keyField="source" empty="No usage recorded this period." />
      </Section>

      <Section title="Cache savings">
        <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var">
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

/** "Daily budget" — the Guardrails cap beside the spend it is held to.
 *
 *  Both numbers come from `GET /api/usage/budget`: the spend meter's day total and the cap it is
 *  compared with. This line used to set the ledger's total for the page's "Today" beside the cap:
 *  chat turns included (no cap covers them), on a UTC day (the cap resets on the host's), so it
 *  could read a cap as spent that was not, and the reverse. A cap of 0 is unlimited and not shown.
 *
 *  A metered call nothing priced is not in the dollar total, and the line says how many there
 *  were: shown as the whole spend, the total told the owner those calls were free, and the cap
 *  cannot hold spend it cannot count. */
/** Today's metered calls the dollar total leaves out, as a person reads it: none of them had a
 *  price, and where one is set. */
function unpricedCallsSentence(n: number): string {
  const one = n === 1
  const calls = one ? '1 unattended call' : `${n.toLocaleString()} unattended calls`
  return `${calls} today had no price, so the dollar cap could not count ${one ? 'it' : 'them'}: `
    + `give ${one ? 'its model' : 'their models'} a price under Model prices below.`
}

export function DailyBudgetSection({ budget }: { budget: UsageBudget }) {
  const dollarCap = budget.max_dollars_per_day ?? 0
  const tokenCap = budget.max_tokens_per_day ?? 0
  if (!budget.cap_unreadable && dollarCap <= 0 && tokenCap <= 0) return null
  return (
    <Section title="Daily budget">
      <div data-type="body-s" className="flex flex-col gap-s rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var">
        {budget.cap_unreadable ? (
          <span role="status">The daily cap could not be read.</span>
        ) : (
          <>
            {dollarCap > 0 && (
              <span>
                <Coins size={13} className="mr-1.5 inline text-primary" />
                Unattended spend today:{' '}
                <span className="tabular-nums text-on-surface">{fmtUsd(budget.spent_dollars)}</span> of your{' '}
                <span className="tabular-nums text-on-surface">${dollarCap.toFixed(2)}</span> daily cap
              </span>
            )}
            {dollarCap > 0 && budget.unpriced_calls > 0 && <span>{unpricedCallsSentence(budget.unpriced_calls)}</span>}
            {tokenCap > 0 && (
              <span>
                Unattended tokens today:{' '}
                <span className="tabular-nums text-on-surface">{fmtTokens(budget.spent_tokens)}</span> of your{' '}
                <span className="tabular-nums text-on-surface">{fmtTokens(tokenCap)}</span> daily cap
              </span>
            )}
          </>
        )}
        <span className="text-on-surface-low">
          The cap counts the model calls automations, loops, subagents and background work make.
          Your chat turns are not capped.
          {dollarCap > 0 && !budget.cap_unreadable && (
            <>
              {' '}A call that costs money starts only when what it may cost fits in what is left,
              beside what the calls already running have set aside. A model with no price is
              refused, and one that costs nothing is not limited by it.
            </>
          )}
        </span>
      </div>
    </Section>
  )
}

const KEY_HEADER = { model: 'Model', provider: 'Provider', source: 'Source' } as const

/** What a row's calls used, as its Tokens cell reads it: its tokens, and how many images,
 *  seconds of video, minutes of audio or characters of speech the calls billed by those were
 *  billed for. Exported for test. */
export function usedText(r: Pick<UsageAgg, 'input_tokens' | 'output_tokens' | 'units'>): string {
  const tokens = (r.input_tokens || 0) + (r.output_tokens || 0)
  const n = (v: number) => Number(v.toFixed(2)).toLocaleString()
  const units = Object.entries(r.units ?? {}).filter(([, v]) => (v ?? 0) > 0).map(([unit, v]) => {
    const q = v ?? 0
    switch (unit) {
      case 'image': return `${n(q)} ${q === 1 ? 'image' : 'images'}`
      case 'second': return `${n(q)} s of video`
      case 'minute': return `${n(q)} min of audio`
      case 'character': return `${n(q)} characters`
      default: return `${n(q)} ${unit}`
    }
  })
  if (units.length === 0) return fmtTokens(tokens)
  return tokens > 0 ? `${fmtTokens(tokens)} · ${units.join(' · ')}` : units.join(' · ')
}

function UsageTable({ rows, keyField, empty, error, onRetry }: {
  /** `undefined` is UNKNOWN — loading or failed. Never defaulted to `[]` by a caller. */
  rows: Array<UsageAgg & Record<string, string>> | undefined
  keyField: 'model' | 'provider' | 'source'
  empty: string
  error?: unknown
  onRetry?: () => void
}) {
  // Error, then loading, then empty — in that order, because `rows === undefined` satisfies the
  // first two and `empty` is a CLAIM about the ledger, not a placeholder. One line rather than a
  // centred block: this is a section inside a page that has five more of them, and the tiles above
  // still hold real numbers worth reading.
  if (!rows && error) return <InlineLoadError what={`usage by ${keyField}`} error={error} onRetry={onRetry} />
  if (!rows) return <ListSkeleton rows={3} what={`usage by ${keyField}`} />
  const total = rows.reduce((s, r) => s + (r.cost_usd || 0), 0)
  if (rows.length === 0) {
    return <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-low">{empty}</div>
  }
  return (
    <Table
      sized={false}
      data-type="body-s" className="border-collapse"
      caption={`Token usage and cost per ${keyField}`}>
      <THead>
        <tr>
          <Th pad={false} className="border-b border-outline-variant/40 px-2 py-1.5 font-normal">{KEY_HEADER[keyField]}</Th>
          <Th align="right" pad={false} className="border-b border-outline-variant/40 px-2 py-1.5 font-normal">Tokens</Th>
          <Th align="right" pad={false} className="border-b border-outline-variant/40 px-2 py-1.5 font-normal">Cost</Th>
          <Th align="right" pad={false} className="border-b border-outline-variant/40 px-2 py-1.5 font-normal">Share</Th>
        </tr>
      </THead>
      <tbody>
        {rows.map((r) => {
          const label = r[keyField] || '(none)'
          const share = total > 0 && r.priced ? Math.round((r.cost_usd / total) * 100) : 0
          return (
            <tr key={label} className="text-on-surface-var">
              <Td pad={false} className="border-b border-outline-variant/25 px-2 py-1.5 font-mono">{label}</Td>
              <Td align="right" pad={false} className="border-b border-outline-variant/25 px-2 py-1.5 tabular-nums">{usedText(r)}</Td>
              <Td align="right" pad={false} className="border-b border-outline-variant/25 px-2 py-1.5 tabular-nums">{r.priced ? fmtUsd(r.cost_usd) : 'unpriced'}</Td>
              <Td align="right" pad={false} className="border-b border-outline-variant/25 px-2 py-1.5 tabular-nums text-on-surface-low">{r.priced ? `${share}%` : '—'}</Td>
            </tr>
          )
        })}
      </tbody>
    </Table>
  )
}

/** Human labels for the purpose vocabulary the backend fold uses (`interactive|background|loop|
 *  eval|app`). Only purposes the fold actually returned are rendered, so a bucket no writer can
 *  fill yet (`eval`) never shows as a permanent 0 row. */
const PURPOSE_LABEL: Record<string, string> = {
  interactive: 'Interactive — turns you watched',
  background: 'Background — automations, subagents and housekeeping',
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
      <div data-type="caption" className="mt-1 flex justify-between text-on-surface-low tabular-nums">
        <span>{series[0]?.date}</span>
        <span>{series[series.length - 1]?.date}</span>
      </div>
    </div>
  )
}

/** "By day and purpose" — the durable per-day fold of the same usage ledger the tiles
 *  above read, grouped into the fixed purpose vocabulary and shaped per day.
 *
 *  Its honesty markers are load-bearing, because this is a money surface:
 *  · a "~" on every figure (each dollar is computed from the price table, not reported by a provider)
 *  · an explicit FLOOR when some model has no price row, instead of a confident total
 *  · the unattended spend that is NOT included, stated with its size — every model call writes
 *    its row when it finishes, and one that did not finish (it failed) wrote none, so it is only in
 *    the model-call log. A call a row already counts is left out of that figure (the row names
 *    it), so nothing is counted twice. Saying "excluded, ~$X" is honest; silently omitting it
 *    would claim a completeness the data lacks. */
function ByDayAndPurposeSection({ fold, days }: { fold: UsageFold | null; days: number }) {
  if (!fold) return null
  const total = fold.total
  const uncounted = fold.uncounted
  const apps = Object.keys(fold.app_sources ?? {})
  const window = days === 1 ? 'today' : `the last ${days} days`
  const excluded = uncounted?.calls > 0 && (
    <div data-type="body-s" className="text-on-surface-var" role="status">
      <span className="text-on-surface">Not included:</span>{' '}
      {uncounted.calls.toLocaleString()} unattended model{' '}
      {uncounted.calls === 1 ? 'call' : 'calls'} (~{fmtUsd(uncounted.total_dollars_est)} across the
      whole log{uncounted.total_unpriced_calls > 0
        ? `, not counting ${uncounted.total_unpriced_calls.toLocaleString()} that had no price`
        : ''}). A call that does not finish writes no usage row, so these are recorded only in
      the model-call log.
    </div>
  )
  if (total.calls === 0) {
    return (
      <Section title="By day and purpose" hint={`How ${window} broke down.`}>
        <div data-type="body-s" className="flex flex-col gap-s rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-low">
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
          <div data-type="body-s" className="text-on-surface-var">
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
          <div data-type="body-s" className="text-on-surface-var">
            App spend came from {apps.join(', ')}.
          </div>
        )}

        {!total.priced && (
          <div data-type="body-s" className="text-on-surface-var" role="status">
            <span className="text-warning">Floor</span> — {total.unpriced_calls.toLocaleString()}{' '}
            {total.unpriced_calls === 1 ? 'turn ran' : 'turns ran'} on a model with no price row, so
            their tokens count but their cost does not. Real spend is higher than the figure above.
          </div>
        )}
        {excluded}
        <p data-type="caption" className="text-on-surface-low">
          Every figure here is prefixed “~” because it is computed from the price table, not
          reported by the provider{fold.estimated_share < 1
            ? ` (${Math.round(fold.estimated_share * 100)}% of this total is estimated)`
            : ''}.
        </p>
      </div>
    </Section>
  )
}
