import { useId, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'
import { spring, bounce } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { Toggle } from '../../ui/Toggle'

/** Shared settings-subpage primitives for consistent layout across panels. */

export function PanelHeader({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-l">
      <h2 className="text-on-surface" data-type="title-l">{title}</h2>
      {hint && <p className="mt-1 text-on-surface-low text-[0.8125rem]">{hint}</p>}
    </div>
  )
}

export function Section({ title, hint, children }: { title?: string; hint?: string; children: ReactNode }) {
  return (
    <section className="mb-2xl">
      {title && <h3 className="mb-s text-on-surface text-[0.9375rem]" style={fvs(600)}>{title}</h3>}
      {hint && <p className="mb-m text-on-surface-low text-[0.8125rem]">{hint}</p>}
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

/** Stacked variant for controls that need full width under their label. */
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="border-b border-outline-variant/30 py-3 last:border-0">
      <div className="text-on-surface text-[0.8125rem]">{label}</div>
      {hint && <div className="mt-0.5 mb-2 text-on-surface-low text-[0.8125rem]">{hint}</div>}
      <div className="mt-2">{children}</div>
    </div>
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

export function SavedToast({ show }: { show: boolean }) {
  // "Saved ✓" springs in (a small earned confirmation) rather than a flat fade.
  return (
    <AnimatePresence>
      {show && (
        <motion.span initial={{ opacity: 0, scale: 0.8, y: 2 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.8 }}
          transition={bounce.playful} className="text-[0.75rem]" style={{ color: 'var(--color-success)' }}>Saved ✓</motion.span>
      )}
    </AnimatePresence>
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
  /** `(key, value, onSaved)` — the panel's own config PATCH. Typed at its widest shape so a
   *  panel whose callback declares `v: boolean` or a required `cb` still satisfies it. */
  patch: (k: string, v: never, cb: () => void) => void
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
        <Toggle on={on} onChange={(v) => patch(field, v as never, flash)} label={label} />
      </div>
    </Row>
  )
}
