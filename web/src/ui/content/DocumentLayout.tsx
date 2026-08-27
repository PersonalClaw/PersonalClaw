/** The document editor's layout controls and its page-geometry preview (DFE-6 T3.1-T3.4).
 *
 *  **Every control shows the DOCUMENT's own value, never a default.** That is the atom's
 *  clause and it is also the only safe behaviour: `PageSetup`'s zero values mean "the
 *  writer's template decides", so a size dropdown that displayed "Letter" for a document
 *  which named no size would write Letter into it on the first unrelated save. The unset
 *  state is therefore a real, selectable option ("Template default") rather than a blank.
 *
 *  **The preview is a labelled APPROXIMATION.** No rasterizer exists in this project and
 *  none is being added (§6) — the preview reflects the configured page proportions and
 *  margin insets, and says so in words next to itself. A preview that looked like a render
 *  would be read as one, and then a user would trust it about line breaks it cannot know.
 *
 *  Units: the model is in POINTS end to end; the margin controls show centimetres because
 *  that is what a person setting a 2cm margin types. The conversion is display-only.
 */
import type { DocumentBlock, DocumentPageSetup, DocumentParagraphStyle } from '../../lib/api'
import { Field, NumberField, Select, TextInput } from '../forms'
import { Toggle } from '../Toggle'
import {
  ALIGN_LABEL,
  ORIENTATION_LABEL,
  PAGE_SIZE_LABEL,
  cmToPt,
  previewGeometry,
  ptToCm,
  type PageSizeName,
} from './documentPage'

const EDGES = ['top', 'bottom', 'left', 'right'] as const
type Edge = (typeof EDGES)[number]

const options = (labels: Record<string, string>, keys: readonly string[]) =>
  keys.map((value) => ({ value, label: labels[value] ?? value }))

const SIZE_OPTIONS = options(PAGE_SIZE_LABEL, ['', 'letter', 'a4', 'legal', 'tabloid'])
const ORIENTATION_OPTIONS = options(ORIENTATION_LABEL, ['', 'portrait', 'landscape'])
const ALIGN_OPTIONS = options(ALIGN_LABEL, ['', 'left', 'center', 'right', 'justify'])

/** The page's proportions and text area, drawn from the configured geometry.
 *
 *  Renders nothing when no size is named: there is no page to draw, and drawing Letter
 *  would state a fact the document does not contain.
 */
export function PageGeometryPreview({ page }: { page: DocumentPageSetup }) {
  const geometry = previewGeometry(page.size as PageSizeName, page.orientation, {
    top: page.margin_top_pt,
    bottom: page.margin_bottom_pt,
    left: page.margin_left_pt,
    right: page.margin_right_pt,
  })
  if (!geometry) {
    return (
      <p className="text-[0.75rem] text-on-surface-low">
        Choose a page size to preview the page geometry.
      </p>
    )
  }
  const { aspect, inset } = geometry
  return (
    <div>
      <div
        role="img"
        aria-label={`Approximate ${PAGE_SIZE_LABEL[page.size as PageSizeName] ?? page.size} ${
          page.orientation || 'portrait'
        } page with the text area inset by the configured margins`}
        className="relative mx-auto w-full max-w-[13rem] rounded-sm border border-outline/60 bg-surface"
        style={{ aspectRatio: String(aspect) }}
        // Mirrored as data because `aspect-ratio` is a CSS property jsdom does not model,
        // so a test asserting the drawn proportions can only read it from here.
        data-aspect={aspect.toFixed(4)}
      >
        <div
          className="absolute border border-dashed border-outline/70"
          style={{
            top: `${inset.top}%`,
            bottom: `${inset.bottom}%`,
            left: `${inset.left}%`,
            right: `${inset.right}%`,
          }}
        />
      </div>
      {/* The label is part of the control, not a tooltip: it has to be readable at the
          same moment the shape is, or the shape reads as a rendering. */}
      <p className="mt-2 text-center text-[0.75rem] text-on-surface-low">
        Approximate page geometry — proportions and margins only, not a preview of how the
        text will lay out or where pages will break.
      </p>
    </div>
  )
}

/** Why the controls are not here, when they are not.
 *
 *  **Absent, not disabled** — the same call DFE-5 recorded for the editor itself: a row of
 *  dead dropdowns over a historical version is worse than a sentence saying why there is
 *  nothing to set, and a disabled control that never states its reason is a control a
 *  keyboard user tabs onto and learns nothing from.
 */
function LayoutUnavailable({ reason }: { reason: string }) {
  return (
    <p className="text-[0.8125rem] text-on-surface-var">
      {reason || 'This version is read-only — open the current version to change its layout.'}
    </p>
  )
}

export function PageSetupControls({
  page,
  readOnly,
  disabledReason,
  onChange,
}: {
  page: DocumentPageSetup
  readOnly: boolean
  disabledReason: string
  onChange: (patch: Partial<DocumentPageSetup>) => void
}) {
  const margin = (edge: Edge) => page[`margin_${edge}_pt` as const]
  if (readOnly) {
    return (
      <div className="space-y-3">
        <LayoutUnavailable reason={disabledReason} />
        {/* The PREVIEW still renders: reading the geometry is not editing it, and it is the
            fastest way to see what a historical version's layout was. */}
        <PageGeometryPreview page={page} />
      </div>
    )
  }
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Page size">
          <Select
            value={page.size}
            onChange={(v) => onChange({ size: v })}
            options={SIZE_OPTIONS}
            ariaLabel="Page size"
          />
        </Field>
        <Field label="Orientation">
          <Select
            value={page.orientation}
            onChange={(v) => onChange({ orientation: v })}
            options={ORIENTATION_OPTIONS}
            ariaLabel="Orientation"
          />
        </Field>
      </div>

      <Field label="Margins (cm)" hint="0 keeps the template's own margin for that edge.">
        <div className="flex flex-wrap gap-3">
          {EDGES.map((edge) => (
            <label key={edge} className="flex items-center gap-1.5 text-[0.8125rem] text-on-surface-var">
              <span className="capitalize">{edge}</span>
              <NumberField
                value={ptToCm(margin(edge))}
                onChange={(cm) => onChange({ [`margin_${edge}_pt`]: cmToPt(cm) })}
                min={0}
                step={0.1}
                width="w-20"
                ariaLabel={`${edge} margin in centimetres`}
              />
            </label>
          ))}
        </div>
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Header" hint="Plain text only — one line.">
          <TextInput
            value={page.header_text}
            onChange={(v) => onChange({ header_text: v })}
            size="md"
            ariaLabel="Header text"
          />
        </Field>
        <Field label="Footer" hint="Plain text only — one line.">
          <TextInput
            value={page.footer_text}
            onChange={(v) => onChange({ footer_text: v })}
            size="md"
            ariaLabel="Footer text"
          />
        </Field>
      </div>

      <div className="flex items-center gap-2">
        <Toggle
          on={page.page_numbers}
          onChange={(v) => onChange({ page_numbers: v })}
          size="sm"
          label="Number the pages"
        />
        <span className="text-[0.8125rem] text-on-surface-var">
          Number the pages
          <span className="ml-1 text-on-surface-low">— a field in the footer, so it counts per page.</span>
        </span>
      </div>

      <PageGeometryPreview page={page} />
    </div>
  )
}

export function ParagraphLayoutControls({
  block,
  style,
  readOnly,
  disabledReason,
  onChange,
}: {
  block: DocumentBlock
  style: DocumentParagraphStyle
  readOnly: boolean
  disabledReason: string
  onChange: (patch: Partial<DocumentParagraphStyle>) => void
}) {
  void block
  if (readOnly) {
    return (
      <div className="mt-2 rounded-lg border border-outline/30 bg-surface-container/30 p-2">
        <LayoutUnavailable reason={disabledReason} />
      </div>
    )
  }
  return (
    <div className="mt-2 grid grid-cols-2 gap-2 rounded-lg border border-outline/30 bg-surface-container/30 p-2 sm:grid-cols-4">
      <Field label="Align">
        <Select
          value={style.align}
          onChange={(v) => onChange({ align: v })}
          options={ALIGN_OPTIONS}
          ariaLabel="Paragraph alignment"
        />
      </Field>
      <Field label="Space before (pt)">
        <NumberField
          value={style.space_before_pt}
          onChange={(v) => onChange({ space_before_pt: v })}
          min={0}
          width="w-20"
          ariaLabel="Space before, in points"
        />
      </Field>
      <Field label="Space after (pt)">
        <NumberField
          value={style.space_after_pt}
          onChange={(v) => onChange({ space_after_pt: v })}
          min={0}
          width="w-20"
          ariaLabel="Space after, in points"
        />
      </Field>
      <Field label="Line spacing" hint="0 = template; 1.5 = 150%.">
        <NumberField
          value={style.line_spacing}
          onChange={(v) => onChange({ line_spacing: v })}
          min={0}
          step={0.25}
          width="w-20"
          ariaLabel="Line spacing multiple"
        />
      </Field>
      <Field label="Indent left (pt)">
        <NumberField
          value={style.indent_left_pt}
          onChange={(v) => onChange({ indent_left_pt: v })}
          min={0}
          width="w-20"
          ariaLabel="Left indent, in points"
        />
      </Field>
      <Field label="Indent right (pt)">
        <NumberField
          value={style.indent_right_pt}
          onChange={(v) => onChange({ indent_right_pt: v })}
          min={0}
          width="w-20"
          ariaLabel="Right indent, in points"
        />
      </Field>
      {/* NO `min` — a negative first-line indent is a HANGING indent, a real request the
          model carries. Clamping it at zero here would make it unexpressible from the UI
          while the file format and the writer both support it. */}
      <Field label="First line (pt)" hint="Negative hangs the first line.">
        <NumberField
          value={style.first_line_indent_pt}
          onChange={(v) => onChange({ first_line_indent_pt: v })}
          width="w-20"
          ariaLabel="First line indent, in points; negative hangs the first line"
        />
      </Field>
      {/* The switch's accessible name must CONTAIN the visible label (WCAG 2.5.3 Label in
          Name) — "Keep this paragraph with the next" does not contain "Keep with next", so
          voice control could not act on what the user can read. */}
      <Field label="Keep with next">
        <Toggle
          on={style.keep_with_next}
          onChange={(v) => onChange({ keep_with_next: v })}
          size="sm"
          label="Keep with next paragraph"
        />
      </Field>
    </div>
  )
}
