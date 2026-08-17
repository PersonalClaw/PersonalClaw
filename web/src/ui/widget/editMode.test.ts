/** The EDITMODE block contract: parse an agent-authored declaration, and rewrite it
 *  without touching the artifact around it.
 *
 *  Both halves are trust boundaries in their own right. The block is authored by a
 *  model INSIDE untrusted artifact HTML, so a descriptor is validated or dropped, and
 *  a value that could not be a CSS value never becomes one. And the rewrite is a
 *  write into a document the user owns: everything outside the fence has to come back
 *  byte-identical, and saving twice must not nest a second block. */
import { describe, it, expect } from 'vitest'
import {
  MAX_EDIT_PARAMS,
  parseEditModeBlock,
  rewriteEditModeBlock,
  hexForPicker,
  rangeNumber,
} from './editMode'

const BEGIN = '/*EDITMODE-BEGIN*/'
const END = '/*EDITMODE-END*/'

/** A fixture whose bytes OUTSIDE the fence are deliberately ugly — blank lines,
 *  trailing spaces, tabs, CRLF, a stray backslash. A rewrite that "tidied" any of
 *  that would be corrupting a document the user owns, and a fixture made only of
 *  single newlines would let the tidying pass unnoticed. */
function artifact(block: string): string {
  return [
    '<div id="card">hello   ',
    '',
    '\t</div>\r',
    '<script>',
    `${BEGIN}${block}${END}`,
    '',
    'document.title = "x\\y";  ',
    '',
    '</script>',
    '<footer>bye</footer>',
    '',
  ].join('\n')
}

const FOUR_TYPES = JSON.stringify({
  accent: { label: 'Accent', type: 'color', value: '#3b82f6' },
  radius: { label: 'Corners', type: 'range', value: '12px', min: 0, max: 32, step: 2, unit: 'px' },
  density: { label: 'Density', type: 'select', value: 'compact', options: ['compact', 'roomy'] },
  shadow: { label: 'Shadow', type: 'toggle', value: 'none', on: '0 2px 8px', off: 'none' },
})

describe('parseEditModeBlock', () => {
  it('returns null for an artifact that declares no block', () => {
    expect(parseEditModeBlock('<p>plain</p>')).toBeNull()
  })

  it('returns null for a half-fenced or unparseable block rather than throwing', () => {
    expect(parseEditModeBlock(`<script>${BEGIN}{"a":1}`)).toBeNull()
    expect(parseEditModeBlock(artifact('{not json'))).toBeNull()
    expect(parseEditModeBlock(artifact('[1,2,3]'))).toBeNull()
  })

  it('derives one typed param per declaration, in authored order', () => {
    const block = parseEditModeBlock(artifact(FOUR_TYPES))
    expect(block).not.toBeNull()
    expect(block!.params.map((p) => [p.key, p.type])).toEqual([
      ['accent', 'color'], ['radius', 'range'], ['density', 'select'], ['shadow', 'toggle'],
    ])
    expect(block!.dropped).toBe(0)
    const radius = block!.params[1]
    expect({ min: radius.min, max: radius.max, step: radius.step, unit: radius.unit })
      .toEqual({ min: 0, max: 32, step: 2, unit: 'px' })
  })

  it('drops descriptors it cannot trust, and counts them', () => {
    const block = parseEditModeBlock(artifact(JSON.stringify({
      ok: { type: 'color', value: '#fff' },
      // a key that is not a CSS custom-property name
      'bad key': { type: 'color', value: '#fff' },
      // a value the CSS allowlist refuses (semicolon injection)
      inject: { type: 'color', value: 'red;}html{display:none' },
      // url() — allowed characters, denied function
      sneak: { type: 'color', value: 'url(http://x)' },
      // a range with no upper bound is not a range
      unbounded: { type: 'range', value: '4px', min: 0 },
      // a "choice" of one
      lonely: { type: 'select', value: 'a', options: ['a'] },
      // a toggle with no second position
      halfToggle: { type: 'toggle', value: 'none', off: 'none' },
      // an unknown type has no control
      mystery: { type: 'font', value: 'serif' },
    })))
    expect(block!.params.map((p) => p.key)).toEqual(['ok'])
    expect(block!.dropped).toBe(7)
  })

  it(`renders at most ${MAX_EDIT_PARAMS} params and reports the surplus`, () => {
    const many: Record<string, unknown> = {}
    for (let i = 0; i < MAX_EDIT_PARAMS + 3; i++) many[`k${i}`] = { type: 'color', value: '#fff' }
    const block = parseEditModeBlock(artifact(JSON.stringify(many)))
    expect(block!.params).toHaveLength(MAX_EDIT_PARAMS)
    expect(block!.dropped).toBe(3)
  })

  it('falls back to the key as the label, and coerces an out-of-set value', () => {
    const block = parseEditModeBlock(artifact(JSON.stringify({
      gap: { type: 'range', value: '8px', max: 40 },
      density: { type: 'select', value: 'nonsense', options: ['compact', 'roomy'] },
      shadow: { type: 'toggle', value: 'nonsense', on: '1', off: '0' },
    })))
    expect(block!.params[0].label).toBe('gap')
    expect(block!.params[1].value).toBe('compact')  // first option
    expect(block!.params[2].value).toBe('0')        // the off position
  })
})

describe('rewriteEditModeBlock', () => {
  const source = artifact(FOUR_TYPES)

  it('writes the new values and leaves every byte outside the fence identical', () => {
    const next = rewriteEditModeBlock(source, { accent: '#ff0000', radius: '20px' })
    expect(next).not.toBe(source)
    const before = (s: string) => s.slice(0, s.indexOf(BEGIN) + BEGIN.length)
    const after = (s: string) => s.slice(s.indexOf(END))
    expect(before(next)).toBe(before(source))
    expect(after(next)).toBe(after(source))
    // Vacuity floor: the fixture really does carry the whitespace a tidy-up would
    // eat, so the two assertions above cannot pass by having nothing to compare.
    expect(before(source)).toMatch(/\n\n/)
    expect(before(source)).toMatch(/[ \t\r]\n/)
    expect(after(source)).toMatch(/\n\n/)
    const reparsed = parseEditModeBlock(next)!
    expect(reparsed.params.find((p) => p.key === 'accent')!.value).toBe('#ff0000')
    expect(reparsed.params.find((p) => p.key === 'radius')!.value).toBe('20px')
    // the untouched params kept their authored values
    expect(reparsed.params.find((p) => p.key === 'density')!.value).toBe('compact')
  })

  it('is idempotent — a second save writes the block ONCE, not nested', () => {
    const once = rewriteEditModeBlock(source, { accent: '#ff0000' })
    const twice = rewriteEditModeBlock(once, { accent: '#ff0000' })
    expect(twice).toBe(once)
    const fences = (s: string) => s.split(BEGIN).length - 1
    expect(fences(once)).toBe(1)
    expect(fences(twice)).toBe(1)
  })

  it('preserves authored fields it does not own', () => {
    const withExtra = artifact(JSON.stringify({
      accent: { label: 'Accent', type: 'color', value: '#000000', note: 'brand blue' },
    }))
    const next = rewriteEditModeBlock(withExtra, { accent: '#ffffff' })
    expect(next).toContain('"note": "brand blue"')
    expect(next).toContain('"value": "#ffffff"')
  })

  it('refuses a value the CSS allowlist rejects, and an undeclared key', () => {
    const next = rewriteEditModeBlock(source, {
      accent: 'red;}html{display:none',
      'not a key': '#fff',
      neverDeclared: '#fff',
    })
    expect(next).toBe(source)
  })

  it('returns the source untouched when there is no block', () => {
    expect(rewriteEditModeBlock('<p>plain</p>', { a: '#fff' })).toBe('<p>plain</p>')
  })
})

describe('control helpers', () => {
  it('gives a native picker a #rrggbb, or nothing at all', () => {
    expect(hexForPicker('#AABBCC')).toBe('#aabbcc')
    expect(hexForPicker('#abc')).toBe('#aabbcc')
    // A non-hex authored colour cannot seed a picker — the rail must fall back to a
    // text field rather than silently rewriting the author's value.
    expect(hexForPicker('oklch(0.7 0.1 250)')).toBe('')
    expect(hexForPicker('rebeccapurple')).toBe('')
  })

  it('clamps a range value into its declared bounds', () => {
    const p = { key: 'r', label: 'r', type: 'range' as const, value: '99px', min: 0, max: 32 }
    expect(rangeNumber(p)).toBe(32)
    expect(rangeNumber({ ...p, value: '-5px' })).toBe(0)
    expect(rangeNumber({ ...p, value: 'auto' })).toBe(0)
    expect(rangeNumber({ ...p, value: '12px' })).toBe(12)
  })
})
