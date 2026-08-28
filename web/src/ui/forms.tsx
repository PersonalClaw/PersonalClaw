import { createContext, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { cx } from './cx'

// ── Shared form-field family (design-system consistency, plan S2/Owner task 2) ─
// The canonical form primitives, extracted from pages/tasks/formControls.tsx so
// they live under ui/ alongside the other primitives (Button, Modal, Segmented).
// This is the SAME shape the app already uses — a byte-identical relocation, not
// a redesign — now with a single home. The task-specific editors that were once
// co-located (DependencyEditor/ChecklistEditor/NotesEditor) stay in
// pages/tasks/formControls.tsx because they depend on task domain code (dag,
// taskMeta, TaskNote); a ui/ → pages/ dependency would be backwards.
//
// A Field publishes the id of its (visible, uppercase) label so the single
// control it wraps can point back to it with aria-labelledby — turning the
// sighted-only label into a real accessible name for screen readers, with zero
// call-site changes. Controls fall back to this when they have no id/name of
// their own. Only the FIRST control in a Field should claim it (multi-control
// Fields like Variables keep their own per-input aria-labels).
const FieldLabelCtx = createContext<string | undefined>(undefined)
export function useFieldLabelId() { return useContext(FieldLabelCtx) }
/** Publish a label id to the controls inside, so a NON-`Field` wrapper can still give them an
 *  accessible name.
 *
 *  Only the reader (`useFieldLabelId`) was exported before, which made this contract one-directional:
 *  any other label+control wrapper — `settingsUI`'s settings-row `Field`, for one — silently produced
 *  unnamed controls, because a control claims its name via `aria-labelledby` and there was nothing to
 *  claim. Exporting the provider is what lets a second layout own the label without owning the a11y
 *  bug. */
export const FieldLabelProvider = FieldLabelCtx.Provider

/** And a Field publishes the id of its visible HINT, so the control can point at it with
 *  `aria-describedby`.
 *
 *  🔴 Measured on `#/settings/account`: all six inputs were correctly NAMED (the label contract above
 *  works) and not one had `aria-describedby` — so every hint was sighted-only. That includes a
 *  CONSTRAINT ("At least 12 characters") and a consequence ("Leave it empty to keep records
 *  unattributed"): a screen-reader user heard "Username, edit text" and none of the rule they were
 *  expected to follow. **271** hinted publishers render today — **236** DIRECT call sites (Field 120,
 *  settingsUI's Row 77, NumberRow 39) plus **35** that arrive through five local wrappers which forward
 *  a hint into one of those three (ToggleRow 25, EnumRow 3, CheckList 3, TextRow 2, StrListField 2).
 *  Recounted **2026-08-27** with the depth-tracking scan `fieldHintCounts.test.ts` runs; the earlier
 *  196/99/69/28 reading is stale, and its "69" is the number of hinted `Row` CALL SITES, not the
 *  number of switches, which is a distinction the Q13/BE-8 queue entry lost.
 *
 *  🪤 THESE NUMBERS ROT IN A DAY, so treat them as a dated observation, not a fact. The previous line
 *  here read 260/229 (Field 118, NumberRow 34) and was recounted **the day before** — one tick of
 *  ordinary feature work moved Field +2 and NumberRow +5. `fieldHintCounts.test.ts` therefore asserts
 *  a FLOOR, not an equality: an exact pin would red the gate on every new hinted row, and a rail that
 *  reds on healthy growth is a rail someone weakens. The floor catches the failure that matters — the
 *  scan breaking, or publishers disappearing — and the test names the command to refresh the prose.
 *
 *  None of the 271 has to change — the id is published here and claimed by the same controls that
 *  already claim the label. axe cannot see this: an unassociated paragraph is valid HTML. */
const FieldHintCtx = createContext<string | undefined>(undefined)
export function useFieldHintId() { return useContext(FieldHintCtx) }
export const FieldHintProvider = FieldHintCtx.Provider

/** Field wrapper — label row (optional right slot for a SoonTag) + control.
 *  The label carries a stable id and is exposed via context so the wrapped
 *  control associates with it for accessibility. */
/** The one-line failure message under a control or beside an action — the shape 30 call sites
 *  had hand-rolled as `<p className="text-danger text-[0.8125rem]">{err}</p>`.
 *
 *  Byte-identical on screen; what it adds is `role="alert"`. Measured on `#/settings/design`
 *  with `POST /api/themes` forced to 500: the failure text appeared on screen and the page
 *  held **zero** live regions, so a screen-reader user pressed Save, watched the button return
 *  to idle, and was told nothing. The app's other two failure surfaces — `InlineError` (the
 *  danger band) and `LoadError` — both announce; this line was the family's silent member.
 *
 *  `InlineError` stays the choice for a banner ABOVE a body (a tinted, dismissible strip).
 *  Reach for FieldError for the terse line that belongs to one control or one action. */
export function FieldError({ children, className }: {
  children: ReactNode
  /** Per-site spacing only (e.g. `mt-2`, `mb-m`, `shrink-0`); the tone and size are fixed. */
  className?: string
}) {
  return <p role="alert" className={cx('text-danger text-[0.8125rem]', className)}>{children}</p>
}

export function Field({ label, hint, right, children }: { label: string; hint?: string; right?: ReactNode; children: ReactNode }) {
  const labelId = useId()
  const hintId = useId()
  return (
    <FieldLabelCtx.Provider value={labelId}>
      {/* The hint id is published only when there IS a hint — an `aria-describedby` pointing at a
          missing element is worse than none, because assistive tech resolves it to nothing while the
          attribute claims a description exists. */}
      <FieldHintCtx.Provider value={hint ? hintId : undefined}>
      <div>
        <div className="mb-1.5 flex items-center gap-s">
          <span id={labelId} className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">{label}</span>
          {right}
        </div>
        {children}
        {hint && <p id={hintId} className="mt-1 text-on-surface-low text-[0.75rem]">{hint}</p>}
      </div>
      </FieldHintCtx.Provider>
    </FieldLabelCtx.Provider>
  )
}

// ── The standard-field size/surface scale ──────────────────────────────────
// The single canonical shape for a labeled standard field, expressed as a small
// principled scale rather than the h-7…h-11 / four-surface / 15-text-size spread
// that pages hand-rolled. This is *codification, not redesign*: every step below
// is a shape the app already ships, and every size uses a DESIGN.md-blessed type
// step (0.8125rem / 0.9375rem — 0.875rem/14px is 1px off the ramp and is drift we
// normalize onto `sm`/`md`, never a tier we bless).
//
//  • size   sm → h-8  (dense in-panel fields)      — pairs with Button/Segmented sm
//           md → h-9  (default rows, side panels)
//           lg → h-10 (page forms; the DESIGN.md canonical Input)   ← DEFAULT
//  • surface container (sits in a panel)            ← DEFAULT
//           high      (sits on a panel / toolbar)
//           base      (sits on the raw surface)
//
// Defaults are lg/container so the fields already adopting this family stay
// byte-identical. `INPUT_BASE` is TextInput's invariant chrome; the size/surface
// tables are the only axes that vary. TextArea/DateInput/Select will join this
// scale as adopters need them. Locked by forms.test.tsx.
type FieldSize = 'sm' | 'md' | 'lg'
type FieldSurface = 'container' | 'high' | 'base'

const FIELD_SIZE: Record<FieldSize, string> = {
  sm: 'h-8 text-[0.8125rem]',
  md: 'h-9 text-[0.8125rem]',
  lg: 'h-10 text-[0.9375rem]',
}
const FIELD_SURFACE: Record<FieldSurface, string> = {
  container: 'bg-surface-container',
  high: 'bg-surface-high',
  base: 'bg-surface',
}
// Shared invariant chrome. Rounding, focus ring, text/placeholder tone, no
// outline — everything a standard field has regardless of size or surface.
// Inline padding is NOT baked in here: it is `px-m` normally, or `pl-9 pr-m`
// when a leadingIcon is present (pl-9 clears the fixed left-3 icon; pr-m keeps
// the canonical right pad). Applied conditionally so px-m and pl-9 never both
// emit — a `padding-inline` + `padding-left` cascade race — the same split the
// prior leading-icon primitive used.
const INPUT_BASE = 'w-full rounded-md text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'

/** The one standard text field. Chrome is fixed; the only axes are `size`
 *  (sm/md/lg) and `surface` (container/high/base) — the family variants the app's
 *  height/fill spread collapses onto. Defaults reproduce the prior fixed h-10 /
 *  container / 0.9375rem field exactly, so every existing call-site is byte-
 *  identical. Behavioral/structural props (type, mono, leadingIcon, …) are grown
 *  in lockstep with the first real adopter — never ahead of one. */
/** `required` publishes `aria-required` and nothing else — no asterisk, no colour, no layout. Measured
 *  before this: **`aria-required` appeared 0 times in the whole app** and no `<input>`/`<textarea>`
 *  carried the platform `required`, while **40 buttons** carried a `disabledReason` of the shape "Enter a
 *  … first". So the app enforced mandatory fields and explained them ONLY at the submit button — a
 *  screen-reader user tabbing the field heard nothing about it and discovered the requirement by failing.
 *  (WCAG 3.3.2, level A: instructions are provided when content requires user input.) A VISIBLE marker is
 *  a separate, owner-facing decision; this is the invisible half, which is unambiguous. */
export function TextInput({ value, onChange, placeholder, autoFocus, onKeyDown, name, ariaLabel, required, size = 'lg', surface = 'container', type, mono, leadingIcon, disabled, disabledReason }: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  autoFocus?: boolean
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void
  name?: string
  ariaLabel?: string
  /** Publishes `aria-required`. Visual treatment is deliberately unchanged. */
  required?: boolean
  size?: FieldSize
  surface?: FieldSurface
  /** Masks a secret (API keys, tokens). Defaults to a plain text field. */
  type?: 'text' | 'password'
  /** Monospace — technical values (commands, endpoints, keys). Mirrors TextArea's. */
  mono?: boolean
  /** A leading glyph (typically a search icon) pinned inside the left edge. Adds
   *  the canonical left inset (pl-9) that clears the fixed left-3 icon; the caller
   *  passes the raw icon (e.g. `<Search size={14} />`) and it inherits the muted
   *  tone from the icon span. */
  leadingIcon?: ReactNode
  /** Dim + block the field. Grown for an editing surface behind a consent gate (the
   *  document/sheet/deck editors), where a text field that could still be typed into
   *  would make the gate a notice instead of a mechanism. */
  disabled?: boolean
  /** Why the field is off, for a CONDITIONALLY disabled one. Same carrier `Select` and
   *  `Button` have, for the same reason: a natively disabled control leaves the tab order,
   *  so without it a keyboard user tabs past a dead field with no way to learn what is
   *  missing. Applied only WHILE disabled — a tooltip on a working field would be noise. */
  disabledReason?: string
}) {
  const labelId = useFieldLabelId()
  const hintId = useFieldHintId()
  const autoId = useId()
  // Accessible name: a labelless, name-less control that sits in a Field claims
  // that Field's published label via aria-labelledby. Otherwise (a control with
  // its own name — a multi-control Field member or an autofill-suppressed picker —
  // or one outside any Field) an explicit ariaLabel provides the name. A `name`
  // attribute is NOT an accessible name, so it must never suppress ariaLabel.
  // An explicit ariaLabel WINS over the Field's published label. The comment above always promised
  // this ("a multi-control Field member … an explicit ariaLabel provides the name") but the condition
  // did not honour it, so a caller could not override: two password inputs in one "Set a password"
  // Field both announced "Set a password" and were indistinguishable. `ariaLabel` is the caller
  // saying "this control is not the Field", which only the caller can know.
  const claimsFieldLabel = !!labelId && !name && !ariaLabel
  const input = (
    <input value={value} type={type} autoFocus={autoFocus} name={name} id={name || autoId}
      aria-labelledby={claimsFieldLabel ? labelId : undefined} aria-label={claimsFieldLabel ? undefined : ariaLabel}
      aria-describedby={hintId}
      aria-required={required || undefined}
      disabled={disabled}
      title={disabled ? disabledReason || undefined : undefined}
      onChange={(e) => onChange(e.target.value)} onKeyDown={onKeyDown} placeholder={placeholder}
      className={cx(INPUT_BASE, FIELD_SIZE[size], FIELD_SURFACE[surface], leadingIcon ? 'pl-9 pr-m' : 'px-m', mono && 'font-mono', disabled && 'opacity-50')} />
  )
  if (!leadingIcon) return input
  // The canonical leading-icon geometry (icon at left-3, input pl-9) — the shape
  // the prior form primitive defined; the app's leading-icon search fields
  // converge onto it.
  return (
    <div className="relative w-full">
      <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-low">{leadingIcon}</span>
      {input}
    </div>
  )
}

// DateInput / Select keep the prior fixed chrome. They will adopt the shared
// size/surface scale above when — and only when — a real migration needs a
// non-default variant (grown in lockstep with the adopter, never ahead of one).

// TextArea's typographic size axis — the same on-ramp type steps as FIELD_SIZE,
// minus the height rung (a textarea's height comes from `rows`, not a fixed h-*).
// sm/md share the dense 0.8125rem (matching TextInput, where those tiers differ
// only in height); lg is the page-form 0.9375rem. Default lg keeps every prior
// call-site — and the mono branch below — byte-identical; a real adopter that
// needs the dense size opts into `sm`.
const TEXTAREA_TEXT: Record<FieldSize, string> = {
  sm: 'text-[0.8125rem]',
  md: 'text-[0.8125rem]',
  lg: 'text-[0.9375rem]',
}

export function TextArea({ value, onChange, placeholder, rows = 4, mono, ariaLabel, autoFocus, size = 'lg', disabled, disabledReason }: { value: string; onChange: (v: string) => void; placeholder?: string; rows?: number; mono?: boolean; ariaLabel?: string; autoFocus?: boolean; size?: FieldSize
  /** Dim + block the field, and why — TextInput's pair, same reasoning (an editor behind a
   *  consent gate needs the gate to be a mechanism, and a dead control owes a reason). */
  disabled?: boolean
  disabledReason?: string }) {
  const labelId = useFieldLabelId()
  const hintId = useFieldHintId()
  const autoId = useId()
  // Prefer a Field's published label (aria-labelledby); else an explicit ariaLabel
  // for call-sites that wrap the control in their own (non-Field) section label.
  // The mono branch is unchanged — it still appends `font-mono text-[0.8125rem]`
  // after the size text, so every existing (default-size) mono adopter renders
  // byte-for-byte as before.
  // Same precedence as TextInput: an explicit ariaLabel WINS, so a multi-control Field can name each
  // member. `aria-labelledby={labelId}` used to be unconditional, silently ignoring a caller's
  // ariaLabel.
  return (
    <textarea value={value} rows={rows} autoFocus={autoFocus} id={autoId} aria-describedby={hintId} aria-labelledby={!ariaLabel ? labelId : undefined} aria-label={!labelId || ariaLabel ? ariaLabel : undefined} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
      disabled={disabled} title={disabled ? disabledReason || undefined : undefined}
      className={`w-full rounded-md bg-surface-container px-m py-2 text-on-surface ${TEXTAREA_TEXT[size]} placeholder:text-on-surface-low outline-none resize-y focus:ring-2 focus:ring-inset focus:ring-primary ${mono ? 'font-mono text-[0.8125rem]' : ''}`} />
  )
}

// ── The canonical numeric stepper ─────────────────────────────────────────────
// A small right-aligned <input type="number"> with clamp-on-commit: the settings
// panels hand-rolled this THREE times verbatim (ChatPanel, AgentDefaultsPanel,
// InboxSettingsPanel) — the SAME chrome AND the same fiddly behavior (local string
// state so a half-typed value isn't clobbered mid-edit; on blur/Enter, parse →
// clamp to [min,max] → commit only if changed; revert an empty/NaN entry to the
// last good value). This is the one home for that role. It is deliberately NOT the
// standard TextInput scale: a stepper is a distinct role (fixed-width, right-
// aligned, tabular-nums) that must not be forced onto the full-width text field.
//
// `width` is the only visual axis (the panels ship w-20/w-24); the rest of the
// chrome is invariant. Defaults reproduce the prior hand-rolled field exactly, so
// every migrated call-site is byte-identical.
export function NumberField({ value, onChange, min, max, step, width = 'w-24', ariaLabel }: {
  value: number
  onChange: (n: number) => void
  min?: number
  max?: number
  step?: number
  /** Tailwind width class — the one axis the steppers vary (w-20/w-24). */
  width?: string
  ariaLabel?: string
}) {
  const labelId = useFieldLabelId()
  const hintId = useFieldHintId()
  const [local, setLocal] = useState(String(value))
  // Re-sync when the committed value changes out from under us (external patch,
  // clamp, another editor) — but never mid-edit, since we only read `value`.
  useEffect(() => { setLocal(String(value)) }, [value])
  const commit = () => {
    const n = Number(local)
    if (local === '' || Number.isNaN(n)) { setLocal(String(value)); return }
    const clamped = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n))
    setLocal(String(clamped))
    if (clamped !== value) onChange(clamped)
  }
  return (
    <input type="number" value={local} min={min} max={max} step={step ?? 1}
      aria-labelledby={!ariaLabel ? labelId : undefined} aria-label={ariaLabel} aria-describedby={hintId}
      onChange={(e) => setLocal(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
      className={cx('h-8 rounded-md bg-surface-high px-2 text-right text-[0.8125rem] text-on-surface tabular-nums outline-none focus:ring-2 focus:ring-inset focus:ring-primary', width)} />
  )
}

export function DateInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const labelId = useFieldLabelId()
  const hintId = useFieldHintId()
  const autoId = useId()
  return (
    <input type="date" value={value} id={autoId} aria-labelledby={labelId} aria-describedby={hintId} onChange={(e) => onChange(e.target.value)}
      className="h-10 rounded-md bg-surface-container px-m text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
  )
}

/** Styled native select — matches the TextInput chrome. */
export function Select({ value, onChange, options, disabled, name, ariaLabel, disabledReason }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; disabled?: boolean; name?: string
  /** The accessible name for a Select OUTSIDE any `Field` (a floating toolbar control, or a
   *  second control in a multi-control Field). Mirrors `TextInput`/`ChipInput`, which both
   *  already take one — Select was the odd primitive out, so an unlabelled select was the
   *  only way to render one here. An explicit ariaLabel WINS over the Field's label, same
   *  precedence as TextInput's. */
  ariaLabel?: string
  /** Why this select is off, for a CONDITIONALLY disabled one. `Button` has carried this
   *  since `unavailable.ts` (a natively disabled control leaves the tab order, so a
   *  keyboard user tabs straight past it with no way to learn what is missing); Select was
   *  the odd primitive out again, and a caller's only options were an unexplained dead
   *  control or wrapping it in something that could hold a `title`. Applied only WHILE
   *  disabled — a tooltip on a working select would be noise. */
  disabledReason?: string }) {
  const labelId = useFieldLabelId()
  const hintId = useFieldHintId()
  const autoId = useId()
  const claimsFieldLabel = !!labelId && !name && !ariaLabel
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} name={name} id={name || autoId}
      aria-labelledby={claimsFieldLabel ? labelId : undefined} aria-label={claimsFieldLabel ? undefined : ariaLabel}
      aria-describedby={hintId}
      title={disabled ? disabledReason || undefined : undefined}
      className="w-full h-10 appearance-none rounded-md bg-surface-container pl-m pr-8 text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary disabled:opacity-50">
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )
}

// The canonical Segmented lives in ui/Segmented.tsx; re-exported here so the
// existing form call-sites (status/priority pickers) keep a single form-family
// import path.
export { Segmented, type SegOption } from './Segmented'

/** Tag / chip input — type + Enter (or comma) to add, × to remove.
 *
 *  🔴 THE FIELD WAS 19.5px TALL INSIDE A 40px WELL. Measured on `#/tasks/new` and `#/prompts/new`
 *  (834×1112, and identical at 1440): every other input in this family renders 36-40px — `TextInput`
 *  40, `NumberField` 40, `DateInput` 40, the plain-textarea rows 36 — and this one was **19.5px**,
 *  `min-height: auto`, the family's only sub-24px field. WCAG 2.2 SC 2.5.8 wants 24px, and the
 *  undersized-target spacing exception cannot rescue it once a chip exists: chips are `h-7` (28px)
 *  sitting `gap-1.5` (6px) away, so the 24px circles intersect. **12 call sites** — knowledge (×3),
 *  prompts (×2), reports (×2), settings (×3), schedule, tasks.
 *
 *  Two things were wrong, and they are one defect: the control's own box was under the floor, and the
 *  40px well that LOOKS like the field was not a way into it — 20 of its 40 pixels did nothing.
 *
 *   · `min-h-6` raises the hit box to 24px while the drawn text stays 13px. It is PIXEL-NEUTRAL: the
 *     well is `min-h-10` with `py-2`, so its content box is already exactly 24px, and a chip row is
 *     28px, which 24 still fits inside. Same move as the `sm` Toggle's 36×20 → 36×24 and the loop
 *     composer's Scratch label — grow the target, leave the drawn size alone.
 *   · clicking the well focuses the field, which is what the well's own `focus-within:ring` already
 *     promises. Guarded to the well itself so a click on a chip's remove button is not hijacked; that
 *     button removes its chip and focus then lands in the field, which is where typing should go next.
 *     `preventDefault` on mousedown keeps the caret from being placed and then stolen. */
export function ChipInput({ values, onChange, placeholder, max, suggestions, ariaLabel }: { values: string[]; onChange: (v: string[]) => void; placeholder?: string; max?: number; suggestions?: string[]; ariaLabel?: string }) {
  const [draft, setDraft] = useState('')
  const fieldRef = useRef<HTMLInputElement>(null)
  const listId = useId()
  const labelId = useFieldLabelId()
  const hintId = useFieldHintId()
  const add = () => {
    const v = draft.trim().replace(/,$/, '')
    if (v && !values.includes(v) && (!max || values.length < max)) onChange([...values, v])
    setDraft('')
  }
  // Suggest existing values the user hasn't already added (autocomplete to avoid
  // near-duplicate fragments like "Kubernetes" vs "kubernetes").
  const remaining = suggestions?.filter((s) => !values.includes(s)) ?? []
  return (
    <div
      className="flex flex-wrap items-center gap-1.5 rounded-md bg-surface-container px-2 py-2 min-h-10 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary"
      onMouseDown={(e) => { if (e.target === e.currentTarget) { e.preventDefault(); fieldRef.current?.focus() } }}
    >
      {values.map((v) => (
        <span key={v} className="inline-flex items-center rounded-pill bg-surface-high pl-2 pr-0 h-7 text-on-surface-var text-[0.8125rem]">
          {v}
          {/* One per chip, icon-only: without a name every remove button is announced as bare
              "button" — N identical ones, each destructive. Named from the chip's own text, which is
              also what makes them distinct. The input below already resolves a name carefully; this
              row never did. */}
          {/* 🔴 AND IT WAS A 12×12 TARGET — half of SC 2.5.8's 24px. Measured on `#/settings/voice`
              (834×1112): three chips, each remove button `12x12` inside a 28px chip, sitting `gap-1`
              (4px) from the chip's text, so the undersized-target spacing exception cannot rescue it
              either. Twelve call sites render this chip.
              The chip's own trailing space pays for the fix exactly. Before, the right side was
              `gap 4 + glyph 12 + padding-right 8` = 24px from the text to the chip's edge; now it is
              `gap 0 + a 24px button (glyph centred, 6px each side) + padding-right 0` = the same 24px,
              so THE CHIP'S WIDTH DOES NOT CHANGE and the glyph lands 2px further right. That is why
              `gap-1` and the right half of `px-2` go: they are not decoration being dropped, they are
              the budget being spent on the target. */}
          <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))} className="inline-flex size-6 shrink-0 items-center justify-center text-on-surface-low hover:text-on-surface"><X size={12} /></button>
        </span>
      ))}
      <input ref={fieldRef} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={values.length ? '' : placeholder}
        list={remaining.length ? listId : undefined} name={`chip-${listId}`} aria-describedby={hintId} aria-labelledby={labelId} aria-label={labelId ? undefined : ariaLabel ?? 'Add a tag'}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add() } else if (e.key === 'Backspace' && !draft && values.length) onChange(values.slice(0, -1)) }}
        onBlur={add}
        className="flex-1 min-h-6 min-w-[80px] bg-transparent text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none" />
      {remaining.length > 0 && <datalist id={listId}>{remaining.map((s) => <option key={s} value={s} />)}</datalist>}
    </div>
  )
}

/** Checkbox — a single boolean tick, for row selection and inline opt-ins.
 *
 *  Distinct from `Switch`: a switch applies a SETTING immediately (and reads as
 *  on/off state), while a checkbox marks a SELECTION the user then acts on. The
 *  multi-select bars that drive bulk actions want the latter.
 *
 *  `stopPropagation` is built in, not left to callers: these live inside clickable
 *  rows, and every call site forgetting it would make ticking a row also open it. */
export function Checkbox({ checked, onChange, ariaLabel, className }: {
  checked: boolean
  onChange: (v: boolean) => void
  /** Required in practice — a bare tick has no accessible name of its own. */
  ariaLabel: string
  /** Extra classes for visibility rules (e.g. reveal-on-hover in a list row). */
  className?: string
}) {
  return (
    <input
      type="checkbox"
      checked={checked}
      aria-label={ariaLabel}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => { e.stopPropagation(); onChange(e.target.checked) }}
      className={cx(
        'size-4 shrink-0 cursor-pointer accent-primary',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
        className,
      )}
    />
  )
}
