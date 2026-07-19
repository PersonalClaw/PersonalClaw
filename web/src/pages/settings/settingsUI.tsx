import { useId, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { spring, bounce } from '../../design/motion'

/** Shared settings-subpage primitives for consistent layout across panels. */

export function PanelHeader({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-l">
      <h2 className="text-on-surface" data-type="title-l">{title}</h2>
      {hint && <p className="mt-1 text-on-surface-low text-[0.875rem]">{hint}</p>}
    </div>
  )
}

export function Section({ title, hint, children }: { title?: string; hint?: string; children: ReactNode }) {
  return (
    <section className="mb-2xl">
      {title && <h3 className="mb-s text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 600' }}>{title}</h3>}
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
        <div className="text-on-surface text-[0.875rem]">{label}</div>
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
      <div className="text-on-surface text-[0.875rem]">{label}</div>
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
