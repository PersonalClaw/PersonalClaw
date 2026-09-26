import type { UpdateCheck } from '../../lib/api'

/** What an update check can truthfully say about this install, in the order that decides it.
 *
 *  🔴 THE HUB TILE SAID "Up to date" FOR AN INSTALL NOTHING HAD EVER COMPARED. It read one bit —
 *  `available` — and rendered its absence as a verdict, so a pip install whose check had produced
 *  no answer, an install whose pin names no release, and an install with checking switched off all
 *  read "Up to date". The panel read `checked`, but `checked` itself only ever came from the git
 *  half of the check, so on the same pip install the panel said "No update check yet" beside the
 *  tile's "Up to date": two surfaces, one install, two claims, neither of them true.
 *
 *  ONE derivation for both surfaces now, so they cannot disagree again:
 *
 *    available    a newer release than the running one is known
 *    pin_miss     `updates.pin` names a version no known release carries — nothing is offered or
 *                 installed while it stands (the server's `pin_miss`, read only when a pin is set)
 *    checks_off   the check is switched off, so no current answer exists to report
 *    up_to_date   the check compared this install with the release it resolves to, and it is current
 *    not_checked  no comparison has an answer — nothing fetched, nothing cached
 *
 *  `pin_miss` outranks `checks_off`: the server computes it from the last fetched list even with
 *  checking off, and it is the one state here the user caused and can fix in place. */
export type UpdateVerdict = 'available' | 'pin_miss' | 'checks_off' | 'up_to_date' | 'not_checked'

export function updateVerdict(u: UpdateCheck): UpdateVerdict {
  if (u.available) return 'available'
  if (u.pin && u.pin_miss) return 'pin_miss'
  if (u.check_enabled === false) return 'checks_off'
  if (u.checked) return 'up_to_date'
  return 'not_checked'
}

/** The verdict in words — the panel's headline and the tile's pill are this one string. */
export function updateVerdictLabel(u: UpdateCheck): string {
  switch (updateVerdict(u)) {
    case 'available': return `Update available${u.latest ? ` — ${u.latest}` : ''}`
    case 'pin_miss': return `No release matches pin ${u.pin}`
    case 'checks_off': return 'Update checks are off'
    case 'up_to_date': return 'Up to date'
    case 'not_checked': return "Couldn't check for updates"
  }
}
