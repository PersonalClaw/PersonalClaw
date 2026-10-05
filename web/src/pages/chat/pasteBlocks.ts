/** Large-paste handling for the composer.
 *
 *  Per the agreed UX: a big paste becomes a removable attachment CARD above the
 *  composer AND leaves an inline marker `[Paste #N]` at the paste position in the
 *  textarea, so the user sees WHERE it landed in their prompt. Whichever way the
 *  message leaves the page, `asSent` puts each block back in place of its marker for
 *  the model; the user bubble keeps the markers (rendered as chips). */

import type { TurnPaste } from './PasteChip'

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

export function nextSeq(blocks: readonly { seq: number }[]): number {
  return blocks.reduce((m, b) => Math.max(m, b.seq), 0) + 1
}

export function makePasteId(seq: number): string {
  return `paste-${seq}-${seq * 2654435761 % 100000}`
}

/** Drop blocks whose marker no longer appears in `text` (user deleted it). */
export function pruneBlocks<B extends { seq: number }>(text: string, blocks: readonly B[]): B[] {
  const present = new Set<number>()
  let m: RegExpExecArray | null
  PASTE_MARKER_RE.lastIndex = 0
  while ((m = PASTE_MARKER_RE.exec(text)) !== null) present.add(Number(m[1]))
  return blocks.filter((b) => present.has(b.seq))
}

/** Her message as it leaves the page, whichever way it goes: a send, a steer into a running turn,
 *  a message queued for when the turn ends, one brought back with ↑ or from the queue, an edit, a
 *  rewind, a copy. `text` holds each block in place of its `[Paste #N]` marker: what the agent
 *  reads and her chat row keeps. `pastes` are the blocks it holds, which the row keeps beside it,
 *  so her bubble shows each as its chip, live and read back. A marker with no block is words she
 *  typed, and stays as she wrote it.
 *
 *  The one place a marker becomes its paste: nothing else puts a block in a message. The message
 *  is trimmed, as the gateway keeps it, so the block it begins or ends with is kept as the message
 *  then holds it (a final newline goes with the message's), and the row finds each block in it. */
export function asSent(text: string, blocks: readonly TurnPaste[]): { text: string; pastes: TurnPaste[] } {
  const draft = text.trim()
  const held = pruneBlocks(draft, blocks)
  if (!held.length) return { text: draft, pastes: [] }
  const bySeq = new Map(held.map((b) => [b.seq, b]))
  // The draft is trimmed, so only a block it begins or ends with can meet the message's own trim.
  let first: number | undefined
  let last: number | undefined
  for (const m of draft.matchAll(PASTE_MARKER_RE)) {
    const seq = Number(m[1])
    if (!bySeq.has(seq)) continue
    if (m.index === 0) first = seq
    if (m.index + m[0].length === draft.length) last = seq
  }
  const sent = draft.replace(PASTE_MARKER_RE, (marker, seq) => bySeq.get(Number(seq))?.content ?? marker).trim()
  const pastes = held.map(({ seq, content }) => {
    const start = seq === first ? content.trimStart() : content
    const kept = seq === last ? start.trimEnd() : start
    return { seq, lines: kept.split('\n').length, content: kept }
  })
  return { text: sent, pastes }
}

/** A message she sent, taken back into a composer that holds the blocks `held` (↑, or the queue
 *  editor): `text` with its markers, and the blocks to add for them. A block keeps its number
 *  unless the composer holds another block under it; it then takes a free one, and its marker in
 *  `text` with it, so no two blocks share a marker. One the composer already holds is not added. */
export function takeIn(text: string, pastes: readonly TurnPaste[], held: readonly PasteBlock[]): { text: string; blocks: PasteBlock[] } {
  const holding = new Map(held.map((b) => [b.seq, b]))
  const theirs = pruneBlocks(text, pastes)
  let free = Math.max(nextSeq(held), nextSeq(theirs))
  const renumbered = new Map<number, number>()
  const blocks: PasteBlock[] = []
  for (const p of theirs) {
    const other = holding.get(p.seq)
    if (other?.content === p.content) continue
    const seq = other ? free++ : p.seq
    if (seq !== p.seq) renumbered.set(p.seq, seq)
    blocks.push({ id: makePasteId(seq), seq, lines: p.lines, content: p.content })
  }
  const back = renumbered.size
    ? text.replace(PASTE_MARKER_RE, (marker, seq) => {
        const to = renumbered.get(Number(seq))
        return to === undefined ? marker : markerFor(to)
      })
    : text
  return { text: back, blocks }
}
