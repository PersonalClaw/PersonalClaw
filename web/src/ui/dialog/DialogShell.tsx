import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'
import { spring, bounce, expr } from '../../design/motion'
import { useFocusTrap } from '../useFocusTrap'
import type { DialogField, DialogResult, DialogRequest } from './dialogStore'

/** The single visual shell behind every imperative dialog (confirm / prompt /
 *  alert). One component so the design language — scrim, spring, rounded sheet,
 *  pill buttons, danger tint, focus trap, keyboard contract — lives in ONE place
 *  and every dialog the app raises looks and behaves identically.
 *
 *  Keyboard contract:
 *   - Esc always cancels (dismiss).
 *   - Enter confirms a benign confirm/alert and submits a valid prompt; for a
 *     `danger` action Enter does NOT fire (a destructive confirm needs a
 *     deliberate click — guards against a reflexive keystroke).
 *   - Initial focus: a prompt focuses its first field; a danger confirm focuses
 *     Cancel (so a stray Space/Enter cancels, not destroys); a benign dialog
 *     focuses Confirm for quick acceptance. */
export function DialogShell({ request, onClose }: {
  request: DialogRequest
  onClose: (result: DialogResult) => void
}) {
  const { kind, title, body, tone, confirmLabel, cancelLabel, fields, icon: Icon } = request
  const danger = tone === 'danger'
  const isPrompt = kind === 'prompt'
  const isAlert = kind === 'alert'
  const trapRef = useFocusTrap<HTMLDivElement>()

  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {}
    for (const f of fields ?? []) init[f.name] = f.initial ?? ''
    return init
  })
  const [errors, setErrors] = useState<Record<string, string>>({})

  const fieldList: DialogField[] = fields ?? []

  const validateAll = (): boolean => {
    const errs: Record<string, string> = {}
    for (const f of fieldList) {
      const v = (values[f.name] ?? '').trim()
      if (f.required && !v) { errs[f.name] = 'Required'; continue }
      if (f.validate) {
        const e = f.validate(values[f.name] ?? '')
        if (e) errs[f.name] = e
      }
    }
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  // Submit-enabled gate (required fields filled) — recomputed each render so the
  // confirm button reflects the live input without a separate effect.
  const canSubmit = fieldList.every((f) => !f.required || (values[f.name] ?? '').trim().length > 0)

  const cancel = () => onClose(isPrompt ? null : false)
  const confirmAction = () => {
    if (isPrompt) {
      if (!validateAll()) return
      onClose({ ...values })
    } else {
      onClose(true)  // confirm + alert both resolve "acknowledged/yes"
    }
  }

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); cancel() }
      else if (e.key === 'Enter' && !danger) {
        // In a textarea, Enter inserts a newline — don't submit.
        const target = e.target as HTMLElement | null
        if (target && target.tagName === 'TEXTAREA') return
        e.preventDefault(); confirmAction()
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values, danger])

  const confirmText = confirmLabel ?? (isPrompt ? 'Save' : isAlert ? 'OK' : 'Confirm')
  const showCancel = !isAlert

  return createPortal(
    <motion.div className="fixed inset-0 z-[70] flex items-center justify-center p-2xl"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
      <div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" onClick={cancel} />
      {/* The sheet rises + scales in; the lift depth scales through the
          expressiveness knob (bold rises from further, refined barely) — consistent
          with Modal/ComposerStage so every raised surface shares one motion language. */}
      <motion.div ref={trapRef} role={isAlert || danger ? 'alertdialog' : 'dialog'} aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        className="relative w-full max-w-[420px] overflow-hidden rounded-xl bg-surface shadow-sheet"
        initial={{ opacity: 0, scale: 0.97, y: 8 + expr(10, 0.3) }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98, y: 6 }}
        transition={bounce.lift}>
        <div className="flex items-start gap-3 px-l pt-l">
          {(danger || Icon) && (
            // On a destructive confirm, the icon gives ONE attention pulse (scale
            // beat, not a loop) to draw the eye to the stakes — restrained: a delete
            // dialog shouldn't throb, but a single beat earns the glance.
            <motion.span className={`mt-0.5 shrink-0 ${danger ? 'text-danger' : 'text-on-surface-var'}`}
              initial={danger ? { scale: 0.6 } : false}
              animate={danger ? { scale: [0.6, 1.18, 1] } : {}}
              transition={{ duration: 0.42, ease: 'easeOut', delay: 0.08 }}>
              {Icon ? <Icon size={18} /> : <AlertTriangle size={18} />}
            </motion.span>
          )}
          <div className="min-w-0 flex-1">
            <div data-type="title-l" className="text-on-surface">{title}</div>
            {body && <div className="mt-1.5 text-on-surface-var text-[0.875rem] whitespace-pre-line">{body}</div>}
          </div>
        </div>

        {isPrompt && fieldList.length > 0 && (
          <div className="mt-3 flex flex-col gap-3 px-l">
            {fieldList.map((f, i) => (
              <PromptFieldRow
                key={f.name}
                field={f}
                value={values[f.name] ?? ''}
                error={errors[f.name]}
                autoFocus={i === 0}
                onChange={(v) => { setValues((p) => ({ ...p, [f.name]: v })); if (errors[f.name]) setErrors((p) => ({ ...p, [f.name]: '' })) }}
                onSubmit={confirmAction}
              />
            ))}
          </div>
        )}

        <div className="flex justify-end gap-2 px-l py-l">
          {showCancel && (
            <button type="button" onClick={cancel} autoFocus={danger}
              className="rounded-pill px-4 h-9 text-[0.875rem] text-on-surface-var bg-surface-high hover:bg-surface-highest transition-colors">
              {cancelLabel ?? 'Cancel'}
            </button>
          )}
          <button type="button" onClick={confirmAction} autoFocus={!danger && !isPrompt}
            disabled={isPrompt && !canSubmit}
            className="rounded-pill px-4 h-9 text-[0.875rem] transition-colors disabled:opacity-40"
            style={danger
              ? { background: 'var(--color-danger)', color: 'var(--color-on-danger)' }
              : { background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
            {confirmText}
          </button>
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  )
}

function PromptFieldRow({ field, value, error, autoFocus, onChange, onSubmit }: {
  field: DialogField
  value: string
  error?: string
  autoFocus?: boolean
  onChange: (v: string) => void
  onSubmit: () => void
}) {
  const ref = useRef<HTMLInputElement | HTMLTextAreaElement>(null)
  useEffect(() => { if (autoFocus) ref.current?.focus() }, [autoFocus])
  const base = 'w-full rounded-lg bg-surface-high px-3 text-[0.9375rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50'
  return (
    <div>
      {field.label && <label className="mb-1 block text-on-surface-var text-[0.8125rem]">{field.label}</label>}
      {field.type === 'textarea' ? (
        <textarea ref={ref as React.RefObject<HTMLTextAreaElement>} value={value} placeholder={field.placeholder} rows={4}
          onChange={(e) => onChange(e.target.value)}
          className={`${base} py-2 resize-y min-h-[88px] ${error ? 'ring-2 ring-danger/50' : ''}`} />
      ) : (
        <input ref={ref as React.RefObject<HTMLInputElement>} type={field.type === 'password' ? 'password' : 'text'}
          value={value} placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onSubmit() } }}
          className={`${base} h-10 ${error ? 'ring-2 ring-danger/50' : ''}`} />
      )}
      {error && <div className="mt-1 text-[0.75rem] text-danger">{error}</div>}
    </div>
  )
}
