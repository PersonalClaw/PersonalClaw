import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, X } from 'lucide-react'
import { cx } from './cx'

/** The inline, danger-tinted error band shown when an action fails — above
 *  list/detail bodies, inline in the chat transcript, or as a transient banner.
 *  A rounded danger strip holding the message, an optional leading AlertTriangle,
 *  and an optional corner "×".
 *
 *  This is the single shape behind the several `{err && <div role="alert" …>}`
 *  banners that had drifted into per-page copies. Consolidated here: the Projects
 *  list + hub and Code section banners (byte-identical), plus — folded in per the
 *  owner's maximum-convergence call, pixel moves accepted — ChatPage's
 *  non-dismissible multi-line turn error (`multiline`, no `onDismiss`), the
 *  FilesSection file-op strip, and the Tasks board's rejected-drag banner
 *  (`animated`, re-toned from warn to danger). Every failure banner is now this
 *  one band; the per-site 14%-tint / tighter-padding / warn-tone variants
 *  collapsed onto this canonical chrome.
 *
 *  Modes (orthogonal): omit `onDismiss` for a non-dismissible strip; set
 *  `multiline` to top-align and wrap a multi-line message; set `animated` for the
 *  slide-in entrance used by transient banners. */
export function InlineError({ children, onDismiss, icon = false, multiline = false, animated = false, className, onRetry }: {
  children: ReactNode
  /** Show a corner "×"; omit for a non-dismissible strip (e.g. chat turn errors). */
  onDismiss?: () => void
  /** Lead with an AlertTriangle glyph (matches the Code section's banner). */
  icon?: boolean
  /** Top-align and wrap a multi-line message instead of the single-line default. */
  multiline?: boolean
  /** Slide-in entrance for transient banners (e.g. a rejected board drag). */
  animated?: boolean
  /** Per-site outer spacing, e.g. `mx-l mt-2`. */
  className?: string
  /** Offer a "Retry" beside the message. For a FAILED READ inside a form field, where the field's
   *  own control cannot be used until the read succeeds — `LoadError`'s centred empty-state
   *  treatment is wrong at that scale, but the retry it offers is still the thing the user needs. */
  onRetry?: () => void
}) {
  const cls = cx('flex gap-2 rounded-lg px-3 py-2 text-[0.8125rem]', multiline ? 'items-start' : 'items-center', className)
  const style = { background: 'color-mix(in srgb, var(--color-danger) 10%, transparent)', color: 'var(--color-danger)' }
  const body = (
    <>
      {icon && <AlertTriangle size={14} className={cx('shrink-0', multiline && 'mt-0.5')} />}
      <span className={cx('min-w-0 flex-1', multiline && 'whitespace-pre-wrap break-words')}>{children}</span>
      {onRetry && (
        <button type="button" onClick={onRetry}
          className="shrink-0 rounded-md px-1.5 py-0.5 underline decoration-dotted underline-offset-2 hover:opacity-70">
          Retry
        </button>
      )}
      {onDismiss && <button type="button" onClick={onDismiss} aria-label="Dismiss" className="shrink-0 hover:opacity-70"><X size={14} /></button>}
    </>
  )
  if (animated) {
    return (
      <motion.div role="alert" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} className={cls} style={style}>
        {body}
      </motion.div>
    )
  }
  return (
    <div role="alert" className={cls} style={style}>
      {body}
    </div>
  )
}
