import { useId } from 'react'
import { ChartPie } from 'lucide-react'
import type { ContextPart } from '../../lib/api'
import { Button } from '../Button'
import { IconButton } from '../IconButton'
import { Meter } from '../Meter'
import { Popover } from '../Popover'
import { Toggle } from '../Toggle'
import { cx } from '../cx'

// ── The composer's Context control: context and cost, collapsed until opened (#3808, #3823) ──
//
// The approved update keeps cost and context collapsed unless the owner pins them. The model pill
// used to wear a context ring and the chat header a cost chip, both on all the time; this one
// control holds both instead. Collapsed it shows no number. Opened it says how full the context
// window is (or why that is not measured) and what the chat has cost so far, with a switch that
// pins the reading to the status line. From `CONTEXT_NEAR_LIMIT_PCT` the composer says how full it
// is in a line of its own, pinned or not, and the control takes the warning tone.

/** What a chat has cost so far, from the usage rollup scoped to it. `priced: false` = the total
 *  mixes a model with no price, so it is no total. */
export interface ContextCost { cost: number; tokens: number; priced: boolean }

/** A chat's context reading as its composer has it: the share of the window used (`undefined` when
 *  not measured), the window the gateway named (`null` = none declared or served, `undefined` =
 *  nothing said yet), what fills it part by part (`null`/absent when the gateway measured no
 *  parts), and its cost (`null` before any usage is recorded). */
export interface ContextReading { pct?: number; window?: number | null; parts?: ContextPart[] | null; cost?: ContextCost | null }

/** What a part's action does: open the Tools page, open what memory recalls, remove an attached
 *  file, or compact the conversation. */
export type ContextAction = ContextPart['action']

/** The parts in the order the gateway says them (`context_parts.PART_KINDS`), each with its name
 *  and the words of its action. */
const PARTS: { kind: ContextPart['kind']; label: string; action: ContextAction; actionLabel: string }[] = [
  { kind: 'instructions', label: 'Instructions and tools', action: 'tools', actionLabel: 'Review tools' },
  { kind: 'memory', label: 'Memory and profile', action: 'memory', actionLabel: 'Review what’s recalled' },
  { kind: 'files', label: 'Attached files', action: 'files', actionLabel: 'Remove' },
  { kind: 'conversation', label: 'Conversation', action: 'compact', actionLabel: 'Compact now' },
  { kind: 'tool_results', label: 'Tool results', action: 'compact', actionLabel: 'Compact now' },
]

/** One part as the Context view lists it and `/context` prints it: its name, what it holds in
 *  words ("12,000 tokens, 6% of the window", or "not measured"), and its action when measured. */
export interface ContextPartRow { kind: ContextPart['kind']; label: string; said: string; measured: boolean; action: ContextAction; actionLabel: string }

/** Every part, measured or not, in one order: the one source the view and `/context` read, so the
 *  two always agree. A part the gateway did not measure says so rather than guess. */
export function contextPartRows(r: Pick<ContextReading, 'parts' | 'window'>): ContextPartRow[] {
  return PARTS.map((p) => {
    const part = r.parts?.find((x) => x.kind === p.kind)
    if (!part) return { ...p, said: 'not measured', measured: false }
    const share = typeof r.window === 'number' && r.window > 0 ? `, ${Math.round((part.tokens / r.window) * 100)}% of the window` : ''
    return { ...p, said: `${part.tokens.toLocaleString('en-US')} tokens${share}`, measured: true }
  })
}

/** What `/context` prints in the chat: the reading, then every part as the view lists it. */
export function contextReport(r: ContextReading): string {
  const reading = r.pct !== undefined ? `${readingSaid(r.pct, r.window)}.` : unmeasuredSaid(r.window)
  return ['## Context', '', reading, '', ...contextPartRows(r).map((row) => `- ${row.label}: ${row.said}`)].join('\n')
}

/** From this share of the window the composer warns, in a line of its own (#3823's call). */
export const CONTEXT_NEAR_LIMIT_PCT = 80

/** Compact token count: 940 → "940", 46_000 → "46k", 1_200_000 → "1.2M". */
export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

const clamp = (pct: number) => Math.max(0, Math.min(100, pct))

/** Why there is no reading. Only one of the three causes is the owner's to fix, so only that one
 *  (`null`: the gateway said nothing declares or serves a window) names the field that fixes it.
 *  Otherwise nothing has been measured since this page opened or the session restarted, which the
 *  next answer puts right. */
export function unmeasuredSaid(windowTokens: number | null | undefined): string {
  if (windowTokens === null) return 'Context usage unknown — no context window is declared for this model. Set “Served context window” on the provider in Settings → Models.'
  const against = typeof windowTokens === 'number' ? `, against its ${windowTokens.toLocaleString('en-US')}-token window` : ''
  return `Context usage not measured yet — it's measured each time the model answers${against}.`
}

/** "34% of the 200,000-token context window used". A measured 0 is a reading, said as one. */
export function readingSaid(pct: number, windowTokens: number | null | undefined): string {
  const share = `${clamp(pct).toFixed(0)}%`
  return typeof windowTokens === 'number'
    ? `${share} of the ${windowTokens.toLocaleString('en-US')}-token context window used`
    : `${share} of the context window used`
}

/** What the chat has cost so far, never a confident $0 for a total that mixes an unpriced model. */
export function costSaid(cost: ContextCost | null | undefined): string {
  if (!cost) return 'Nothing spent yet.'
  if (!cost.priced) return `${fmtTokens(cost.tokens)} tokens so far; a model in this chat has no price, so there is no total.`
  return `${money(cost.cost)} so far, over ${fmtTokens(cost.tokens)} tokens`
}

const money = (dollars: number) => `$${dollars.toFixed(dollars < 1 ? 4 : 2)}`

/** The warning line near the limit: "This chat's context is 84% full." */
export function nearLimitSaid(pct: number): string {
  return `This chat's context is ${clamp(pct).toFixed(0)}% full.`
}

/** The status line's pinned reading, short ("Context 34% · $0.0123 · 46k tokens"), with the whole
 *  of it in words for its title. */
export function contextLineSaid(r: ContextReading): { line: string; sentence: string } {
  const parts = [r.pct !== undefined ? `Context ${clamp(r.pct).toFixed(0)}%` : r.window === null ? 'Context window unknown' : 'Context not measured yet']
  if (r.cost) parts.push(r.cost.priced ? money(r.cost.cost) : 'unpriced', `${fmtTokens(r.cost.tokens)} tokens`)
  const said = r.pct !== undefined ? readingSaid(r.pct, r.window) : unmeasuredSaid(r.window)
  return { line: parts.join(' · '), sentence: `${said}. ${costSaid(r.cost)}`.replace(/\.\./g, '.') }
}

/** Whether a reading is near the limit. */
export const nearLimit = (pct: number | undefined) => pct !== undefined && clamp(pct) >= CONTEXT_NEAR_LIMIT_PCT

export function ContextDisclosure({ pct, window: windowTokens, parts, cost, pin, actionFor }: ContextReading & {
  /** The status-line pin, when the host has a status line to pin to. */
  pin?: { pinned: boolean; onPin: (pinned: boolean) => void }
  /** What a part's action does here, or `undefined` when it cannot run now (Compact while a turn
   *  runs) or this host has nothing for it: then the part shows no action. */
  actionFor?: (action: ContextAction) => (() => void) | undefined
}) {
  const near = nearLimit(pct)
  const id = useId()
  const tone = pct !== undefined && clamp(pct) >= 90 ? 'var(--color-danger)' : near ? 'var(--color-warn)' : 'var(--color-primary)'
  return (
    <Popover portal width={300} trigger={(open, toggle) => (
      <IconButton icon={ChartPie} label="Context" expanded={open} size={36} iconSize={18} onClick={toggle}
        title={near ? nearLimitSaid(pct!) : 'Context and cost'} className={cx(near && 'text-warn')} />
    )}>
      {() => (
        <div role="dialog" aria-label="Context" className="flex flex-col gap-s p-m">
          <p data-type="label-m" className="text-on-surface">Context</p>
          {pct !== undefined && <Meter label="Context used" pct={pct} tone={tone} />}
          {/* Each reading under its label, every state in words: the bar's colour adds nothing the
              text does not say (near the limit is said, not only tinted). */}
          <dl className="grid grid-cols-[auto_1fr] gap-x-m gap-y-xs" data-type="body-s">
            <dt className="text-on-surface-low">Context window</dt>
            <dd className={near ? 'text-warn' : 'text-on-surface-var'}>
              {pct !== undefined ? `${readingSaid(pct, windowTokens)}${near ? ', near the limit' : ''}` : unmeasuredSaid(windowTokens)}
            </dd>
            <dt className="text-on-surface-low">Cost so far</dt>
            <dd className="text-on-surface-var">{costSaid(cost)}</dd>
          </dl>
          {/* What fills it, part by part, as the gateway measured it: the same rows `/context`
              prints. Each measured part offers the action that frees its space. */}
          <section aria-labelledby={`${id}-parts`} className="flex flex-col gap-xs border-t border-outline-variant/30 pt-s">
            <p id={`${id}-parts`} data-type="label-s" className="text-on-surface">What fills it</p>
            <ul className="flex flex-col gap-xs">
              {contextPartRows({ parts, window: windowTokens }).map((row) => {
                const run = row.measured ? actionFor?.(row.action) : undefined
                return (
                  <li key={row.kind} data-part={row.kind} className="flex items-center justify-between gap-s" data-type="body-s">
                    <span className="min-w-0">
                      <span className="text-on-surface">{row.label}</span>
                      <span className="text-on-surface-low">: {row.said}</span>
                    </span>
                    {run && <Button variant="ghost-accent" size="xs" onClick={run}>{row.actionLabel}</Button>}
                  </li>
                )
              })}
            </ul>
          </section>
          {pin && (
            <div className="flex items-center justify-between gap-s border-t border-outline-variant/30 pt-s">
              <span data-type="body-s" className="text-on-surface">Pin to the status line</span>
              <Toggle on={pin.pinned} onChange={pin.onPin} label="Pin to the status line" size="sm" />
            </div>
          )}
        </div>
      )}
    </Popover>
  )
}
