/** The playground's pose sheet (#3892): every pose of the four status families at 18, 24, 48 and
 *  96 px, and the focused card's orb for each family at the card's 48, each with its words beside
 *  it, in the app's current scheme and mode. Every pose is drawn as its still, the frame the
 *  contact sheets record, so the renderer's snapshots (#3891) are taken from it: one per scheme
 *  and mode. */

import { BuddyOrb } from '../../ui/BuddyOrb'
import { Eyebrow } from '../../ui/Eyebrow'
import { CARD_STATES, FAMILIES, FAMILY_STATES, wordsOf } from '../../buddyRig/poseSet'

export const SHEET_SIZES = [18, 24, 48, 96] as const

export function PoseSheet() {
  return (
    <div data-pose-sheet="" className="flex min-w-0 flex-col gap-xl">
      {FAMILIES.map((family) => (
        <section key={family} role="region" aria-label={`The ${family} family`} className="flex min-w-0 flex-col gap-m">
          <Eyebrow as="h3">The {family} family</Eyebrow>
          <ul className="flex flex-col gap-l">
            {FAMILY_STATES[family].map((state) => (
              <li key={state} data-pose={`${family}-${state}`} className="flex min-w-0 flex-wrap items-center gap-l">
                {SHEET_SIZES.map((size) => <BuddyOrb key={size} state={state} line={wordsOf(state)} size={size} still />)}
              </li>
            ))}
          </ul>
        </section>
      ))}
      <section role="region" aria-label="The focused card's orb" className="flex min-w-0 flex-col gap-m">
        <Eyebrow as="h3">The focused card's orb</Eyebrow>
        <ul className="flex flex-wrap gap-xl">
          {FAMILIES.map((family) => (
            <li key={family} data-pose={`card-${family}`}>
              <BuddyOrb state={CARD_STATES[family]} line={wordsOf(CARD_STATES[family])} size={48} still />
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
