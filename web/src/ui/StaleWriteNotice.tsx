import { useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Button } from './Button'
import { Modal } from './Modal'
import { UnifiedDiff } from './UnifiedDiff'
import { ERROR_SURFACE_PAINT } from '../design/errorTreatments'
import { HELD_CHANGE_REASON, differencePatch } from '../lib/staleWrite'
import type { StaleWriteGuard } from '../lib/useStaleWriteGuard'

/** ── A save refused because the document changed elsewhere ────────────────────────────────
 *
 *  The one recovery every whole-document save offers (`lib/useStaleWriteGuard`). The gateway
 *  answered `409 stale_write`: another tab, another device, or PersonalClaw itself saved since this
 *  page read the document, and saving this page's copy would have undone that. So nothing was
 *  written, and this says so — with the user's change KEPT until they choose:
 *
 *    · **Reload and reapply** — put their change back on top of what is stored now, and save that;
 *    · **Review the difference** — see what changed elsewhere and what their save would change;
 *    · **Discard my change** — keep what is stored.
 *
 *  When the change cannot be put back on its own — the other save changed the same part — there
 *  is no Reapply. Review says why, and offers to save theirs over it only from inside the review,
 *  where the user can see what that replaces.
 *
 *  🔑 AN ALERT. The user pressed Save and it did not save: that changes what the screen means, the
 *  same reason a failed save line is `role="alert"`. Painted as the failure band (`InlineError`).
 *
 *  🔴 AND BROUGHT TO THE USER WHEN IT APPEARS. On a long form it rendered below the fold — measured
 *  at y=2114 in a 1000px window on the prompt editor, 1200px under the sticky Save bar — and the only
 *  sign on screen that the save had not happened was Save turning grey, with focus dropped to
 *  `<body>`. It scrolls just far enough to be seen (with room for a sticky bar above or below) and
 *  takes focus, as `FormFooter` does for its own refusal, so a keyboard user lands on the choice.
 */
export function StaleWriteNotice<T>({ guard, what, present, className }: {
  /** The guard the surface's save runs through. Renders nothing until it holds a conflict. */
  guard: StaleWriteGuard<T>
  /** The document, as it starts a sentence: "Your projection rules", "This skill". */
  what: string
  /** The document as the review shows it, when the saved form would mislead — a stored secret the
   *  editor holds blank ("keep it") reads as an empty value unless it is shown as saved. */
  present?: (doc: T) => unknown
  className?: string
}) {
  const [reviewing, setReviewing] = useState(false)
  const band = useRef<HTMLDivElement>(null)
  const c = guard.conflict
  const open = c !== null
  useEffect(() => {
    if (!open) return
    // `block: 'nearest'`: already on screen, nothing moves; off it, the smallest scroll shows it —
    // clear of a sticky bar by the band's scroll margin. Then focus, without a second scroll.
    band.current?.scrollIntoView?.({ block: 'nearest' })
    band.current?.focus({ preventScroll: true })
  }, [open])
  if (!c) return null
  const shown = (doc: T) => (present ? present(doc) : doc)
  const reading = c.theirs === undefined && !c.error
  const clean = c.rebased !== null && c.rebased !== undefined
  // Both actions work on the stored version, so until it is read there is nothing to act on. The
  // reason is the true one for each state: still reading, or the read failed (Try again beside it).
  const unread = c.theirs === undefined
    ? (c.error ? 'The current version could not be read' : 'Reading the current version…')
    : undefined
  const close = () => setReviewing(false)
  const act = async (run: () => Promise<boolean>) => { if (await run()) close() }
  return (
    <div ref={band} role="alert" tabIndex={-1} data-stale-write="true" data-type="body-s"
      className={`flex scroll-my-20 items-start gap-s rounded-lg px-m py-s outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-danger ${className ?? ''}`}
      style={ERROR_SURFACE_PAINT}>
      <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-s">
        <p>
          <span className="fw-500">{what} changed elsewhere</span>, so your change
          wasn’t saved. It’s kept until you reapply or discard it.
        </p>
        {c.rebased === null && (
          <p>Your change and the other one touch the same part, so it can’t be re-applied on its own — review the difference to choose.</p>
        )}
        {c.error && <p>{c.error}</p>}
        <div className="flex flex-wrap items-center gap-s">
          {c.rebased !== null && (
            <Button size="xs" onClick={() => void guard.reapply()} loading={guard.busy || reading}
              loadingLabel={reading ? 'Reading…' : 'Saving…'}
              disabled={!clean} disabledReason={unread}>
              Reload and reapply
            </Button>
          )}
          <Button size="xs" variant="secondary" onClick={() => setReviewing(true)}
            disabled={c.theirs === undefined} disabledReason={unread}>
            Review the difference
          </Button>
          {c.error && c.theirs === undefined && (
            <Button size="xs" variant="secondary" onClick={() => void guard.retry()}>Try again</Button>
          )}
          {/* Not offered while a re-applied save is on the wire: that save may land, and a Discard
              beside it would promise an outcome the gateway may already have overruled. */}
          {!guard.busy && (
            <Button size="xs" variant="ghost" onClick={guard.discard}>Discard my change</Button>
          )}
        </div>
      </div>
      {reviewing && c.theirs && (
        <Modal title="Review the difference" onClose={close}>
          <div data-type="body-s" className="flex flex-col gap-l text-on-surface">
            <section className="flex flex-col gap-1.5">
              <h2 data-type="title-s">Changed elsewhere</h2>
              <p className="text-on-surface-low">What is stored now, compared with the copy you were editing.</p>
              <Difference patch={differencePatch(shown(c.base), shown(c.theirs.value))} label="What changed elsewhere" />
            </section>
            <section className="flex flex-col gap-1.5">
              <h2 data-type="title-s">Your change</h2>
              {clean ? (
                <>
                  <p className="text-on-surface-low">What saving yours now would change, on top of what is stored.</p>
                  <Difference patch={differencePatch(shown(c.theirs.value), shown(c.rebased as T))} label="Your change, re-applied" />
                </>
              ) : (
                <>
                  <p className="text-on-surface-low">Your version, compared with the copy you were editing. Both changed the same part, so saving yours replaces the change above.</p>
                  <Difference patch={differencePatch(shown(c.base), shown(c.mine))} label="Your change" />
                </>
              )}
            </section>
            {c.error && <p className="text-danger">{c.error}</p>}
            <div className="flex flex-wrap justify-end gap-s">
              {!guard.busy && <Button variant="ghost" onClick={() => { guard.discard(); close() }}>Discard my change</Button>}
              {clean ? (
                <Button onClick={() => void act(guard.reapply)} loading={guard.busy}>Reapply my change</Button>
              ) : (
                <Button variant="danger" onClick={() => void act(guard.keepMine)} loading={guard.busy}>Save mine over it</Button>
              )}
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}

/** One difference, or the sentence for none — an empty patch box would read as a broken one. */
function Difference({ patch, label }: { patch: string; label: string }) {
  if (!patch) return <p className="text-on-surface-low">No difference — it already matches.</p>
  return (
    <UnifiedDiff patch={patch} label={label}
      className="max-h-64 overflow-auto rounded-md bg-surface-container px-s py-xs font-mono leading-snug" />
  )
}

/** The editor a refused change was made in, frozen while the notice holds that change.
 *
 *  "It’s kept until you reapply or discard it" is a promise about ONE change — the one the save was
 *  built from. Reload and reapply puts exactly that change back, so an edit typed into the form
 *  after the refusal was silently dropped by it. While the change is held, the controls it was made
 *  with are off (a disabled `<fieldset>` turns off every control inside it), with the reason every
 *  locked control gives; the notice and its actions stay outside, where the choice is made. */
export function HeldChange<T>({ guard, children }: { guard: StaleWriteGuard<T>; children: ReactNode }) {
  const held = guard.conflict !== null
  return (
    <fieldset data-held-change="" disabled={held} title={held ? HELD_CHANGE_REASON : undefined} className="contents">
      {children}
    </fieldset>
  )
}
