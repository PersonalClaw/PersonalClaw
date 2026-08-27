import { useId, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, Plus, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { spring, physics } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { Toggle } from '../../ui/Toggle'
import { Surface } from '../../ui/Surface'
import { FieldHintProvider, FieldLabelProvider, NumberField } from '../../ui/forms'

/** Shared settings-subpage primitives for consistent layout across panels. */

/** The container-surface slab that a run of `Row`/`Field`/`ToggleRow`/`NumberRow` sits on — one
 *  tonal step, one radius, one padding, for the whole `Section > RowGroup > Row` hierarchy.
 *
 *  Measured before this: `rounded-lg bg-surface-container px-4 py-1` appeared **43 times verbatim**
 *  — 42 across `pages/settings/**` and a 43rd mirroring it in `ui/ListScaffold.tsx`'s `FormSkeleton`
 *  — as a bare `<div>` with no other class at any of the 42 settings sites. Four more sites were the
 *  same shape (a group whose only child is a self-padding row) at a different vertical padding:
 *  `GuardrailsPanel:70` (`py-3`, wrapping one `Field`), `AgentDefaultsPanel:165` (`py-2`),
 *  `PacksPanel:262` and `:394` (`py-3`). `GuardrailsPanel` carried both spellings **26 lines apart**
 *  in one file, which is the tightest available proof this was drift rather than intent.
 *
 *  🪤 WHY THE PADDING MOVES TO TOKENS. `px-4 py-1` are Tailwind's own defaults, so they are FROZEN
 *  against the user's density and space-scale sliders (`system.md` trap 3). Measured on
 *  `#/settings/agent`: those groups stayed 16px/4px at comfortable AND dense AND cli AND at
 *  `--space-scale: 1.4`, while the token-spelled sibling in the same subtree moved 24 → 19.2 →
 *  16.32 → 33.6px. `--spacing-l` is `16px * --space-scale` and `--spacing-xs` is `4px * --space-scale`,
 *  so `px-l py-xs` is byte-for-byte the same 16px/4px at default and starts tracking the sliders
 *  everywhere else. 43 of the 47 adopted sites are therefore ZERO-pixel changes; the four near-misses
 *  converge onto the 42-site majority.
 *
 *  NO `pad` / `className` / `tone` PROP, deliberately. All 42 exact sites pass only children, and the
 *  ~38 remaining `py-3` groups in this tree are a genuinely different shape — free-form content
 *  (a paragraph, a `Loading…` line, a flex cluster) where nothing inside pads itself, so 12px is
 *  doing real work there. A variant with no adopter would be speculative API. */
export function RowGroup({ children }: { children: ReactNode }) {
  return <Surface tone="container" radius="lg" className="px-l py-xs">{children}</Surface>
}

/** A settings sub-route's page title, and therefore the TOP-LEVEL heading of that page — an `h1`.
 *  Measured before this: every `#/settings/*` route had **ZERO** `h1`s and its outline began at `h2`
 *  with nothing above it, while `#/settings` itself (`h1: Settings`) and every other destination
 *  (`h1: Tasks`, …) had one. 30 call sites, one per panel.
 *
 *  No `level` prop: the only settings panel also mounted elsewhere is Inbox, and `#/inbox` renders its
 *  OWN copy (`pages/inbox/InboxSettingsPanel.tsx`), which does not use this component — so no caller
 *  needs a lower level, and a prop for a hypothetical one would be speculative.
 *
 *  The tag changes; the size does not — `data-type="title-l"` drives that, so this is pixel-identical. */
export function PanelHeader({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-l">
      <h1 className="text-on-surface" data-type="title-l">{title}</h1>
      {hint && <p className="mt-1 text-on-surface-low text-[0.8125rem]">{hint}</p>}
    </div>
  )
}

export function Section({ title, hint, icon: Icon, iconTone = 'primary', right, children }: {
  /** ReactNode, not string: a count badge belongs INSIDE the heading, where it reads as part of the
   *  section's name rather than as a control parked at the far edge. `ui/PageTitle` already sanctions
   *  exactly that for the page h1 ("lets the title own trailing chrome"). A plain string is still a
   *  ReactNode, so every existing caller is untouched. */
  title?: ReactNode
  /** ReactNode, not string: `DiagnosticsPanel`'s "Live logs" hint carries a live connection dot and
   *  a count, which is why that panel hand-rolled its heading rather than adopt this. */
  hint?: ReactNode
  /** Leading glyph inside the heading — `DesignPanel`'s three control sections each have one. */
  icon?: LucideIcon
  /** Tone for that glyph. `primary` is right where the icon marks a live, primary thing (Design's
   *  three control sections). It is WRONG for a decorative category glyph: coral in this app means
   *  "active / primary", and `ProvidersPanel` has NINE entity glyphs down one page — rendering them
   *  coral would make the accent decorative, which the design system forbids. Default keeps every
   *  existing adopter byte-identical. */
  iconTone?: 'primary' | 'muted'
  /** Trailing slot on the title row (a mode switcher, a log toolbar). Keeps a bespoke header row
   *  from being the reason a panel opts out of the primitive. */
  right?: ReactNode
  children: ReactNode
}) {
  // `h2`, not `h3`: the panel title above is an `h1`, so a level-3 section would skip a level
  // (axe `heading-order`). Size is set by the class, not the tag — pixel-identical.
  //
  // 🪤 The three new slots must not disturb the 23 panels that already use this. Measured while
  // adding them: wrapping the header in a flex row unconditionally and moving the title→hint gap
  // from the h2's `mb-s` to the hint's `mt-0.5` moved `#/settings/chat` by 8% and
  // `#/settings/guardrails` by 13.5%. So the extra markup appears ONLY when its slot is used —
  // no `right`, no wrapper; no `icon`, no flex on the heading — and those two panels went back to
  // 0%. A primitive gaining an option must be inert for everyone who does not pass it.
  const heading = title && (
    <h2 className={`mb-s text-on-surface text-[0.9375rem]${Icon ? ' flex items-center gap-s' : ''}`} style={fvs(600)}>
      {Icon && <Icon size={16} className={`shrink-0 ${iconTone === 'muted' ? 'text-on-surface-low' : 'text-primary'}`} />}
      {title}
    </h2>
  )
  const hintEl = hint && <p className="mb-m text-on-surface-low text-[0.8125rem]">{hint}</p>
  return (
    <section className="mb-2xl">
      {right ? (
        <div className="flex items-start justify-between gap-s">
          <div className="min-w-0">{heading}{hintEl}</div>
          <div className="shrink-0">{right}</div>
        </div>
      ) : (<>{heading}{hintEl}</>)}
      {children}
    </section>
  )
}

/** A labeled row — label/description on the left, control on the right.
 *
 *  🪤 THE CONTROL SHARES THE LABEL'S LINE, NOT THE LABEL+HINT BLOCK'S CENTRE. This was a two-column
 *  flex with `items-center`, which centres the control against the WHOLE left block — so the longer
 *  the hint, the further the control drifted from the thing it belongs to. Measured live on a
 *  `demo-home` gateway across all 34 `#/settings/*` routes, control centre-y minus label centre-y
 *  over the 103 rendered rows in 18 panels:
 *
 *    viewport      rows off by >1px    median      worst
 *    390x844       100 of 103          30.25px     225.25px  durability "Encrypt shards"
 *    834x1112      100 of 103          20.50px      79.00px  documents "Edit documents in place"
 *    1280x900      100 of 103          10.75px      40.00px  documents "Edit documents in place"
 *    1440x1000     100 of 103          10.75px      40.00px  documents "Edit documents in place"
 *
 *  93 of the 103 wrap their hint at 390px, so this was the normal case on a phone, not an edge one.
 *  It was also already shaping product copy: the Evaluations panel trimmed its own hint from 456 to
 *  148 characters to work around the drift rather than touch the primitive.
 *
 *  The fix is a 2x2 grid: label and control share ROW 1 and are both centred in it, and the hint
 *  takes row 2 of the label's column. The control is therefore centred on the LABEL — measured 0.00px
 *  on all 103 rows at all four viewports and all seven control kinds present (switch, button, `a`,
 *  `select`, number, text, and the four rows whose right side is plain text). That is `delta == 0` by
 *  construction at every control height, hint length, viewport and density, rather than "small enough
 *  at the widths we happened to check". `items-center` is safe on the container because row 2's track
 *  is exactly the hint's own height, so centring is a no-op there.
 *
 *  Plain `items-start` was the cheaper alternative and is not a fix: it leaves the control
 *  (controlHeight − lineHeight)/2 BELOW the label's line, which is 10.25px on the 40px controls
 *  measured here and grows with the control. Grid reaches zero for the same markup budget.
 *
 *  What moves: a row whose control is taller than its label+hint block grows by the difference,
 *  because the control no longer overlaps the hint's vertical band — 89 of 103 rows at 390px
 *  (+629.5px over the whole tree, worst single row +16.5px) and 94 of 103 at 1440px (worst +20.5px,
 *  the one 40px `Select`). One row SHRANK 51 → 48px: a hintless switch, where the removed line box
 *  was the only thing making the row taller than its control. Hint wrapping is untouched — the hint's
 *  line count is identical on every one of the 103 rows at every viewport, because `minmax(0,1fr)` on
 *  column 1 carries exactly the `min-w-0` the old left wrapper had. Nothing overflows or clips that
 *  did not already: the three clipping rows on `#/settings/agent` at 390px measure byte-identical
 *  before and after.
 *
 *  DOM order is unchanged — label, hint, control — because the control is placed explicitly at
 *  `col-start-2 row-start-1` instead of by auto-placement, which would have required moving the
 *  control ahead of the hint in the markup. Explicitly-placed items are positioned before
 *  auto-placed ones, so the hint lands on row 2 rather than colliding with the control.
 *
 *  NOT the same shape as the three settings record rows (`DevicesPanel`'s device list,
 *  `GuardrailsPanel`'s autonomy ladder and its `HealthRow`), which spell out this container's old
 *  class string but hold an icon plus two to four sublines rather than one label and one hint — so
 *  "centre the control on the label" is not a well-formed request there. Their right-hand alignment is
 *  a separate list-row question, and the three already disagree with each other (two `items-center`,
 *  one `items-start`). `rowAlignsControlToLabel.test.tsx` ratchets their count at three so a fourth
 *  cannot appear quietly. */
export function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  const hintId = useId()
  // 🪤 A `Row` deliberately does NOT publish a label id — its control names itself (69 hinted rows, and
  // ux-690 recorded the divided-row layout as a distinction, not drift). The hint is independent of
  // that: a control with its own `aria-label` still needs the sentence beside it to be its description,
  // so this provides the hint id without claiming to name anything.
  return (
    <FieldHintProvider value={hint ? hintId : undefined}>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-l border-b border-outline-variant/30 py-3 last:border-0">
        <div className="text-on-surface text-[0.8125rem]">{label}</div>
        {hint && <div id={hintId} className="mt-0.5 text-on-surface-low text-[0.8125rem]">{hint}</div>}
        {/* `flex items-center`, not a plain block: a block slot builds a LINE BOX around an
            inline-level control, so the control sits on the text baseline with the strut's
            descender space below it and ends up low of centre even inside a correctly centred
            grid track. A flex slot has no inline formatting context, so its height IS the
            control's height and the grid centres the real thing. */}
        <div className="col-start-2 row-start-1 flex items-center">{children}</div>
      </div>
    </FieldHintProvider>
  )
}

/** Stacked variant for controls that need full width under their label.
 *
 *  Publishes its label id through `FieldLabelProvider` exactly as `ui/forms`' `Field` does. That is
 *  not decoration: a form-family control (`TextInput`/`TextArea`/`Select`) gets its accessible name by
 *  claiming the surrounding Field's label via `aria-labelledby`, and with no provider it claims
 *  nothing. Measured before this change — six inputs on `#/settings/account`, including "New password"
 *  and "Confirm password", had NO accessible name, because this Field owned the visible label and
 *  published nothing.
 *
 *  This is the same defect the ToolsPage local `Field` had, in a second place: the label is on screen,
 *  so it looks correct, and only assistive tech sees the gap. Publishing here fixes every consumer of
 *  this Field at once rather than asking each call site to remember an `ariaLabel`. */
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  const labelId = useId()
  const hintId = useId()
  // This row already publishes its LABEL through the shared provider, which is what gives its control
  // an accessible name. The hint rides the same mechanism: measured on `#/settings/account`, all six
  // inputs were named and NONE was described, so sentences like "At least 12 characters" and "Leave it
  // empty to keep records unattributed" existed only for sighted users.
  return (
    <FieldLabelProvider value={labelId}>
      <FieldHintProvider value={hint ? hintId : undefined}>
        <div className="border-b border-outline-variant/30 py-3 last:border-0">
          <div id={labelId} className="text-on-surface text-[0.8125rem]">{label}</div>
          {hint && <div id={hintId} className="mt-0.5 mb-2 text-on-surface-low text-[0.8125rem]">{hint}</div>}
          <div className="mt-2">{children}</div>
        </div>
      </FieldHintProvider>
    </FieldLabelProvider>
  )
}

// Toggle now lives in ui/ as the canonical app-wide switch; re-export so existing
// `import { Toggle } from '../settingsUI'` call sites keep working (no dual impl).
export { Toggle } from '../../ui/Toggle'

/** 🔴 `ariaLabel` is REQUIRED, and it is the dimension this group sets ("Scan mode", "Widget
 *  density") — not a value. Measured on the live DOM across four settings routes before it existed:
 *  **34 groups, 126 options, 0 with the dimension in any name and 0 with a pressed state.** The
 *  notification matrix alone renders 26 identical `[Never | Badge | Notify | Digest]` groups, so a
 *  screen-reader user heard 26 indistinguishable sets of four bare buttons with no way to tell which
 *  rule they belonged to or which mode was live (WCAG 4.1.2, level A).
 *
 *  This is the form `SegToggle`, `WidthPill`, `TokenControls` and `HeaderModePill` already ship —
 *  `<dimension>: <value>` plus `aria-pressed` — and it is required for the same reason SegToggle's is:
 *  typecheck stops an unnamed new call site before a rail has to. The VISIBLE label is untouched. */
export function SegPills<T extends string>({ value, onChange, options, ariaLabel }: {
  value: T; onChange: (v: T) => void; options: { key: T; label: string }[]; ariaLabel: string
}) {
  // Per-instance layoutId so the sliding pill in one SegPills can't fly to another.
  const indicatorId = `segpills-${useId()}`
  return (
    <div className="inline-flex rounded-pill bg-surface-container p-0.5">
      {options.map((o) => {
        const on = o.key === value
        return (
          <button key={o.key} type="button" onClick={() => onChange(o.key)}
            aria-label={`${ariaLabel}: ${o.label}`} aria-pressed={on}
            className="relative rounded-pill px-3 h-7 text-[0.8125rem] transition-colors"
            style={{ color: on ? 'var(--color-on-surface)' : 'var(--color-on-surface-low)' }}>
            {/* liquid active pill — slides between options via layoutId instead of
                the highlight blink-jumping (the Segmented pattern, on a settings pill). */}
            {on && <motion.span layoutId={indicatorId} transition={spring.spatialFast}
              className="absolute inset-0 rounded-pill" style={{ background: 'var(--color-surface-highest)' }} />}
            <span className="relative">{o.label}</span>
          </button>
        )
      })}
    </div>
  )
}

/** The shared "your change was saved" confirmation for a settings control.
 *
 *  It is the ONLY confirmation these controls give: a config PATCH has no other success surface, so
 *  a user who cannot see this span has no way to know the setting persisted (WCAG 4.1.3 Status
 *  Messages). The visual "Saved ✓" springs in — a small earned confirmation rather than a flat fade
 *  — and a polite live region carries the same fact to assistive tech.
 *
 *  Two details that are load-bearing, not stylistic:
 *
 *  · The region is ALWAYS MOUNTED and empty at rest. A live region created at the same moment its
 *    content appears is not reliably observed — the same reasoning `ResultAnnouncement` records, and
 *    the reason this is not simply an `aria-live` attribute on the animated span below (which
 *    `AnimatePresence` mounts and unmounts).
 *  · The visual span is `aria-hidden`. The live region already carries the message, so leaving it in
 *    the accessibility tree would announce the confirmation twice and read the "✓" as "check mark".
 *
 *  This is deliberately NOT `ResultAnnouncement`: that component is list-specific
 *  (`count`/`noun`/`active`) and renders "N tasks" / "No matching tasks". Its doc's warning is
 *  against a second copy of the LIST-RESULT region on a page, which is why `AudioRecorder` and
 *  `Onboarding` also carry their own regions for their own messages.
 */
export function SavedToast({ show }: { show: boolean }) {
  return (
    <>
      <span role="status" aria-live="polite" className="sr-only">{show ? 'Saved' : ''}</span>
      <AnimatePresence>
        {show && (
          <motion.span aria-hidden="true" initial={{ opacity: 0, scale: 0.8, y: 2 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.8 }}
            transition={physics.playful} className="text-[0.75rem]" style={{ color: 'var(--color-success)' }}>Saved ✓</motion.span>
        )}
      </AnimatePresence>
    </>
  )
}

/** A labelled config switch that patches one key and flashes its own "Saved ✓".
 *
 *  Five panels had declared this privately — Sources, Legibility, Ambient, Packs and
 *  AgentDefaults — and four of the five were BYTE-IDENTICAL across all 12 lines, down to the
 *  1500ms flash timeout. Their `cfg` prop wore five different names (`SourcesCfg`, `LegibilityCfg`,
 *  `AmbientCfg`, `PacksCfg`, `AgentCfg`) that are each literally `Record<string, unknown>`, so even
 *  the apparent type variation was five aliases for one type.
 *
 *  The fifth (AgentDefaults) adds `danger`: a warning glyph shown only while the switch is ON, for
 *  a setting that loosens a safety default. That is a real distinction, so it lives here as an
 *  opt-in prop rather than being flattened away — every other panel simply omits it.
 *
 *  Owning the flash state here is the point: five copies of "toggle, patch, flash for 1500ms" is
 *  five places for that timing to drift, on rows that sit in the same settings tree and are read
 *  as one family. */
export function ToggleRow({ label, hint, cfg, field, patch, danger }: {
  label: string
  hint?: string
  cfg: Record<string, unknown>
  field: string
  /** `(key, value, onSaved, label)` — the panel's own config PATCH. Typed at its widest shape so a
   *  panel whose callback declares `v: boolean` or a required `cb` still satisfies it.
   *
   *  🪤 THE LABEL IS THE FOURTH ARGUMENT BECAUSE THE FAILURE TOAST NEEDS IT. This row holds both the
   *  control's visible name and its config key, and used to hand the patch only the key — so a
   *  rejected save said "Couldn't save soft_stop_budget_secs" about a control the UI calls "Subagent
   *  timeout". The user has never seen that string anywhere on screen. Optional, so a panel that has
   *  not adopted it still type-checks and still shows the key. */
  patch: (k: string, v: never, cb: () => void, label?: string) => void
  /** Show a warning glyph while ON — for a switch that relaxes a safety default. */
  danger?: boolean
}) {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  const on = Boolean(cfg[field])
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        {danger && on && <AlertTriangle size={14} className="text-warn" />}
        <Toggle on={on} onChange={(v) => patch(field, v as never, flash, label)} label={label} />
      </div>
    </Row>
  )
}

/** A labelled numeric config field that patches one key and flashes its own "Saved ✓" — the
 *  `ToggleRow` sibling for a clamped number.
 *
 *  Sources and Ambient had declared this privately, BYTE-IDENTICAL, each alongside its own copy of
 *  the same `num()` coercion helper. Both are folded in here.
 *
 *  SCOPE — this is the `cfg`/`field`/`patch` contract only, NOT every `NumberRow` in settings.
 *  Three other panels declare a `NumberRow` on a genuinely different contract: Guardrails,
 *  Durability and Chat take `{value, onCommit}` and are TOLD what to save, with the flash state
 *  owned by the panel (and Guardrails' `onSave` returns a Promise it flashes off). Those are told
 *  a value; this one patches by key. Picking a winner between the two shapes is a judgement about
 *  which contract the settings panels should standardise on, so it stays an open question rather
 *  than something a dedup decides quietly. AgentDefaults' `NumberRow` is cfg-driven too but adds
 *  `suffix` + optional min/max/step and uses `Row` rather than `Field`; it is left alone here for
 *  the same reason — its shape is a superset, and folding it in would mean changing its layout. */
export function NumberRow({ label, hint, cfg, field, min, max, step = 1, patch }: {
  label: string
  hint?: string
  cfg: Record<string, unknown>
  field: string
  min: number
  max: number
  /** Stepper increment. Defaults to 1, so every existing adopter is byte-identical — this exists
   *  because a FRACTIONAL field cannot use this row otherwise. `evals.judge_agreement_floor` is a
   *  rate in 0…1 whose default is 0.6: at step 1 the only reachable values are 0 and 1, so the
   *  control could not express the value it was displaying. That is the same class of defect as a
   *  min/max that disagrees with the backend allowlist — an offer the save path refuses.
   *
   *  Deliberately NOT the `suffix` prop `AgentDefaultsPanel`'s private NumberRow also carries: that
   *  one uses `Row` rather than `Field`, so folding it in here would change this row's layout for
   *  its two existing adopters. The step is layout-neutral; the suffix is not. */
  step?: number
  /** `(key, value, onSaved, label)` — the panel's own config PATCH, typed at its widest shape. The
   *  label travels so a rejected save can name the control rather than its config key; see
   *  `ToggleRow` for the measurement. */
  patch: (k: string, v: never, cb: () => void, label?: string) => void
}) {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  // A key the backend has not written yet reads as NaN through Number(); fall back to `min` so the
  // stepper starts at a legal value instead of showing NaN.
  const raw = Number(cfg[field])
  const value = Number.isFinite(raw) ? raw : min
  return (
    <Field label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <NumberField value={value} min={min} max={max} step={step} onChange={(n) => patch(field, n as never, flash, label)} ariaLabel={label} />
        <SavedToast show={saved} />
      </div>
    </Field>
  )
}

/** A labelled list-of-strings config field — chips you can remove, one input that appends. The
 *  `ToggleRow`/`NumberRow` sibling for `_EDITABLE_CONFIG`'s `str_list` type.
 *
 *  Declared HERE rather than a second time in a second panel: `AgentDefaultsPanel` had the only
 *  copy, module-private, and `panelFieldNames.test.tsx` already anticipated "a second call site".
 *  Every edit commits the WHOLE list — the PATCH allowlist takes a `str_list`, not a delta — so a
 *  removed chip and an added one travel the same way and neither can half-apply.
 *
 *  `placeholder` is a prop because the add input is the only vendor-specific pixel: "Add path…"
 *  and "Add host…" are the same control over different nouns. The `aria-label` is NOT a prop —
 *  it derives from `label`, so a raw input inside a `Field` (which cannot claim the Field's
 *  published label; only the form-family components read `FieldLabelCtx`) still names itself
 *  correctly at every call site. */
export function StrListField({ label, hint, cfg, field, patch, placeholder = 'Add…' }: {
  label: string
  hint?: string
  cfg: Record<string, unknown>
  field: string
  /** `(key, value, onSaved, label)` — the panel's own config PATCH, typed at its widest shape. */
  patch: (k: string, v: never, cb: () => void, label?: string) => void
  placeholder?: string
}) {
  const [saved, setSaved] = useState(false)
  const flash = () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
  const list = Array.isArray(cfg[field]) ? (cfg[field] as string[]) : []
  const [adding, setAdding] = useState('')
  const commit = (next: string[]) => patch(field, next as never, flash, label)
  const add = () => { commit([...list, adding.trim()]); setAdding('') }
  return (
    <Field label={label} hint={hint}>
      <div className="flex flex-wrap items-center gap-1.5">
        {list.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2.5 py-1 text-on-surface text-[0.75rem] font-mono">
            {v}
            <button type="button" onClick={() => commit(list.filter((x) => x !== v))} aria-label={`Remove ${v}`} className="text-on-surface-low hover:text-on-surface"><X size={12} /></button>
          </span>
        ))}
        {/* A RAW input inside this module's Field cannot claim the Field's published label — only the
            form-family components read FieldLabelCtx. So it names itself, from `label`, which keeps
            it correct across every call site. */}
        <input value={adding} onChange={(e) => setAdding(e.target.value)} placeholder={placeholder}
          aria-label={`Add to ${label.toLowerCase()}`}
          onKeyDown={(e) => { if (e.key === 'Enter' && adding.trim()) add() }}
          className="h-8 w-40 rounded-md bg-surface-high px-2 text-[0.75rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        {adding.trim() && (
          <SquareIconButton icon={Plus} iconSize={15} label={`Add ${label.toLowerCase()}`} onClick={add} />
        )}
        <SavedToast show={saved} />
      </div>
    </Field>
  )
}
