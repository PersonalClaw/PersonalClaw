/** The leading accent rail that marks a notification row UNREAD, in the kind's tone.
 *
 *  This was `unreadRail(tone, acked)` — an inline `{ boxShadow: 'inset 2px 0 0 0 <tone>' }`
 *  shared by the `#/notifications` row and the bell's dropdown row. It had to stop being a
 *  box-shadow: Tailwind's focus `ring` is box-shadow too, and an INLINE value replaces the
 *  whole composite, so both rows resolved `--tw-ring-shadow` and painted no ring at all
 *  (measured: `box-shadow: rgb(255,107,91) 2px 0 0 0 inset`, a single layer, while the tasks
 *  and loops rows show the ring as the 4th of five). A row you can focus but cannot see
 *  focused is not fixed, so the rail moved onto a property nothing else here contends.
 *
 *  It stays a COMPONENT rather than two spans, because the shared helper it replaces exists
 *  for that reason: the rail is one pattern with one home, and the last time it lived at two
 *  call sites they drifted.
 *
 *  The row must be `relative`; the radius matches the row's own (`rounded-lg` list row,
 *  `rounded-md` bell row), so the rail follows the corner instead of cutting across it.
 */
export function UnreadRail({ tone, acked, radius = 'lg' }: {
  /** A `kindMeta().tone` — already a token/CSS colour, so this stays token-routed. */
  tone: string
  /** Read rows carry no rail; the component renders nothing rather than an invisible span. */
  acked: boolean
  radius?: 'lg' | 'md'
}) {
  if (acked) return null
  return (
    <span
      aria-hidden="true"
      className={`absolute left-0 top-0 bottom-0 w-[2px] ${radius === 'lg' ? 'rounded-l-lg' : 'rounded-l-md'}`}
      style={{ background: tone }}
    />
  )
}
