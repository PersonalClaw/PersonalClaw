/**
 * #616 (frontend half) — a manifest's declared constraints reach the browser.
 *
 * The backend now enforces minimum/maximum/minLength/maxLength/pattern; the
 * form mirrors them as NATIVE input attributes so the field hints and rejects
 * before the round trip. Driven with the issue's own measured schema.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { AppConfigFields, type SchemaProp } from './appConfigForm'

const PROPS: Record<string, SchemaProp> = {
  timeout_secs: { type: 'integer', minimum: 1, maximum: 120 },
  lang: { type: 'string', pattern: '^[a-z]{2}$', maxLength: 2 },
}

afterEach(() => cleanup())

describe('app config form mirrors declared constraints (#616)', () => {
  it('numeric bounds land as min/max/step on the number input', () => {
    render(<AppConfigFields appName="wiki" props={PROPS} cur={{}} set={() => {}} />)
    const num = document.getElementById('app-cfg-wiki-timeout_secs') as HTMLInputElement
    expect(num.type).toBe('number')
    expect(num.min).toBe('1')
    expect(num.max).toBe('120')
    expect(num.step).toBe('1')
  })

  it('string constraints land as pattern/maxLength on the text input', () => {
    render(<AppConfigFields appName="wiki" props={PROPS} cur={{}} set={() => {}} />)
    const txt = document.getElementById('app-cfg-wiki-lang') as HTMLInputElement
    expect(txt.type).toBe('text')
    expect(txt.pattern).toBe('^[a-z]{2}$')
    expect(txt.maxLength).toBe(2)
  })

  it('an unconstrained field renders no stray attributes', () => {
    render(
      <AppConfigFields appName="w2" props={{ note: { type: 'string' } }} cur={{}} set={() => {}} />,
    )
    const txt = document.getElementById('app-cfg-w2-note') as HTMLInputElement
    expect(txt.getAttribute('pattern')).toBeNull()
    expect(txt.getAttribute('maxlength')).toBeNull()
    // screen import kept for the a11y-visible label sanity check:
    expect(screen.getByText('note')).toBeTruthy()
  })
})
