/** One-line PLAIN-TEXT preview of markdown-ish content — for list rows, tooltips, and
 *  accessible names, where markup markers read as garbage (`**bold**` is announced
 *  star-star-bold-star-star, and a heading's `##` renders literally in a truncated row).
 *
 *  This is deliberately NOT a markdown parser: a preview needs the marks GONE, not the
 *  tree honored. The renderer (`ui/Markdown`) stays the one place markdown becomes
 *  markup; this is the one place it becomes plain text. Both existing consumers of raw
 *  bodies (the inbox row preview, the artifact-card excerpt label path) route here, so
 *  the next preview surface inherits the same stripping instead of leaking marks its
 *  own way.
 */
export function previewText(md: string | null | undefined, cap?: number): string {
  let s = md ?? ''
  // Fenced code: keep the code, drop the fence lines (a preview of a code artifact
  // should show code, not ```lang).
  s = s.replace(/^```[^\n]*$/gm, '')
  // Headings, blockquotes, list bullets — line-leading structural marks.
  s = s.replace(/^\s{0,3}#{1,6}\s+/gm, '')
  s = s.replace(/^\s{0,3}>\s?/gm, '')
  s = s.replace(/^\s{0,3}(?:[-*+]|\d+[.)])\s+/gm, '')
  // Links and images: keep the human text, drop the URL.
  s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
  s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
  // Inline emphasis/code marks. Order matters: ** before *.
  s = s.replace(/\*\*([^*]+)\*\*/g, '$1')
  s = s.replace(/__([^_]+)__/g, '$1')
  s = s.replace(/\*([^*\n]+)\*/g, '$1')
  s = s.replace(/(^|\W)_([^_\n]+)_(?=\W|$)/g, '$1$2')
  s = s.replace(/`([^`]+)`/g, '$1')
  // One line: newlines and runs of space collapse, exactly like the visual truncate
  // renders them.
  s = s.replace(/\s+/g, ' ').trim()
  return cap && s.length > cap ? `${s.slice(0, cap - 1)}…` : s
}
