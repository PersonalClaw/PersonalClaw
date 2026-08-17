import { useId, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { spring, physics } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { Toggle } from '../../ui/Toggle'
import { FieldLabelProvider, NumberField } from '../../ui/forms'

/** Shared settings-subpage primitives for consistent layout across panels. */

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

export function Section({ title, hint, icon: Icon, right, children }: {
  title?: string
  /** ReactNode, not string: `DiagnosticsPanel`'s "Live logs" hint carries a live connection dot and
   *  a count, which is why that panel hand-rolled its heading rather than adopt this. */
  hint?: ReactNode
  /** Leading glyph inside the heading — `DesignPanel`'s three control sections each have one. */
  icon?: LucideIcon
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
      {Icon && <Icon size={16} className="shrink-0 text-primary" />}
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

/** A labeled row — label/description on the left, control on the right. */
export function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-l border-b border-outline-variant/30 py-3 last:border-0">
      <div className="min-w-0">
        <div className="text-on-surface text-[0.8125rem]">{label}</div>
        {hint && <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
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
  return (
    <FieldLabelProvider value={labelId}>
      <div className="border-b border-outline-variant/30 py-3 last:border-0">
        <div id={labelId} className="text-on-surface text-[0.8125rem]">{label}</div>
        {hint && <div className="mt-0.5 mb-2 text-on-surface-low text-[0.8125rem]">{hint}</div>}
        <div className="mt-2">{children}</div>
      </div>
    </FieldLabelProvider>
  )
}

// Toggle now lives in ui/ as the canonical app-wide switch; re-export so existing
// `import { Toggle } from '../settingsUI'` call sites keep working (no dual impl).
export { Toggle } from '../../ui/Toggle'

export function SegPills<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void; options: { key: T; label: string }[]
}) {
  // Per-instance layoutId so the sliding pill in one SegPills can't fly to another.
  const indicatorId = `segpills-${useId()}`
  return (
    <div className="inline-flex rounded-pill bg-surface-container p-0.5">
      {options.map((o) => {
        const on = o.key === value
        return (
          <button key={o.key} type="button" onClick={() => onChange(o.key)}
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
export function NumberRow({ label, hint, cfg, field, min, max, patch }: {
  label: string
  hint?: string
  cfg: Record<string, unknown>
  field: string
  min: number
  max: number
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
        <NumberField value={value} min={min} max={max} step={1} onChange={(n) => patch(field, n as never, flash, label)} ariaLabel={label} />
        <SavedToast show={saved} />
      </div>
    </Field>
  )
}
