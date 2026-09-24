import { motion } from 'framer-motion'
import { Archive, PauseCircle, Reply } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { StatusPill } from '../../ui/StatusPill'
import { messageEnter } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { parkedQueue, roundBudgetLabel } from './roomMeta'
import type { RoomRecord } from '../../lib/api'

/** The round-budget pause card (`AGENT-ROOMS` C4/C9, `AR-8`).
 *
 *  Shown when a room has spent its round budget without a word from its human. It sits at the
 *  END of the transcript, in the slot the chat's approval card occupies for the same reason:
 *  the thing blocking the conversation belongs where the conversation stopped, not in a corner.
 *
 *  ── WHAT IT MUST SAY, AND WHY EACH PART IS HERE ──
 *
 *  **The budget, resolved.** `rounds_used` against `effective_round_budget` — never the
 *  declared `round_budget`, which is 0 when the room inherits the configured default. "0 of 0"
 *  is exactly the inert reading this atom exists to remove.
 *
 *  **The queue, in order.** `pending_queue` is the members that were owed a turn when the
 *  ceiling was reached, and it is the whole reason the field is persisted: a pause SUSPENDS the
 *  queue, so replying continues the deliberation rather than restarting it. A card that showed
 *  only a count would leave the user unable to tell "my analyst never got to answer" from "the
 *  room simply stopped", which is the difference the park exists to preserve.
 *
 *  **Only the actions the backend supports.** There is no resume endpoint and there must not be
 *  a Resume button: `reset_round_budget` is called by ANY human message, so the reply IS the
 *  resume. A separate button would be a second way to do one thing, and the one that did not
 *  also say anything would leave the room running with nothing new to discuss. So the primary
 *  action focuses the composer, and the wording says what will happen to the queue.
 *
 *  ── WHY IT IS NOT `ApprovalPrompt` ──
 *
 *  That component is the one renderer for "the agent is blocked waiting on your PERMISSION" —
 *  a tool, its arguments, and a grant/deny vocabulary. A paused room is blocked on the user's
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
 *  Info-toned, matching the `agent/room_paused` notification row exactly. The room reached a
 *  ceiling its user configured: nothing failed and nothing is at risk. Warn here would make a
 *  working feature read as a fault and would disagree with the inbox about one event.
 */
export function RoomPauseCard({ room, onReply, onArchive }: {
  room: RoomRecord
  /** Focus the composer. The reply is the resume — see the docblock. */
  onReply: () => void
  onArchive: () => void
}) {
  const parked = parkedQueue(room)
  return (
    <motion.div
      variants={messageEnter}
      initial="initial"
      animate="animate"
      // `role="group"` + a name, the shape `ApprovalPrompt` uses: this is a labelled region
      // holding controls, not an alert. It is NOT announced — the inbox item already did that,
      // and a live region here would tell the user twice about one event.
      role="group"
      aria-label={`${room.title} is paused`}
      // 🔑 A SURFACE RAMP STEP, NOT A TINT. The obvious shape here is an info-tinted fill — which
      // is what `ApprovalPrompt` does for its warn card — but every inline `color-mix` in `pages/`
      // is counted by `design/statusTint` precisely so a page reaches for the shipped primitive
      // instead of minting its own tint. The tone is carried where the rail wants it: by the
      // `StatusPill` and the icon, both info-toned. `surface-high` is one step above the
      // `surface-container` a room note uses, which is what keeps the two distinguishable.
      className="rounded-lg bg-surface-high px-l py-l">
      <div className="flex items-start gap-m">
        <PauseCircle size={20} className="mt-0.5 shrink-0" style={{ color: 'var(--color-info)' }} aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-s">
            {/* The pause sentence, verbatim from `rooms.arbiter.PAUSE_TITLE`. It is product
                tone rather than an incidental string, and it is the same words the inbox row
                carries — a user who arrives from the inbox must read the same sentence here. */}
            {/* `h2`, one rung under the room title's `h1` (`PageTitle`). The card is a section of
                the room, not a subsection of another heading — and the tree's heading ladder is
                asserted by `discover/discoverHeadingLevel`. */}
            <h2 data-type="title-m" className="text-on-surface">Your agents have been talking for a while.</h2>
            <StatusPill tone="info">{roundBudgetLabel(room)}</StatusPill>
          </div>
          <p data-type="body-m" className="mt-xs text-on-surface-var" style={fvs(400)}>
            {parked.length > 0
              ? 'Reply to keep it going — the members below speak first, then anyone your message names.'
              : 'Reply to keep it going, or archive the room if the thread is finished.'}
          </p>
          {parked.length > 0 && (
            <div className="mt-m">
              {/* A `span`, not a heading: the card's own h3 is the pause sentence, and a second
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
                {parked.map((name, i) => (
                  <li key={name} data-type="body-s" className="inline-flex items-center gap-xs text-on-surface-var">
                    <span className="text-on-surface-low">{i + 1}.</span>
                    <span style={fvs(500)}>{name}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}
          <div className="mt-l flex flex-wrap items-center gap-s">
            <Button size="sm" onClick={onReply}>
              <Reply size={14} aria-hidden /> Write a reply
            </Button>
            <Button size="sm" variant="secondary" onClick={onArchive}>
              <Archive size={14} aria-hidden /> Archive the room
            </Button>
          </div>
        </div>
      </div>
    </motion.div>
  )
}
