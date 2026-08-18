/** The shared rules for listing security-scan findings on a consent surface.
 *
 *  🔑 BOTH CONSENT SURFACES TRUNCATE THE SAME LIST, and each had hardcoded its own `8`. An app
 *  install (`apps/installConsent`) and a skill install (`skills/MarketplaceDetail`) render findings
 *  from the same `scan.findings` shape for the same decision — "do I trust this?" — so the cap and
 *  the sentence that discloses it belong in one place. Two independently-chosen limits are two
 *  numbers that drift, and a user comparing the two surfaces has no way to know either is a limit.
 *
 *  🪤 THE CAP IS NOT THE BUG; THE SILENCE IS. `installConsent`'s own comment says the surface exists
 *  so "the scanner findings and the 'Install anyway' action are reachable without re-typing the
 *  source", and that a warning verdict "must show the same scanner findings" wherever the install
 *  started. A list that stops at 8 with nothing said contradicts that: the user reads eight findings
 *  as ALL the findings and consents to an app with fourteen. */

/** How many findings a consent surface lists before it says how many it is hiding. */
export const SCAN_FINDINGS_SHOWN = 8

/** The sentence a truncated findings list owes the user, or `null` when nothing is hidden. */
export function hiddenFindingsNote(total: number): string | null {
  const hidden = total - SCAN_FINDINGS_SHOWN
  if (hidden <= 0) return null
  return `+${hidden} more finding${hidden === 1 ? '' : 's'} not shown`
}
