import { type ReactNode, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, Loader2, type LucideIcon } from 'lucide-react'
import { Toggle } from '../../ui/Toggle'
import { spring, expr } from '../../design/motion'

/** A coarse weight hint kept on each widget for future tuning. The home page lays
 *  cards out as a fixed-width masonry (each card sizes to its own content), so this
 *  no longer drives a column span — card height is intrinsic to its content. */
export type BentoSize = 'sm' | 'md' | 'lg' | 'wide' | 'tall'

/** Highlight every occurrence of `query` inside `text` with a marked span — used
 *  so a settings search that matches a value surfaced ON a card visibly marks it. */
export function Highlight({ text, query }: { text: string; query: string }) {
  const q = query.trim()
  if (!q) return <>{text}</>
  const lower = text.toLowerCase()
  const ql = q.toLowerCase()
  const out: ReactNode[] = []
  let i = 0
  let n = 0
  while (i < text.length) {
    const hit = lower.indexOf(ql, i)
    if (hit < 0) { out.push(text.slice(i)); break }
    if (hit > i) out.push(text.slice(i, hit))
    out.push(
      <mark key={n++} className="rounded-[3px] bg-primary/30 px-0.5 text-on-surface" style={{ color: 'inherit' }}>
        {text.slice(hit, hit + q.length)}
      </mark>,
    )
    i = hit + q.length
  }
  return <>{out}</>
}

/** The bento card shell. The card deep-links into its subpage, but it also hosts
 *  inline interactive controls (toggles, selects) — and you can't nest interactive
 *  elements inside a <button>. So the navigation affordance is a full-card overlay
 *  <button> sitting BEHIND the content; the content layer is click-through
 *  (`pointer-events-none`) except for the controls it explicitly re-enables
 *  (`pointer-events-auto`). Clicking empty card space navigates; clicking a control
 *  operates it (the control stops propagation). The title + arrow act as the
 *  explicit "open" affordance.
 *
 *  `loading` shows a skeleton mirroring the card shape so it paints instantly. */
export function BentoCard({ icon: Icon, title, query, onClick, loading, accent, footer, rows, children }: {
  icon: LucideIcon
  title: string
  query?: string
  onClick: () => void
  loading?: boolean
  /** Tint the icon chip (defaults to primary). */
  accent?: string
  /** Optional muted footer line (e.g. a hint or secondary count). */
  footer?: ReactNode
  /** Skeleton hint — how many body lines to shimmer while loading (default 2). */
  rows?: number
  children?: ReactNode
}) {
  const tint = accent || 'var(--color-primary)'
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={spring.spatialDefault}
      // physical liftable bento card: rises + gains shadow on hover (depth via
      // expr), consistent with ListRow/TaskCard/AppCard/Surface. NO whileTap here —
      // the card hosts inline controls (toggles/selects) over a full-card nav
      // overlay, and a whole-card press-squish would fire when tapping those.
      whileHover={{ y: -expr(4, 0.3), boxShadow: 'var(--shadow-lift)' }}
      className="group relative flex w-full flex-col rounded-xl bg-surface-container p-4 transition-colors focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary/50 hover:bg-surface-high"
    >
      {/* Behind-content nav overlay: the whole card opens the subpage. */}
      <button type="button" onClick={onClick} aria-label={`Open ${title} settings`}
        className="absolute inset-0 z-0 rounded-xl outline-none" />
      {/* Content sits above the overlay but is click-through except for controls. */}
      <div className="pointer-events-none relative z-10 flex min-h-0 flex-col">
        <div className="mb-3 flex items-center gap-2">
          <span className="grid size-7 shrink-0 place-items-center rounded-md" style={{ background: `color-mix(in srgb, ${tint} 16%, transparent)`, color: tint }}>
            <Icon size={15} />
          </span>
          <span className="flex-1 truncate text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 600' }}>
            {query ? <Highlight text={title} query={query} /> : title}
          </span>
          <ArrowRight size={14} className="shrink-0 text-on-surface-low transition-transform group-hover:translate-x-0.5" />
        </div>
        {loading
          ? <CardSkeleton rows={rows ?? 2} />
          : <div className="flex min-h-0 flex-1 flex-col">{children}</div>}
        {footer && <div className="mt-2 text-on-surface-low text-[0.72rem]">{footer}</div>}
      </div>
    </motion.div>
  )
}

/** Per-card loading skeleton — a shimmer block per body line so opening the home
 *  paints the card's shape immediately, then fills in once its data lands. */
export function CardSkeleton({ rows = 2 }: { rows?: number }) {
  const widths = ['w-2/3', 'w-1/2', 'w-3/5', 'w-2/5', 'w-3/4']
  return (
    <div className="flex-1 space-y-2" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className={`h-3 ${widths[i % widths.length]} animate-pulse rounded bg-surface-high/70`} />
      ))}
    </div>
  )
}

// ── Inline controls (live on the card; operate without opening the subpage) ──────
// Each stops click propagation so it doesn't trigger the card's nav overlay, and
// shows a brief spinner while its async mutation is in flight.

/** A labelled inline switch. `onToggle` may be async; a spinner shows while it runs. */
export function Switch({ on, onToggle, label, disabled }: {
  on: boolean; onToggle: (next: boolean) => void | Promise<void>; label?: string; disabled?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const run = async () => {
    if (busy || disabled) return
    setBusy(true)
    try { await onToggle(!on) } finally { setBusy(false) }
  }
  // Sits inside the card's click-through content layer over a full-card nav overlay:
  // re-enable pointer events and stop the click from reaching the overlay. The shared
  // Toggle renders the track+knob; a spinner overlays it while the async save runs.
  return (
    <span className="pointer-events-auto relative inline-flex" onClickCapture={(e) => e.stopPropagation()}>
      <Toggle on={on} onChange={run} label={label} disabled={disabled || busy} size="sm" />
      {busy && (
        <span className="pointer-events-none absolute inset-0 grid place-items-center">
          <Loader2 size={9} className="animate-spin text-on-surface-low" />
        </span>
      )}
    </span>
  )
}

/** A compact segmented toggle for a small set of choices. */
export function SegToggle<T extends string>({ value, options, onPick }: {
  value: T; options: { key: T; label: string }[]; onPick: (v: T) => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const pick = async (e: React.MouseEvent, k: T) => {
    e.stopPropagation()
    if (busy || k === value) return
    setBusy(true)
    try { await onPick(k) } finally { setBusy(false) }
  }
  return (
    <div className="pointer-events-auto inline-flex rounded-pill bg-surface-high p-0.5" style={{ opacity: busy ? 0.7 : 1 }}>
      {options.map((o) => (
        <button key={o.key} type="button" onClick={(e) => pick(e, o.key)}
          className="rounded-pill px-2 h-[22px] text-[0.7rem] transition-colors"
          style={o.key === value ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** A small native select for longer option lists (channels, models, agents). */
export function InlineSelect({ value, options, onPick, ariaLabel }: {
  value: string; options: { value: string; label: string }[]; onPick: (v: string) => void | Promise<void>; ariaLabel?: string
}) {
  const [busy, setBusy] = useState(false)
  return (
    <select value={value} aria-label={ariaLabel} disabled={busy}
      onClick={(e) => e.stopPropagation()}
      onChange={async (e) => { e.stopPropagation(); setBusy(true); try { await onPick(e.target.value) } finally { setBusy(false) } }}
      className="pointer-events-auto max-w-[10rem] truncate rounded-md bg-surface-high px-2 h-7 text-[0.75rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark] disabled:opacity-60">
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}

/** A big headline number with a caption — the focal stat on a card. */
export function BigStat({ value, caption, tone }: { value: ReactNode; caption: ReactNode; tone?: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="tabular-nums text-on-surface text-[1.75rem] leading-none" style={{ fontVariationSettings: '"wght" 600', color: tone }}>{value}</span>
      <span className="text-on-surface-low text-[0.8rem]">{caption}</span>
    </div>
  )
}

/** A compact key→value row list. Values can be highlighted against the query, or
 *  be an interactive control (set `control` so the value isn't truncated). */
export function KVList({ rows, query }: { rows: { k: string; v: ReactNode; vText?: string; mono?: boolean; control?: boolean }[]; query?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      {rows.map((r, i) => (
        <div key={i} className="flex min-h-[1.75rem] items-center justify-between gap-2 text-[0.8rem]">
          <span className="shrink-0 text-on-surface-low">{query ? <Highlight text={r.k} query={query} /> : r.k}</span>
          <span className={`min-w-0 text-right text-on-surface ${r.control ? 'shrink-0' : 'truncate'} ${r.mono ? 'font-mono text-[0.72rem]' : 'tabular-nums'}`}>
            {query && r.vText !== undefined ? <Highlight text={r.vText} query={query} /> : r.v}
          </span>
        </div>
      ))}
    </div>
  )
}

/** A small status pill (on/off, ok/warn). */
export function StatusPill({ label, tone, query }: { label: string; tone?: 'ok' | 'warn' | 'muted' | 'primary'; query?: string }) {
  const map = {
    ok: { bg: 'color-mix(in srgb, var(--color-ok) 16%, transparent)', fg: 'var(--color-ok)' },
    warn: { bg: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', fg: 'var(--color-warn)' },
    primary: { bg: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', fg: 'var(--color-primary)' },
    muted: { bg: 'var(--color-surface-high)', fg: 'var(--color-on-surface-low)' },
  }[tone || 'muted']
  return (
    <span className="inline-flex items-center gap-1 rounded-pill px-2 h-[22px] text-[0.72rem]" style={{ background: map.bg, color: map.fg }}>
      {query ? <Highlight text={label} query={query} /> : label}
    </span>
  )
}

/** A wrapped row of tag/value chips (e.g. keywords, runtimes, use-cases). */
export function ChipRow({ chips, query }: { chips: { label: string; tone?: 'ok' | 'warn' | 'muted' | 'primary' }[]; query?: string }) {
  return (
    <div className="flex flex-wrap gap-1">
      {chips.map((c, i) => <StatusPill key={i} label={c.label} tone={c.tone} query={query} />)}
    </div>
  )
}
