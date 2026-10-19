/**
 * The editor-compatible layer, handed to the two editors that cannot read CSS variables.
 *
 * Monaco (`monaco.editor.defineTheme`) and xterm (`ITheme`) take concrete colours, so this adapter
 * reads the computed layer-3 tokens off `<html>` and builds each editor's theme from them, and
 * rebuilds it whenever the scheme, the mode or an owner's colour changes. A layer-3 token is named
 * after the key it feeds (`editor.background` → `--editor-background`, xterm's `brightBlack` →
 * `--terminal-bright-black`), so the map below is the whole adapter: no page keeps per-theme code
 * (`web/DESIGN.md` §2, "The token layers").
 */
import { useEffect, useState } from 'react'
import type { ITheme } from '@xterm/xterm'

/** The Monaco colour ids the editor layer sets. Everything else (syntax colours, diff tints, widgets)
 *  is inherited from Monaco's own `vs` / `vs-dark` base, unchanged. */
export const MONACO_COLOR_IDS = [
  'editor.background',
  'editor.foreground',
  'editor.lineHighlightBackground',
  'editor.selectionBackground',
  'editorCursor.foreground',
  'editorLineNumber.foreground',
  'editorLineNumber.activeForeground',
  'editorGutter.background',
] as const

/** The xterm `ITheme` fields the terminal layer sets: its surface, cursor, selection and the ANSI 16. */
export const XTERM_THEME_KEYS = [
  'background', 'foreground', 'cursor', 'cursorAccent', 'selectionBackground',
  'black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white',
  'brightBlack', 'brightRed', 'brightGreen', 'brightYellow', 'brightBlue', 'brightMagenta', 'brightCyan', 'brightWhite',
] as const satisfies readonly (keyof ITheme)[]

const kebab = (s: string) => s.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)

/** `editor.selectionBackground` → `--editor-selection-background`. */
export const monacoVar = (id: string) => `--${id.split('.').map(kebab).join('-')}`
/** `brightBlack` → `--terminal-bright-black`. */
export const xtermVar = (key: string) => `--terminal-${kebab(key)}`

/** The name of the one Monaco theme every editor in the app uses. */
export const MONACO_THEME = 'personalclaw'

export type TokenReader = (name: string) => string

/** `#abc` → `#aabbcc`; anything else passes through. The sheets are minified, so white computes as
 *  `#fff`, and Monaco's theme parser wants the long form. */
function longHex(v: string): string {
  const s = v.trim()
  return /^#[0-9a-f]{3}$/i.test(s) ? `#${s[1]}${s[1]}${s[2]}${s[2]}${s[3]}${s[3]}` : s
}

/** The computed value of a token on `<html>`. Chromium substitutes `var()` in a computed custom
 *  property, so this is the literal the token resolves to under the active scheme, mode and overrides. */
export const computedToken: TokenReader = (name) =>
  longHex(getComputedStyle(document.documentElement).getPropertyValue(name))

const isLight = () => document.documentElement.classList.contains('light')

export interface MonacoThemeData {
  base: 'vs' | 'vs-dark'
  inherit: true
  rules: []
  colors: Record<string, string>
}

export function monacoTheme(read: TokenReader = computedToken, light = isLight()): MonacoThemeData {
  const colors: Record<string, string> = {}
  for (const id of MONACO_COLOR_IDS) {
    const v = longHex(read(monacoVar(id)))
    if (v) colors[id] = v
  }
  return { base: light ? 'vs' : 'vs-dark', inherit: true, rules: [], colors }
}

export function xtermTheme(read: TokenReader = computedToken): ITheme {
  const theme: Record<string, string> = {}
  for (const key of XTERM_THEME_KEYS) {
    const v = longHex(read(xtermVar(key)))
    if (v) theme[key] = v
  }
  return theme as ITheme
}

/** Calls `cb` whenever something that can move a layer-3 value changes on `<html>`: the mode
 *  (`class`), the scheme (`data-theme`) or an owner's colour (`style`). Returns the unsubscribe. */
export function onEditorTokensChange(cb: () => void): () => void {
  if (typeof MutationObserver === 'undefined') return () => {}
  const mo = new MutationObserver(cb)
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] })
  return () => mo.disconnect()
}

interface MonacoLike {
  editor: { defineTheme(name: string, data: MonacoThemeData): void; setTheme(name: string): void }
}

/** Defines the app's Monaco theme now, and again (then re-applies it) whenever the editor layer
 *  moves. Called once, from `app/monacoSetup.ts`, with the bundled instance. */
export function syncMonacoTheme(monaco: MonacoLike, read: TokenReader = computedToken): () => void {
  let last = ''
  const apply = () => {
    const data = monacoTheme(read)
    const sig = JSON.stringify(data)
    if (sig === last) return
    last = sig
    monaco.editor.defineTheme(MONACO_THEME, data)
    monaco.editor.setTheme(MONACO_THEME)
  }
  apply()
  return onEditorTokensChange(apply)
}

/** The terminal's xterm theme, kept current with the editor layer. */
export function useTerminalTheme(read: TokenReader = computedToken): ITheme {
  const [theme, setTheme] = useState<ITheme>(() => xtermTheme(read))
  useEffect(() => {
    let last = JSON.stringify(theme)
    const update = () => {
      const next = xtermTheme(read)
      const sig = JSON.stringify(next)
      if (sig !== last) { last = sig; setTheme(next) }
    }
    update()
    return onEditorTokensChange(update)
  }, [read]) // eslint-disable-line react-hooks/exhaustive-deps
  return theme
}
