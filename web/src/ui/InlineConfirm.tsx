import { useCallback, useEffect, useRef, useState, type ButtonHTMLAttributes, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react'
import { matchBinding } from '../app/keymap'
import { buttonLook } from './Button'
import { ARM_MS } from './dialog/DialogShell'
import { TOUCH_TARGET } from './Toggle'

/** The keys that only modify another: pressed alone they press nothing. */
const MODIFIERS = new Set(['Meta', 'Control', 'Shift', 'Alt', 'AltGraph', 'CapsLock', 'Fn', 'OS'])

/** A card answer's look from the kit's (`buttonLook`), made to wrap: `Button` never does, and a
 *  verb or a name a server wrote must never run past a card at 320 px. Its height becomes a floor,
 *  it takes a 44 px box below 1024 px, and it gets a transparent edge forced colours draw as
 *  `ButtonBorder`, so a pair keeps its edges there. */
export function wrapping(look: { className: string; role: string }): { className: string; role: string } {
  const className = look.className
    .replace(/\bwhitespace-nowrap\b/, 'whitespace-normal')
    .replace(/\bh-7\b/, 'min-h-7')
  return { className: `${className} max-w-full border border-transparent text-left ${TOUCH_TARGET}`, role: look.role }
}

/** A card's answer whose words a server wrote (a run's choice), drawn in the kit's secondary pill
 *  made to wrap (`wrapping`), so a long one never runs past the card at 320 px. */
export function WrappingAnswer({ children, ariaLabel, ariaBusy, ...button }: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className' | 'type' | 'aria-label' | 'aria-busy'> & {
  children: ReactNode
  /** The answer's name, "<verb> <subject>". */
  ariaLabel?: string
  /** While the answer is on its way. */
  ariaBusy?: boolean
}) {
  const look = wrapping(buttonLook('secondary', 'xs', 'pill'))
  return (
    <button {...button} type="button" aria-label={ariaLabel} aria-busy={ariaBusy || undefined}
      data-type={look.role} className={look.className}>{children}</button>
  )
}

/** The one way a card asks once more before an answer it can't take back, the inline twin of a
 *  `deliberate` dialog (`DialogShell`): a delete, a write outside a chat's folder, a merge, trusting
 *  a folder.
 *
 *  It opens under the control that asked for it, in the words the panel would use, and nothing is
 *  sent before it. The pressed control never moves and every press of it only asks again, so a
 *  double click's second press lands on it and asks again. Nothing moves the focus onto Yes. Yes
 *  answers only a press that begins after it has been in view and quiet for {@link ARM_MS}: from
 *  when it opens, every key (but a modifier pressed alone, the ⌘ of ⌘Enter) and every press anywhere
 *  starts that wait again, and so does the window
 *  losing or regaining focus or visibility, so a run of clicks or a held key is never its yes. A
 *  keyboard press counts only from a Yes that has held the focus that long with no other key, and
 *  ⌘Enter answers only from Yes. Escape and Back step back. Yes reads "Yes, <verb>", never the
 *  label that opened it, and both answers are touch targets with an edge forced colours draw. */
export function InlineConfirm({ sentence, verb, onYes, onBack }: {
  /** What answering does, in the panel's own words ("It deletes files. Run it?"). */
  sentence: string
  /** The answer's verb and subject, after "Yes, " ("run it", "trust it"). */
  verb: string
  onYes: () => void
  /** Step back: the card shows its answers again. */
  onBack: () => void
}) {
  const [armed, setArmed] = useState(false)
  const armedRef = useRef(false)
  const [refused, setRefused] = useState('')
  const yesRef = useRef<HTMLButtonElement>(null)
  const box = useRef<HTMLDivElement>(null)
  /** The press now under way on Yes began while it was armed. */
  const pressArmed = useRef(false)
  /** Since when Yes has held the focus with no other key: a keyboard press needs ARM_MS of it. */
  const keyQuietSince = useRef<number | null>(null)
  const restartRef = useRef<() => void>(() => {})

  useEffect(() => {
    let timer: number | undefined
    let inView = typeof IntersectionObserver !== 'function'
    const restart = () => {
      window.clearTimeout(timer)
      armedRef.current = false
      setArmed(false)
      if (inView) timer = window.setTimeout(() => { armedRef.current = true; setArmed(true); setRefused('') }, ARM_MS)
    }
    restartRef.current = restart
    const onYesTarget = (t: EventTarget | null) => !!yesRef.current && t instanceof Node && yesRef.current.contains(t)
    const onKey = (e: globalThis.KeyboardEvent) => {
      // A modifier pressed alone is the start of a chord (⌘ of ⌘Enter), not a press: it starts no
      // wait again, or ⌘Enter could never answer.
      if (MODIFIERS.has(e.key)) return
      const answers = (e.key === 'Enter' || e.key === ' ') && onYesTarget(e.target)
      if (answers && armedRef.current && !e.repeat) return
      if (onYesTarget(document.activeElement)) keyQuietSince.current = Date.now()
      restart()
    }
    const onPointer = (e: globalThis.PointerEvent) => {
      if (onYesTarget(e.target) && armedRef.current) { pressArmed.current = true; return }
      pressArmed.current = false
      restart()
    }
    const seen = typeof IntersectionObserver === 'function'
      ? new IntersectionObserver((entries) => {
        const visible = entries.some((entry) => entry.isIntersecting)
        if (visible === inView) return
        inView = visible
        restart()
      })
      : null
    if (seen && box.current) seen.observe(box.current)
    restart()
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('pointerdown', onPointer, true)
    window.addEventListener('blur', restart)
    window.addEventListener('focus', restart)
    document.addEventListener('visibilitychange', restart)
    return () => {
      window.clearTimeout(timer)
      restartRef.current = () => {}
      seen?.disconnect()
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('pointerdown', onPointer, true)
      window.removeEventListener('blur', restart)
      window.removeEventListener('focus', restart)
      document.removeEventListener('visibilitychange', restart)
    }
  }, [])

  /** Whether a click on Yes may answer: a pointer press begun while armed, or a keyboard press
   *  from a Yes that has held the focus ARM_MS with no other key. */
  const mayAnswer = useCallback((keyboard: boolean) => {
    if (!armedRef.current) return false
    if (!keyboard) return pressArmed.current
    const since = keyQuietSince.current
    return since !== null && Date.now() - since >= ARM_MS
  }, [])

  const refuse = () => {
    setRefused('')
    window.setTimeout(() => setRefused('That did nothing: this answers once it has been still a moment.'), 50)
  }

  const onYesClick = (e: MouseEvent<HTMLButtonElement>) => {
    const keyboard = e.detail === 0
    const ok = mayAnswer(keyboard)
    pressArmed.current = false
    if (!ok) { refuse(); restartRef.current(); return }
    onYes()
  }

  const onYesKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    // ⌘Enter, the focused approval's own key (`keymap.ts`), answers yes here too.
    if (matchBinding(e, 'approve-focused')) {
      e.preventDefault()
      if (mayAnswer(true)) onYes()
      else refuse()
    }
  }

  // Escape steps back, and goes no further: the panel behind the card stays open.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Escape') return
    e.preventDefault()
    e.stopPropagation()
    onBack()
  }

  const onYesFocus = () => { keyQuietSince.current = Date.now() }
  const onYesBlur = () => { keyQuietSince.current = null }
  // The kit's own looks, wrapping (`Button` never does) so words a server wrote never run past the
  // card, with a transparent edge forced colours draw as `ButtonBorder`.
  const yes = wrapping(buttonLook('secondary', 'xs', 'pill'))
  const back = wrapping(buttonLook('ghost', 'xs', 'pill'))
  return (
    <div ref={box} data-inline-confirm role="group" aria-label={sentence} onKeyDown={onKeyDown} className="flex flex-col gap-s">
      <p data-type="body-s" className="text-on-surface">{sentence}</p>
      <div className="flex flex-wrap items-center gap-s">
        <button ref={yesRef} type="button" data-type={yes.role} data-armed={armed ? 'true' : 'false'}
          onClick={onYesClick} onKeyDown={onYesKey} onFocus={onYesFocus} onBlur={onYesBlur}
          className={yes.className}>
          Yes, {verb}
        </button>
        <button type="button" data-type={back.role} onClick={onBack} className={back.className}>
          Back
        </button>
      </div>
      <span role="status" data-type="caption" className="text-on-surface-low empty:hidden">{refused}</span>
    </div>
  )
}
