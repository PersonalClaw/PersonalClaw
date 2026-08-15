import type { ReactNode } from 'react'

/** Full-height centering wrapper — the `flex h-full items-center justify-center`
 *  box that holds a pane's loading spinner, load-failure placeholder, or empty
 *  hint centered in the available height. The CodeCockpitPage, the file viewer,
 *  and the content surface each defined this exact helper inline; this is the
 *  single source. Onboarding had a fourth copy under the same name but a
 *  *different* layout (a short `py-2` spinner row, not full-height) — a genuine
 *  distinction wearing a colliding name; it is now `SpinnerRow` there, so this
 *  name means one thing. Pure chrome. */
export function Centered({ children }: { children: ReactNode }) {
  return <div className="flex h-full items-center justify-center">{children}</div>
}
