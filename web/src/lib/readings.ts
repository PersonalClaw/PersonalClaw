/** Host readings from a polled status payload (`/api/system`), formatted for display.
 *
 *  The gateway LEAVES OUT a reading its probe could not take: memory comes from `sysctl` and
 *  `vm_stat`, CPU from `ps`, each with a 2 s timeout, and on a loaded host they time out. So a field
 *  present on one poll can be absent on the next, and a widget that formatted it unguarded threw
 *  "Cannot read properties of undefined (reading 'toFixed')" on that one poll. Everything that
 *  prints a reading goes through here, so a missing one prints the placeholder, never NaN and
 *  never a made-up zero. */

/** What a missing reading prints as. */
export const NO_READING = '—'

/** A reading that was taken: a finite number. */
export function isReading(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** `value.toFixed(digits)`, or the placeholder when the reading is missing. */
export function fixedReading(value: number | null | undefined, digits: number): string {
  return isReading(value) ? value.toFixed(digits) : NO_READING
}

/** `part` as a percentage of `whole`, or `null` when either is missing or the whole is not positive. */
export function percentReading(part: number | null | undefined, whole: number | null | undefined): number | null {
  return isReading(part) && isReading(whole) && whole > 0 ? (part / whole) * 100 : null
}
