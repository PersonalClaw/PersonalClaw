import { confirmDestructive } from '../../ui/dialog'

/** Ask before forgetting a learned preference.
 *
 *  The one prompt the Learned preferences list and the chat's "Forget it" share, so the place a
 *  preference is undone from can never change what forgetting it promises. `confirmDestructive`,
 *  not `confirmDelete`: the verb is not delete and the row survives, so the prompt has to state
 *  what is actually irreversible rather than lean on the word. */
export function confirmForgetPreference(text: string): Promise<boolean> {
  return confirmDestructive(
    'Forget this preference?',
    <>
      <p>“{text}” drops out of the profile block immediately and its strength reads 0.</p>
      <p className="mt-s">
        This cannot be undone — not by pinning it, and not by the assistant observing the same
        preference again. The row stays in the memory log, marked forgotten, so it is never
        re-learned.
      </p>
    </>,
    { confirmLabel: 'Forget' },
  )
}
