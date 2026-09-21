import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AppConfigFields, type SchemaProp } from './appConfigForm'

afterEach(() => cleanup())

describe('app config advanced-field disclosure (#500 defect B)', () => {
  it('keeps ordinary and required fields visible while optional advanced fields disclose on demand', () => {
    const props: Record<string, SchemaProp> = {
      prompt: { type: 'string', 'x-meta': { label: 'Prompt' } },
      model: { type: 'string', 'x-meta': { label: 'Model', tags: ['advanced'] } },
      capability: { type: 'string', 'x-meta': { label: 'Capability', tags: ['advanced'] } },
    }

    render(
      <AppConfigFields
        appName="run-prompt-action"
        props={props}
        cur={{}}
        set={() => {}}
        required={['capability']}
      />,
    )

    expect(document.getElementById('app-cfg-run-prompt-action-prompt')).toBeTruthy()
    expect(document.getElementById('app-cfg-run-prompt-action-capability')).toBeTruthy()
    expect(
      document.getElementById('app-cfg-run-prompt-action-model'),
      'optional advanced fields start hidden',
    ).toBeNull()

    const disclosure = screen.getByRole('button', { name: 'Advanced' })
    expect(disclosure.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(disclosure)

    expect(disclosure.getAttribute('aria-expanded')).toBe('true')
    expect(document.getElementById('app-cfg-run-prompt-action-model')).toBeTruthy()
  })

  it('falls back to the flat form when EVERY field is advanced, rather than hiding the whole surface', () => {
    // The bundled `native-tasks` app is this shape: one optional field tagged
    // `advanced`. Filtering it out leaves a modal with no fields at all.
    const props: Record<string, SchemaProp> = {
      lookback_days: { type: 'number', 'x-meta': { label: 'Lookback days', tags: ['advanced'] } },
    }

    render(
      <AppConfigFields appName="native-tasks" props={props} cur={{}} set={() => {}} required={[]} />,
    )

    expect(
      document.getElementById('app-cfg-native-tasks-lookback_days'),
      'an all-advanced schema still shows its fields',
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Advanced' }), 'nothing left to rank').toBeNull()
  })

  it('keeps the bundled run-prompt-action manifest as the high-fan-out fixture', () => {
    const manifest = JSON.parse(readFileSync(
      join(process.cwd(), '../src/personalclaw/apps/native/run-prompt-action/app.json'),
      'utf8',
    )) as {
      provider: {
        settingsSchema: {
          properties: Record<string, SchemaProp>
        }
      }
    }
    const props = manifest.provider.settingsSchema.properties
    const advanced = Object.values(props).filter(
      (prop) => prop['x-meta']?.tags?.includes('advanced'),
    )

    expect(Object.keys(props)).toHaveLength(9)
    expect(advanced).toHaveLength(8)
  })
})
