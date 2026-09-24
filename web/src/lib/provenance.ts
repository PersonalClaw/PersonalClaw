/** THE one place that turns "where did these bytes come from" into words a user reads.
 *
 *  Provenance is one fact with several representations, and the surfaces used to disagree
 *  about it:
 *    • the Store card never showed it as TEXT at all before install (#2528) — only the
 *      source divider heading (a folder basename) and the Sources rail hinted at it, so a
 *      card whose bytes came from a remote could be read as the local copy you just added;
 *    • Settings → Tools badged every non-locked native provider `built-in` (#2514), which
 *      reads as provenance while not being provenance — two bundles added from a
 *      user-created local Store source carried the identical badge as `personalclaw-artifacts`.
 *
 *  The FACT is owned by the backend (`apps/catalog.py`: `sourceKind`, `SOURCE_PRECEDENCE`,
 *  `source_kind_for_origin`). This module owns its RENDERING, and nothing else derives a
 *  provenance label — `test_one_owner_labels_app_provenance` reds on a second labeller.
 */

/** The catalog's `sourceKind` vocabulary — mirrors `SOURCE_PRECEDENCE` in apps/catalog.py. */
export type SourceKind = 'native' | 'bundled' | 'first-party' | 'local' | 'git'

export interface Provenance {
  /** Chip text. Lowercase to match the existing Tools-page chip vocabulary. */
  label: string
  /** The tooltip — says what the label MEANS, since "local" alone is not self-explaining. */
  title: string
}

const PROVENANCE: Record<string, Provenance> = {
  native: { label: 'built-in', title: 'Ships with PersonalClaw — it was installed with the product, not from a source you added.' },
  bundled: { label: 'built-in', title: 'Ships with PersonalClaw — it was installed with the product, not from a source you added.' },
  'first-party': { label: 'first-party', title: 'From the first-party apps directory on this machine.' },
  local: { label: 'local', title: 'From a folder on this machine that you added as a Store source.' },
  git: { label: 'git', title: 'From a remote git source. These bytes are fetched over the network.' },
}

/** The platform provider the product cannot run without — not an installed app at all, so
 *  it has no `sourceKind`. Its own word, because calling it `built-in` would put it in the
 *  same bucket as an uninstallable-but-ordinary bundled app. */
const PLATFORM: Provenance = {
  label: 'platform',
  title: 'A core capability of PersonalClaw itself — it cannot be removed or turned off.',
}

/** The provenance chip for a thing whose origin is `sourceKind`, or `null` when the origin
 *  is not known.
 *
 *  🔴 `null` is load-bearing: a caller renders NOTHING rather than falling back to
 *  `built-in`. Guessing "built-in" for an unknown origin is precisely the #2514 defect —
 *  it turns an absent fact into a false claim on the one screen where the user is asking
 *  "did this ship with the product, or did I install it?". */
export function provenance(input: { sourceKind?: string | null; locked?: boolean }): Provenance | null {
  if (input.locked) return PLATFORM
  const kind = (input.sourceKind ?? '').trim()
  return PROVENANCE[kind] ?? null
}

/** ── A REGISTRY LISTING's own claims about itself (ET-5) ──────────────────────────────────────
 *
 *  `sourceKind: 'git'` above answers "these bytes came over the network". It cannot answer the
 *  question a user actually has in front of a community listing: *who put this here, and has
 *  anybody looked at it?* The index publishes three fields that do (`maintainer`,
 *  `last_validated`, `last_scan_verdict` — see `apps/catalog.py: RegistryPointer`), and until now
 *  the catalog dropped all three, so the Store rendered a remote card with no provenance text at
 *  all.
 *
 *  🔴 THE RISK HERE IS TRUST-WASHING, AND IT IS THE REASON THIS COPY IS CENTRALISED.
 *  A month-old `last_scan_verdict: "clean"` rendered as a green ✓ "Verified 7 Sep" would read as
 *  *PersonalClaw checked this app for you*. It is not that, on three counts: the check was the
 *  REGISTRY's, not ours; it was of the LISTING at that moment, not of the bytes you are about to
 *  install; and it is not what gates the install — `app_manager.install`'s scanner still runs,
 *  unchanged, every time. So the wording has three non-negotiable jobs, in this order:
 *
 *    1. **Lead with the disclaimer, not the reassurance.** `headline` comes first on screen.
 *       A reader who stops after four words must have read "community-listed", not "clean".
 *    2. **Attribute the verdict.** "registry check" — never a bare "Verified"/"Scanned", and
 *       never PersonalClaw's voice.
 *    3. **Point at the real gate.** "rescanned when you install" is what makes the stale verdict
 *       harmless: it tells the reader the number that matters arrives later.
 *
 *  A non-clean verdict must NOT render in the same neutral register as a clean one, so `clean`
 *  is exposed for the caller to style — a grey "flagged" sitting where a grey "clean" sat is the
 *  same defect in the other direction.
 */
export interface RegistryListing {
  /** The lead clause. Deliberately the non-endorsement and nothing else. */
  headline: string
  /** The three facts, in one line: who listed it · what the index's last check said · the gate
   *  that still runs. Whichever parts the listing actually published — a missing field is
   *  omitted, never rendered as "unknown", which would read as a finding. */
  detail: string
  /** Did the index's own last check come back clean? `false` ⇒ do not render `detail` neutrally. */
  clean: boolean
  /** The long form, for `title` — spells out all three reasons the verdict is not a guarantee. */
  title: string
}

const NOT_ENDORSED = 'Community-listed, not endorsed'

const LISTING_TITLE =
  'Listed in a community app registry. PersonalClaw does not review, endorse or vouch for ' +
  'listed apps. A "registry check" is the registry\'s own last automated look at the LISTING, ' +
  'not a guarantee about the code — PersonalClaw runs its own scan of the actual bytes when ' +
  'you install, and that scan is what can stop an install.'

/** The provenance line for a card that came from a registry INDEX, or `null` when the listing
 *  published none of the three fields.
 *
 *  🔑 `null` for "published nothing" is the same load-bearing silence as `provenance()` above,
 *  and it is also what keeps the done-when clause *local/first-party cards unchanged* true by
 *  construction rather than by a caller's `if`: only `_pointer_to_entry` populates these fields,
 *  so a bundled/local/scanned card reaches this function with all three empty and gets nothing
 *  back. No caller has to know which kind of card it is holding.
 *
 *  `dayStamp` is borrowed from `lib/epoch` rather than re-parsed here — that module owns
 *  timestamp→words and already has the "unreadable renders nothing" contract this needs. */
export function registryListing(input: {
  maintainer?: string | null
  lastValidated?: string | null
  lastScanVerdict?: string | null
  /** `lib/epoch.dayStamp`, injected so this module stays free of date handling (and so a test
   *  can pin the copy without pinning a locale). */
  day: (ts?: string | null) => string
}): RegistryListing | null {
  const maintainer = (input.maintainer ?? '').trim()
  const verdict = (input.lastScanVerdict ?? '').trim()
  const validated = (input.lastValidated ?? '').trim()
  if (!maintainer && !verdict && !validated) return null

  const when = input.day(validated)
  const parts: string[] = []
  if (maintainer) parts.push(`by ${maintainer}`)
  if (verdict && when) parts.push(`registry check: ${verdict}, ${when}`)
  else if (verdict) parts.push(`registry check: ${verdict}`)
  // A date with no verdict is still worth saying — it dates the listing — but it must not be
  // phrased as though something passed. "last checked" claims only that a look happened.
  else if (when) parts.push(`last checked ${when}`)
  // Job 3. Unconditional: it is true whether or not the index said anything, and it is the
  // sentence that makes an absent or stale verdict safe to show.
  parts.push('rescanned when you install')

  return {
    headline: NOT_ENDORSED,
    detail: parts.join(' · '),
    // Absent is NOT clean. A listing that published no verdict must style as un-vouched-for,
    // not as passing — defaulting the other way is how a silence becomes a claim.
    clean: verdict.toLowerCase() === 'clean',
    title: LISTING_TITLE,
  }
}
