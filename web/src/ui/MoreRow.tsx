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
export function MoreRow({ total, shown, className }: {
  /** How many items exist — the number the surrounding label states. */
  total: number
  /** How many are rendered, i.e. the cap actually applied. */
  shown: number
  /** Layout-only override for a caller whose list is a chip row rather than stacked rows. */
  className?: string
}) {
  const hidden = total - shown
  if (hidden <= 0) return null
  return (
    <div className={`text-on-surface-low text-[0.75rem] ${className ?? ''}`}>… {hidden} more</div>
  )
}
