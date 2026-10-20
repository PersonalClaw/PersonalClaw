import { useState } from 'react'
import { Onboarding } from '../app/Onboarding'

/** The first-run flow, wrapped in a WORKING router.
 *
 *  The flow keeps its position in the URL (`#/onboarding/<step>`), which is what makes refresh,
 *  browser Back, browser Forward and a deep link all land in the same place. A test that passed a
 *  frozen `sub` would therefore be driving a flow that cannot advance — every step change would be
 *  reverted by the next render. So this holds the sub-path in state exactly as `useHashRoute` holds
 *  it in the hash: `navigate` writes it, and `replace` is reported so a test can tell a URL
 *  CORRECTION (the clamp) from a MOVE the user made.
 *
 *  Lives in `src/test/` beside the vitest setup file: it is test support, not app code, and nothing
 *  in the app imports it. */
export function OnboardingHarness({ sub: initial = '', deferred = '', onFinished, onNavigate }: {
  /** The step slug the flow starts on — `''` is `#/onboarding`, which resolves to wherever the
   *  run belongs. Pass a slug to simulate a reload on that step or a deep link to it. */
  sub?: string
  /** The route the guard deferred, as `App` passes it down. `''` is the ordinary entry. */
  deferred?: string
  onFinished?: () => void
  /** Every navigation the flow performs, in order. */
  onNavigate?: (path: string, replace: boolean) => void
}) {
  const [sub, setSub] = useState(initial)
  return (
    <Onboarding
      sub={sub}
      deferred={deferred}
      onFinished={onFinished ?? (() => { /* the shell's job; not under test here */ })}
      navigate={(path, opts) => {
        onNavigate?.(path, !!opts?.replace)
        setSub(path.replace(/^onboarding\/?/, ''))
      }}
    />
  )
}
