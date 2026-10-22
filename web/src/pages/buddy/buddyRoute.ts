import { createElement, lazy } from 'react'
import type { RouteProps } from '../../app/useQueryState'

const PlaygroundPage = lazy(() => import('./PlaygroundPage').then((m) => ({ default: m.PlaygroundPage })))
const BuddyPage = lazy(() => import('./BuddyPage').then((m) => ({ default: m.BuddyPage })))

/** What `#/buddy` renders, in the app's shell (`App()`) and in the buddy's own window
 *  (`app/BuddyWindowRoot.tsx`) alike: one place, so the two never show different pages. The bare
 *  route is the live buddy (#3832, `BuddyPage`), and `#/buddy/playground` the playground (#3892). */
export function BuddyRoutePage(props: RouteProps) {
  return createElement(props.sub === 'playground' ? PlaygroundPage : BuddyPage, props)
}
