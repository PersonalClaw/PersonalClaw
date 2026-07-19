/**
 * Curated color SCHEMES. A scheme is a named set of color-token overrides — it
 * retints the brand + glow/gradient identity (accent, primary, focus ring, the
 * spatial-backdrop glow) while leaving surfaces / content / layout coherent. The
 * user picks one; advanced users fork the current scheme into a saved custom one.
 *
 * Each scheme provides {dark, light} for the accent-driving tokens. The keys map
 * 1:1 to tokenRegistry color varNames; the appearance store applies them as
 * overrides (so a scheme = a known override set, and "reset" = the default
 * scheme 'lavender').
 */

export interface Scheme {
  id: string
  label: string
  /** decorative emoji / `icon:<LucideName>` token — only saved custom themes carry one */
  emoji?: string
  /** representative accent swatch (dark, light) for the picker tile */
  swatch: { dark: string; light: string }
  /** color-token overrides keyed by CSS varName → {dark, light} */
  colors: Record<string, { dark: string; light: string }>
}

/** Build the brand+glow override block from a small accent spec, so each scheme
 *  is defined by a few colors but drives the full accent identity coherently. */
function scheme(id: string, label: string, s: {
  primary: [string, string]          // [dark, light]
  primaryEmphasis: [string, string]
  onPrimary: [string, string]
  primaryContainer: [string, string]
  secondary: [string, string]
  gradient: [string, string, string, string]  // grad-1..4 (shared dark/light here for the wave)
  glowA: [string, string]
  glowB: [string, string]
  info: [string, string]
}): Scheme {
  const dl = (d: string, l: string) => ({ dark: d, light: l })
  return {
    id, label,
    swatch: { dark: s.primary[0], light: s.primary[1] },
    colors: {
      '--color-primary': dl(s.primary[0], s.primary[1]),
      '--color-primary-emphasis': dl(s.primaryEmphasis[0], s.primaryEmphasis[1]),
      '--color-on-primary': dl(s.onPrimary[0], s.onPrimary[1]),
      '--color-primary-container': dl(s.primaryContainer[0], s.primaryContainer[1]),
      '--color-secondary': dl(s.secondary[0], s.secondary[1]),
      '--color-info': dl(s.info[0], s.info[1]),
      '--grad-1': dl(s.gradient[0], s.gradient[0]),
      '--grad-2': dl(s.gradient[1], s.primary[1]),
      '--grad-3': dl(s.gradient[2], s.glowB[1]),
      '--grad-4': dl(s.gradient[3], s.gradient[3]),
      '--glow-a': dl(s.glowA[0], s.glowA[1]),
      '--glow-b': dl(s.glowB[0], s.glowB[1]),
    },
  }
}

export const SCHEMES: Scheme[] = [
  // DEFAULT — PersonalClaw coral/terracotta. Warm, energetic, off-Gemini; the
  // ownable accent. Neutral surfaces stay from tokens.css; this drives only the
  // accent identity (primary/focus/loaders/glow), so the whole app re-tints warm.
  scheme('coral', 'Coral', {
    primary: ['#ff6b5b', '#e85a3f'], primaryEmphasis: ['#ff9a86', '#c8452e'],
    onPrimary: ['#3f1008', '#ffffff'], primaryContainer: ['#5a1d12', '#ffe0d6'],
    secondary: ['#ffb454', '#cf7a23'], info: ['#4e9ff8', '#1668d8'],
    gradient: ['#c85a48', '#ff6b5b', '#ff9a7a', '#ffb454'],
    glowA: ['#ff6b5b', '#e85a3f'], glowB: ['#ff9a7a', '#e07a54'],
  }),
  // Honey — warm like coral but golden/muted; cozy, understated.
  scheme('honey', 'Honey', {
    primary: ['#f2a93b', '#c17d18'], primaryEmphasis: ['#ffca7a', '#a96812'],
    onPrimary: ['#3a2504', '#ffffff'], primaryContainer: ['#523611', '#ffe9c2'],
    secondary: ['#e8785a', '#c2503a'], info: ['#4e9ff8', '#1668d8'],
    gradient: ['#b0832f', '#f2a93b', '#ffca7a', '#e8785a'],
    glowA: ['#f2a93b', '#c17d18'], glowB: ['#ffca7a', '#d89a4a'],
  }),
  // Jade — calm, professional, cool-but-not-blue; the grounded counterpart.
  scheme('jade', 'Jade', {
    primary: ['#2dd4bf', '#0d9488'], primaryEmphasis: ['#7fe8da', '#0a7268'],
    onPrimary: ['#04231f', '#ffffff'], primaryContainer: ['#0c3b35', '#cbf5ee'],
    secondary: ['#4e9ff8', '#1668d8'], info: ['#4e9ff8', '#1668d8'],
    gradient: ['#2a9e90', '#2dd4bf', '#7fe8da', '#4e9ff8'],
    glowA: ['#2dd4bf', '#0d9488'], glowB: ['#7fe8da', '#3aa898'],
  }),
  // Ember — near-monochrome + a single warm spark (max restraint, ChatGPT-quiet).
  scheme('ember', 'Ember (mono + spark)', {
    primary: ['#ff7a5c', '#d1543a'], primaryEmphasis: ['#ffa98f', '#b0432c'],
    onPrimary: ['#2a0f08', '#ffffff'], primaryContainer: ['#3a2018', '#f0ddd6'],
    secondary: ['#9a9a96', '#5a5a56'], info: ['#8a9098', '#565c64'],
    gradient: ['#6a6560', '#ff7a5c', '#b09a92', '#8a8580'],
    glowA: ['#ff7a5c', '#d1543a'], glowB: ['#b0a49e', '#8a7f78'],
  }),
  // Legacy lavender scheme (matches the pre-rebrand tokenRegistry defaults).
  scheme('lavender', 'Lavender', {
    primary: ['#9d8bff', '#6a4fd0'], primaryEmphasis: ['#b6bdff', '#563bbf'],
    onPrimary: ['#21134f', '#ffffff'], primaryContainer: ['#2e2168', '#e7deff'],
    secondary: ['#4e8ff8', '#1668d8'], info: ['#4e8ff8', '#1668d8'],
    gradient: ['#8e75b2', '#9d8bff', '#c597ff', '#d8627e'],
    glowA: ['#9d8bff', '#6a4fd0'], glowB: ['#c597ff', '#9168c0'],
  }),
  scheme('ocean', 'Ocean', {
    primary: ['#4aa8ff', '#1668d8'], primaryEmphasis: ['#86c6ff', '#0e4fa8'],
    onPrimary: ['#04243f', '#ffffff'], primaryContainer: ['#0e3358', '#d8ecff'],
    secondary: ['#28c2c8', '#0a8f95'], info: ['#4aa8ff', '#1668d8'],
    gradient: ['#3a7bb0', '#4aa8ff', '#7fd0ff', '#28c2c8'],
    glowA: ['#4aa8ff', '#1668d8'], glowB: ['#7fd0ff', '#3a92c8'],
  }),
  scheme('forest', 'Forest', {
    primary: ['#4fc97f', '#1f9b58'], primaryEmphasis: ['#86e0a6', '#157a44'],
    onPrimary: ['#06280f', '#ffffff'], primaryContainer: ['#0f3a22', '#d6f3e0'],
    secondary: ['#9bcf3a', '#5f8f15'], info: ['#3ab0a0', '#0a8f80'],
    gradient: ['#3a8f5e', '#4fc97f', '#9bdf6f', '#d8c24a'],
    glowA: ['#4fc97f', '#1f9b58'], glowB: ['#9bdf6f', '#5f9f3a'],
  }),
  scheme('rose', 'Rose', {
    primary: ['#ff7eb0', '#d8407e'], primaryEmphasis: ['#ffb0cf', '#bf2f6a'],
    onPrimary: ['#3f0a22', '#ffffff'], primaryContainer: ['#5a1638', '#ffdcec'],
    secondary: ['#c597ff', '#8b5cd8'], info: ['#7e8fff', '#5060d8'],
    gradient: ['#b0567e', '#ff7eb0', '#ffb0cf', '#c597ff'],
    glowA: ['#ff7eb0', '#d8407e'], glowB: ['#ffa6c8', '#c2607e'],
  }),
  scheme('amber', 'Amber', {
    primary: ['#ffb454', '#cf7a23'], primaryEmphasis: ['#ffd08a', '#b8651a'],
    onPrimary: ['#3f2404', '#ffffff'], primaryContainer: ['#5a3810', '#ffe8c8'],
    secondary: ['#f55e57', '#c8362f'], info: ['#ffb454', '#cf7a23'],
    gradient: ['#b07a3a', '#ffb454', '#ffd08a', '#f55e57'],
    glowA: ['#ffb454', '#cf7a23'], glowB: ['#ffce7f', '#d89a4a'],
  }),
  scheme('slate', 'Slate', {
    primary: ['#9aa6b8', '#5a6a82'], primaryEmphasis: ['#c0c8d4', '#46556e'],
    onPrimary: ['#1a212e', '#ffffff'], primaryContainer: ['#2a3340', '#dde3ec'],
    secondary: ['#7f9cff', '#4f6fd8'], info: ['#7f9cff', '#4f6fd8'],
    gradient: ['#6a7588', '#9aa6b8', '#c0c8d4', '#7f8cb0'],
    glowA: ['#9aa6b8', '#5a6a82'], glowB: ['#c0c8d4', '#7a86a0'],
  }),
  scheme('mono', 'Mono', {
    primary: ['#d4d4d4', '#3a3a3a'], primaryEmphasis: ['#f0f0f0', '#242424'],
    onPrimary: ['#171717', '#ffffff'], primaryContainer: ['#333333', '#e4e4e4'],
    secondary: ['#a0a0a0', '#5a5a5a'], info: ['#a0a0a0', '#5a5a5a'],
    gradient: ['#8a8a8a', '#d4d4d4', '#f0f0f0', '#a0a0a0'],
    glowA: ['#d4d4d4', '#5a5a5a'], glowB: ['#f0f0f0', '#8a8a8a'],
  }),
]

export const DEFAULT_SCHEME = 'coral'
export function getScheme(id: string): Scheme | undefined { return SCHEMES.find((s) => s.id === id) }

/** Token groups that constitute the COLOR scheme (everything else — layout,
 *  shape, 3D backdrop, motion — is a separate concern, not part of a scheme). */
export const COLOR_GROUPS = ['Brand', 'Surfaces', 'Content', 'Semantic', 'Glow & gradient']
/** Non-color customization groups, surfaced as their own controls. */
export const BACKDROP_GROUPS = ['3D surface', 'Motion', 'Elevation & glass']
export const TYPOGRAPHY_GROUPS = ['Typography']
export const LAYOUT_GROUPS = ['Layout', 'Shape']
