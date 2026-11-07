import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'
import { duration, spring, physics, expr } from '../../design/motion'
import { useFocusTrap } from '../useFocusTrap'
import { unavailableWhen } from '../unavailable'
import type { DialogField, DialogResult, DialogRequest } from './dialogStore'

/** How long a `deliberate` confirm stays quiet before its Confirm answers. */
export const ARM_MS = 1000
/** Past this after mount the sheet counts as shown, should its entrance never report done. */
export const SHOWN_BY_MS = 600

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
 *     focuses Confirm for quick acceptance.
 *   - A `deliberate` confirm (an ask a page's own content can raise) focuses Cancel, takes no
 *     Enter from the window, and its Confirm answers only a press that begins after the dialog
 *     has been quiet for {@link ARM_MS}: from when the sheet is in full view, every key and every
 *     press anywhere starts that wait again, and so do focus reaching Confirm, and the window
 *     losing or regaining focus or visibility. A press begun before it armed does nothing, and a
 *     keyboard press is read at its click, which counts only when Confirm has held focus that
 *     long with no other key. So typing that runs on into the dialog, a held key, or a run of
 *     clicks is never its yes. */
export function DialogShell({ request, onClose }: {
  request: DialogRequest
  onClose: (result: DialogResult) => void
}) {
  const { kind, title, body, tone, confirmLabel, cancelLabel, fields, icon: Icon } = request
  const danger = tone === 'danger'
  const deliberate = request.deliberate === true
  const isPrompt = kind === 'prompt'
  const isAlert = kind === 'alert'
  const trapRef = useFocusTrap<HTMLDivElement>()
  const bodyId = `dialog-body-${useId()}`

  /** What a refused press on Confirm is told, in a polite line: why it did nothing. A refusal
   *  while the line already says so clears it first, so a reader hears each one; the line clears
   *  once Confirm arms. */
  const [refused, setRefused] = useState('')
  const refusedRef = useRef('')
  const refusedTimer = useRef<number | undefined>(undefined)
  const sayRefused = useCallback((text: string) => {
    window.clearTimeout(refusedTimer.current)
    const again = text !== '' && refusedRef.current === text
    refusedRef.current = text
    if (!again) { setRefused(text); return }
    setRefused('')
    refusedTimer.current = window.setTimeout(() => setRefused(text), 50)
  }, [])
  useEffect(() => () => window.clearTimeout(refusedTimer.current), [])

  // A deliberate confirm's arming: Confirm answers only a press that begins once the dialog has
  // been quiet for ARM_MS since the sheet was fully shown. Any key or press (bar the press that
  // answers, on an armed Confirm), focus reaching Confirm, and the window losing or regaining
  // focus or visibility start the wait again.
  const [shown, setShown] = useState(false)
  const [armed, setArmed] = useState(!deliberate)
  const armedRef = useRef(!deliberate)
  /** The press now under way on Confirm began while it was unarmed, so its click is void. */
  const pressedUnarmed = useRef(false)
  /** A pointer press on an armed Confirm is under way: the focus it moves there is its own. */
  const pressingArmed = useRef(false)
  /** Since when Confirm has held focus with no other key: a keyboard press needs ARM_MS of it. */
  const keyQuietSince = useRef<number | null>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const restartRef = useRef<() => void>(() => {})
  useEffect(() => {
    if (!deliberate) return
    const t = window.setTimeout(() => setShown(true), SHOWN_BY_MS)
    return () => window.clearTimeout(t)
  }, [deliberate])
  useEffect(() => {
    if (!deliberate || !shown) return
    let timer: number | undefined
    const restart = () => {
      window.clearTimeout(timer)
      armedRef.current = false
      setArmed(false)
      timer = window.setTimeout(() => { armedRef.current = true; setArmed(true); sayRefused('') }, ARM_MS)
    }
    restartRef.current = restart
    const onConfirm = (t: EventTarget | null) => !!confirmRef.current && t instanceof Node && confirmRef.current.contains(t)
    const onKey = (e: KeyboardEvent) => {
      // The press that answers: a fresh Enter or Space on an armed Confirm (Confirm's own
      // onKeyDown says whether it may).
      const activates = (e.key === 'Enter' || e.key === ' ') && onConfirm(e.target)
      if (activates && armedRef.current && !e.repeat) return
      // Any other key: Confirm's keyboard quiet starts again too.
      if (onConfirm(document.activeElement)) keyQuietSince.current = Date.now()
      restart()
    }
    const onPointer = (e: PointerEvent) => {
      if (onConfirm(e.target) && armedRef.current) { pressingArmed.current = true; return }
      pressingArmed.current = false
      restart()
    }
    // A press that goes nowhere (a drag off, a pan) leaves nothing behind.
    const onCancel = () => { pressingArmed.current = false }
    restart()
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('pointerdown', onPointer, true)
    window.addEventListener('pointercancel', onCancel, true)
    window.addEventListener('blur', restart)
    window.addEventListener('focus', restart)
    document.addEventListener('visibilitychange', restart)
    return () => {
      window.clearTimeout(timer)
      restartRef.current = () => {}
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('pointerdown', onPointer, true)
      window.removeEventListener('pointercancel', onCancel, true)
      window.removeEventListener('blur', restart)
      window.removeEventListener('focus', restart)
      document.removeEventListener('visibilitychange', restart)
    }
  }, [deliberate, shown, sayRefused])
  /** Whether a click on Confirm may answer: a pointer press begun while armed, or a keyboard
   *  press from a Confirm that has held focus ARM_MS with no other key. */
  const mayAnswer = (e: React.MouseEvent): boolean => {
    if (!deliberate) return true
    const early = pressedUnarmed.current
    pressedUnarmed.current = false
    pressingArmed.current = false
    if (!armedRef.current || early) return false
    if (e.detail !== 0) return true
    const since = keyQuietSince.current
    if (document.activeElement === confirmRef.current && since !== null && Date.now() - since >= ARM_MS) return true
    // A keyboard press refused is a key too: Confirm's own keyboard quiet starts again from it.
    if (document.activeElement === confirmRef.current) keyQuietSince.current = Date.now()
    return false
  }

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
      else if (e.key === 'Enter' && !danger && !deliberate) {
        // In a textarea, Enter inserts a newline — don't submit.
        const target = e.target as HTMLElement | null
        if (target && target.tagName === 'TEXTAREA') return
        e.preventDefault(); confirmAction()
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values, danger, deliberate])

  const confirmText = confirmLabel ?? (isPrompt ? 'Save' : isAlert ? 'OK' : 'Confirm')
  const showCancel = !isAlert

  return createPortal(
    <motion.div className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center p-2xl"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
      <div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" onClick={cancel} />
      {/* The sheet rises + scales in; the lift depth scales through the
          expressiveness knob (bold rises from further, refined barely) — consistent
          with Modal/ComposerStage so every raised surface shares one motion language. */}
      <motion.div ref={trapRef} role={isAlert || danger ? 'alertdialog' : 'dialog'} aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        aria-describedby={body ? bodyId : undefined}
        className="relative max-h-[calc(100dvh-2*var(--spacing-2xl))] w-full max-w-[420px] overflow-y-auto rounded-xl bg-surface shadow-sheet"
        initial={{ opacity: 0, scale: 0.97, y: 8 + expr(10, 0.3) }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98, y: 6 }}
        onAnimationComplete={() => setShown(true)}
        transition={physics.playful}>
        <div className="flex items-start gap-3 px-l pt-l">
          {(danger || Icon) && (
            // On a destructive confirm, the icon gives ONE attention pulse (scale
            // beat, not a loop) to draw the eye to the stakes — restrained: a delete
            // dialog shouldn't throb, but a single beat earns the glance.
            <motion.span className={`mt-0.5 shrink-0 ${danger ? 'text-danger' : 'text-on-surface-var'}`}
              initial={danger ? { scale: 0.6 } : false}
              animate={danger ? { scale: [0.6, 1.18, 1] } : {}}
              transition={{ duration: duration.long, ease: 'easeOut', delay: duration.short }}>
              {Icon ? <Icon size={18} /> : <AlertTriangle size={18} />}
            </motion.span>
          )}
          <div className="min-w-0 flex-1">
            <div data-type="title-l" className="text-on-surface">{title}</div>
            {body && <div id={bodyId} data-type="body-s" className="mt-1.5 text-on-surface-var whitespace-pre-line">{body}</div>}
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

        {/* A deliberate dialog says, politely, why a press on its Confirm did nothing. */}
        {deliberate && (
          <p role="status" aria-live="polite" data-type="caption" data-testid="dialog-refused"
            className="px-l pt-s text-on-surface-low empty:pt-0">{refused}</p>
        )}
        <div className="flex justify-end gap-2 px-l py-l">
          {showCancel && (
            <button type="button" onClick={cancel} autoFocus={danger || deliberate} data-type="body-s"
              className="rounded-pill px-4 h-9 text-on-surface-var bg-surface-high hover:bg-surface-highest transition-colors">
              {cancelLabel ?? 'Cancel'}
            </button>
          )}
          {/* The prompt dialog's confirm stays REACHABLE while its required fields are empty, so
              a keyboard user in the dialog can find it and hear what is missing. The dim is
              restated on `aria-disabled:` because `disabled:` no longer fires. */}
          <button type="button" ref={confirmRef}
            onPointerDown={() => { if (deliberate) pressedUnarmed.current = !armedRef.current }}
            onKeyDown={(e) => {
              // Every activating key on Confirm, from the moment the dialog opens, says whether
              // the press it starts may answer: one begun before it armed, or a held key's
              // repeat, never does.
              if (deliberate && (e.key === 'Enter' || e.key === ' ')) pressedUnarmed.current = !armedRef.current || e.repeat
            }}
            onFocus={() => {
              if (!deliberate) return
              // Focus reaching Confirm starts the quiet again, unless a press on it moved it there.
              keyQuietSince.current = Date.now()
              if (!pressingArmed.current) restartRef.current()
              pressingArmed.current = false
            }}
            onBlur={() => { keyQuietSince.current = null; pressingArmed.current = false }}
            onClick={(e) => {
              if (mayAnswer(e)) { confirmAction(); return }
              if (deliberate) sayRefused('That did nothing: this answers once the dialog has been still a moment.')
            }}
            autoFocus={!danger && !deliberate && !isPrompt}
            {...unavailableWhen(
              (isPrompt && !canSubmit) || (deliberate && !armed),
              isPrompt && !canSubmit ? 'Fill in the required fields first' : 'Read it first: this answers in a moment',
            )}
            // A deliberate Confirm's every click reaches `mayAnswer`, which refuses one while it
            // waits and says why: its aria-disabled and title stay, without the capture that would
            // swallow the click unexplained.
            {...(deliberate ? { onClickCapture: undefined } : {})}
            data-type="body-s"
            className="rounded-pill px-4 h-9 transition-colors disabled:opacity-40 aria-disabled:opacity-40 aria-disabled:cursor-not-allowed"
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
  // The field's id, which is also what ties the visible label to it: without one the label
  // named the field for sighted users only, and the browser could not tell the fields apart.
  const fieldId = `dialog-field-${useId()}`
  useEffect(() => { if (autoFocus) ref.current?.focus() }, [autoFocus])
  const base = 'w-full rounded-lg bg-surface-high px-3 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'
  return (
    <div>
      {field.label && <label htmlFor={fieldId} data-type="body-s" className="mb-1 block text-on-surface-var">{field.label}</label>}
      {field.type === 'textarea' ? (
        <textarea ref={ref as React.RefObject<HTMLTextAreaElement>} id={fieldId} value={value} placeholder={field.placeholder} rows={4}
          onChange={(e) => onChange(e.target.value)} data-type="body-m"
          className={`${base} py-2 resize-y min-h-[88px] ${error ? 'ring-2 ring-danger/50' : ''}`} />
      ) : (
        <input ref={ref as React.RefObject<HTMLInputElement>} type={field.type === 'password' ? 'password' : 'text'}
          id={fieldId} autoComplete="off" value={value} placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onSubmit() } }}
          data-type="body-m"
          className={`${base} h-10 ${error ? 'ring-2 ring-danger/50' : ''}`} />
      )}
      {error && <div data-type="caption" className="mt-1 text-danger">{error}</div>}
    </div>
  )
}
