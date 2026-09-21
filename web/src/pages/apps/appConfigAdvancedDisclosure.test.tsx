import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AppConfigFields, type SchemaProp } from './appConfigForm'
import { ActionConfig } from '../triggers/ActionConfig'

afterEach(() => cleanup())

interface NativeManifest {
  provider: {
    settingsSchema: {
      properties: Record<string, SchemaProp>
      required?: string[]
    }
  }
}

function nativeManifest(name: string): NativeManifest {
  return JSON.parse(readFileSync(
    join(process.cwd(), `../src/personalclaw/apps/native/${name}/app.json`),
    'utf8',
  )) as NativeManifest
}

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

    const disclosure = screen.getByRole('button', { name: 'Advanced (1)' })
    expect(disclosure.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(disclosure)

    expect(disclosure.getAttribute('aria-expanded')).toBe('true')
    expect(document.getElementById('app-cfg-run-prompt-action-model')).toBeTruthy()
  })

  it('falls back to the flat form when EVERY field is advanced, rather than hiding the whole surface', () => {
    // Pin the escape to the shipped shape whose whole config surface depends on it: one optional,
    // advanced-tagged STRING field. A fictional property here let the real manifest drift away
    // from the branch without making the regression red.
    const manifest = nativeManifest('native-tasks')
    const props = manifest.provider.settingsSchema.properties
    expect(Object.keys(props)).toEqual(['storage_dir'])
    expect(props.storage_dir.type).toBe('string')

    render(
      <AppConfigFields
        appName="native-tasks"
        props={props}
        cur={{}}
        set={() => {}}
        required={manifest.provider.settingsSchema.required ?? []}
      />,
    )

    expect(
      document.getElementById('app-cfg-native-tasks-storage_dir'),
      'an all-advanced schema still shows its fields',
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Advanced/ }), 'nothing left to rank').toBeNull()
  })

  it('states both the hidden-field count and how many values differ from their defaults', () => {
    const manifest = nativeManifest('create-task-action')
    const props = manifest.provider.settingsSchema.properties

    render(
      <AppConfigFields
        appName="create-task-action"
        props={props}
        cur={{
          provider: 'native',
          project: 'personalclaw',
          assignee: 'owner',
          due: '2026-09-30',
        }}
        set={() => {}}
        required={manifest.provider.settingsSchema.required ?? []}
      />,
    )

    // Five fields are hidden. The schema-default provider does not count as configured; the three
    // saved values do, so reopening Configure says exactly how much live state is behind the fold.
    expect(screen.getByRole('button', { name: 'Advanced (5, 3 set)' })).toBeTruthy()
  })

  it('gives Apps Configure and Trigger ActionConfig the same answer for the same schema', () => {
    const props: Record<string, SchemaProp> = {
      prompt: { type: 'string', 'x-meta': { label: 'Prompt' } },
      model: { type: 'string', 'x-meta': { label: 'Model', tags: ['advanced'] } },
      capability: { type: 'string', 'x-meta': { label: 'Capability', tags: ['advanced'] } },
    }

    render(
      <ActionConfig
        providers={[{
          name: 'run-prompt-action',
          display_name: 'Run Prompt',
          supports_blocking: false,
          settingsSchema: { type: 'object', properties: props, required: ['capability'] },
        }]}
        provider="run-prompt-action"
        config={{}}
        onProvider={() => {}}
        onConfig={() => {}}
        vars={[]}
      />,
    )

    expect(screen.getByLabelText('Prompt')).toBeTruthy()
    expect(screen.getByLabelText('Capability')).toBeTruthy()
    expect(screen.queryByLabelText('Model'), 'the same optional advanced field starts hidden').toBeNull()

    const disclosure = screen.getByRole('button', { name: 'Advanced (1)' })
    fireEvent.click(disclosure)
    expect(screen.getByLabelText('Model')).toBeTruthy()
  })

  it('keeps the bundled run-prompt-action manifest as the high-fan-out fixture', () => {
    const manifest = nativeManifest('run-prompt-action')
    const props = manifest.provider.settingsSchema.properties
    const advanced = Object.values(props).filter(
      (prop) => prop['x-meta']?.tags?.includes('advanced'),
    )

    expect(Object.keys(props)).toHaveLength(9)
    expect(advanced).toHaveLength(8)
  })

  it('routes all six production schema forms through the one shared disclosure', () => {
    const forms = [
      'pages/apps/appConfigForm.tsx',
      'pages/triggers/ActionConfig.tsx',
      'pages/tools/ToolInspector.tsx',
      'pages/workflows/WorkflowDefDetail.tsx',
      'pages/settings/ModelBackends.tsx',
      'app/onboarding/EssentialsStep.tsx',
    ]
    for (const form of forms) {
      const source = readFileSync(join(process.cwd(), 'src', form), 'utf8')
      expect(source, form).toContain('<SchemaFields')
      expect(source, `${form} must not grow a local tag classifier`)
        .not.toMatch(/tags\?\.includes\(['"]advanced['"]\)/)
    }

    const shared = readFileSync(join(process.cwd(), 'src/pages/tools/schema.tsx'), 'utf8')
    expect(
      shared.match(/tags\?\.includes\(['"]advanced['"]\)/g),
      'the advanced-tag classifier exists exactly once',
    ).toHaveLength(1)
  })
})
