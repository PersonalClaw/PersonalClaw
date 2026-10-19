/**
 * Which colour layer a token belongs to (`web/DESIGN.md` §2, "The token layers").
 *
 *   1  palette    `--<hue>-<step>`, raw values (design/palette.ts)
 *   2  semantic   `--color-*`, the brand gradient (`--grad-*`, `--glow-*`) and the ring highlight
 *   3  editor     (none yet)
 *   4  component  composed from layer 2 for one component, listed below
 *
 * A scheme sheet and the `.light` block set layers 1 and 2 only; a component reads layers 2 to 4.
 * `tokenLayers.test.ts` holds `tokens.css` and every scheme sheet to this partition.
 */
import { PALETTE_TOKEN } from './palette'

export type TokenLayer = 1 | 2 | 3 | 4

const SEMANTIC = /^--(?:color-[\w-]+|grad-\d|glow-[ab]|ring-stop-2)$/

/** Tokens made for one component out of layer 2. None is reassigned per mode or per scheme: they
 *  follow because what they are made of does. */
export const COMPONENT_TOKENS = [
  '--ring-stop-1', '--ring-stop-3', '--ring-stop-4', // the composer's focus-ring gradient
  '--glass-bg', // the glass-sky material
  '--shadow-lift', '--shadow-composer-focus', // the two shadows tinted with the primary
] as const

export function tokenLayer(name: string): TokenLayer | undefined {
  if (PALETTE_TOKEN.test(name)) return 1
  if (SEMANTIC.test(name)) return 2
  if ((COMPONENT_TOKENS as readonly string[]).includes(name)) return 4
  return undefined
}
