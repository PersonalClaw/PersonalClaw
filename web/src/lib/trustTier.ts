/** The ONE place a supply-chain trust tier is spelled for a human.
 *
 *  🔑 TWO SURFACES DISAGREEING ABOUT A BUNDLE'S TIER *IS* THE DEFECT (#2627). The install dialog
 *  discloses "Unsigned — community tier" and the user consents to that; the Tools page then badged
 *  the same installed bundle `built-in` — the identical word core's own first-party providers get.
 *  The tier exists to inform consent, and the Tools page is where a user later goes to audit what
 *  is running and where it came from, so an answer that is wrong in the *reassuring* direction
 *  there undoes the disclosure the install flow just made.
 *
 *  🪤 SO THE STRING CANNOT LIVE TWICE. Both surfaces now read this map. Two independent literals
 *  would drift again the moment one surface is reworded — which is exactly how the badge and the
 *  dialog came to say different things about the same bytes. `web/src/lib/scanFindings` is the
 *  precedent: the same reasoning for the findings cap that two consent surfaces had each hardcoded.
 *
 *  The vocabulary is CLOSED and mirrors `supply_chain.TrustTier` — the enum the install gate
 *  computes and `ScanReport.to_dict` puts on the wire. A tier the backend can emit and this map
 *  does not know would render as raw JSON, so a Python-side addition has to land here too. */

/** The tiers `supply_chain.TrustTier` can emit, in ascending order of what the platform can vouch for. */
export type TrustTier = 'community' | 'trusted' | 'official' | 'builtin'

/** How each tier is SPOKEN. `builtin` reads `built-in` rather than "builtin tier" because a
 *  first-party provider is not a tier a user weighs — it is the platform itself, and the Tools
 *  page has always called it that. The other three are provenance claims about third-party bytes,
 *  so each says "tier" out loud: the word is what tells the reader this is a trust statement and
 *  not a category. */
export const TRUST_TIER_LABEL: Record<TrustTier, string> = {
  builtin: 'built-in',
  official: 'official tier',
  trusted: 'trusted tier',
  community: 'community tier',
}

/** What each tier MEANS, for a badge that has room for a word and a tooltip.
 *
 *  Lives beside the labels for the same reason the labels are shared: a surface that spells the
 *  tier is a surface that will be asked what the tier implies, and two answers to that is the
 *  same drift in a longer form. The MCP health dot on the Tools page already pairs a word with a
 *  `title`, so this is the shape that page established. */
export const TRUST_TIER_HINT: Record<TrustTier, string> = {
  builtin: 'Ships with PersonalClaw — first-party code, not an installed bundle',
  official:
    'Installed from a curated source, or carrying a maintainer signature PersonalClaw verified',
  trusted: 'Installed from a registry you marked as trusted',
  community:
    "Installed from a Store source at community tier — unsigned, so PersonalClaw can't confirm who published it",
}

/** The human spelling of a tier the wire reported.
 *
 *  🪤 THE FALLBACK IS `community`, NEVER `built-in`. An absent or unrecognised tier means "we could
 *  not establish provenance", and the whole point of #2627 is that such a case must not read as
 *  shipped-with-the-product. `community` is also what `ScanReport.tier` defaults to server-side, so
 *  the two ends agree on the unknown case as well as the known ones.
 *
 *  Callers that genuinely know the provider is core pass `'builtin'` explicitly. */
export function trustTierLabel(tier: string | null | undefined): string {
  const key = String(tier || '') as TrustTier
  return TRUST_TIER_LABEL[key] ?? TRUST_TIER_LABEL.community
}

/** The tooltip that goes with :func:`trustTierLabel`, same fallback for the same reason. */
export function trustTierHint(tier: string | null | undefined): string {
  const key = String(tier || '') as TrustTier
  return TRUST_TIER_HINT[key] ?? TRUST_TIER_HINT.community
}

/** Is this a tier the backend actually reported, as opposed to absent data?
 *
 *  🪤 A SURFACE MUST BE ABLE TO SAY "I DO NOT KNOW". The wire always carries a tier for a native
 *  provider, so absence means legacy or stale-cached data — and neither `built-in` (the #2627
 *  defect: absence reading as shipped-with-the-product) nor `community tier` (a false alarm about
 *  the platform's own providers) is an honest thing to render for it. Callers use this to render
 *  NOTHING instead, which self-corrects on the next fetch. Same discipline as `installConsent`'s
 *  three-state network claim: absent and declared are different facts. */
export function isKnownTrustTier(tier: string | null | undefined): boolean {
  return String(tier || '') in TRUST_TIER_LABEL
}
