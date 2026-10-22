import { Bookmark, BookmarkCheck } from 'lucide-react'
import type { SessionMapEntry, SessionMapOutput } from './sessionMap'
import { clockTime, fullStamp, isoStamp } from '../../lib/epoch'
import { rowSubject } from '../../lib/rowSubject'
import { IconButton } from '../../ui/IconButton'

/** SESSION MAP PREVIEW — what a marker says when you point at it, arrow to it or scrub over it.
 *
 *  A marker is a short line: it says WHERE a message is in the session and nothing about WHAT it
 *  was. The preview answers that (#4776): the request, a muted excerpt of the start of the reply,
 *  what the reply did (its first two typed outputs, and how many more), the message's time, and
 *  the control that bookmarks it. The rail shows ONE preview, beside whichever marker is the
 *  target, so two can never be open at once.
 *
 *   · the REQUEST in `--color-on-surface` — deliberately NOT `--color-on-surface-low`, which is the
 *     "very low contrast metadata" ramp. The dimmed ramp is for chrome (the clock time), never for
 *     the sentence you came to read.
 *   · the REPLY EXCERPT under a hairline divider, muted in `--color-on-surface-var` — muted, and
 *     still text-legible on the card, which is what `e2e/a11y.spec.ts` sweeps with the card open.
 *     While the newest question's reply is still on its way, a still placeholder holds its place,
 *     so the card does not grow under the pointer when the reply arrives.
 *   · NO ROLE LABEL. Every entry IS a user message and its excerpt IS the reply to it
 *     (`sessionMapEntries`), so the roles are implicit in the map's shape.
 */

/** How much of a message may become part of a marker's accessible NAME. The 40-char budget is
 *  `lib/rowSubject`'s row-scoped one (`design/computedNames.test.tsx` measured the app-wide cap): a
 *  name must be DISTINGUISHING and BOUNDED, and a rail of markers is a dense row list. */
const NAME_SUBJECT_CAP = 40

/** The accessible NAME of a marker: its position among the user's messages, whether it is
 *  bookmarked, and a bounded piece of the message — "Message 12 of 40, bookmarked: {first words}",
 *  never the full text. The rail's marker, the drawer's row and the rail's jump announcement all
 *  read this position, so the two forms cannot number a message differently. */
export function sessionMapMarkName(entries: SessionMapEntry[], index: number, audio?: MarkAudio): string {
  const entry = entries[index]
  return `Message ${index + 1} of ${entries.length}${entry.bookmarked ? ', bookmarked' : ''}${audio ? `, ${audio}` : ''}: ${rowSubject([entry.preview], NAME_SUBJECT_CAP)}`
}

/** A marker's audio state, in the words its name and its preview say it: its reply is being read
 *  aloud, or the microphone is listening at the conversation's end. */
export type MarkAudio = 'playing' | 'listening'

const AUDIO_WORDS: Record<MarkAudio, string> = { playing: 'Playing — this reply is being read aloud', listening: 'Listening — your microphone is on' }

/** How many of an exchange's outputs the preview lists before it counts the rest. */
export const PREVIEW_OUTPUTS = 2

/** The word an output is listed under. A failed tool call says so. */
function outputWord(o: SessionMapOutput): string {
  if (o.kind === 'tool') return o.ok === false ? 'Failed' : 'Ran'
  if (o.kind === 'approval') return 'Asked'
  if (o.kind === 'error') return 'Error'
  return 'Step'
}

/** The bookmark control's name and, when it cannot act, why. */
export function bookmarkLabel(entry: SessionMapEntry): { label: string; unavailable?: string } {
  if (!entry.ts) return { label: 'Bookmark this message', unavailable: 'It can be bookmarked once it is saved' }
  return { label: entry.bookmarked ? 'Remove bookmark' : 'Bookmark this message' }
}

export interface SessionMapCardProps {
  /** The entry being previewed. */
  entry: SessionMapEntry
  /** The newest question's reply is still on its way, so its excerpt is held by a placeholder. */
  replyPending?: boolean
  /** Bookmark the message, or take its bookmark off. Absent: no control (nothing can keep one). */
  onToggleBookmark?: (entry: SessionMapEntry) => void
  /** The keys that move between messages from anywhere, as this platform writes them: the rail's
   *  preview is where they are shown, as a tooltip shows a control's keys. */
  keys?: string
  /** This message's audio state, said in words (the rail's still indicator, labelled). */
  audio?: MarkAudio
}

/** The preview's body. The rail owns where it sits; this owns what is in it.
 *
 *  NOT `aria-hidden`. The card carries strictly MORE than its trigger's accessible name (a
 *  timestamp, the reply excerpt, what the reply did and the bookmark control), and hiding real
 *  content from assistive tech to dodge a double-read would be trading a whole surface for a
 *  duplicated phrase. */
export function SessionMapCard({ entry, replyPending = false, onToggleBookmark, keys, audio }: SessionMapCardProps) {
  const iso = isoStamp(entry.ts)
  const shown = entry.outputs.slice(0, PREVIEW_OUTPUTS)
  const more = entry.outputs.length - shown.length
  const bookmark = bookmarkLabel(entry)
  return (
    <div data-session-map-card>
      <div className="flex min-h-8 items-center gap-s">
        {/* An unreadable stamp renders NOTHING rather than a placeholder — `lib/epoch`'s contract,
            so a message with no `ts` shows a card with no clock instead of "Invalid Date". */}
        {iso ? (
          <time
            data-type="caption"
            data-session-map-time
            dateTime={iso}
            title={fullStamp(entry.ts)}
            className="block tabular-nums text-on-surface-low"
          >
            {clockTime(entry.ts)}
          </time>
        ) : null}
        <span className="flex-1" />
        {onToggleBookmark ? (
          <IconButton
            icon={entry.bookmarked ? BookmarkCheck : Bookmark}
            label={bookmark.label}
            active={entry.bookmarked}
            size={32}
            iconSize={16}
            disabled={!!bookmark.unavailable}
            disabledReason={bookmark.unavailable}
            onClick={() => onToggleBookmark(entry)}
          />
        ) : null}
      </div>
      {audio ? <p data-type="caption" data-session-map-audio={audio} className="mt-xs text-on-surface">{AUDIO_WORDS[audio]}</p> : null}
      <p data-type="body-s" data-session-map-request className="mt-xs line-clamp-2 text-on-surface">
        {entry.preview}
      </p>
      {entry.response ? (
        <p
          data-type="caption"
          data-session-map-response
          className="mt-s line-clamp-3 border-t border-outline-variant pt-s text-on-surface-var"
        >
          {entry.response}
        </p>
      ) : replyPending ? (
        // Still, on purpose: a placeholder that shimmered would be motion on a rail whose rest is
        // still. One caption line's height, so the arriving reply replaces it in place.
        <span data-session-map-pending className="mt-s block border-t border-outline-variant pt-s">
          <span className="sr-only">The reply is on its way</span>
          <span aria-hidden className="block h-4 w-3/4 rounded-sm bg-surface-high" />
        </span>
      ) : null}
      {shown.length ? (
        <ul data-session-map-outputs className="mt-s flex flex-col gap-xs">
          {shown.map((o, i) => (
            <li key={i} data-type="caption" data-session-map-output={o.kind} className="flex min-w-0 gap-xs text-on-surface-var">
              <span className="shrink-0 text-on-surface">{outputWord(o)}</span>
              <span className="truncate">{o.preview}</span>
            </li>
          ))}
          {more > 0 ? (
            <li data-type="caption" data-session-map-more className="text-on-surface-var">+{more} more</li>
          ) : null}
        </ul>
      ) : null}
      {keys ? (
        <p data-type="caption" data-session-map-keys className="mt-s border-t border-outline-variant pt-s text-on-surface-var">{keys}</p>
      ) : null}
    </div>
  )
}
