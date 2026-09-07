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
