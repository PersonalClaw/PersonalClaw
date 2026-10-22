/** The keymap: the app's keyboard shortcuts, in one table, and the one handler that runs them.
 *
 *  Each row is a command: an id, the label the palette shows, search words, a scope, the chords
 *  that run it, and, for a row the palette lists but may not run, why. The one global handler and
 *  the palette read this table, and so does every tooltip that shows one of its keys
 *  (`bindingLabel`), so a key is written down once. The desktop app's Go menu sends a row's id,
 *  never a key (`DESKTOP_COMMANDS`, `desktop/goMenu.js`). No other file reads `metaKey`/`ctrlKey`
 *  but the editors that still read their own keys (a send, a save), which
 *  `everyChordIsOneRegistryRow.test.tsx` lists in a ratchet that only shrinks.
 *
 *  Adding a key: add a row here, then give it a runner with `useKeyBinding(id, run)` where the thing
 *  it toggles lives. A row whose runner lives in a page reaches the shell's palette through
 *  `useBoundRunner(id)`.
 *
 *  Scopes:
 *  - `global`: runs from anywhere in the app, a text field included, through the one handler.
 *  - `focused`: the focused control handles the key itself (an approval card's ⌘Enter, the
 *    composer's ↑). The palette lists the row, greyed out with its reason, and never runs it: an
 *    approval is never answered from the palette.
 *
 *  Keys are written for macOS; elsewhere ⌘ is Ctrl and ⌥ is Alt. Shortcuts can't be remapped. */
import { useEffect, useRef, useSyncExternalStore } from 'react'

export type KeyScope = 'global' | 'focused'

export const THREAD_NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 9] as const

export type CommandId =
  | 'palette' | 'sidebar' | 'drawer' | 'focus-mode' | 'reading-mode' | 'workspace-tree' | 'next-needs-you' | 'mark-all-read'
  | 'new-chat'
  | `chat-${(typeof THREAD_NUMBERS)[number]}`
  | 'approve-focused' | 'restore-last-prompt'

export interface KeyBinding {
  id: CommandId
  label: string
  keywords?: string
  scope: KeyScope
  /** The chords that run it, in the grammar `matchChord` reads: `mod+shift+p`, `mod+1`, `arrowup`. */
  chords: readonly string[]
  /** Why the palette cannot run it: a `focused` row's key belongs to the control that has focus,
   *  and a `global` row with nothing to act on where the owner is has no runner there. */
  disabledReason?: string
}

export const KEYMAP: readonly KeyBinding[] = [
  { id: 'palette', label: 'Command palette', keywords: 'search commands go to', scope: 'global', chords: ['mod+k', 'mod+shift+p'] },
  { id: 'sidebar', label: 'Show or hide the sidebar', keywords: 'navigation rail collapse expand', scope: 'global', chords: ['mod+b'] },
  { id: 'drawer', label: 'Show or hide the terminal drawer', keywords: 'shell pty console bottom', scope: 'global', chords: ['mod+j', 'mod+`'] },
  // Focus mode and reading mode (`viewModes.tsx`). ⌘⇧F was the first proposal, but a browser on a
  // Mac takes it for full screen; these two are taken by no browser's published shortcuts for a
  // page, by the desktop app's menus or by the composer's editor. ⌘⇧\ was the pair's other half
  // until it turned out to be a browser's tab overview.
  {
    id: 'focus-mode', label: 'Focus mode: hide the sidebar and the side pane', scope: 'global', chords: ['mod+\\'],
    keywords: 'focus full window distraction hide sidebar side pane chrome',
  },
  {
    id: 'reading-mode', label: 'Reading mode: fold the composer', scope: 'global', chords: ['mod+shift+.'],
    keywords: 'reading read long answer fold hide collapse composer', disabledReason: 'Open a chat to fold its composer',
  },
  // The chat's working folder beside its conversation (`pages/chat/panes`). Its runner is the open
  // chat's, so the palette runs it only while a chat is open (`useBoundRunner`).
  {
    id: 'workspace-tree', label: "Show or hide the chat's working folder", scope: 'global', chords: ['mod+shift+e'],
    keywords: 'files file tree explorer workspace directory folder', disabledReason: 'Open a chat to see its working folder',
  },
  { id: 'next-needs-you', label: 'Go to the next chat that needs you', keywords: 'approval waiting attention your turn', scope: 'global', chords: ['mod+alt+a'] },
  // Every chat seen, on the gateway's record (`POST /api/chat/seen`), so every window and the phone
  // clear their unread marks. What waits on you stays marked: seeing a chat answers nothing.
  { id: 'mark-all-read', label: 'Mark every chat as read', keywords: 'unread seen clear marks dismiss', scope: 'global', chords: ['shift+escape'] },
  // A new thread, and the sidebar's threads by number: ⌘n opens the nth thread row the sidebar shows,
  // top to bottom (`threadRail.tsx`, `threadNumberRoute`). The ids keep the names they had when the
  // keys counted the chat list, since the desktop app's Go menu sends them (`desktop/goMenu.js`), and
  // an app built before the rename would send the old ones.
  { id: 'new-chat', label: 'New thread', keywords: 'chat start conversation', scope: 'global', chords: ['mod+n'] },
  ...THREAD_NUMBERS.map((n): KeyBinding => ({
    id: `chat-${n}`, label: `Open thread ${n} in the sidebar`, keywords: 'chat switch recent jump', scope: 'global', chords: [`mod+${n}`],
  })),
  {
    id: 'approve-focused', label: 'Approve the focused request', keywords: 'allow permission approval', scope: 'focused',
    chords: ['mod+enter'], disabledReason: 'Move focus to an approval, then press the key there',
  },
  {
    id: 'restore-last-prompt', label: 'Restore the last prompt', keywords: 'history recall previous', scope: 'focused',
    chords: ['arrowup'], disabledReason: 'Press it in an empty composer',
  },
]

/** The rows the desktop app's menus may send by id (`desktop/goMenu.js`). Never an approval. */
export const DESKTOP_COMMANDS: readonly CommandId[] = ['new-chat', ...THREAD_NUMBERS.map((n) => `chat-${n}` as const)]

/** The window event a desktop menu sends a row's id on. */
export const COMMAND_EVENT = 'pclaw:command'

export function bindingOf(id: CommandId): KeyBinding {
  const row = KEYMAP.find((b) => b.id === id)
  if (!row) throw new Error(`no keymap row ${id}`)
  return row
}

function isMac(): boolean {
  const nav = typeof navigator === 'undefined' ? undefined : navigator
  return /Mac|iPhone|iPad/.test(nav?.platform || nav?.userAgent || '')
}

const KEY_WORDS: Record<string, string> = { enter: 'Enter', arrowup: '↑', escape: 'Esc', '`': '`' }

/** A chord as this platform writes it: `⌘⇧P` on a Mac, `Ctrl+Shift+P` elsewhere. */
export function chordLabel(chord: string): string {
  const parts = chord.split('+')
  const key = parts[parts.length - 1]
  const name = KEY_WORDS[key] ?? key.toUpperCase()
  const mods = parts.slice(0, -1)
  if (isMac()) {
    const sym: Record<string, string> = { mod: '⌘', alt: '⌥', shift: '⇧' }
    return mods.map((m) => sym[m]).join('') + name
  }
  const word: Record<string, string> = { mod: 'Ctrl', alt: 'Alt', shift: 'Shift' }
  return [...mods.map((m) => word[m]), name].join('+')
}

/** A row's chords, as the palette and a tooltip show them: `⌘K or ⌘⇧P`. */
export function bindingLabel(id: CommandId): string {
  return bindingOf(id).chords.map(chordLabel).join(' or ')
}

/** The physical key of each punctuation chord, which shifted or on another layout types another
 *  character (`.` shifted is `>`). */
const PUNCTUATION_CODES: Record<string, string> = { '`': 'Backquote', '\\': 'Backslash', '.': 'Period' }

type ChordEvent = Pick<KeyboardEvent, 'key' | 'code' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey'>

/** Whether *e* is *chord*. Letters, digits and punctuation are matched by the physical key as well
 *  as by the character, so ⌥ (which changes the character on a Mac) and other layouts still match.
 *  The modifiers must be exactly the chord's: ⌘ or Ctrl for `mod`, so Alt (which AltGr is outside
 *  a Mac) never runs a chord without it. */
export function matchChord(e: ChordEvent, chord: string): boolean {
  const parts = chord.split('+')
  const key = parts[parts.length - 1]
  const mods = new Set(parts.slice(0, -1))
  if (mods.has('mod') !== (e.metaKey || e.ctrlKey)) return false
  if (mods.has('shift') !== e.shiftKey) return false
  if (mods.has('alt') !== e.altKey) return false
  const pressed = (e.key || '').toLowerCase()
  if (/^[a-z]$/.test(key)) return e.code === `Key${key.toUpperCase()}` || pressed === key
  if (/^[0-9]$/.test(key)) return e.code === `Digit${key}` || pressed === key
  if (key in PUNCTUATION_CODES) return e.code === PUNCTUATION_CODES[key] || pressed === key
  return pressed === key
}

/** Whether *e* is pressed with ⌘, Ctrl or ⌥ held: a chord, which no plain-typing handler takes. */
export function heldWithAModifier(e: Pick<KeyboardEvent, 'metaKey' | 'ctrlKey' | 'altKey'>): boolean {
  return e.metaKey || e.ctrlKey || e.altKey
}

/** Whether *e* is any of *id*'s chords. */
export function matchBinding(e: KeyboardEvent | React.KeyboardEvent, id: CommandId): boolean {
  return bindingOf(id).chords.some((c) => matchChord(e, c))
}

// ── The one global handler ──────────────────────────────────────────────────────────────────────

const runners = new Map<CommandId, () => void>()
let listening = false
/** Who is told when a runner is given or taken back (`useBoundRunner`). */
const watchers = new Set<() => void>()
const runnersChanged = () => { for (const w of watchers) w() }
const watchRunners = (w: () => void) => { watchers.add(w); return () => { watchers.delete(w) } }

function onKey(e: KeyboardEvent) {
  if (e.defaultPrevented) return
  for (const row of KEYMAP) {
    if (row.scope !== 'global') continue
    const run = runners.get(row.id)
    if (!run || !row.chords.some((c) => matchChord(e, c))) continue
    e.preventDefault()
    // A held key runs its command once.
    if (!e.repeat) run()
    return
  }
}

function onCommand(e: Event) {
  const id = (e as CustomEvent).detail
  if (typeof id !== 'string' || !(DESKTOP_COMMANDS as readonly string[]).includes(id)) return
  runners.get(id as CommandId)?.()
}

function listen() {
  if (listening || typeof window === 'undefined') return
  listening = true
  window.addEventListener('keydown', onKey)
  window.addEventListener(COMMAND_EVENT, onCommand)
}

/** Give the global row *id* its runner while the caller is mounted. The latest *run* is the one
 *  the key calls. With no *run* (nothing to act on here) the row has no runner, and its key is left
 *  to the browser. */
export function useKeyBinding(id: CommandId, run: (() => void) | undefined): void {
  const latest = useRef(run)
  latest.current = run
  const runs = run !== undefined
  useEffect(() => {
    if (bindingOf(id).scope !== 'global') throw new Error(`${id} is handled by the focused control`)
    if (!runs) return
    const call = () => latest.current?.()
    runners.set(id, call)
    runnersChanged()
    listen()
    return () => { if (runners.get(id) === call) { runners.delete(id); runnersChanged() } }
  }, [id, runs])
}

/** The runner a mounted page gave the global row *id* with `useKeyBinding`, or none: how the shell's
 *  palette runs a row whose runner lives in a page (a chat's working folder) while that page is
 *  there, and lists it dimmed with its reason when it is not. */
export function useBoundRunner(id: CommandId): (() => void) | undefined {
  return useSyncExternalStore(watchRunners, () => runners.get(id))
}
