import * as Lucide from 'lucide-react'
import { Blocks, icons as lucideIconRegistry, type LucideIcon } from 'lucide-react'

/** Every component object lucide ships AS AN ICON, held by IDENTITY rather than by name.
 *
 *  🔑 THE MANIFEST `icon` FIELD IS UNTRUSTED INPUT and the old resolver validated only
 *  truthiness: `reg[name] ?? Blocks`. `??` fires on null/undefined, but the failure mode here is
 *  a value of the WRONG TYPE, which is truthy — so the fallback could not observe its own case.
 *  Measured against the installed lucide (v1.40.0, 6166 exports), six letter-starting exports
 *  break a render, in two different ways:
 *
 *    - invalid element type ....... `icons`, `default`, `module.exports`  (plain objects)
 *    - throws INSIDE a valid one .. `Icon` (wants an `iconNode`), `createLucideIcon` (wants a
 *                                   name string), `useLucideContext` (a hook, returns an object)
 *
 *  🪤 SO A TYPE GUARD IS NOT ENOUGH. Checking `typeof === 'function' || '$$typeof' in v` accepts
 *  `createLucideIcon` (a function) and `Icon` (a real forwardRef), and would fix only three of the
 *  six. What makes a value safe here is not its shape but whether lucide considers it an icon.
 *
 *  🪤 AND NAME-KEYED LOOKUP IS NOT ENOUGH EITHER. Resolving `lucideIconRegistry[name]` looks like
 *  the obvious fix and silently breaks 4367 names: the registry is keyed by the 1799 CANONICAL
 *  names, while the module namespace also exports every alias (`SquareTerminalIcon`,
 *  `LucideSquareTerminal`, `AlarmCheck`, …). An alias is the SAME component object as its
 *  canonical name, so matching on identity keeps all 6166 working names while still rejecting all
 *  six hostile ones. Measured: 0 crashers accepted, 0 working names lost.
 *
 *  The one name this deliberately stops resolving is `LucideProvider` — renderable, but a context
 *  provider renders nothing, so an app declaring it got an invisible icon. `Blocks` is honest. */
const LUCIDE_ICON_COMPONENTS: ReadonlySet<unknown> = new Set(Object.values(lucideIconRegistry))

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
  const candidate = (Lucide as unknown as Record<string, unknown>)[name]
  // Identity, not shape and not name — see LUCIDE_ICON_COMPONENTS. A manifest value that is not
  // one of lucide's own icon components falls back, so no app can put a non-component into a React
  // element. This resolver is the only door, and the sidebar nav renders through it: an invalid
  // element type there throws during the app SHELL's render, so the blast radius of one bad
  // manifest word was the whole dashboard rather than that app's own card.
  return LUCIDE_ICON_COMPONENTS.has(candidate) ? (candidate as LucideIcon) : Blocks
}
