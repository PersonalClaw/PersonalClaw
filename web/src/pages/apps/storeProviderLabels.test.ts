import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { categoryLabel } from './AppsSection'

// ── The Store showed four of sixteen rail categories as apparent typos, and one raw slug ───────────
//
// Two label defects in one file, both the SAME defect: a machine identifier reaching the eye.
//
// 1. `categoryLabel` sentence-cased every tag, so an ACRONYM tag came out mangled. Measured across all
//    54 shipped manifests: `llm` (16 apps) → **"Llm"**, `acp` (4) → **"Acp"**, `tts` (3) → "Tts",
//    `stt` (2) → "Stt", `onnx` → "Onnx". The Categories rail shows the **16 most common tags**, so
//    four of its sixteen entries read as misspellings. `macos` → "Macos", `gemini-cli` → "Gemini cli"
//    and `kiro-cli` → "Kiro cli" are the same thing on rarer tags.
//
//    🔑 That function's own docstring already says a raw slug in a rail heading "looks like a leaked
//    identifier" — so the acronym case is the defect it was written to prevent, in a form it missed.
//
// 2. `PROVIDER_ENTITY_LABEL` had no `trigger` key, so `shared-automations` (`provider.type: "trigger"`)
//    rendered "**trigger** provider" on its Store card and a bare lowercase "trigger" in the rail
//    facet. 🔑 The comment sitting right above that map records `trigger_source` and `duty_gate` being
//    added for *precisely* this fall-through — so this was the third occurrence of a documented class.
//
// 🪤 THE PER-WORD DESIGN IS LOAD-BEARING. A whole-tag allowlist — the obvious fix — repairs `llm` and
// leaves `gemini-cli` reading "Gemini cli", because there the acronym is the SECOND word.
//
// 🪤 AND THE OUTPUT MUST STAY SENTENCE CASE. Title-casing every word would render `image_gen` as
// "Image Gen", against a house convention measured at 1,122 of 1,130 multi-word labels. Both halves
// are asserted below, because a fix for one that breaks the other is not a fix.

const CORE_ROOT = join(process.cwd(), '..')
const MANIFEST_PY = join(CORE_ROOT, 'src/personalclaw/apps/manifest.py')

describe('a category label never reads as a machine identifier', () => {
  it('renders the acronym tags that four of sixteen rail entries used', () => {
    expect(categoryLabel('llm')).toBe('LLM')
    expect(categoryLabel('acp')).toBe('ACP')
    expect(categoryLabel('tts')).toBe('TTS')
    expect(categoryLabel('stt')).toBe('STT')
    expect(categoryLabel('onnx')).toBe('ONNX')
  })

  it('fixes an acronym in a LATER word, which a per-tag allowlist would have missed', () => {
    // 🪤 The reason the map is keyed on words. Both of these ship today.
    expect(categoryLabel('gemini-cli')).toBe('Gemini CLI')
    expect(categoryLabel('kiro-cli')).toBe('Kiro CLI')
  })

  it('renders a brand with its own casing, including a lowercase first letter', () => {
    // The map supplies the whole spelling, so it can override the capitalisation entirely.
    expect(categoryLabel('macos')).toBe('macOS')
  })

  it('leaves ordinary words in SENTENCE case — the first word only', () => {
    // 🔑 The half a title-casing fix would have broken. These are the shipped multi-word tags.
    expect(categoryLabel('image_gen')).toBe('Image gen')
    expect(categoryLabel('video_gen')).toBe('Video gen')
    expect(categoryLabel('brag-doc')).toBe('Brag doc')
    expect(categoryLabel('self-hosted')).toBe('Self hosted')
    expect(categoryLabel('claude-code')).toBe('Claude code')
    expect(categoryLabel('no-key')).toBe('No key')
  })

  it('does not mistake a short ordinary word for an acronym', () => {
    // `sync`, `tool`, `team` and `web` are all ≤4 letters and all render correctly as words. A
    // length-based heuristic instead of an explicit map would have shouted at every one of them.
    expect(categoryLabel('sync')).toBe('Sync')
    expect(categoryLabel('tool')).toBe('Tool')
    expect(categoryLabel('team')).toBe('Team')
    expect(categoryLabel('web')).toBe('Web')
  })

  it('is idempotent and leaves an unknown tag alone', () => {
    expect(categoryLabel('productivity')).toBe('Productivity')
    expect(categoryLabel('LLM')).toBe('LLM')
  })
})

describe('every provider type the backend accepts has a human label', () => {
  // 🔑 THE GUARD THAT WAS MISSING. `test_manifest_types_match_handlers` pins PROVIDER_TYPES to the
  // Python handler registry, so a new type cannot ship without a handler — but nothing pinned it to
  // this map, which is how `trigger`, `trigger_source` and `duty_gate` each reached the UI unlabelled.
  // Parsing the Python set is deliberate: a hand-copied list here would drift the same way.
  const py = readFileSync(MANIFEST_PY, 'utf8')
  const block = py.match(/PROVIDER_TYPES = frozenset\(\s*\{([\s\S]*?)\n\s*\}\s*\)/)?.[1] ?? ''
  const types = [...new Set([...block.matchAll(/^\s*"([a-z_]+)",/gm)].map((m) => m[1]))]

  const tsx = readFileSync(join(process.cwd(), 'src/pages/apps/AppsSection.tsx'), 'utf8')
  const mapBody = tsx.match(/PROVIDER_ENTITY_LABEL: Record<string, string> = \{([\s\S]*?)\n\}/)?.[1] ?? ''
  const labelled = new Set([...mapBody.matchAll(/(\w+):\s*'/g)].map((m) => m[1]))

  it('parsed both sides — otherwise the assertion below is vacuous', () => {
    // A regex that matched nothing would make an empty-set comparison pass forever.
    expect(types.length, 'PROVIDER_TYPES parsed from manifest.py').toBeGreaterThanOrEqual(15)
    expect(labelled.size, 'PROVIDER_ENTITY_LABEL entries parsed from AppsSection').toBeGreaterThanOrEqual(15)
  })

  it('has no unlabelled type — an unlabelled one renders its raw snake_case key to the user', () => {
    const missing = types.filter((t) => !labelled.has(t))
    expect(
      missing,
      `these provider types would render a raw slug in the Store card and rail facet:\n${missing.join('\n')}`,
    ).toEqual([])
  })
})
