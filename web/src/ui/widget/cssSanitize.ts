// Positive-allowlist CSS value sanitizer — used when serializing parent theme
// vars into a sandboxed widget iframe (widgetSrcdoc.ts).
//
// Only permits characters that appear in legitimate color / shadow / length
// values: hex digits, letters, digits, parentheses (rgb/hsl/oklch/calc), commas,
// dots, hyphens, spaces, percent signs, forward-slash (modern color syntax).
// Everything else — semicolons, braces, backslashes, angle brackets, quotes,
// at-signs, colons — is rejected, blocking semicolon-injection, brace-escape,
// Unicode-escape, @-rule, and HTML-escape vectors in one check. A function
// denylist catches url()/expression()/image()/paint()/element() whose chars are
// harmless but whose effect (external loads, legacy IE XSS, Houdini worklets)
// is not.

export const CSS_VALUE_ALLOWED_RE = /^[a-zA-Z0-9#(),.\- %/]+$/
export const CSS_DANGEROUS_FUNC_RE =
  /url\s*\(|expression\s*\(|image\s*\(|image-set\s*\(|paint\s*\(|element\s*\(/i
export const CSS_VALUE_MAX_LEN = 200

/** Sanitize a CSS value (color/length/calc). Returns the trimmed input if it
 *  passes the allowlist + function denylist + length cap; `''` otherwise. NOT
 *  safe for HTML/URL/JS contexts. */
export function sanitizeCssValue(val: unknown): string {
  if (typeof val !== 'string' || val.length > CSS_VALUE_MAX_LEN) return ''
  const trimmed = val.trim()
  if (!trimmed) return ''
  if (!CSS_VALUE_ALLOWED_RE.test(trimmed)) return ''
  if (CSS_DANGEROUS_FUNC_RE.test(trimmed)) return ''
  return trimmed
}
