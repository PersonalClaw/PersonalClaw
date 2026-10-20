import type { ReactNode } from 'react'

/** Full-height centering wrapper — the `flex h-full items-center justify-center`
 *  box that holds a pane's loading spinner, load-failure placeholder, or empty
 *  hint centered in the available height. The CodeCockpitPage, the file viewer,
 *  and the content surface each defined this exact helper inline (and a fourth,
 *  drifted, copy in Onboarding used a *different* layout — the very hazard
 *  duplication invites); this is the single source. Pure chrome. */
export function Centered({ children }: { children: ReactNode }) {
  return <div className="flex h-full items-center justify-center">{children}</div>
}
