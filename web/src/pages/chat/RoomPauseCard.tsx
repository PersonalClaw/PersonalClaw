import { motion } from 'framer-motion'
import { Archive, CircleAlert, PauseCircle, Play, Reply } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { StatusPill } from '../../ui/StatusPill'
import { messageEnter } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { roundBudgetLabel } from './roomMeta'
import type { RoomRecord } from '../../lib/api'

/** The card for a room that has STOPPED short of answering (`AGENT-ROOMS` C4/C9, `AR-8`) — for
 *  one of two reasons, with two different ways back.
 *
 *  · **Paused** — the room spent its round budget without a word from its human.
 *  · **Interrupted** — a round was running and stopped before everyone answered: the gateway
 *    restarted mid-round, or the round could not run at all. The human's message is on the
 *    transcript and unanswered.
 *
 *  It sits at the END of the transcript, in the slot the chat's approval card occupies for the
 *  same reason: the thing blocking the conversation belongs where the conversation stopped, not
 *  in a corner.
 *
 *  ── WHAT IT MUST SAY, AND WHY EACH PART IS HERE ──
 *
 *  **The budget, resolved.** `rounds_used` against `effective_round_budget` — never the
 *  declared `round_budget`, which is 0 when the room inherits the configured default. "0 of 0"
 *  is exactly the inert reading this atom exists to remove.
 *
 *  **The queue, in order.** `room.owed` is the members still owed a turn — the cut-off one
 *  first — and it is the whole reason the queue is persisted: a stop SUSPENDS the queue, so going
 *  on continues the deliberation rather than restarting it. A card that showed only a count
 *  would leave the user unable to tell "my analyst never got to answer" from "the room simply
 *  stopped", which is the difference the park exists to preserve.
 *
 *  **Only the actions the backend supports, and they differ by kind.** A PAUSED room has no
 *  resume endpoint and there must not be a Resume button: `reset_round_budget` is called by ANY
 *  human message, so the reply IS the resume — and a button that resumed without a word would
 *  restart the members' talk with nothing new to discuss, which is what the budget exists to
 *  stop. An INTERRUPTED room is the opposite case: its human already spoke and nobody answered,
 *  so Continue (`POST .../continue`) is the primary action — it answers the message that is
 *  already there, where replying would make the user re-ask.
 *
 *  ── WHY IT IS NOT `ApprovalPrompt` ──
 *
 *  That component is the one renderer for "the agent is blocked waiting on your PERMISSION" —
 *  a tool, its arguments, and a grant/deny vocabulary. A stopped room is blocked on the user's
 *  ATTENTION, has no tool and no arguments, and nothing about answering it grants anything.
 *  Rendering it through a permission prompt would say a security decision is pending when none
 *  is.
 *
 *  ── WHY IT DOES NOT DUPLICATE THE INBOX ROW ──
 *
 *  The pause raises exactly one attention item (`emit_attention_item`, `dedup_key`
 *  `room_paused:<id>`) and that item's deep link lands HERE, on this room. So this card is the
 *  DESTINATION of that row, not a second notice: it announces nothing, plays no cue, and
 *  creates no state. The row closes when the human replies, by the same message that clears
 *  this card — one event, one item, one lifecycle.
 *
 *  Info-toned when paused, matching the `agent/room_paused` notification row exactly: the room
 *  reached a ceiling its user configured, nothing failed and nothing is at risk. Warn-toned when
 *  interrupted, because an answer the user asked for did not arrive.
 */
export function RoomPauseCard({ room, interrupted = false, continuing = false, onContinue, onReply, onArchive }: {
  room: RoomRecord
  /** The round stopped before everyone answered, rather than pausing at its budget. */
  interrupted?: boolean
  /** The Continue request is in flight. */
  continuing?: boolean
  /** Finish the interrupted round — the members still owed answer the message already sent. */
  onContinue?: () => void
  /** Focus the composer. For a paused room the reply is the resume — see the docblock. */
  onReply: () => void
  onArchive: () => void
}) {
  const owed = room.owed
  const Icon = interrupted ? CircleAlert : PauseCircle
  return (
    <motion.div
      variants={messageEnter}
      initial="initial"
      animate="animate"
      // `role="group"` + a name, the shape `ApprovalPrompt` uses: this is a labelled region
      // holding controls, not an alert. It is NOT announced — the inbox item already did that,
      // and a live region here would tell the user twice about one event.
      role="group"
      aria-label={interrupted ? `${room.title} was interrupted` : `${room.title} is paused`}
      // 🔑 A SURFACE RAMP STEP, NOT A TINT. The obvious shape here is a tone-tinted fill — which
      // is what `ApprovalPrompt` does for its warn card — but every inline `color-mix` in `pages/`
      // is counted by `design/statusTint` precisely so a page reaches for the shipped primitive
      // instead of minting its own tint. The tone is carried where the rail wants it: by the
      // `StatusPill` and the icon. `surface-high` is one step above the `surface-container` a room
      // note uses, which is what keeps the two distinguishable.
      className="rounded-lg bg-surface-high px-l py-l">
      <div className="flex items-start gap-m">
        <Icon size={20} className="mt-0.5 shrink-0"
          style={{ color: interrupted ? 'var(--color-warn)' : 'var(--color-info)' }} aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-s">
            {/* The paused sentence is verbatim `rooms.arbiter.PAUSE_TITLE`: product tone rather
                than an incidental string, and the same words the inbox row carries — a user who
                arrives from the inbox must read the same sentence here. */}
            {/* `h2`, one rung under the room title's `h1` (`PageTitle`). The card is a section of
                the room, not a subsection of another heading — and the tree's heading ladder is
                asserted by `discover/discoverHeadingLevel`. */}
            <h2 data-type="title-m" className="text-on-surface">
              {interrupted ? 'This round stopped before everyone answered.' : 'Your agents have been talking for a while.'}
            </h2>
            {/* The budget is the REASON for a pause, so the pause card carries it. It is not why an
                interrupted round stopped, and a warn-toned count there read as if it were; the
                composer below states the count either way. */}
            {!interrupted && <StatusPill tone="info">{roundBudgetLabel(room)}</StatusPill>}
          </div>
          <p data-type="body-m" className="mt-xs text-on-surface-var" style={fvs(400)}>
            {interrupted
              ? 'Continue to let the members below answer your last message. Or write a reply — they still speak first.'
              : owed.length > 0
                ? 'Reply to keep it going — the members below speak first, then anyone your message names.'
                : 'Reply to keep it going, or archive the room if the thread is finished.'}
          </p>
          {owed.length > 0 && (
            <div className="mt-m">
              {/* A `span`, not a heading: the card's own h2 is the pause sentence, and a second
                  same-level heading for its sub-list would flatten the outline. `aria-labelledby`
                  names the list from it either way. */}
              <Eyebrow as="span" id="room-pause-queue">
                Still owed a turn
              </Eyebrow>
              {/* An ORDERED list, because FIFO order is the fact: these members were enqueued
                  before the message the user is about to write, so they are ahead of it. A
                  bulleted list would drop the only information the queue carries beyond its
                  membership. */}
              <ol aria-labelledby="room-pause-queue" className="mt-xs flex flex-wrap items-center gap-s">
                {owed.map((name, i) => (
                  <li key={name} data-type="body-s" className="inline-flex items-center gap-xs text-on-surface-var">
                    <span className="text-on-surface-low">{i + 1}.</span>
                    <span style={fvs(500)}>{name}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}
          <div className="mt-l flex flex-wrap items-center gap-s">
            {interrupted ? (
              <>
                <Button size="sm" loading={continuing} loadingLabel="Continuing the round" onClick={() => onContinue?.()}>
                  <Play size={14} aria-hidden /> Continue
                </Button>
                <Button size="sm" variant="secondary" onClick={onReply}>
                  <Reply size={14} aria-hidden /> Write a reply
                </Button>
              </>
            ) : (
              <>
                <Button size="sm" onClick={onReply}>
                  <Reply size={14} aria-hidden /> Write a reply
                </Button>
                <Button size="sm" variant="secondary" onClick={onArchive}>
                  <Archive size={14} aria-hidden /> Archive the room
                </Button>
              </>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  )
}
