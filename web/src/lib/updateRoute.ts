import type { InstallKind } from './api'

/** How a new version reaches an install that does NOT install one on its own — the sentence the
 *  done screen's update pointer and Settings → Updates' "Apply updates" row both say.
 *
 *  Only a source checkout applies an update unattended (`self_update.applies_updates_unattended`,
 *  read as `unattended_apply`); both surfaces offer the switch there and nowhere else. Every other
 *  kind is told here what it does instead, in one place, so the two surfaces cannot describe the
 *  same install two ways. Location-free on purpose: each surface adds its own way there.
 *
 *  `''` for a kind nobody could read, and for a source checkout, which has the switch instead. */
export function howAnUpdateArrives(kind: InstallKind | undefined): string {
  switch (kind) {
    case 'pip':
      return 'When a new version ships, pressing Update installs it and restarts. An install from pip or uv never updates on its own.'
    case 'container':
      return 'A container is updated from the host: it never changes itself, so a new version means pulling the new image and recreating the container. The exact commands are shown when one ships.'
    case 'desktop':
      return 'When a new version ships, install it from the releases page and reopen the app. The app never updates on its own.'
    default:
      return ''
  }
}
