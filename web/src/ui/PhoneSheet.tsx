import { createContext, useContext, useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { motion, useReducedMotion } from 'framer-motion'
import { returnFocusTo, useFocusTrap } from './useFocusTrap'
import { spring } from '../design/motion'
import { framesMayHaveMoved } from './framesMayHaveMoved'

/** Whether a row is drawn inside a phone sheet: `MenuRow` and `FilterRow` are 44 px rows there. */
const InPhoneSheet = createContext(false)

/** Draw the rows inside as a phone sheet's, 44 px: for a host that draws its own sheet (`Modal`). */
export function PhoneSheetRows({ children }: { children: ReactNode }) {
  return <InPhoneSheet.Provider value>{children}</InPhoneSheet.Provider>
}

/** True inside a `PhoneSheet`: its rows take the compact widths' 44 px height. */
export function useInPhoneSheet(): boolean {
  return useContext(InPhoneSheet)
}

/** A menu or picker as a phone draws it: a bottom sheet over a scrim, with a grab line.
 *
 *  The phone sheet form (`phoneSheet` on `Popover`, `ContextMenu` and `Modal`): from 320 to 768 px a
 *  menu anchored to a 44 px control, or a dialog centred over a narrow window, left most of the
 *  screen unused and its 32 px rows under the touch floor. A sheet is a dialog named by what opened
 *  it, that keeps the focus inside it (Tab cycles its stops); Escape and the scrim close it, and the
 *  focus goes back to `returnTo`, the trigger. Every `MenuRow` and `FilterRow` in it is a 44 px row
 *  (`useInPhoneSheet`). Its hosts open it only on a phone; elsewhere they are as before. */
export function PhoneSheet({ name, onClose, returnTo, children }: {
  /** The dialog's name: the trigger's own ("Filter", "Add"). */
  name: string
  /** Close it: Escape, the scrim, or a row that acts. */
  onClose: () => void
  /** Where the focus goes once it closes: the control that opened it. */
  returnTo?: HTMLElement | null
  children: ReactNode
}) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  const reduce = useReducedMotion()
  useEffect(() => () => framesMayHaveMoved(), [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      onClose()
      returnFocusTo(returnTo)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, returnTo])
  return createPortal(
    <div data-phone-sheet-host className="fixed inset-0 z-[var(--z-modal)] flex flex-col justify-end">
      <motion.div aria-hidden data-phone-sheet-scrim className="absolute inset-0 bg-canvas/70"
        onClick={() => { onClose(); returnFocusTo(returnTo) }}
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects} />
      <motion.div ref={trapRef} role="dialog" aria-modal="true" aria-label={name} data-phone-sheet=""
        className="relative flex max-h-[85vh] flex-col rounded-t-xl bg-surface-container px-m pb-l pt-s shadow-sheet"
        initial={reduce ? { opacity: 0 } : { opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
        transition={spring.effects}>
        <span aria-hidden data-grab-line className="mx-auto mb-s h-1 w-9 shrink-0 rounded-pill bg-outline" />
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <InPhoneSheet.Provider value>{children}</InPhoneSheet.Provider>
        </div>
      </motion.div>
    </div>,
    document.body,
  )
}
