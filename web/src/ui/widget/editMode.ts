/** EDITMODE — the artifact's own tunable-parameter block (AMBIENT-SURFACES §3).
 *
 *  An agent that authors a visual artifact declares its tunables ONCE, as a
 *  marker-fenced JSON object inside the artifact's own source:
 *
 *      /*EDITMODE-BEGIN*\/{
 *        "accent":  {"label": "Accent",  "type": "color", "value": "#3b82f6"},
 *        "radius":  {"label": "Corners", "type": "range", "value": "12px",
 *                    "min": 0, "max": 32, "step": 1, "unit": "px"}
 *      }/*EDITMODE-END*\/
 *
 *  Each KEY is a `:root` CSS custom property, sans the `--`. The renderer derives
 *  typed controls from the block, applies a drag straight into the frame's custom
 *  properties (no LLM, no network), and on Save reads the LIVE values back out of
 *  the frame and rewrites this block in place.
 *
 *  This module is the whole parse/rewrite contract and is deliberately pure: the
 *  block is authored by a model and lives inside untrusted artifact HTML, so every
 *  field is validated here rather than at the twelve places that consume one.
 *
 *  Two properties this file owes the rest of the feature:
 *   · **drop-invalid, never throw** — a malformed descriptor costs its own control,
 *     not the artifact's preview.
 *   · **rewrite touches only the fenced bytes** — everything outside the first
 *     BEGIN/END pair comes back byte-identical, and rewriting twice is a no-op.
 */
import { sanitizeCssValue } from './cssSanitize'

export const EDITMODE_BEGIN = '/*EDITMODE-BEGIN*/'
export const EDITMODE_END = '/*EDITMODE-END*/'

/** More than this signals poor CSS-variable hygiene in the artifact AND a rail
 *  nobody can scan; the surplus is dropped and reported rather than rendered. */
export const MAX_EDIT_PARAMS = 8

/** A key becomes `--<key>` on `:root`. Anything outside this shape could smuggle
 *  a second declaration or a selector into the child's style attribute. */
export const EDIT_KEY_RE = /^[a-zA-Z][a-zA-Z0-9-]*$/

export type EditParamType = 'color' | 'range' | 'select' | 'toggle'

export interface EditModeParam {
  /** CSS custom property name without the leading `--`. */
  key: string
  label: string
  type: EditParamType
  /** The authored value — a CSS string, already sanitized. */
  value: string
  /** range only. */
  min?: number
  max?: number
  step?: number
  /** range only — the unit appended to the numeric value (`px`, `rem`, `%`, ''). */
  unit?: string
  /** select only — the allowed CSS strings. */
  options?: string[]
  /** toggle only — the CSS string for each position. */
  on?: string
  off?: string
}

export interface EditModeBlock {
  params: EditModeParam[]
  /** Descriptors refused (malformed) or past MAX_EDIT_PARAMS — surfaced in the UI
   *  so a truncated rail is never silent about it. */
  dropped: number
}

/** Locate the FIRST fence pair. Returns the byte offsets of the JSON body plus the
 *  prefix/suffix that a rewrite must preserve exactly. */
function locateFence(source: string): { start: number; end: number } | null {
  const b = source.indexOf(EDITMODE_BEGIN)
  if (b < 0) return null
  const start = b + EDITMODE_BEGIN.length
  const e = source.indexOf(EDITMODE_END, start)
  if (e < 0) return null
  return { start, end: e }
}

function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

/** Validate one authored descriptor into an `EditModeParam`, or null to drop it. */
function readParam(key: string, raw: unknown): EditModeParam | null {
  if (!EDIT_KEY_RE.test(key)) return null
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const d = raw as Record<string, unknown>
  const type = d.type
  if (type !== 'color' && type !== 'range' && type !== 'select' && type !== 'toggle') return null
  // The authored value lands in a CSS custom property, so it passes the same
  // allowlist the injected theme vars do — a value that cannot be a CSS value is
  // not a tunable, it is an injection attempt or a typo.
  const value = sanitizeCssValue(d.value)
  if (!value) return null
  const label = typeof d.label === 'string' && d.label.trim()
    ? d.label.trim().slice(0, 60)
    : key
  const p: EditModeParam = { key, label, type, value }
  if (type === 'range') {
    const min = num(d.min) ?? 0
    const max = num(d.max)
    // An unbounded range has no slider — a range without a max is not a range.
    if (max === undefined || max <= min) return null
    p.min = min
    p.max = max
    p.step = num(d.step) ?? 1
    p.unit = typeof d.unit === 'string' && /^[a-z%]{0,4}$/.test(d.unit) ? d.unit : ''
  }
  if (type === 'select') {
    const opts = Array.isArray(d.options)
      ? d.options.map((o) => sanitizeCssValue(o)).filter((o) => !!o)
      : []
    // Fewer than two options is a label, not a choice.
    if (opts.length < 2) return null
    p.options = opts.slice(0, 12)
    if (!p.options.includes(value)) p.value = p.options[0]
  }
  if (type === 'toggle') {
    const on = sanitizeCssValue(d.on)
    const off = sanitizeCssValue(d.off)
    if (!on || !off) return null
    p.on = on
    p.off = off
    if (value !== on && value !== off) p.value = off
  }
  return p
}

/** Parse the marker-fenced EDITMODE block out of an artifact's source.
 *
 *  Returns null when the artifact declares no block (the overwhelmingly common
 *  case) or when the fenced body is not a JSON object — an artifact is never
 *  penalized for a malformed block beyond losing its rail. */
export function parseEditModeBlock(source: string): EditModeBlock | null {
  const at = locateFence(source)
  if (!at) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(source.slice(at.start, at.end))
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const params: EditModeParam[] = []
  let dropped = 0
  for (const [key, raw] of Object.entries(parsed as Record<string, unknown>)) {
    const p = readParam(key, raw)
    if (!p) { dropped++; continue }
    if (params.length >= MAX_EDIT_PARAMS) { dropped++; continue }
    params.push(p)
  }
  if (!params.length && !dropped) return null
  return { params, dropped }
}

/** Rewrite the fenced block's `value` fields from `values`, leaving every other
 *  authored field — and every byte outside the fence — exactly as it was.
 *
 *  Idempotent: the re-serialization is deterministic and the fence is matched
 *  positionally, so saving twice writes the block once rather than nesting a
 *  second one. Returns `source` unchanged when there is no parseable block or
 *  when no supplied value actually differs.
 */
export function rewriteEditModeBlock(source: string, values: Record<string, string>): string {
  const at = locateFence(source)
  if (!at) return source
  const body = source.slice(at.start, at.end)
  let parsed: unknown
  try {
    parsed = JSON.parse(body)
  } catch {
    return source
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return source
  const obj = parsed as Record<string, unknown>
  let changed = false
  for (const [key, next] of Object.entries(values)) {
    if (!EDIT_KEY_RE.test(key)) continue
    const d = obj[key]
    if (!d || typeof d !== 'object' || Array.isArray(d)) continue
    const clean = sanitizeCssValue(next)
    if (!clean) continue
    const rec = d as Record<string, unknown>
    if (rec.value === clean) continue
    rec.value = clean
    changed = true
  }
  if (!changed) return source
  const rendered = `\n${JSON.stringify(obj, null, 2)}\n`
  return source.slice(0, at.start) + rendered + source.slice(at.end)
}

// ── parent → child wire (the namespace AS-5 reserved) ────────────────────────

/** Apply live values to the child's `:root` custom properties. Batched: one
 *  message carries every key the user moved since the last frame. */
export const EDIT_MODE_SET_KEYS = '__edit_mode_set_keys'
/** Ask the child for the CURRENT computed value of each key — the read half of
 *  Save. The parent's own state is what it BELIEVES it sent; this is what the
 *  document actually holds. */
export const EDIT_MODE_READ_KEYS = '__edit_mode_read_keys'
/** Turn click-annotation capture on/off in the child. */
export const EDIT_MODE_ANNOTATE = '__edit_mode_annotate'

export interface EditKey { key: string; value: string }

/** A `<input type="color">` only accepts `#rrggbb`. Expand `#rgb`, pass `#rrggbb`
 *  through, and return '' for anything else (oklch/hsl/named) so the rail can
 *  offer a text field instead of silently mangling the authored value. */
export function hexForPicker(value: string): string {
  const v = value.trim()
  if (/^#[0-9a-fA-F]{6}$/.test(v)) return v.toLowerCase()
  const short = /^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$/.exec(v)
  if (short) return `#${short[1]}${short[1]}${short[2]}${short[2]}${short[3]}${short[3]}`.toLowerCase()
  return ''
}

/** Split a range param's CSS value into its number, for the slider. */
export function rangeNumber(param: EditModeParam): number {
  const n = Number.parseFloat(param.value)
  const min = param.min ?? 0
  const max = param.max ?? 100
  if (!Number.isFinite(n)) return min
  return Math.min(max, Math.max(min, n))
}
