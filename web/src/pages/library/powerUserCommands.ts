import { Gauge, Gavel, Network, Scale, Settings2, Tag } from 'lucide-react'
import type { Command } from '../../app/CommandPalette'
import { peekPowerUserView, savePowerUserView } from './powerUserView'

// ── The Power user view in the palette ────────────────────────────────────────────────────────────
//
// Turn on (or off) Power user view, as the header's switch does: on opens the graph, off the list.
// And a Go to row for each of its tabs, which a link opens for the visit.

export function powerUserCommands(navigate: (path: string) => void): Command[] {
  const on = peekPowerUserView()
  return [
    {
      id: 'act:power-user-view', label: on ? 'Turn off Power user view' : 'Turn on Power user view', hint: 'Action', icon: Gauge,
      keywords: 'Library graph tags conflicts decisions settings details counts',
      run: () => { void savePowerUserView(!on); navigate(on ? 'library' : 'library?view=graph') },
    },
    { id: 'go:library-graph', label: 'Library › Graph', hint: 'Go to', icon: Network, keywords: 'Power user view people places ideas entities', run: () => navigate('library?view=graph') },
    { id: 'go:library-tags', label: 'Library › Tags', hint: 'Go to', icon: Tag, keywords: 'Power user view tag tree', run: () => navigate('library?view=tags') },
    { id: 'go:library-conflicts', label: 'Library › Conflicts', hint: 'Go to', icon: Scale, keywords: 'Power user view disagree compare', run: () => navigate('library?view=conflicts') },
    { id: 'go:library-decisions', label: 'Library › Decisions', hint: 'Go to', icon: Gavel, keywords: 'Power user view journal', run: () => navigate('library?view=decisions') },
    { id: 'go:library-settings', label: 'Library settings', hint: 'Go to', icon: Settings2, keywords: 'Power user view search by meaning re-index process', run: () => navigate('library?view=settings') },
  ]
}
