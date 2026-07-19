// The customization registry — the single declarative source of every tunable
// UI token. Drives the appearance store (what to persist + apply) and the
// Appearance settings UI (what to render). Add a token here → it's instantly
// customizable everywhere. Defaults mirror design/tokens.css.

export type TokenKind = 'color' | 'scalar' | 'select'

export interface ColorToken {
  kind: 'color'
  /** CSS var name set on <html> (overrides @theme defaults). */
  varName: string
  label: string
  group: string
  /** default hex per mode */
  dark: string
  light: string
}

export interface ScalarToken {
  kind: 'scalar'
  varName: string
  label: string
  group: string
  /** default value (mode-independent) */
  value: number
  min: number
  max: number
  step: number
  /** unit appended when writing the CSS var ('' for unitless multipliers) */
  unit?: string
  /** runtime key (if this scalar also feeds the canvas / motion via runtime.ts) */
  runtimeKey?: 'glow' | 'animSpeed' | 'waveAmount' | 'surfaceAngle' | 'surfaceDistance' | 'dotSize' | 'dotDensity' | 'bounciness' | 'expressiveness'
}

export interface SelectToken {
  kind: 'select'
  varName: string
  label: string
  group: string
  value: string
  options: string[]
  /** runtime key for non-CSS params the canvas reads */
  runtimeKey?: 'dotShape' | 'dotPattern'
}

export type Token = ColorToken | ScalarToken | SelectToken

const c = (varName: string, label: string, group: string, dark: string, light: string): ColorToken =>
  ({ kind: 'color', varName, label, group, dark, light })

const s = (
  varName: string, label: string, group: string, value: number,
  min: number, max: number, step: number, unit = '', runtimeKey?: ScalarToken['runtimeKey'],
): ScalarToken => ({ kind: 'scalar', varName, label, group, value, min, max, step, unit, runtimeKey })

const sel = (varName: string, label: string, group: string, value: string, options: string[], runtimeKey?: SelectToken['runtimeKey']): SelectToken =>
  ({ kind: 'select', varName, label, group, value, options, runtimeKey })

export const TOKENS: Token[] = [
  // ── Brand ──
  c('--color-primary', 'Primary (coral)', 'Brand', '#ff6b5b', '#e85a3f'),
  c('--color-primary-emphasis', 'Primary emphasis', 'Brand', '#ff9a86', '#c8452e'),
  c('--color-on-primary', 'On primary', 'Brand', '#3f1008', '#ffffff'),
  c('--color-primary-container', 'Primary container', 'Brand', '#5a1d12', '#ffe0d6'),
  c('--color-secondary', 'Secondary (amber)', 'Brand', '#ffb454', '#cf7a23'),

  // ── Surfaces ──
  c('--color-canvas', 'Canvas', 'Surfaces', '#0f0f0f', '#f0f4f8'),
  c('--color-surface', 'Surface', 'Surfaces', '#131314', '#ffffff'),
  c('--color-surface-low', 'Surface low', 'Surfaces', '#1b1b1b', '#f4f6f9'),
  c('--color-surface-container', 'Surface container', 'Surfaces', '#1e1f20', '#ffffff'),
  c('--color-surface-high', 'Surface high', 'Surfaces', '#282a2c', '#eef1f5'),
  c('--color-surface-highest', 'Surface highest', 'Surfaces', '#333537', '#e6eaef'),
  c('--color-rail', 'Nav rail', 'Surfaces', '#1f1f1f', '#f0f4f8'),

  // ── Content ──
  c('--color-on-surface', 'Text', 'Content', '#e3e3e3', '#1f1f1f'),
  c('--color-on-surface-low', 'Text muted', 'Content', '#9a9b9c', '#444746'),
  c('--color-on-surface-var', 'Text variant', 'Content', '#c4c7c5', '#5f6368'),
  c('--color-outline', 'Outline', 'Content', '#8e918f', '#8e918f'),
  c('--color-outline-variant', 'Outline subtle', 'Content', '#444746', '#e1e3e1'),

  // ── Semantic ──
  c('--color-ok', 'Success', 'Semantic', '#0ebc5f', '#0a9b4e'),
  c('--color-warn', 'Warning', 'Semantic', '#ff8d41', '#cf6a23'),
  c('--color-danger', 'Danger', 'Semantic', '#f55e57', '#c8362f'),
  c('--color-info', 'Info', 'Semantic', '#4e8ff8', '#1668d8'),

  // ── Glow & gradient (the wave surface + spark + ring) ──
  c('--grad-1', 'Gradient 1', 'Glow & gradient', '#c85a48', '#c85a48'),
  c('--grad-2', 'Gradient 2', 'Glow & gradient', '#ff6b5b', '#e85a3f'),
  c('--grad-3', 'Gradient 3', 'Glow & gradient', '#ff9a7a', '#e07a54'),
  c('--grad-4', 'Gradient 4', 'Glow & gradient', '#ffb454', '#cf7a23'),
  c('--glow-a', 'Glow color A', 'Glow & gradient', '#ff6b5b', '#e85a3f'),
  c('--glow-b', 'Glow color B', 'Glow & gradient', '#ff9a7a', '#e07a54'),
  c('--ring-stop-2', 'Focus-ring highlight', 'Glow & gradient', '#fff5f2', '#3f1008'),

  // ── Typography ── (whole-UI scale + typeface; applied to <html> by the
  //    appearance provider — see app/appearance.tsx)
  s('--ui-zoom', 'UI zoom', 'Typography', 100, 80, 150, 5, '%'),
  s('--font-scale', 'Font size', 'Typography', 100, 85, 160, 5, '%'),
  sel('--font-family', 'Font family', 'Typography', 'dm-sans', ['dm-sans', 'inter', 'mono', 'system']),

  // ── Layout ──
  // NOTE: --content-width is driven by the Account → Content width PRESET
  // (Narrow/Default/Full), not a raw slider, so it's intentionally not a tunable
  // token here.
  // P19 density axis (orthogonal to palette + dark/light): appearance.tsx maps this
  // select to the <html> data-ui attribute, whose tokens.css blocks re-scale the
  // whole app's spacing/radius (and, for cli, the font family). comfortable = default.
  sel('--ui-density', 'UI density', 'Layout', 'comfortable', ['comfortable', 'dense', 'cli']),

  // ── Shape ──
  s('--radius-scale', 'Corner roundness', 'Shape', 1, 0, 2, 0.05),

  // ── 3D surface (the dot wave point-of-view + dots) ──
  s('--surface-angle', 'View angle', '3D surface', 45, 0, 180, 1, '°', 'surfaceAngle'),
  s('--surface-distance', 'View distance', '3D surface', 1, 0.4, 2.2, 0.05, '', 'surfaceDistance'),
  s('--dot-size', 'Dot size', '3D surface', 1, 0.3, 9, 0.1, '', 'dotSize'),
  s('--dot-density', 'Dot density', '3D surface', 1, 0.3, 2, 0.05, '', 'dotDensity'),
  sel('--dot-shape', 'Dot shape', '3D surface', 'claude', ['circle', 'square', 'diamond', 'star', 'sparkle', 'burst', 'claude'], 'dotShape'),
  sel('--dot-pattern', 'Arrangement', '3D surface', 'hex', ['grid', 'diamond', 'hex', 'brick'], 'dotPattern'),

  // ── Motion ──
  s('--anim-speed', 'Animation speed', 'Motion', 1, 0, 2.5, 0.05, '', 'animSpeed'),
  s('--wave-amount', 'Wave amount', 'Motion', 1, 0, 2.5, 0.05, '', 'waveAmount'),
  s('--glow', 'Glow amount', 'Motion', 1, 0, 2.5, 0.05, '', 'glow'),
  // Bounciness/expressiveness: 0 = calm (no overshoot) … 1 = playful (default).
  // Scales the bounce-tier spring overshoot + shape-morph amount app-wide.
  s('--bounciness', 'Bounciness', 'Motion', 1, 0, 1, 0.05, '', 'bounciness'),
  // Expressiveness: the PRIMARY intensity dial (0 = refined/tasteful … 1 = bold/
  // showpiece). Scales hover-lift, press depth, liquid morph / container-transform,
  // and the heavy-effect sheen gate app-wide via expr()/exprHeavy() in
  // design/motion.ts. Default 0.8 (bold-leaning).
  s('--expressiveness', 'Expressiveness', 'Motion', 0.8, 0, 1, 0.05, '', 'expressiveness'),

  // ── Elevation & glass (brand rebrand §3.1 — frosted overlay intensity) ──
  s('--glass-blur', 'Glass blur', 'Elevation & glass', 16, 0, 40, 1, 'px'),
  s('--glass-alpha', 'Glass opacity', 'Elevation & glass', 0.72, 0.4, 1, 0.02),
  // Duration (seconds) of ONE outward pulse of the gateway-connectivity status
  // dot (top-right shell corner). Higher = slower, calmer pulse. The default 2.4s
  // is a gentle breath; the built-in Tailwind ping (1s) read as too fast.
  s('--status-pulse-speed', 'Status dot pulse', 'Motion', 2.4, 0.6, 6, 0.1, 's'),
]

export const GROUPS = ['Brand', 'Surfaces', 'Content', 'Semantic', 'Glow & gradient', 'Typography', 'Layout', 'Shape', 'Elevation & glass', '3D surface', 'Motion']
