/** Large-paste handling for the composer.
 *
 *  Per the agreed UX: a big paste becomes a removable attachment CARD above the
 *  composer AND leaves an inline marker `[Paste #N]` at the paste position in the
 *  textarea, so the user sees WHERE it landed in their prompt. On send, each
 *  marker is expanded back to the full pasted text for the model; the user
 *  bubble keeps the markers (rendered as chips). */

export interface PasteBlock { id: string; seq: number; lines: number; content: string }

export const PASTE_THRESHOLD_LINES = 4
export const PASTE_THRESHOLD_CHARS = 320

/** Inline marker placed at the paste position, e.g. `[Paste #2]`. The seq pairs
 *  the marker with its backing block. */
export const PASTE_MARKER_RE = /\[Paste #(\d+)\]/g
export const markerFor = (seq: number) => `[Paste #${seq}]`

export function shouldCollapsePaste(text: string): boolean {
  if (!text) return false
  return text.split('\n').length >= PASTE_THRESHOLD_LINES || text.length >= PASTE_THRESHOLD_CHARS
}

export function nextSeq(blocks: PasteBlock[]): number {
  return blocks.reduce((m, b) => Math.max(m, b.seq), 0) + 1
}

export function makePasteId(seq: number): string {
  return `paste-${seq}-${seq * 2654435761 % 100000}`
}

/** Expand `[📋 Paste #N]` markers in `text` to the full block content (for the
 *  model). Markers without a matching block are left as-is. */
export function expandPasteMarkers(text: string, blocks: PasteBlock[]): string {
  if (!blocks.length) return text
  const bySeq = new Map(blocks.map((b) => [b.seq, b]))
  return text.replace(PASTE_MARKER_RE, (whole, seq) => {
    const b = bySeq.get(Number(seq))
    return b ? b.content : whole
  })
}

/** Drop blocks whose marker no longer appears in `text` (user deleted it). */
export function pruneBlocks(text: string, blocks: PasteBlock[]): PasteBlock[] {
  const present = new Set<number>()
  let m: RegExpExecArray | null
  PASTE_MARKER_RE.lastIndex = 0
  while ((m = PASTE_MARKER_RE.exec(text)) !== null) present.add(Number(m[1]))
  return blocks.filter((b) => present.has(b.seq))
}
