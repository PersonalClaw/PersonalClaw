import { createContext, useContext, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import type { LucideIcon } from 'lucide-react'
import type { Command } from './CommandPalette'

/** Focus mode and reading mode: two ways to give the window to the work (`web/REDESIGN.md` §4 and
 *  §5 convention 11).
 *
 *  - **Focus mode** hides the sidebar and closes the side pane, so the work surface takes the
 *    window. Something that needs a pane opens only that pane. It lasts until the owner leaves it,
 *    across reloads, as the collapsed sidebar does.
 *  - **Reading mode** folds a chat's composer while the owner reads a long answer. It comes back on
 *    the key, on focus and on typing, with its draft. It belongs to the chat on screen, so a move to
 *    another page ends it.
 *
 *  The shell owns both and hands them to the pages through `useViewModes`. Each has one key, listed
 *  with it in the palette:
 *
 *  - ⌘\ for focus mode, and ⌘⇧. for reading mode (Ctrl elsewhere). ⌘⇧F was the first proposal, but
 *    a browser on a Mac takes it for full screen. These two are taken by no browser's published
 *    shortcuts for a page, by the desktop app's menus or by the composer's editor. ⌘⇧\ was the
 *    pair's other half until it turned out to be a browser's tab overview.
 *
 *  `VIEW_MODE_KEYS` is written as the keymap writes its rows, so the two join it as they are. */

export type ViewModeId = 'focus-mode' | 'reading-mode'

export interface ViewModeKey {
  id: ViewModeId
  label: string
  keywords: string
  scope: 'global'
  /** The chords that run it: `mod` is ⌘ on a Mac and Ctrl elsewhere. */
  chords: readonly string[]
}

export const VIEW_MODE_KEYS: readonly ViewModeKey[] = [
  {
    id: 'focus-mode', label: 'Focus mode: hide the sidebar and the side pane',
    keywords: 'focus full window distraction hide sidebar side pane chrome', scope: 'global', chords: ['mod+\\'],
  },
  {
    id: 'reading-mode', label: 'Reading mode: fold the composer',
    keywords: 'reading read long answer fold hide collapse composer', scope: 'global', chords: ['mod+shift+.'],
  },
]

/** The physical key of each punctuation chord, which shifted or on another layout types another
 *  character. */
const KEY_CODES: Record<string, string> = { '\\': 'Backslash', '.': 'Period' }

function isMac(): boolean {
  const nav = typeof navigator === 'undefined' ? undefined : navigator
  return /Mac|iPhone|iPad/.test(nav?.platform || nav?.userAgent || '')
}

/** Whether *e* is *chord*. The modifiers must be exactly the chord's, so Alt (which AltGr is
 *  outside a Mac) never runs one. */
function matchChord(e: KeyboardEvent, chord: string): boolean {
  const parts = chord.split('+')
  const key = parts[parts.length - 1]
  const mods = new Set(parts.slice(0, -1))
  if (mods.has('mod') !== (e.metaKey || e.ctrlKey)) return false
  if (mods.has('shift') !== e.shiftKey) return false
  if (e.altKey) return false
  return e.code === KEY_CODES[key] || e.key === key
}

/** A mode's key as this platform writes it: `⌘\` on a Mac, `Ctrl+\` elsewhere. */
export function viewModeKeyLabel(id: ViewModeId): string {
  const row = VIEW_MODE_KEYS.find((k) => k.id === id)!
  const parts = row.chords[0].split('+')
  const key = parts[parts.length - 1]
  const mods = parts.slice(0, -1)
  if (isMac()) return mods.map((m) => (m === 'mod' ? '⌘' : '⇧')).join('') + key
  return [...mods.map((m) => (m === 'mod' ? 'Ctrl' : 'Shift')), key].join('+')
}

/** A mode's row in the palette: its label, its key and its search words. */
export function viewModeCommand(id: ViewModeId, icon: LucideIcon, run: () => void): Command {
  const row = VIEW_MODE_KEYS.find((k) => k.id === id)!
  return { id: `act:${id}`, label: row.label, hint: `Action · ${viewModeKeyLabel(id)}`, icon, keywords: row.keywords, run }
}

/** Run each mode's key while the caller is mounted. A mode with no runner leaves its key to the
 *  browser; a held key runs once. */
export function useViewModeKeys(runners: Partial<Record<ViewModeId, () => void>>): void {
  const latest = useRef(runners)
  latest.current = runners
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      for (const row of VIEW_MODE_KEYS) {
        const run = latest.current[row.id]
        if (!run || !row.chords.some((c) => matchChord(e, c))) continue
        e.preventDefault()
        if (!e.repeat) run()
        return
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}

const FOCUS_MODE_KEY = 'focus-mode'

export interface ViewModes {
  focus: boolean
  setFocus: Dispatch<SetStateAction<boolean>>
  reading: boolean
  setReading: Dispatch<SetStateAction<boolean>>
}

/** The shell's two modes. Focus mode is stored like `nav-collapsed`; reading mode is not stored,
 *  since it belongs to the chat on screen. */
export function useViewModeState(): ViewModes {
  const [focus, setFocus] = useState(() => localStorage.getItem(FOCUS_MODE_KEY) === '1')
  useEffect(() => { localStorage.setItem(FOCUS_MODE_KEY, focus ? '1' : '0') }, [focus])
  const [reading, setReading] = useState(false)
  return { focus, setFocus, reading, setReading }
}

const ViewModesContext = createContext<ViewModes>({ focus: false, setFocus: () => {}, reading: false, setReading: () => {} })

export const ViewModesProvider = ViewModesContext.Provider

/** The shell's modes, for a page to honour. Outside the shell both are off. */
export function useViewModes(): ViewModes {
  return useContext(ViewModesContext)
}
