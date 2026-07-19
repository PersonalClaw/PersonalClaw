import * as Lucide from 'lucide-react'
import { Blocks, type LucideIcon } from 'lucide-react'

/** Resolve an app manifest `icon` (a lucide icon NAME, e.g. "ClipboardList") to
 *  a lucide component. Per the no-emoji tenet, apps declare icons by lucide name,
 *  never an emoji glyph. Unknown / empty names fall back to the Blocks app glyph.
 *  A legacy emoji value (single non-letter glyph) also falls back rather than
 *  rendering the emoji. */
export function AppIcon({ name, size = 18 }: { name?: string; size?: number }) {
  const Icon = resolveAppIcon(name)
  return <Icon size={size} />
}

export function resolveAppIcon(name?: string): LucideIcon {
  if (!name || !/^[A-Za-z]/.test(name)) return Blocks
  const reg = Lucide as unknown as Record<string, LucideIcon>
  return reg[name] ?? Blocks
}
