import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { EvalsPanel } from './EvalsPanel'

// ── The fifth point of the config round-trip contract, derived from the OTHER FOUR ───────────────
//
// `evals.*` had four of the five: the dataclass + `_meta` (`config/loader.py:EvalsConfig`),
// `load()`, `to_dict()`, and five entries in the PATCH allowlist
// (`dashboard/handlers/core.py:_EDITABLE_CONFIG`). It had no frontend control — measured,
// `git grep -in evals -- web/src/pages/settings` returned **0** across 33 subpages — while
// `#/learning` rendered four panels telling the user to turn the substrate on.
//
// 🔑 THIS RAIL DERIVES BOTH SIDES AND COMPARES THEM. It does not restate a list of five keys: it
// PARSES the allowlist out of `core.py` and the labels/help/defaults out of `loader.py`, then asks
// the RENDERED panel whether each one is there, named with that label, described with that help,
// and bounded by that min/max. So the failure modes it catches are the ones a hand-written
// expectation cannot:
//
//   · a sixth key added to the allowlist and never surfaced  → the panel is missing a control
//   · a control for a key the allowlist REFUSES              → a switch that flips, 400s, rolls back
//   · a stepper whose range disagrees with the backend's     → an offer the save path rejects
//   · copy drifting from `_meta`                             → the CLI's `--describe` and the UI
//                                                              give two answers to one question
//
// 🪤 `evals.bakeoff_capture_enabled` IS THE TRAP, and it is asserted from both ends. It exists in
// `EvalsConfig` (privacy-sensitive input capture, off by default, SEL-audited) and is DELIBERATELY
// absent from `_EDITABLE_CONFIG`, mirroring `inbound.mcp.allow_remote`. A panel that surfaced it
// would ship a control the backend refuses. Asserting only "the panel has no bakeoff row" would go
// vacuously green if the field were deleted, so this also asserts the field is still IN the
// dataclass and still OUT of the allowlist — the distinction is the property, not the absence.
//
// DRIVEN on a real gateway before this was written (port 10788, `PERSONALCLAW_HOME=/tmp/wave2-ux-
// blast-radius`, `--seed demo-home`): each of the five patched from the UI, read back through
// `GET /api/config/personalclaw`, and `#/learning` re-rendered from four "off" panels to four
// live ones. `bakeoff_capture_enabled` was `curl`-ed at the same endpoint and answered 400.

const SETTINGS = join(process.cwd(), 'src', 'pages', 'settings')
const PY = join(__dirname, '../../../../src/personalclaw')
const py = (rel: string) => readFileSync(join(PY, rel), 'utf8')
const web = (rel: string) => readFileSync(join(SETTINGS, rel), 'utf8')

// ── Side 1: the backend PATCH allowlist ──────────────────────────────────────────────────────────

interface AllowEntry { type: string; min?: number; max?: number }

/** Every `"evals.<key>": {...}` entry in `_EDITABLE_CONFIG`, with its declared bounds. */
function allowlist(): Record<string, AllowEntry> {
  const src = py('dashboard/handlers/core.py')
  const out: Record<string, AllowEntry> = {}
  const re = /^\s*"evals\.([a-z_]+)":\s*\{([^}]*)\},?\s*$/gm
  for (const m of src.matchAll(re)) {
    const body = m[2]
    const num = (name: string) => {
      const hit = new RegExp(`"${name}":\\s*(-?[0-9.]+)`).exec(body)
      return hit ? Number(hit[1]) : undefined
    }
    out[m[1]] = { type: /"type":\s*"(\w+)"/.exec(body)?.[1] ?? '', min: num('min'), max: num('max') }
  }
  return out
}

// ── Side 2: the dataclass, its `_meta` copy, and its defaults ────────────────────────────────────

interface MetaField { label: string; help: string; def: string }

/** Split a python arg list on TOP-LEVEL commas (the help strings contain none, but a default like
 *  `field(default=..., metadata=...)` does — so depth tracking is not optional). */
function topLevelSplit(body: string): string[] {
  const parts: string[] = []
  let depth = 0, quote = '', cur = ''
  for (const ch of body) {
    if (quote) { cur += ch; if (ch === quote) quote = ''; continue }
    if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue }
    if ('([{'.includes(ch)) depth++
    if (')]}'.includes(ch)) depth--
    if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; continue }
    cur += ch
  }
  if (cur.trim()) parts.push(cur)
  return parts
}

/** Join every adjacent string literal in a fragment — python's implicit concatenation, which is how
 *  every multi-line `_meta` help sentence is written. */
const joinLiterals = (frag: string) =>
  [...frag.matchAll(/"([^"]*)"|'([^']*)'/g)].map((m) => m[1] ?? m[2]).join('')

/** Read a `_meta(...)` call's balanced body starting at `from`. */
function balanced(src: string, from: number): string {
  const open = src.indexOf('(', from)
  let depth = 0, quote = ''
  for (let i = open; i < src.length; i++) {
    const ch = src[i]
    if (quote) { if (ch === quote) quote = ''; continue }
    if (ch === '"' || ch === "'") { quote = ch; continue }
    if (ch === '(') depth++
    else if (ch === ')') { depth--; if (depth === 0) return src.slice(open + 1, i) }
  }
  throw new Error('unbalanced')
}

/** `EvalsConfig`'s fields → their `_meta` label, `_meta` help, and literal default. */
function evalsMeta(): Record<string, MetaField> {
  const src = py('config/loader.py')
  const at = src.indexOf('class EvalsConfig:')
  expect(at, 'EvalsConfig must exist in config/loader.py').toBeGreaterThan(-1)
  const end = src.indexOf('\n@dataclass', at)
  const block = src.slice(at, end > -1 ? end : undefined)
  const out: Record<string, MetaField> = {}
  const re = /^    ([a-z_]+):\s*\w+\s*=\s*field\(/gm
  for (const m of block.matchAll(re)) {
    const body = balanced(block, m.index! + m[0].length - 1)
    const def = /default=([^,\n]+)/.exec(body)?.[1]?.trim() ?? ''
    const metaAt = body.indexOf('_meta(')
    const args = topLevelSplit(balanced(body, metaAt + 5))
    out[m[1]] = { label: joinLiterals(args[0] ?? ''), help: joinLiterals(args[1] ?? ''), def }
  }
  return out
}

const ALLOW = allowlist()
const META = evalsMeta()
const EDITABLE = Object.keys(ALLOW).sort()

// ── The parse itself must not be vacuously green ─────────────────────────────────────────────────

describe('the derivation reads the real files', () => {
  it('finds the five allowlisted evals keys, and only those', () => {
    expect(py('dashboard/handlers/core.py').length, 'the handler must be readable').toBeGreaterThan(5000)
    // Named, not counted: a floor like `> 3` stays green when a key is dropped AND one is added.
    expect(EDITABLE).toEqual([
      'ablation_cadence_days', 'default_budget_usd', 'enabled',
      'judge_agreement_floor', 'study_default_k',
    ])
  })

  it('finds every EvalsConfig field with a non-empty label and help', () => {
    expect(py('config/loader.py').length).toBeGreaterThan(50000)
    // 🪤 THE CEILING PIN. `config/loader.py` has zero headroom, and this panel exists to surface
    // fields that ALREADY existed — it must not have grown one. Six fields: the five editable ones
    // plus the privacy-gated capture flag. A seventh is a deliberate decision, so it fails here.
    expect(Object.keys(META).sort()).toEqual([...EDITABLE, 'bakeoff_capture_enabled'].sort())
    for (const [k, m] of Object.entries(META)) {
      expect(m.label.length, `${k} label`).toBeGreaterThan(3)
      expect(m.help.length, `${k} help`).toBeGreaterThan(60)
      expect(m.def, `${k} default`).not.toBe('')
    }
    // Proves the literal-joining parser actually reassembles a multi-line help sentence rather
    // than returning its first fragment.
    expect(META.enabled.help).toContain('~/.personalclaw/evals/')
    expect(META.bakeoff_capture_enabled.help).toContain('privacy-sensitive')
  })

  it('the capture flag is IN the dataclass and OUT of the allowlist', () => {
    // Both halves. Deleting the field would make a bare "no bakeoff control" assertion vacuous.
    expect(META.bakeoff_capture_enabled, 'the field must still exist').toBeTruthy()
    expect(ALLOW.bakeoff_capture_enabled, 'and must NOT be one-click PATCHable').toBeUndefined()
    expect(py('dashboard/handlers/core.py'), 'the exclusion must stay stated, not incidental')
      .toContain('`evals.bakeoff_capture_enabled`')
  })
})

// ── The panel, rendered ──────────────────────────────────────────────────────────────────────────

const personalclawConfig = vi.fn()
const patchConfig = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: (...a: unknown[]) => personalclawConfig(...a),
    patchConfig: (...a: unknown[]) => patchConfig(...a),
  },
}))
const notify = vi.fn()
vi.mock('../../app/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))

/** The saved config, built from the dataclass's OWN defaults — so the fixture cannot drift from
 *  what a fresh install actually holds. */
function savedConfig(): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, m] of Object.entries(META)) {
    out[k] = m.def === 'False' ? false : m.def === 'True' ? true : Number(m.def)
  }
  return out
}

const DEFAULTS = savedConfig()

async function mount() {
  personalclawConfig.mockResolvedValue({ evals: { ...DEFAULTS } })
  const r = render(<EvalsPanel />)
  await waitFor(() => expect(screen.getByRole('switch', { name: META.enabled.label })).toBeTruthy())
  return r
}

/** The text a control's `aria-describedby` actually resolves to. */
const describedText = (el: Element) =>
  (el.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? '').join(' ')

/** The rendered hint element for a field — the one whose text BEGINS with that field's `_meta`
 *  help. Found by content rather than by structure so the assertion does not encode `Row`'s
 *  markup, and so a hint that drifted from `_meta` fails as "not found" rather than as a
 *  mismatched string.
 *
 *  🔎 FOUND, NOT FIXED — and this is why the hint is located this way instead of through the
 *  switch's `aria-describedby`. `ui/forms`' `NumberField`/`TextInput`/`Select`/`DateInput` all
 *  consume `useFieldHintId()`, and `ui/Toggle` does NOT — so on all 69 hinted `ToggleRow`s in
 *  settings the sentence beside the switch is visible but not programmatically its description,
 *  even though `Row` publishes the id for exactly that purpose (its own comment says so). A
 *  one-line fix in `ui/Toggle.tsx` with a 69-row blast radius is its own change with its own
 *  before/after, not a rider on this panel. The four numeric rows here DO get the association
 *  asserted below, which is what makes the gap a measured difference rather than a hunch. */
function hintEl(key: string): HTMLElement {
  const help = META[key].help.trim()
  const hit = [...document.querySelectorAll('div, p')]
    .find((el) => el.children.length === 0 && (el.textContent ?? '').trim().startsWith(help))
  expect(hit, `${key}: the rendered hint must BE the _meta help, not a second wording of it`).toBeTruthy()
  return hit as HTMLElement
}

describe('#/settings/evals surfaces exactly what the allowlist permits', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    patchConfig.mockResolvedValue({})
  })

  it('renders one control per allowlisted key — and no others', async () => {
    const { container } = await mount()
    const controls = [...container.querySelectorAll('[role="switch"], input[type="number"]')]
    expect(controls.length, 'one control per allowlisted key, no more').toBe(EDITABLE.length)
  })

  it('names each control with the field’s own _meta label', async () => {
    await mount()
    for (const key of EDITABLE) {
      const label = META[key].label
      const role = ALLOW[key].type === 'bool' ? 'switch' : 'spinbutton'
      expect(screen.getByRole(role, { name: label }), `${key} must be named "${label}"`).toBeTruthy()
    }
  })

  it('describes each control with the field’s own _meta help', async () => {
    await mount()
    for (const key of EDITABLE) {
      // Rendered, and in the SAME ROW as its control — a sentence elsewhere on the page describes
      // nothing. (`hintEl` asserts the text; `contains` asserts it is beside the right control.)
      const role = ALLOW[key].type === 'bool' ? 'switch' : 'spinbutton'
      const control = screen.getByRole(role, { name: META[key].label })
      const hint = hintEl(key)
      const row = hint.parentElement?.parentElement
      expect(row?.contains(control), `${key}: the hint must sit with its control`).toBe(true)
    }
  })

  it('and ASSOCIATES it, wherever the primitive supports one', async () => {
    await mount()
    for (const key of EDITABLE) {
      if (ALLOW[key].type === 'bool') continue   // see `hintEl` — ui/Toggle drops the hint id
      const el = screen.getByRole('spinbutton', { name: META[key].label })
      expect(el.getAttribute('aria-describedby'), `${key} must be described`).toBe(hintEl(key).id)
      expect(describedText(el).trim().startsWith(META[key].help.trim()), `${key} description`).toBe(true)
    }
  })

  it('bounds every stepper by the allowlist’s own min/max', async () => {
    await mount()
    for (const key of EDITABLE) {
      if (ALLOW[key].type === 'bool') continue
      const el = screen.getByRole('spinbutton', { name: META[key].label })
      expect(Number(el.getAttribute('min')), `${key} min`).toBe(ALLOW[key].min)
      expect(Number(el.getAttribute('max')), `${key} max`).toBe(ALLOW[key].max)
    }
  })

  it('steps finely enough to express the value it is displaying', async () => {
    await mount()
    for (const key of EDITABLE) {
      if (ALLOW[key].type === 'bool') continue
      const el = screen.getByRole('spinbutton', { name: META[key].label })
      const step = Number(el.getAttribute('step'))
      const def = Number(META[key].def)
      const rungs = (def - (ALLOW[key].min ?? 0)) / step
      // 🪤 THE DEFECT THIS CATCHES: the shared `NumberRow` stepped by 1, and
      // `judge_agreement_floor` is a RATE in 0…1 whose default is 0.6 — two reachable values for a
      // control already showing a third. A stepper that cannot land on the saved value is not a
      // control, it is a way to overwrite one.
      expect(Math.abs(rungs - Math.round(rungs)) < 1e-9,
        `${key}: step ${step} cannot reach its own default ${def}`).toBe(true)
    }
  })

  it('offers NO control for the privacy-gated capture flag', async () => {
    const { container } = await mount()
    expect(screen.queryByRole('switch', { name: META.bakeoff_capture_enabled.label })).toBeNull()
    expect(container.textContent).not.toMatch(/bake-?off/i)
    // And it is not merely un-labelled: nothing in the panel targets the path at all.
    expect(web('EvalsPanel.tsx')).not.toContain('bakeoff_capture_enabled"')
  })

  it('PATCHes the allowlisted path, and rolls the row back when the save is refused', async () => {
    await mount()
    const sw = screen.getByRole('switch', { name: META.enabled.label })
    expect(sw.getAttribute('aria-checked')).toBe('false')
    fireEvent.click(sw)
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('evals.enabled', true))
    await waitFor(() => expect(sw.getAttribute('aria-checked')).toBe('true'))

    // A refused save must not leave the row asserting a value the backend does not hold.
    patchConfig.mockRejectedValueOnce(new Error('nope'))
    fireEvent.click(sw)
    await waitFor(() => expect(sw.getAttribute('aria-checked')).toBe('true'))
    // …and it must name the CONTROL, not the config key nobody has seen on screen.
    expect(notify.mock.calls.at(-1)?.[0]).toContain(META.enabled.label)
    expect(notify.mock.calls.at(-1)?.[0]).not.toContain('evals.enabled')
  })

  it('every numeric row patches its own allowlisted path', async () => {
    await mount()
    for (const key of EDITABLE) {
      if (ALLOW[key].type === 'bool') continue
      const el = screen.getByRole('spinbutton', { name: META[key].label })
      const next = (ALLOW[key].min ?? 0) + Number(el.getAttribute('step'))
      fireEvent.change(el, { target: { value: String(next) } })
      fireEvent.blur(el)
      await waitFor(() => expect(patchConfig).toHaveBeenCalledWith(`evals.${key}`, next))
    }
  })
})

describe('the switch says what turning it on costs', () => {
  beforeEach(() => { sessionStorage.clear(); vi.clearAllMocks(); patchConfig.mockResolvedValue({}) })

  it('names the model AND judge calls, before the first study runs', async () => {
    await mount()
    const hint = (hintEl('enabled').textContent ?? '').toLowerCase()
    // The substrate is free to enable and not free to use. `_meta` says "nothing runs until you
    // invoke a study" — true, and read as "this is free". The row has to close that gap itself,
    // because the row is where the decision is made.
    expect(hint, 'the hint must say it spends model calls').toMatch(/model call/)
    expect(hint, 'and that the judge is one of them').toMatch(/judge/)
    expect(hint, 'and point at the budget knob below').toMatch(/budget/)
  })
})

// ── Findability: a control nobody can reach is not the fifth point ───────────────────────────────

describe('a user can find it', () => {
  it('is a registered subpage at #/settings/evals', () => {
    const page = web('SettingsPage.tsx')
    expect(page).toMatch(/\{ id: 'evals', label: 'Evaluations', icon: \w+, render: \(\) => <EvalsPanel \/> \}/)
  })

  it('has a card on the settings hub, which is the ONLY navigation', () => {
    // `SettingsHome` renders `SETTINGS_WIDGETS` and holds no second list, so a subpage with no
    // widget is reachable only by typing its URL — and invisible to the settings search.
    const home = web('SettingsHome.tsx')
    expect(home).toContain('SETTINGS_WIDGETS')
    const widgets = web('settingsWidgets.tsx')
    expect(widgets).toMatch(/id: 'evals', group: '[^']+', label: 'Evaluations'/)
    // The search index must answer BOTH words: the product says "Evaluations", the config key and
    // every backend message say "evals".
    const at = widgets.indexOf("id: 'evals'")
    const body = widgets.slice(at, at + 2600)
    expect(body).toMatch(/evals evaluations/)
    // And it must not fabricate a config it never loaded — this tile carries a live switch.
    expect(body).toMatch(/loading=\{e === undefined && !evalErr\}/)
    expect(body).toMatch(/Boolean\(evalErr\) && <div/)
  })

  it('distinguishes both in-prose links by more than hue', () => {
    // WCAG 1.4.1. Measured on the live route: `#ff9a86` on `#9a9b9c` is **1.35:1** — a colour-blind
    // reader has nothing to go on. axe's `link-in-text-block` (serious) caught the Settings one and
    // SKIPPED the `#/learning` one, because its matcher ignores a link followed only by
    // punctuation; both are pinned here so the rule's blind spot is not what decides.
    for (const [file, dir] of [['EvalsPanel.tsx', 'settings'], ['EvalsOff.tsx', 'learning']] as const) {
      const src = readFileSync(join(process.cwd(), 'src', 'pages', dir, file), 'utf8')
      const jsx = src.slice(src.indexOf('export function'))
      const link = /<TextLink[^>]*>/.exec(jsx)?.[0] ?? ''
      expect(link, `${file} must have an in-prose link`).toContain('href=')
      expect(link, `${file}: hue alone is not a distinction`).toContain('underline')
      // And the ink must be the shade that clears AA on this ground (canvas, not a card).
      expect(link, `${file}: the base accent fails AA on canvas`).toContain('ink="emphasis"')
    }
  })

  it('is where #/learning sends you when the substrate is off', () => {
    const off = readFileSync(join(process.cwd(), 'src', 'pages', 'learning', 'EvalsOff.tsx'), 'utf8')
    const jsx = off.slice(off.indexOf('export function EvalsOff'))
    expect(jsx, 'the link must be DEEP — the bare hub is 34 cards').toContain('href="#/settings/evals"')
    // Named by the control's `_meta` label, so the words here are the words on the destination.
    expect(jsx).toContain(META.enabled.label)
    // NOT by its config path: `evals.enabled` appears nowhere on that page.
    expect(jsx, 'a dotted path is a terminal instruction, not a link').not.toMatch(/evals\.enabled/)
    expect(jsx, 'and the CLI command it replaced is gone — one instruction').not.toMatch(/config set/)
  })
})
