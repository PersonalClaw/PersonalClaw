/**
 * PERSONALITIES — a theme that carries behavior, not just colors.
 *
 * Today the app has four disconnected identity knobs: the color scheme, the
 * assistant's name (`agent.bot_name`), the wordmark label, and the favicon. A
 * "personality" ties them into ONE switchable identity, so picking retro-terminal
 * doesn't just recolor the UI — it renames the wordmark, swaps the favicon, and
 * offers to rename the assistant.
 *
 * Three constraints keep this from becoming a theming free-for-all:
 *
 * 1. **Colors ride the EXISTING scheme mechanism, unchanged.** A personality
 *    references a `baseScheme` id from `SCHEMES`; it never carries its own color
 *    values. That means every personality's palette is automatically covered by
 *    `schemeContrast.test.ts` (which iterates SCHEMES), so a personality cannot
 *    smuggle in an inaccessible palette.
 * 2. **The behavior block is a CLOSED, typed set.** No arbitrary CSS, no
 *    injected markup, no per-personality code. Adding a capability means adding a
 *    field here deliberately — which is what lets a future provider seam
 *    (APP-PLATFORM-EVOLUTION) validate app-contributed entries against an
 *    allowlist instead of opening an arbitrary-code path.
 * 3. **Anything that changes SAVED state is proposed, never applied.**
 *    `displayName` is the clear case: renaming the assistant writes
 *    `agent.bot_name`, real config the user may have set deliberately. So it is
 *    offered with a checkbox at activation and restored by offer on deactivation
 *    — propose-don't-write.
 *
 * The two entries below are deliberately PLACEHOLDER identities: the deliverable
 * of this session is the registry shape and the switching behavior, not a final
 * brand set (the plan says so explicitly). Real personalities can be swapped in
 * without touching any of the machinery.
 */

/** Assistant-name + prompt-persona + chrome behaviors a personality may carry.
 *  Every field is optional: a personality that only recolors is valid. */
export interface PersonalityBehavior {
  /** Assistant name OFFERED at activation (writes `agent.bot_name` on consent). */
  displayName?: string
  /** Wordmark text in the shell (the `Wordmark` label prop). */
  wordmarkLabel?: string
  /** Favicon path — bundled assets under `web/public/` only, never a remote URL. */
  faviconHref?: string
  /** Bundled prompt-snippet id (`persona-<id>`); the backend validates it against
   *  its own closed set, so a bad value here degrades to "no persona". */
  personaSnippet?: string
  /** Maps to the existing `--ui-density` select — no new mechanism. */
  uiDensity?: 'comfortable' | 'dense' | 'cli'
  /** Browser tab title. */
  documentTitle?: string
}

export interface Personality {
  id: string
  label: string
  /** One-line description shown in the picker. */
  hint: string
  /** MUST be an id in `SCHEMES` — colors ride the existing scheme mechanism. */
  baseScheme: string
  behavior: PersonalityBehavior
}

/** The default identity: PersonalClaw itself. Selecting it is what "off" means,
 *  so deactivation is just activating this — one code path, no special case. */
export const DEFAULT_PERSONALITY = 'personalclaw'

export const PERSONALITIES: Personality[] = [
  {
    id: DEFAULT_PERSONALITY,
    label: 'PersonalClaw',
    hint: 'The default identity — coral, the claw wordmark, no persona.',
    baseScheme: 'coral',
    behavior: {
      wordmarkLabel: 'PersonalClaw',
      documentTitle: 'PersonalClaw',
      faviconHref: '/favicon.svg',
    },
  },
  {
    // Placeholder identity #1 (plan: "the registry entry shape is the deliverable").
    id: 'retro-terminal',
    label: 'Retro Terminal',
    hint: 'Mono-green phosphor, dense CLI spacing, and a terse operator voice.',
    baseScheme: 'phosphor',
    behavior: {
      displayName: 'TERM',
      wordmarkLabel: 'TERM://PC',
      documentTitle: 'TERM://PersonalClaw',
      faviconHref: '/favicon.svg',
      personaSnippet: 'persona-retro-terminal',
      uiDensity: 'cli',
    },
  },
  {
    // Placeholder identity #2 — deliberately reuses an existing scheme, proving a
    // personality needs no new palette to be a distinct identity.
    id: 'claw-arcade',
    label: 'Claw Arcade',
    hint: 'Amber cabinet glow and a playful, high-energy voice.',
    baseScheme: 'amber',
    behavior: {
      displayName: 'CLAW-1',
      wordmarkLabel: 'CLAW ARCADE',
      documentTitle: 'CLAW ARCADE',
      faviconHref: '/favicon.svg',
      uiDensity: 'comfortable',
    },
  },
]

export function getPersonality(id: string | undefined): Personality | undefined {
  return PERSONALITIES.find((p) => p.id === id)
}

/** The personality to treat as active for an id that no longer exists (a saved
 *  override from a removed entry) — never leave the shell in a half-applied state. */
export function resolvePersonality(id: string | undefined): Personality {
  return getPersonality(id) ?? getPersonality(DEFAULT_PERSONALITY) ?? PERSONALITIES[0]
}
