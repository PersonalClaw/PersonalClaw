/** The bundled core genui component set (AMBIENT-SURFACES §5.1).
 *
 *  Every component is token-driven (the tokenLint ratchet applies — no raw hex,
 *  no inline px) and composes the existing `ui/*` primitives / design tokens
 *  rather than hand-rolling styled chrome. Each is registered with
 *  `defineComponent` so it appears in `library.prompt()` and is renderable inside
 *  a `<widget kind="genui">` block. Keep the set SMALL — every component costs
 *  prompt space. Charts follow the dataviz conventions (one series = one token).
 *
 *  These are pure presentational components: AS-4 is the render core. Interactive
 *  action-bearing components (Form/Input/Button with dual-payload actions) land in
 *  AS-6 on top of this registry. */
import type { ReactNode } from 'react'
import { Surface } from '../Surface'
import { fvs } from '../../design/fontWeight'
import { cx } from '../cx'
import { defineComponent, type GenUiRenderProps } from './registry'

// ── helpers ──────────────────────────────────────────────────────────────────

const s = (v: unknown, fallback = ''): string => (v == null ? fallback : String(v))
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])

/** Feedback tone → token classes. A tone the model didn't recognize falls back to
 *  the neutral surface treatment (never a null hole). */
const TONES: Record<string, string> = {
  info: 'bg-primary/10 text-primary',
  ok: 'bg-primary/10 text-on-surface',
  warn: 'text-warn',
  danger: 'text-danger',
  neutral: 'bg-surface-high text-on-surface-var',
}
const toneClass = (t: unknown): string => TONES[s(t, 'neutral')] || TONES.neutral

// ── Layout ─────────────────────────────────────────────────────────────────

const GAPS: Record<string, string> = { s: 'gap-s', m: 'gap-m', l: 'gap-l' }

/** Vertical (or horizontal) stack of child components. */
function Stack({ args, children }: GenUiRenderProps) {
  const gap = GAPS[s(args.gap, 'm')] || GAPS.m
  const row = s(args.direction) === 'row'
  return <div className={cx('flex', row ? 'flex-row flex-wrap items-start' : 'flex-col', gap)}>{children.body}</div>
}

/** A titled tonal card wrapping child components. */
function Card({ args, children }: GenUiRenderProps) {
  return (
    <Surface tone="low" radius="xl" className="p-l">
      {args.title != null && (
        <div className="mb-s text-on-surface" style={fvs(550)}>{s(args.title)}</div>
      )}
      <div className="flex flex-col gap-s">{children.body}</div>
    </Surface>
  )
}

// ── Data ─────────────────────────────────────────────────────────────────────

/** A single metric: label, big value, optional signed delta. */
function StatTile({ args }: GenUiRenderProps) {
  const delta = typeof args.delta === 'number' ? args.delta : undefined
  const deltaClass = delta === undefined ? '' : delta >= 0 ? 'text-primary' : 'text-danger'
  return (
    <Surface tone="high" radius="lg" className="px-l py-m">
      <div className="text-on-surface-low text-[0.8125rem]">{s(args.label)}</div>
      <div className="mt-0.5 flex items-baseline gap-s">
        <span className="text-on-surface text-[1.375rem]" style={fvs(600)}>{s(args.value)}</span>
        {delta !== undefined && (
          <span className={cx('text-[0.8125rem]', deltaClass)} style={fvs(500)}>
            {delta >= 0 ? '+' : ''}{delta}%
          </span>
        )}
      </div>
    </Surface>
  )
}

/** A bordered data table. `columns: string[]` (header), `rows: rows` (body). */
function Table({ args }: GenUiRenderProps) {
  const columns = arr(args.columns).map((c) => s(c))
  const rows = arr(args.rows).map((r) => arr(r))
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[0.8125rem]">
        {columns.length > 0 && (
          <thead>
            <tr>
              {columns.map((c, i) => (
                <th key={i} className="border-b border-outline-variant/50 bg-surface-high px-m py-2 text-left text-on-surface-var" style={fvs(500)}>{c}</th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td key={ci} className="border-b border-outline-variant/30 px-m py-2 text-on-surface">{s(cell)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** A simple bulleted list of strings. */
function List({ args }: GenUiRenderProps) {
  const items = arr(args.items).map((i) => s(i))
  return (
    <ul className="my-1 list-disc space-y-1 pl-7 marker:text-on-surface-low">
      {items.map((it, i) => (
        <li key={i} className="text-on-surface text-[0.9375rem] leading-relaxed">{it}</li>
      ))}
    </ul>
  )
}

// ── Charts ─────────────────────────────────────────────────────────────────

/** A token-driven bar chart. `data: number[]`, optional matching `labels: string[]`.
 *  Bars are sized by percentage of the max (no px), painted with the primary token —
 *  one series, one token, per the dataviz conventions. */
function Bar({ args }: GenUiRenderProps) {
  const data = arr(args.data).map((n) => (typeof n === 'number' ? n : Number(n) || 0))
  const labels = arr(args.labels).map((l) => s(l))
  const max = data.reduce((m, n) => Math.max(m, n), 0) || 1
  return (
    <div className="flex h-40 items-end gap-s">
      {data.map((n, i) => (
        <div key={i} className="flex min-w-0 flex-1 flex-col items-center gap-1">
          <div className="flex w-full flex-1 items-end">
            <div className="w-full rounded-t bg-primary" style={{ height: `${Math.max(2, (n / max) * 100)}%` }} title={String(n)} />
          </div>
          {labels[i] != null && <span className="truncate text-on-surface-low text-[0.75rem]">{labels[i]}</span>}
        </div>
      ))}
    </div>
  )
}

// ── Feedback ─────────────────────────────────────────────────────────────────

/** A tinted callout band. `tone: info|ok|warn|danger|neutral`, `text`. */
function Callout({ args }: GenUiRenderProps) {
  return (
    <div className={cx('rounded-lg px-3 py-2 text-[0.8125rem]', toneClass(args.tone))}>
      {s(args.text)}
    </div>
  )
}

/** A small status chip. `text`, optional `tone`. */
function Badge({ args }: GenUiRenderProps) {
  return (
    <span className={cx('inline-flex w-fit items-center rounded-pill px-2.5 py-0.5 text-[0.75rem]', toneClass(args.tone))} style={fvs(500)}>
      {s(args.text)}
    </span>
  )
}

/** A determinate progress bar. `value` is 0..100; optional `label`. */
function ProgressBar({ args }: GenUiRenderProps) {
  const raw = typeof args.value === 'number' ? args.value : Number(args.value) || 0
  const pct = Math.min(100, Math.max(0, raw))
  return (
    <div className="flex flex-col gap-1">
      {args.label != null && <span className="text-on-surface-low text-[0.75rem]">{s(args.label)}</span>}
      <div className="h-2 w-full overflow-hidden rounded-pill bg-surface-high">
        <div className="h-full rounded-pill bg-primary" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// ── registration ───────────────────────────────────────────────────────────

let _registered = false

/** Idempotent — register the bundled core set. Called once at app bootstrap
 *  (main.tsx) AND lazily by the renderer so a widget renders even if bootstrap
 *  ordering changes. */
export function registerCoreGenUiComponents(): void {
  if (_registered) return
  _registered = true

  defineComponent({
    name: 'Stack', group: 'Layout', description: 'Vertical/horizontal stack of children',
    args: [
      { key: 'body', type: 'refs', required: true, note: 'child line ids, in order' },
      { key: 'gap', type: 'string', note: 's | m | l' },
      { key: 'direction', type: 'string', note: 'column (default) | row' },
    ],
    component: Stack,
  })
  defineComponent({
    name: 'Card', group: 'Layout', description: 'Titled card wrapping children',
    args: [
      { key: 'body', type: 'refs', required: true, note: 'child line ids' },
      { key: 'title', type: 'string' },
    ],
    component: Card,
  })
  defineComponent({
    name: 'StatTile', group: 'Data', description: 'One metric with optional % delta',
    args: [
      { key: 'label', type: 'string', required: true },
      { key: 'value', type: 'string', required: true },
      { key: 'delta', type: 'number', note: 'signed percent change' },
    ],
    component: StatTile,
  })
  defineComponent({
    name: 'Table', group: 'Data', description: 'Header row + body rows',
    args: [
      { key: 'columns', type: 'string[]', required: true },
      { key: 'rows', type: 'rows', required: true, note: 'array of row arrays' },
    ],
    component: Table,
  })
  defineComponent({
    name: 'List', group: 'Data', description: 'Bulleted list of strings',
    args: [{ key: 'items', type: 'string[]', required: true }],
    component: List,
  })
  defineComponent({
    name: 'Bar', group: 'Charts', description: 'Bar chart of one numeric series',
    args: [
      { key: 'data', type: 'number[]', required: true },
      { key: 'labels', type: 'string[]', note: 'per-bar labels' },
    ],
    component: Bar,
  })
  defineComponent({
    name: 'Callout', group: 'Feedback', description: 'Tinted note band',
    args: [
      { key: 'text', type: 'string', required: true },
      { key: 'tone', type: 'string', note: 'info | ok | warn | danger | neutral' },
    ],
    component: Callout,
  })
  defineComponent({
    name: 'Badge', group: 'Feedback', description: 'Small status chip',
    args: [
      { key: 'text', type: 'string', required: true },
      { key: 'tone', type: 'string' },
    ],
    component: Badge,
  })
  defineComponent({
    name: 'ProgressBar', group: 'Feedback', description: 'Determinate 0..100 progress bar',
    args: [
      { key: 'value', type: 'number', required: true },
      { key: 'label', type: 'string' },
    ],
    component: ProgressBar,
  })
}

/** Re-exported so tests can render a component in isolation. */
export type { ReactNode }
