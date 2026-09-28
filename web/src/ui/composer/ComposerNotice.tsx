import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Info, X } from 'lucide-react'
import { IconButton } from '../IconButton'

/** The line by a composer that says what failed, or what just happened — the chat's and the
 *  loop front door's alike.
 *
 *  An ERROR stays until the user dismisses it or sends again. It used to clear itself after six
 *  seconds, and the reasons it carries are sentences a user has to read and act on — a
 *  speech-to-text provider saying which sign-in failed and how to redo it runs past three
 *  hundred characters — so the fix was gone before it was read. Only an INFORMATIONAL notice
 *  clears on its own: nothing failed, and nothing is lost when it goes.
 */
export type ComposerNoticeTone = 'error' | 'info'

export interface ComposerNotice {
  text: string
  tone: ComposerNoticeTone
}

/** How long an informational notice shows before it clears itself. */
export const INFO_NOTICE_MS = 4000

export interface ComposerNoticeState {
  notice: ComposerNotice | null
  /** Show what failed. It stays until {@link ComposerNoticeState.clear}. */
  showError: (text: string) => void
  /** Show what just happened; it clears itself after {@link INFO_NOTICE_MS}. */
  showInfo: (text: string) => void
  /** Take the notice down: the user dismissed it, sent again, or it no longer holds. */
  clear: () => void
}

export function useComposerNotice(): ComposerNoticeState {
  const [notice, setNotice] = useState<ComposerNotice | null>(null)
  // The one pending self-clear. Every change of notice cancels it, so an informational
  // notice's timer can never take down an error shown after it.
  const timer = useRef<number | undefined>(undefined)
  const stopTimer = useCallback(() => {
    if (timer.current !== undefined) window.clearTimeout(timer.current)
    timer.current = undefined
  }, [])
  useEffect(() => stopTimer, [stopTimer])

  const showError = useCallback((text: string) => {
    stopTimer()
    setNotice({ text, tone: 'error' })
  }, [stopTimer])
  const showInfo = useCallback((text: string) => {
    stopTimer()
    setNotice({ text, tone: 'info' })
    timer.current = window.setTimeout(() => {
      timer.current = undefined
      setNotice(null)
    }, INFO_NOTICE_MS)
  }, [stopTimer])
  const clear = useCallback(() => {
    stopTimer()
    setNotice(null)
  }, [stopTimer])

  return { notice, showError, showInfo, clear }
}

/** The notice as a composer shows it: an error with a Dismiss button, announced as an alert; an
 *  informational notice as a status line with none. ``className`` places it in its host's
 *  layout (the chat's line above its composer by default). */
export function ComposerNoticeLine({ notice, onDismiss, className = 'mb-2' }: {
  notice: ComposerNotice | null
  onDismiss: () => void
  className?: string
}) {
  if (!notice) return null
  if (notice.tone === 'info') {
    return (
      <div role="status" data-type="body-s" className={`${className} flex items-center gap-1.5 text-on-surface-var`}>
        <Info size={13} className="shrink-0" /><span>{notice.text}</span>
      </div>
    )
  }
  return (
    <div role="alert" data-type="body-s" className={`${className} flex items-start gap-1.5 rounded-md px-2.5 py-1.5`}
      style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}>
      <AlertTriangle size={13} className="mt-0.5 shrink-0" />
      <span className="min-w-0 flex-1 break-words">{notice.text}</span>
      <IconButton icon={X} label="Dismiss" onClick={onDismiss} size={20} iconSize={12}
        className="shrink-0 opacity-70 hover:opacity-100" />
    </div>
  )
}
