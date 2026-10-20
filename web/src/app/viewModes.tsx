import { createContext, useContext, useEffect, useState, type Dispatch, type SetStateAction } from 'react'

/** Focus mode and reading mode: two ways to give the window to the work (`web/REDESIGN.md` §4 and
 *  §5 convention 11).
 *
 *  - **Focus mode** hides the sidebar and the status line and closes the side pane, so the work
 *    surface takes the window. The status line shows anyway while the gateway isn't answering.
 *    Something that needs a pane opens only that pane. It lasts until the owner leaves it, across
 *    reloads, as the collapsed sidebar does.
 *  - **Reading mode** folds a chat's composer while the owner reads a long answer. It comes back on
 *    the key, on focus and on typing, with its draft. It belongs to the chat on screen, so a move to
 *    another page ends it.
 *
 *  The shell owns both and hands them to the pages through `useViewModes`. Each has one key, ⌘\ and
 *  ⌘⇧. (Ctrl elsewhere): rows of the keymap (`keymap.ts`), run through its one handler and listed
 *  in the palette from there like every other key. */

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
