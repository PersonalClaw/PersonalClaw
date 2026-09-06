/** The line a capped list owes the header that promised a bigger number.
 *
 *  🔑 THE DEFECT THIS EXISTS FOR IS A MISMATCH, NOT A CAP. Eleven lists in the app truncate; eight of
 *  them sit under a label that states the FULL count — `Relations · 47` above thirty rows,
 *  `Chats · 12` above eight. The header is honest and the list is honest, and nothing reconciles them,
 *  so a reader who trusts the header reads the list as all of it. Bounding the list is a fine layout
 *  choice; leaving the promise unmet is not.
 *
 *  🪤 AND THE THREE THAT DID DISCLOSE SPELLED IT THREE WAYS — `…{n} more`, `… {n} more` and `+{n} more`
 *  — which is how a shared sentence drifts when every site writes it again. One component, one wording,
 *  the majority form.
 *
 *  Renders nothing when nothing is hidden, so a caller can pass its numbers unconditionally rather
 *  than repeating the comparison at every site (a repeated `total > cap` is the same drift risk one
 *  level up). */
export function MoreRow({ total, shown, noun, className }: {
  /** How many items exist — the number the surrounding label states. */
  total: number
  /** How many are rendered, i.e. the cap actually applied. */
  shown: number
  /** What is hidden, when "… 6 more" alone would not say. Beneath a stacked list the subject is
   *  obvious from what sits above it; beneath a TABLE it is not — "… 6 more" could mean rows or
   *  columns, and those are very different facts about the data you are reading. Plural; the caller
   *  owns the word because only it knows what its rows are. */
  noun?: string
  /** Layout-only override for a caller whose list is a chip row rather than stacked rows. */
  className?: string
}) {
  const hidden = total - shown
  if (hidden <= 0) return null
  return (
    <div data-type="caption" className={`text-on-surface-low ${className ?? ''}`}>
      … {hidden} more{noun ? ` ${noun}` : ''}
    </div>
  )
}

/** Thousands-separated, so a four-digit count reads as one. `toLocaleString` is the house
 *  formatter for a rendered count (UsagePanel, AuditPanel, ChatPage all use it). */
const n = (v: number) => v.toLocaleString()

/** How much of the thing a view is showing, when the cap was applied UPSTREAM of the view.
 *
 *  🔑 THE SAME MISMATCH AS `MoreRow`, ONE LEVEL EARLIER, and that difference is why the sentence
 *  differs. `MoreRow` names the residue of a `.slice(0, N)` the caller itself wrote, so it can say
 *  "… 17 more" and vanish when nothing is hidden. Here the SERVER decided, the client is handed a
 *  partial answer plus the true total in the same payload, and the view has no rows to stop short of
 *  — a canvas simply draws less. So this states the scope positively ("768 of 1,540 relations") and
 *  always renders, because the count is the fact and the "of" is the caveat.
 *
 *  🪤 IT DOES NOT DISAPPEAR WHEN NOTHING IS HIDDEN. A component that vanishes is fine below a list
 *  whose label already carries the total; inside a summary line it would leave a hole between two
 *  separators, and `ui/danglingSeparator.test.ts` exists because that hole ships as a `·` that
 *  separates nothing. A complete view reads "1,540 relations" — the same sentence minus the caveat.
 *
 *  Measured, on a seeded library of 154 entities and 1,540 relations: the Knowledge header stated
 *  "relations 1540", `/api/knowledge/graph` shipped `edges_total: 1540, edges_kept: 768`, the canvas
 *  drew 768 lines and said nothing at all — issue 808. Nothing was broken except the silence. */
export function PartialCount({ shown, of, noun, singular, className }: {
  /** How many the view actually has in hand — the number it drew, listed or plotted. */
  shown: number
  /** How many exist. `'more'` for a cap whose total is genuinely unknowable — a ripgrep that
   *  stopped at its match limit never counted the rest, and inventing a total there would be a
   *  worse lie than the silence. Pass the count itself (or anything <= `shown`) when the view is
   *  complete; the caveat then drops and the count stays. */
  of: number | 'more'
  /** Plural word for what is counted. Required, unlike `MoreRow`'s: this sentence can be the ONLY
   *  count on the surface (a canvas has no list above it to name its own rows). */
  noun: string
  /** The singular, for the count that can legitimately be 1. Agreement follows the number the noun
   *  actually belongs to — the TOTAL in "1 of 1,540 relations", the shown count everywhere else — so
   *  a one-entity library reads "1 entity" and a heavily thinned one still reads "relations". */
  singular?: string
  /** Layout-only override — a segment inside a pill needs no colour of its own. */
  className?: string
}) {
  const partial = of === 'more' || of > shown
  const governing = of === 'more' || !partial ? shown : of
  const word = governing === 1 ? (singular ?? noun) : noun
  return (
    <span data-type="caption" className={`tabular-nums ${className ?? ''}`}>
      {of === 'more' ? `first ${n(shown)} ${word}` : partial ? `${n(shown)} of ${n(of)} ${word}` : `${n(shown)} ${word}`}
    </span>
  )
}
