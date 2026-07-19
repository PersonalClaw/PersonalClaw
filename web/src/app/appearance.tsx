import { createContext, useContext, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { TOKENS, type Token } from '../design/tokenRegistry'
import { runtime } from '../design/runtime'
import { SCHEMES, getScheme, DEFAULT_SCHEME, type Scheme } from '../design/schemes'
import { api, type ThemeRecord } from '../lib/api'
import { useMode } from './theme'
import { useIsMobile } from './useIsMobile'

/** Persisted overrides (local, per-browser UI state):
 *   colors: { [varName]: { dark?: hex, light?: hex } }  — unsaved live edits
 *   scalars: { [varName]: number }
 *   scheme: active scheme id (curated `<id>`, saved `custom:<slug>`, or `custom:unsaved`)
 *  Anything absent falls back to the registry default. Saved custom themes are NOT
 *  stored here — they live server-side (/api/themes) so they are shareable and
 *  consistent across browsers and surfaces. There is exactly one theme home. */
export type WidthPreset = 'narrow' | 'default' | 'wide' | 'full'
/** Content-width presets — drive `--content-width` (the maxWidth every page's
 *  content column honors). Pages add their own sensible edge padding (`px-l`), so
 *  'full' is a true 100% (edge-to-edge minus that padding), not an artificial cap.
 *  'narrow' = focused reading; 'default' = comfortable; 'wide' = roomy. */
export const WIDTH_PRESETS: Record<WidthPreset, string> = {
  narrow: '768px',
  default: '1100px',
  wide: '1440px',
  full: '100%',
}

interface Overrides {
  colors: Record<string, { dark?: string; light?: string }>
  scalars: Record<string, number>
  selects: Record<string, string>
  scheme?: string
  widthPreset?: WidthPreset
}

const KEY = 'appearance'
const empty: Overrides = { colors: {}, scalars: {}, selects: {}, scheme: DEFAULT_SCHEME, widthPreset: 'full' }

/** Convert a server ThemeRecord ({dark:{var→hex}, light:{var→hex}}) into the
 *  picker's Scheme shape (colors keyed by var → {dark,light}). A saved theme's
 *  scheme id is `custom:<slug>`. Missing per-mode values fall back to the token
 *  default so a partial theme still renders coherently. */
function themeToScheme(t: ThemeRecord): Scheme {
  const colors: Scheme['colors'] = {}
  for (const tok of TOKENS) {
    if (tok.kind !== 'color') continue
    const d = t.dark?.[tok.varName]
    const l = t.light?.[tok.varName]
    if (d == null && l == null) continue
    colors[tok.varName] = { dark: d ?? tok.dark, light: l ?? tok.light }
  }
  const prim = colors['--color-primary'] ?? { dark: '#ff6b5b', light: '#e85a3f' }
  return { id: `custom:${t.slug}`, label: t.name, emoji: t.emoji, swatch: prim, colors }
}

function load(): Overrides {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return { ...empty, ...JSON.parse(raw) }
  } catch { /* ignore */ }
  return structuredClone(empty)
}

const hexToRgb = (hex: string): [number, number, number] => {
  const h = hex.replace('#', '')
  const n = h.length === 3 ? h.split('').map((x) => x + x).join('') : h
  return [parseInt(n.slice(0, 2), 16), parseInt(n.slice(2, 4), 16), parseInt(n.slice(4, 6), 16)]
}

interface Ctx {
  colorValue: (t: Token, mode: 'dark' | 'light') => string
  scalarValue: (t: Token) => number
  selectValue: (t: Token) => string
  setColor: (varName: string, mode: 'dark' | 'light', hex: string) => void
  setScalar: (varName: string, value: number) => void
  setSelect: (varName: string, value: string) => void
  resetAll: () => void
  resetToken: (varName: string) => void
  // content-width preset
  widthPreset: WidthPreset
  setWidthPreset: (p: WidthPreset) => void
  // schemes (curated + server-persisted custom themes, pooled)
  activeScheme: string
  allSchemes: Scheme[]
  applyScheme: (id: string) => void
  /** Save the CURRENT effective colors as a named custom theme (server-persisted)
   *  and activate it. Returns the new scheme id (`custom:<slug>`). Throws on
   *  server/validation error so the caller can surface it. */
  saveCustomScheme: (label: string, emoji?: string) => Promise<string>
  /** Overwrite an existing saved theme's colors with the current effective set. */
  updateCustomScheme: (id: string, label: string, emoji?: string) => Promise<void>
  deleteCustomScheme: (id: string) => Promise<void>
  themesLoading: boolean
}

const AppearanceCtx = createContext<Ctx>(null as unknown as Ctx)

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const { mode } = useMode()
  const isMobile = useIsMobile()
  const [ov, setOv] = useState<Overrides>(load)
  const ovRef = useRef(ov)
  ovRef.current = ov

  // Server-persisted custom themes (the one home for saved color identities).
  const [serverThemes, setServerThemes] = useState<Scheme[]>([])
  const [themesLoading, setThemesLoading] = useState(true)
  const reloadThemes = useCallback(async () => {
    try {
      const list = await api.themes()
      // The summary list lacks color bodies; fetch each record to build a Scheme.
      const full = await Promise.all(list.map((s) => api.theme(s.slug)))
      setServerThemes(full.map(themeToScheme))
    } catch { /* offline / no themes dir yet — leave curated schemes only */ }
    finally { setThemesLoading(false) }
  }, [])
  useEffect(() => { void reloadThemes() }, [reloadThemes])

  // resolve a token's effective value
  const colorValue = (t: Token, m: 'dark' | 'light') => {
    if (t.kind !== 'color') return ''
    return ov.colors[t.varName]?.[m] ?? (m === 'dark' ? t.dark : t.light)
  }
  const scalarValue = (t: Token) => {
    if (t.kind !== 'scalar') return 0
    return ov.scalars[t.varName] ?? t.value
  }
  const selectValue = (t: Token) => {
    if (t.kind !== 'select') return ''
    return ov.selects[t.varName] ?? t.value
  }

  // apply all tokens to <html> + runtime whenever overrides or mode change
  useEffect(() => {
    const root = document.documentElement
    for (const t of TOKENS) {
      if (t.kind === 'color') {
        const hex = ov.colors[t.varName]?.[mode] ?? (mode === 'dark' ? t.dark : t.light)
        root.style.setProperty(t.varName, hex)
      } else if (t.kind === 'scalar') {
        const v = ov.scalars[t.varName] ?? t.value
        root.style.setProperty(t.varName, `${v}${t.unit ?? ''}`)
        if (t.runtimeKey) runtime[t.runtimeKey] = v
      } else {
        const v = ov.selects[t.varName] ?? t.value
        root.style.setProperty(t.varName, v)
        if (t.runtimeKey === 'dotShape') runtime.dotShape = v as typeof runtime.dotShape
        else if (t.runtimeKey === 'dotPattern') runtime.dotPattern = v as typeof runtime.dotPattern
      }
    }
    // feed glow colors to the canvas runtime
    const ga = TOKENS.find((t) => t.varName === '--glow-a') as Token | undefined
    const gb = TOKENS.find((t) => t.varName === '--grad-3') as Token | undefined
    if (ga && ga.kind === 'color') runtime.glowA = hexToRgb(ov.colors[ga.varName]?.[mode] ?? (mode === 'dark' ? ga.dark : ga.light))
    if (gb && gb.kind === 'color') runtime.glowB = hexToRgb(ov.colors[gb.varName]?.[mode] ?? (mode === 'dark' ? gb.dark : gb.light))

    // ── typography: whole-UI zoom, font-size scale, typeface ──
    const zoom = ov.scalars['--ui-zoom'] ?? 100
    root.style.setProperty('zoom', String(zoom / 100))  // whole-UI scale
    const fontScale = ov.scalars['--font-scale'] ?? 100
    root.style.fontSize = `${fontScale}%`  // scales all rem-based sizing
    // font family: an EXPLICIT user pick overrides the base --font-sans the body
    // reads (default = DM Sans, the PersonalClaw face). When no pick is stored,
    // leave the cascade alone — the @theme default applies AND the data-ui="cli"
    // density block can re-face the app monospace (an inline var here would
    // shadow that stylesheet rule and silently kill cli's monospaced surface).
    const fam = ov.selects['--font-family']
    const FONTS: Record<string, string> = {
      'dm-sans': '"DM Sans", system-ui, sans-serif',
      inter: '"Inter", system-ui, sans-serif',
      mono: '"JetBrains Mono", ui-monospace, monospace',
      system: 'system-ui, -apple-system, "Segoe UI", sans-serif',
    }
    if (fam) root.style.setProperty('--font-sans', FONTS[fam] ?? FONTS['dm-sans'])
    else root.style.removeProperty('--font-sans')

    // ── content width preset (overrides the raw --content-width scalar) ──
    // On a mobile viewport the preset is ignored — content always spans the full
    // width (the narrow/default/wide caps would waste scarce horizontal space and
    // leave dead gutters on a phone). The saved preset is preserved and re-applies
    // as soon as the viewport grows back past the mobile breakpoint.
    root.style.setProperty('--content-width', isMobile ? '100%' : WIDTH_PRESETS[ov.widthPreset ?? 'default'])

    // ── P19 orthogonal theming attributes on <html> ──
    // data-theme = active palette identity (custom `--color-*` stay the mechanism; the
    //   attribute lets CSS / embedded surfaces key off the scheme without re-reading
    //   every var). data-ui = the density axis (comfortable/dense/cli) whose CSS blocks
    //   in tokens.css re-scale --space-scale/--radius-scale/--font-sans app-wide.
    //   data-mode stays owned by theme.tsx (canonical; appSdk reads it) — untouched here.
    root.dataset.theme = ov.scheme ?? DEFAULT_SCHEME
    root.dataset.ui = ov.selects['--ui-density'] ?? 'comfortable'
  }, [ov, mode, isMobile])

  // persist (debounced-ish: on every change; small payload)
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(ov)) } catch { /* ignore */ } }, [ov])

  // Editing a color manually means you've diverged from the named scheme —
  // mark the active scheme as a custom/unsaved edit so the picker reflects it.
  const setColor = (varName: string, m: 'dark' | 'light', hex: string) =>
    setOv((p) => ({ ...p, scheme: p.scheme === DEFAULT_SCHEME || getScheme(p.scheme ?? '') ? 'custom:unsaved' : p.scheme, colors: { ...p.colors, [varName]: { ...p.colors[varName], [m]: hex } } }))
  const setScalar = (varName: string, value: number) =>
    setOv((p) => ({ ...p, scalars: { ...p.scalars, [varName]: value } }))
  const setSelect = (varName: string, value: string) =>
    setOv((p) => ({ ...p, selects: { ...p.selects, [varName]: value } }))
  const resetAll = () => setOv(structuredClone(empty))
  const setWidthPreset = (p: WidthPreset) => setOv((o) => ({ ...o, widthPreset: p }))
  const resetToken = (varName: string) =>
    setOv((p) => {
      const colors = { ...p.colors }; delete colors[varName]
      const scalars = { ...p.scalars }; delete scalars[varName]
      const selects = { ...p.selects }; delete selects[varName]
      return { ...p, colors, scalars, selects }
    })

  // ── schemes (curated + server themes, pooled) ──
  const allSchemes = [...SCHEMES, ...serverThemes]
  const activeScheme = ov.scheme ?? DEFAULT_SCHEME
  const resolveScheme = (id: string): Scheme | undefined =>
    getScheme(id) ?? serverThemes.find((s) => s.id === id)
  /** Apply a scheme: replace the color overrides with the scheme's color set so
   *  switching schemes is clean (no stale per-token overrides linger). */
  const applyScheme = (id: string) => {
    const sc = resolveScheme(id)
    if (!sc) return
    setOv((p) => ({ ...p, scheme: id, colors: { ...sc.colors } }))
  }

  /** Snapshot the CURRENT effective color set into the server theme wire shape
   *  ({dark:{var→hex}, light:{var→hex}}) — the full color-token vocabulary, so a
   *  saved theme captures the whole identity, not just brand accents. */
  const snapshotColors = (): { dark: Record<string, string>; light: Record<string, string> } => {
    const dark: Record<string, string> = {}
    const light: Record<string, string> = {}
    for (const t of TOKENS) {
      if (t.kind !== 'color') continue
      dark[t.varName] = ovRef.current.colors[t.varName]?.dark ?? t.dark
      light[t.varName] = ovRef.current.colors[t.varName]?.light ?? t.light
    }
    return { dark, light }
  }

  /** Save current effective colors as a NEW server-persisted theme + activate. */
  const saveCustomScheme = async (label: string, emoji?: string): Promise<string> => {
    const { dark, light } = snapshotColors()
    const res = await api.createTheme({ name: label.trim() || 'Custom', emoji, dark, light })
    await reloadThemes()
    const id = `custom:${res.slug}`
    setOv((p) => ({ ...p, scheme: id, colors: { ...themeToScheme(res.theme).colors } }))
    return id
  }
  /** Overwrite an existing saved theme with the current effective colors. */
  const updateCustomScheme = async (id: string, label: string, emoji?: string): Promise<void> => {
    const slug = id.replace(/^custom:/, '')
    const { dark, light } = snapshotColors()
    const res = await api.updateTheme(slug, { name: label.trim() || 'Custom', emoji, dark, light })
    await reloadThemes()
    setOv((p) => ({ ...p, scheme: id, colors: { ...themeToScheme(res.theme).colors } }))
  }
  const deleteCustomScheme = async (id: string): Promise<void> => {
    const slug = id.replace(/^custom:/, '')
    await api.deleteTheme(slug)
    await reloadThemes()
    // If the deleted theme was active, fall back to the default scheme cleanly.
    setOv((p) => (p.scheme === id
      ? { ...p, scheme: DEFAULT_SCHEME, colors: { ...(getScheme(DEFAULT_SCHEME)?.colors ?? {}) } }
      : p))
  }

  return (
    <AppearanceCtx.Provider value={{ colorValue, scalarValue, selectValue, setColor, setScalar, setSelect, resetAll, resetToken, widthPreset: ov.widthPreset ?? 'default', setWidthPreset, activeScheme, allSchemes, applyScheme, saveCustomScheme, updateCustomScheme, deleteCustomScheme, themesLoading }}>
      {children}
    </AppearanceCtx.Provider>
  )
}

export const useAppearance = () => useContext(AppearanceCtx)
