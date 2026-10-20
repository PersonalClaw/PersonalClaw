import { describe, expect, it } from 'vitest'
import { detectVariables } from './promptMeta'

// ── Issue 596: authoring reads the SAME grammar the engine renders ─────────────────
//
// The old extractor required {{ braces }} to contain only an identifier, so every
// inline typed declaration was invisible — on the issue's measured input the server
// and client returned exact complements. These vectors are the issue's own, plus the
// engine docstring's forms ({{ n::type }}, {{ n::select::[a,b] }}, {{ n::[a,b] }}).

describe('detectVariables mirrors the engine inline-variable grammar (issue 596)', () => {
  it("sees the issue's measured input the way the server does — plus the bare name", () => {
    const vars = detectVariables('Pick {{ color::select::[red,green] }} and {{ topic::text }} plus {{plain}}')
    expect(vars).toEqual([
      { name: 'color', type: 'select', options: ['red', 'green'] },
      { name: 'topic', type: 'text' },
      { name: 'plain', type: 'text' },
    ])
  })

  it('parses every documented typed form', () => {
    expect(detectVariables('{{ n::textarea }}')[0]).toEqual({ name: 'n', type: 'textarea' })
    expect(detectVariables('{{ n::int }}')[0]).toEqual({ name: 'n', type: 'number' })
    expect(detectVariables('{{ n::bool }}')[0]).toEqual({ name: 'n', type: 'boolean' })
    // A bare option list implies select (engine parse_type_decl)
    expect(detectVariables('{{ n::[a, b] }}')[0]).toEqual({ name: 'n', type: 'select', options: ['a', 'b'] })
    // Unknown type names fall back to text, like the engine
    expect(detectVariables('{{ n::mystery }}')[0]).toEqual({ name: 'n', type: 'text' })
  })

  it('excludes what the engine excludes: includes, dotted paths, expressions', () => {
    expect(detectVariables('{{> snippet-name }}')).toEqual([])
    expect(detectVariables('{{ user.name }}')).toEqual([])
    expect(detectVariables('{{ user.name::text }}')).toEqual([])
    expect(detectVariables('{{ upper(name) }}')).toEqual([])
  })

  it('dedupes by name, first declaration wins', () => {
    const vars = detectVariables('{{ x::number }} then {{ x }} then {{ x::text }}')
    expect(vars).toEqual([{ name: 'x', type: 'number' }])
  })
})
