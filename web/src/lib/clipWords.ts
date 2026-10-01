/** `text` on one line, and at most `limit` characters: cut at a word boundary with an ellipsis when
 *  it is longer, so a line a person reads never stops mid-word. Only a single run of text with no
 *  boundary in its second half (a long link, a path) is cut inside itself: dropping it whole would
 *  leave nothing to read.
 *
 *  The server's `textfmt.clip_words`, for text the browser shortens itself — same rule, same cases
 *  (`clipWords.test.ts`), so a line cut on either side reads the same. */
export function clipWords(text: string, limit: number): string {
  const line = String(text ?? '').split(/\s+/).filter(Boolean).join(' ')
  if (line.length <= limit) return line
  let head = line.slice(0, Math.max(limit - 1, 0))
  const cut = head.lastIndexOf(' ')
  if (cut >= Math.floor(head.length / 2)) head = head.slice(0, cut)
  return `${head.replace(/[ ,;:]+$/, '')}…`
}
