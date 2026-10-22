import { lazy } from 'react'

/** What `#/buddy` renders, in the app's shell (`App()`) and in the buddy's own window
 *  (`app/BuddyWindowRoot.tsx`) alike: one place, so the two never show different pages. Today
 *  that is the playground (#3892); #3832 gives the bare route its live buddy and keeps the
 *  playground at `#/buddy/playground`. */
export const BuddyRoutePage = lazy(() => import('./PlaygroundPage').then((m) => ({ default: m.PlaygroundPage })))
