import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { api, hasApiCode, type OnboardingState } from '../../../lib/api'
import { notify } from '../../../app/appSdk'
import { Button } from '../../../ui/Button'
import { Modal } from '../../../ui/Modal'
import { TOUCH_TARGET } from '../../../ui/Toggle'
import { aDialogIsOpen } from '../../../ui/aDialogIsOpen'
import { BUSY_REASON } from '../../../ui/unavailable'
import { productTourRunning } from '../../../app/onboarding/tourLaunch'
import explanationsTable from '../../../../../src/personalclaw/explanations.json'

/** The answers: the Explanations choices (`explanations.json`, the Settings row's table) other
 *  than "No preference", each with the table's label and hint. */
const ANSWERS = (explanationsTable as { id: string; label: string; hint: string }[]).filter((r) => r.id !== 'default')

/** How long the page must have been on screen before the window may open, so a route change
 *  that passes through a new chat never flashes it. */
const SETTLED_MS = 400
/** How often a waiting window looks again: once a second, the most an idle page's timer may. */
const LOOK_AGAIN_MS = 1000

/** Set as a new chat starts to ask, for the page load. Once a claim has answered, a 409 says it was
 *  asked, or the read says asked, no later new chat asks. A failed read, a home not set up, a new
 *  chat left during the read and a claim that failed for any other reason clear it, so the next
 *  new chat tries again. A moment gone by the time the read answers clears it too, and this new
 *  chat looks again. */
let claimed = false
/** Claimed, not yet shown: the claim answered when this new chat had gone or the moment had
 *  passed. The next moment, in this new chat or the next one, opens the window with no second
 *  read or claim. */
let owed = false

/** Something else has the owner now: a window, the palette, the phone's navigation drawer or a
 *  menu is open over the page, or a text field other than the composer's editor has the focus. */
function busyElsewhere(): boolean {
  if (aDialogIsOpen() || document.querySelector('[role=dialog]:not([inert]), [data-command-palette], [data-flyout]')) return true
  const a = document.activeElement
  return a instanceof HTMLElement && !a.closest('.cm-editor')
    && a.matches('textarea, input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=button]):not([type=submit]), [contenteditable]:not([contenteditable=false])')
}

/** "How should I explain things?", asked once, in a small window on the first new chat after setup.
 *
 *  It opens only at a moment when all of these hold: the page has been visible for
 *  {@link SETTLED_MS}; the product tour is not walking the app (`tourLaunch.productTourRunning`),
 *  so nothing is asked of the owner or the gateway while it does; no draft is under way; and
 *  nothing else has the owner (`busyElsewhere`). Then `GET /api/onboarding` must say setup is done
 *  and the question is still open, the moment must still hold, and the claim,
 *  `POST /api/onboarding/explain {"answer": "skip"}`, must answer. It opens at once if the moment
 *  still holds, and at the next one otherwise. A read that fails shows nothing. A 409
 *  (`question_answered`: asked in another window) keeps it closed. While the moment does not hold,
 *  it looks again every {@link LOOK_AGAIN_MS}.
 *
 *  The question is recorded as asked by the claim, in the home's state (`explain_asked`), so a
 *  reload, a closed tab, Back or a second window cannot bring it back. A choice is the owner's
 *  Explanations default (`agent.explain`, written as the Settings row writes it); while it saves,
 *  every answer waits, and a refused one keeps the window open with the server's sentence, which a
 *  toast says too. Use the default, ×, Escape and a scrim click close it and write nothing more.
 *  Rendered through the Modal's portal, so nothing in the new chat's hero moves; `onClosed` runs
 *  once it has gone (the composer's editor takes the focus). */
export function ExplainQuestion({ draftUnderWay, onClosed }: {
  /** Whether the composer holds anything of a draft, or was typed in a moment ago: a window never
   *  opens over a draft under way. */
  draftUnderWay: () => boolean
  onClosed: () => void
}) {
  const [open, setOpen] = useState(false)
  const underWay = useRef(draftUnderWay)
  underWay.current = draftUnderWay

  useEffect(() => {
    if (claimed && !owed) return
    let live = true
    let timer = 0
    let settled = false
    const moment = () => settled && !productTourRunning() && !underWay.current() && !busyElsewhere()
    // Not while the page is out of sight: showing it again settles it anew.
    const look = () => { window.clearTimeout(timer); if (live && settled && (!claimed || owed)) timer = window.setTimeout(() => { void ask() }, LOOK_AGAIN_MS) }
    /** The timer itself marks the page as settled, so the settle reads one clock. */
    const settle = () => {
      settled = false
      window.clearTimeout(timer)
      if (live && document.visibilityState === 'visible') timer = window.setTimeout(() => { settled = true; void ask() }, SETTLED_MS)
    }
    const ask = async () => {
      if (!live) return
      if (!moment()) { look(); return }
      if (owed) { owed = false; setOpen(true); return }
      if (claimed) return
      claimed = true
      let state: OnboardingState | null = null
      try { state = await api.onboarding() } catch { /* a read that fails shows nothing */ }
      if (!live || !state || state.step !== 'done' || state.explain_asked !== false) {
        // Asked already: never again this page load. Not read, not set up, or gone: a later new
        // chat looks again.
        claimed = !!state && state.explain_asked === true
        return
      }
      // The read takes a while: a draft, a window or the tour may have come since.
      if (!moment()) { claimed = false; look(); return }
      try {
        await api.answerExplain('skip')
      } catch (e) {
        if (!hasApiCode(e, 'question_answered')) claimed = false
        return
      }
      owed = true
      void ask()
    }
    document.addEventListener('visibilitychange', settle)
    settle()
    return () => { live = false; window.clearTimeout(timer); document.removeEventListener('visibilitychange', settle) }
  }, [])

  return (
    <AnimatePresence onExitComplete={onClosed}>
      {open && <ExplainWindow onClose={() => setOpen(false)} />}
    </AnimatePresence>
  )
}

/** The window itself: what the feature is, the three choices and Use the default, each described
 *  by its hint, and where to change it. Focus opens on its first paragraph, set in a layout
 *  effect, which runs before the Modal's trap (a passive effect), so an Enter meant for the
 *  composer cannot pick an answer. */
function ExplainWindow({ onClose }: { onClose: () => void }) {
  /** Each answer is described by its hint, read after its name. */
  const hints = useId()
  const lead = useRef<HTMLParagraphElement>(null)
  const refusal = useRef<HTMLParagraphElement>(null)
  /** The answer being saved; every answer waits on it, and its own says so. */
  const [busy, setBusy] = useState<string | null>(null)
  const [refused, setRefused] = useState('')
  useLayoutEffect(() => { lead.current?.focus() }, [])
  // The pressed button was disabled while it saved, which drops the focus: the refusal takes it.
  useEffect(() => { if (refused) refusal.current?.focus() }, [refused])

  const choose = (id: string) => {
    setBusy(id)
    setRefused('')
    api.patchConfig('agent.explain', id)
      .then(() => onClose())
      .catch((e) => {
        const why = String((e as Error)?.message || e)
        setRefused(why)
        // Said in a toast too, which outlasts a window closed before the answer lands.
        notify(`Couldn't save how you like things explained: ${why}`, 'error')
      })
      .finally(() => setBusy(null))
  }

  return (
    <Modal title="How should I explain things?" size="compact" onClose={onClose}>
      <div className="flex flex-col gap-l">
        <p ref={lead} tabIndex={-1} data-type="body-m" className="text-on-surface-var">
          I can shape how I explain things to you. That never changes a fact, a caveat or a refusal.
        </p>
        <div className="flex flex-col gap-m">
          {ANSWERS.map(({ id, label, hint }) => (
            <div key={id} className="flex flex-col items-start gap-1">
              <Button variant="tonal" size="sm" className={TOUCH_TARGET} loading={busy === id} disabled={busy !== null}
                ariaDescribedBy={`${hints}-${id}`} onClick={() => choose(id)}>{label}</Button>
              <span id={`${hints}-${id}`} data-type="caption" className="text-on-surface-low">{hint}</span>
            </div>
          ))}
        </div>
        {refused && <p ref={refusal} tabIndex={-1} role="alert" data-type="caption" className="text-on-surface-low">Not saved: {refused}</p>}
        <div className="flex flex-wrap items-baseline gap-x-m gap-y-1">
          <Button variant="ghost" size="sm" className={TOUCH_TARGET} disabled={busy !== null}
            disabledReason={BUSY_REASON} ariaDescribedBy={`${hints}-default`} onClick={onClose}>
            Use the default
          </Button>
          <span id={`${hints}-default`} data-type="caption" className="text-on-surface-low">No preference: I explain things my usual way.</span>
        </div>
        <p data-type="caption" className="text-on-surface-low">
          Change this any time in Settings → Agent → Agent defaults → Explanations. For one chat only, use Chat preferences, the gear on the chat box.
        </p>
      </div>
    </Modal>
  )
}
