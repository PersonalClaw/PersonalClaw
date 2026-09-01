/** An action config must reach the server in the SHAPES its schema declares.
 *
 * Issue 269, found by driving the UI: `create-task`'s **Labels** field — badge `string[]`,
 * placeholder `[ … ]` — was typed as `market, prep` and persisted as the *string*
 * `"market, prep"`. The provider gates on `isinstance(labels, list)` and drops a non-list, so the
 * created task came out with `labels: []`. No error anywhere in the chain, and everything else in
 * the same config round-tripped fine, which is what made the loss easy to miss.
 *
 * 🔑 THE ISSUE'S DIAGNOSIS POINTED AT THE WRONG LAYER, and the corrected one is smaller. It
 * reported "no renderer for `array`-typed fields … falls through to a plain text input", citing
 * `ActionConfig.tsx:137-150`. Those lines are `PromptVarsFields` — the saved-prompt variable form,
 * a different renderer that has nothing to do with this field. The schema renderer is the shared
 * `tools/schema.tsx`, and it DOES have an array branch: a JSON textarea, which is why the
 * placeholder is `[ … ]`.
 *
 * The parse existed too, and was already correct — `buildArgs` returns
 * `{ error: "labels: invalid JSON" }` rather than storing the raw string. It was simply never on
 * this path: it belongs to the tool inspector, and the two surfaces that render the same schema
 * for an action never called it. So the fix is wiring, and these assertions are on the wiring.
 */

import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { coerceActionConfig } from './ActionConfig'
import type { ActionProvider } from '../../lib/api'

const createTask = {
  name: 'create-task',
  display_name: 'Create Task',
  supports_blocking: false,
  settingsSchema: {
    type: 'object',
    required: ['title_template'],
    properties: {
      title_template: { type: 'string' },
      priority: { type: 'string', enum: ['low', 'high'] },
      labels: { type: 'array', items: { type: 'string' } },
    },
  },
} as unknown as ActionProvider

describe('coerceActionConfig', () => {
  it('parses a JSON array field into a real array', () => {
    const { config, error } = coerceActionConfig([createTask], 'create-task', {
      title_template: 'Market prep', labels: '["market","prep"]',
    })
    expect(error).toBeUndefined()
    expect(config.labels).toEqual(['market', 'prep'])
    expect(config.title_template).toBe('Market prep')
  })

  it('REFUSES the exact input from the issue instead of storing the string', () => {
    // `market, prep` is not JSON. Storing it is what produced `labels: []` on the created task.
    const { config, error } = coerceActionConfig([createTask], 'create-task', {
      title_template: 'Market prep', labels: 'market, prep',
    })
    expect(error).toBe('labels: invalid JSON')
    // And it does NOT hand back a half-coerced config the caller might save anyway.
    expect(config.labels).toBe('market, prep')
  })

  it('leaves the fields that always worked exactly as they were', () => {
    // 🪤 The floor. `priority: "high"` and the `$now` / `$job_name` templates round-tripped
    // correctly all along, and a coercion that touched them would trade one silent loss for a
    // louder one.
    const { config, error } = coerceActionConfig([createTask], 'create-task', {
      title_template: 'Prep for $now', priority: 'high',
    })
    expect(error).toBeUndefined()
    expect(config).toEqual({ title_template: 'Prep for $now', priority: 'high' })
  })

  it('passes the config through untouched for an unknown provider', () => {
    // The picker can hold a name the loaded provider list does not have (a failed read, an app
    // uninstalled mid-edit). Refusing to save then would block the user over a list we could not
    // read; there is nothing to coerce against, so the server's own validation is the backstop.
    const cfg = { anything: 'at all' }
    expect(coerceActionConfig([createTask], 'not-installed', cfg)).toEqual({ config: cfg })
  })
})

describe('every surface that edits an action config coerces before saving', () => {
  const DIR = join(process.cwd(), 'src', 'pages', 'triggers')

  const consumers = readdirSync(DIR)
    .filter((f) => /\.tsx$/.test(f) && !/\.test\./.test(f))
    .filter((f) => statSync(join(DIR, f)).isFile())
    .filter((f) => /<ActionConfig\b/.test(readFileSync(join(DIR, f), 'utf8')))

  it('found the consumers it is supposed to be checking', () => {
    // The vacuity floor: an empty list passes the check below trivially, and this rail exists
    // precisely because ONE of these two surfaces having the coercion is not enough.
    expect(consumers.sort()).toEqual(['LifecycleDetail.tsx', 'TriggerCreatePage.tsx'])
  })

  it.each(consumers)('%s calls coerceActionConfig', (file) => {
    const source = readFileSync(join(DIR, file), 'utf8')
    expect(source).toMatch(/coerceActionConfig\s*\(/)
    // And it must SHOW the refusal. Coercing and then ignoring the error would put the raw string
    // back on the wire while looking fixed.
    expect(source).toMatch(/coerced\.error/)
  })

  it.each(consumers)('%s sends the coerced config, not the raw form state', (file) => {
    const source = readFileSync(join(DIR, file), 'utf8')
    // 🪤 The subtle half. Calling the coercion and then sending `config` anyway is a one-word slip
    // that leaves every assertion above green — so pin that no payload carries the raw `config`
    // identifier.
    //
    // The shorthand pattern is matched POSITION-INDEPENDENTLY, and that matters: the first version
    // anchored on `action: {` and so was vacuous for the create page, which writes
    // `body.action = { provider, config }`. Falsifying the fix proved the leg green against the
    // reintroduced bug — a guard whose own falsification passes is not a guard.
    expect(source).not.toMatch(/\bconfig:\s*config\b/)
    expect(source).not.toMatch(/provider_config:\s*config\b/)
    expect(source).not.toMatch(/\{\s*provider,\s*config\s*\}/)
  })
})
