import { AlertTriangle, CheckCircle2, Circle, Clock, Eye, ShieldAlert, XCircle, Zap } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/** How a web watch's last check of its page reads (`Trigger.last_check`), one row per outcome of the
 *  backend's `WatchCheck` (`triggers/web_poll.py`).
 *
 *  🔴 WHY THE PANEL NEEDS ITS OWN WORDS FOR A CHECK. A check is the watch LOOKING, not the automation
 *  running, so the run vocabulary (`statusMeta`) has no word for "the network settings refused it" or
 *  "nothing on the page can be watched". A watch whose checks came to exactly those read "Firing on its
 *  own · No runs recorded yet", which was false about the one and silent about the other.
 *
 *  `notFiring` is the panel's status line for the checks the watch cannot fire after: said instead of
 *  "Firing on its own" while the server says `can_fire: false`. Whether it can fire is the server's
 *  verdict, never re-derived here. `watchCheckVocabulary.test.ts` reads the enum and the backend's
 *  `CANNOT_FIRE` out of the Python source, so a new outcome cannot reach this page unlabelled. */
export interface WatchCheckMeta {
  label: string
  tone: string
  icon: LucideIcon
  notFiring?: string
}

export const WATCH_CHECK_META: Record<string, WatchCheckMeta> = {
  refused: {
    label: 'Refused by the network settings',
    tone: 'var(--color-warning)',
    icon: ShieldAlert,
    notFiring: 'Not firing — its checks of the page are refused',
  },
  failed: {
    label: 'Could not read the page',
    tone: 'var(--color-danger)',
    icon: XCircle,
    notFiring: 'Not firing — its last check could not read the page',
  },
  budget: {
    label: "Today's checks are spent",
    tone: 'var(--color-warning)',
    icon: Clock,
    notFiring: "Not firing until tomorrow — today's checks of the page are spent",
  },
  empty: {
    label: 'Nothing on the page to watch',
    tone: 'var(--color-warning)',
    icon: AlertTriangle,
    notFiring: 'Not firing — nothing on its page can be watched',
  },
  seeded: { label: 'Started watching', tone: 'var(--color-ok)', icon: Eye },
  unchanged: { label: 'Nothing new', tone: 'var(--color-on-surface-low)', icon: CheckCircle2 },
  fired: { label: 'New items — it fired', tone: 'var(--color-ok)', icon: Zap },
}

/** The row for `outcome`; an outcome this build does not know reads as a plain check, not as one
 *  that went well or badly. */
export function watchCheckMeta(outcome?: string | null): WatchCheckMeta {
  return WATCH_CHECK_META[outcome ?? ''] ?? { label: 'Checked', tone: 'var(--color-on-surface-low)', icon: Circle }
}
