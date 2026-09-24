// How the app-bundle UI validation harness enters a tool's arguments into the Tools
// inspector's "Try it" form — leg 5 (`tool-invoke`) of scripts/app_ui_validate.mjs.
//
// This is its own module, split out of the browser driver, because it is the ONE step
// that could silently decide whether the tool-invoke leg means anything, and a browser
// driver cannot be unit-tested. It is now pinned against the REAL rendered form by
// web/src/pages/tools/harnessFillsToolArgs.test.tsx, which renders `SchemaField` into a
// DOM and drives these functions through a locator shim.
//
// ── THE DEFECT THIS REPLACES ──────────────────────────────────────────────────
// The driver used to locate each field by NAME attribute:
//
//     page.locator(`input[name="${key}"], textarea[name="${key}"], select[name="${key}"]`)
//
// `SchemaField` (web/src/pages/tools/schema.tsx) renders NO `name` attribute. It binds the
// visible `<label htmlFor>` to a React `useId()`, and gives the boolean Toggle / custom
// widgets an accessible name instead. So that selector matched NOTHING, every `.fill()`
// was skipped by the `if (await field.count())` guard that wrapped it, and the tool was
// invoked with EMPTY arguments — while the harness reported the placeholder values it had
// only intended to type.
//
// The leg then failed with the tool's own "needs an X" complaint, and the bundle report
// blamed the BUNDLE. That is worse than a crash: the failure is indistinguishable from a
// real one (the tool genuinely did return an error), so a correct bundle can be recorded
// as broken. It had not bitten yet only by luck — `docs-slides` passed because
// `pickInvokableTool` happened to choose its no-argument tool.
//
// ── WHAT REPLACES IT ──────────────────────────────────────────────────────────
// Two properties, not one:
//
//  1. Locate by ACCESSIBLE NAME (`getByLabel`), which is what the component actually
//     renders — the `<label htmlFor>` binding for inputs/selects/textareas, the
//     `aria-label` for the boolean Toggle. The label text is `x-meta.label` when the
//     schema supplies one, so the key alone is not enough to find the field.
//  2. READ THE VALUE BACK. A locator that silently matches nothing, or a `.fill()` that
//     silently no-ops, is exactly how this hid for as long as it did. Every argument the
//     harness claims to have entered is confirmed against the control, and anything it
//     could not enter is returned in `unfilled` so the driver can report a BLOCKED leg
//     naming the harness/inspector — never a FAIL blamed on the bundle.

/** The accessible name the inspector renders for a schema property.
 *
 *  `SchemaField` shows `x-meta.label ?? name`, so a provider that labels
 *  `require_approval` as "Require Approval" is findable only under that text. */
export function argLabel(key, schema) {
  const label = schema?.['x-meta']?.label
  return typeof label === 'string' && label.trim() ? label : key
}

/** The locator for argument `key`'s control in the "Try it" form.
 *
 *  `getByLabel` — NOT `[name=…]`. See the module header: the component renders no `name`,
 *  and matching on one is indistinguishable from matching a field the user left blank. */
export function argFieldLocator(page, key, schema) {
  return page.getByLabel(argLabel(key, schema), { exact: true }).first()
}

/** A self-describing placeholder value for a required argument, by schema type. */
export function placeholderFor(schema) {
  if (Array.isArray(schema?.enum) && schema.enum.length) return String(schema.enum[0])
  switch (schema?.type) {
    case 'integer': case 'number': return '1'
    case 'boolean': return 'false'
    default: return 'harness-probe'
  }
}

const schemaType = (schema) => (Array.isArray(schema?.type) ? schema.type[0] : schema?.type)

/** Fill every required argument of `tool` in the open "Try it" form.
 *
 *  Returns `{ args, unfilled }` where `args` is what the form DEMONSTRABLY holds (read
 *  back from each control), and `unfilled` names every required argument the harness
 *  could not enter, with the reason. A non-empty `unfilled` must block the leg: running
 *  anyway produces the tool's own "needs an X" error and misattributes it to the bundle. */
export async function fillRequiredArgs(page, tool) {
  const args = {}
  const unfilled = []
  for (const key of tool?.parameters?.required ?? []) {
    const schema = tool?.parameters?.properties?.[key]
    const label = argLabel(key, schema)
    const value = placeholderFor(schema)
    const field = argFieldLocator(page, key, schema)
    if (!(await field.count())) {
      unfilled.push(`${key}: the "Try it" form renders no control labelled "${label}"`)
      continue
    }
    if (schemaType(schema) === 'boolean') {
      // A boolean renders as a Toggle, not a fillable control. `seedArgs` starts it OFF,
      // which already IS the placeholder — so there is nothing to type and nothing to
      // read back, and the form genuinely holds `false`.
      args[key] = false
      continue
    }
    const tag = await field.evaluate((el) => el.tagName.toLowerCase()).catch(() => '')
    try {
      if (tag === 'select') await field.selectOption(value)
      else await field.fill(value)
    } catch (err) {
      const why = String(err?.message ?? err).split('\n')[0]
      unfilled.push(`${key}: could not enter a value in the <${tag || '?'}> labelled "${label}" (${why})`)
      continue
    }
    const landed = await field.inputValue().catch(() => null)
    if (landed !== value) {
      unfilled.push(`${key}: typed ${JSON.stringify(value)} into "${label}" but the control reads ${JSON.stringify(landed)}`)
      continue
    }
    args[key] = value
  }
  return { args, unfilled }
}
