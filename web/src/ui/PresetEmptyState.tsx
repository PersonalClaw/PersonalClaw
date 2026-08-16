import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { TileButton } from './TileButton'
import { fvs } from '../design/fontWeight'

/** One preset offered by a {@link PresetEmptyState}. `prefill` is the consuming
 *  surface's own payload type — the primitive never reads it, it only hands it
 *  back to `onPick`, so each surface keeps its own `Prefill` shape. */
export interface PresetDef<P> {
  /** Stable id — the value a surface puts in the URL so the seeded create flow is deep-linkable. */
  id: string
  icon: LucideIcon
  title: string
  /** The cadence / one-line summary ("Every day · 8:00 AM"). Derive it, never freeze a locale string. */
  summary: string
  description: string
  /** What picking this card seeds the create flow with. Opaque to the primitive. */
  prefill: P
}

/** One preset card: icon, title, cadence/summary line, description — and picking
 *  it hands the caller the preset's `prefill`.
 *
 *  The chrome + button semantics come from {@link TileButton}, so a preset card
 *  inherits the kit's card border/hover and its `focus-visible` ring rather than
 *  growing a second look. The whole card is ONE tab stop and ONE click target:
 *  everything inside it is text and icons, never another control, because a
 *  button that contains a button is `nested-interactive` (axe, serious) and tells
 *  assistive tech about one thing while handing it two.
 *
 *  `ariaLabel` is passed deliberately: a `TileButton` takes its accessible name
 *  from its content, which here is three lines of prose (title + cadence +
 *  description). The name a user needs is what the card WILL DO. */
export function PresetCard<P>({ icon: Icon, title, summary, description, prefill, onPick }: {
  icon: LucideIcon
  title: string
  /** The cadence/summary line, shown under the title in the accent tone. */
  summary: string
  description: string
  /** Handed back verbatim to `onPick`. */
  prefill: P
  onPick: (prefill: P) => void
}) {
  return (
    <TileButton
      ariaLabel={`${title} — ${summary}`}
      onClick={() => onPick(prefill)}
      className="h-full gap-s p-l"
    >
      <span
        className="mb-1 inline-flex size-9 items-center justify-center rounded-lg"
        style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}
      >
        <Icon size={18} className="text-primary" aria-hidden />
      </span>
      <span className="text-on-surface text-[0.9375rem]" style={fvs(550)}>{title}</span>
      <span className="text-primary text-[0.8125rem]">{summary}</span>
      <span className="text-on-surface-low text-[0.8125rem] leading-snug">{description}</span>
    </TileButton>
  )
}

/** The preset-first empty state: a headline, a hint, a grid of {@link PresetCard}s
 *  that SEED the surface's existing create flow, and a `footer` slot for the
 *  expert blank path.
 *
 *  The tenet this encodes: an empty list is the one moment a newcomer has no model
 *  of what the surface makes, so the surface offers finished examples instead of a
 *  blank form over the whole ontology. Picking one opens the SAME create flow with
 *  values in it — the presets never replace the form, and the blank path stays
 *  exactly where it was (the top-bar action, plus `footer` here). */
export function PresetEmptyState<P>({ title, hint, presets, onPick, footer }: {
  title: string
  /** One line under the headline — what these presets are. */
  hint?: string
  presets: PresetDef<P>[]
  onPick: (prefill: P) => void
  /** The expert escape hatch (a blank-create button) rendered under the grid. */
  footer?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-l py-2xl">
      <div className="text-center">
        <h2 data-type="headline-s" className="text-on-surface">{title}</h2>
        {hint && <p className="mt-1 mx-auto max-w-[520px] text-on-surface-low text-[0.9375rem]">{hint}</p>}
      </div>
      {/* Two columns from `sm` up, one below — four presets read as a block, not a list.
          The width cap matches `EmptyState`'s own `max-w-[420px]` idiom (a className, so
          it routes through Tailwind rather than an inline px style token-lint refuses). */}
      <div className="grid w-full max-w-[640px] gap-s sm:grid-cols-2">
        {presets.map((p) => (
          <PresetCard
            key={p.id}
            icon={p.icon}
            title={p.title}
            summary={p.summary}
            description={p.description}
            prefill={p.prefill}
            onPick={onPick}
          />
        ))}
      </div>
      {footer}
    </div>
  )
}
