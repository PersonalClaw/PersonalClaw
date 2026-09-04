import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { missingRequired } from '../tools/schema'
import { AppConfigFields, type SchemaProp } from './appConfigForm'

// ── #491: the Configure dialog ignored the schema's `required` array ────────────────────────────
//
// `grep -nE 'required' web/src/pages/apps/appConfigForm.tsx` returned NOTHING. So a required field
// rendered identical to an optional one, Save was gated on busy-ness alone, and the only feedback
// was a server 400 quoting the SCHEMA KEY (`missing required config key: 'title_template'`) which
// the user then had to map back to the row labelled "Task Title" themselves.
//
// The trigger action-config form (`ActionConfig.tsx`) — the closest sibling, driven by the same kind
// of schema — already did this, including the "no empty option for a required enum" detail. This
// closes the gap and shares the emptiness rule so a third form cannot re-derive it differently.

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); cleanup() })

// ── the emptiness rule, where the silent bugs live ─────────────────────────────────────────────

describe('missingRequired', () => {
  it('counts undefined, null and blank-or-whitespace strings as missing', () => {
    expect(missingRequired({}, ['a'])).toEqual(['a'])
    expect(missingRequired({ a: null }, ['a'])).toEqual(['a'])
    expect(missingRequired({ a: '' }, ['a'])).toEqual(['a'])
    expect(missingRequired({ a: '   ' }, ['a'])).toEqual(['a'])
  })

  it('counts false and 0 as PRESENT', () => {
    // 🪤 The silent one: `!value` would report both as missing and disable Save with no way for the
    // user to satisfy it — a required boolean toggled off, or a required minimum of 0.
    expect(missingRequired({ a: false }, ['a'])).toEqual([])
    expect(missingRequired({ a: 0 }, ['a'])).toEqual([])
  })

  it('treats a key in `satisfied` as filled even when blank', () => {
    // A write-only sensitive field whose secret is already stored: the input is blank BY DESIGN and
    // blank means "keep it" (#43). Counting it missing makes the form permanently unsavable.
    expect(missingRequired({ api_key: '' }, ['api_key'])).toEqual(['api_key'])
    expect(missingRequired({ api_key: '' }, ['api_key'], { satisfied: ['api_key'] })).toEqual([])
  })

  it('reports every missing key, in the order given', () => {
    expect(missingRequired({ b: 'x' }, ['a', 'b', 'c'])).toEqual(['a', 'c'])
  })

  it('accepts a Set, matching schemaProps() which returns one', () => {
    expect(missingRequired({}, new Set(['a']))).toEqual(['a'])
  })
})

// ── the field affordance ───────────────────────────────────────────────────────────────────────

const PROPS: Record<string, SchemaProp> = {
  title_template: { type: 'string', 'x-meta': { label: 'Task Title' } },
  description: { type: 'string', 'x-meta': { label: 'Description' } },
  priority: { type: 'string', enum: ['low', 'high'], 'x-meta': { label: 'Priority' } },
  notify: { type: 'boolean', 'x-meta': { label: 'Notify' } },
}

describe('a required field is marked and announced', () => {
  it('marks the required label and leaves the optional one alone', () => {
    render(<AppConfigFields appName="t" props={PROPS} cur={{}} set={() => {}} required={['title_template']} />)
    expect(screen.getByText('Task Title *')).toBeTruthy()
    expect(screen.getByText('Description')).toBeTruthy()
    expect(screen.queryByText('Description *'), 'an optional field must not be marked').toBeNull()
  })

  it('publishes aria-required on the control, so the cue is not glyph-only', () => {
    render(<AppConfigFields appName="t" props={PROPS} cur={{}} set={() => {}} required={['title_template']} />)
    expect(document.getElementById('app-cfg-t-title_template')!.getAttribute('aria-required')).toBe('true')
    expect(document.getElementById('app-cfg-t-description')!.getAttribute('aria-required')).toBeNull()
  })

  it('marks a required enum and a required boolean too', () => {
    render(<AppConfigFields appName="t" props={PROPS} cur={{}} set={() => {}} required={['priority', 'notify']} />)
    expect(screen.getByText('Priority *')).toBeTruthy()
    expect(document.getElementById('app-cfg-t-priority')!.getAttribute('aria-required')).toBe('true')
    expect(document.getElementById('app-cfg-t-notify')!.getAttribute('aria-required')).toBe('true')
  })

  it('renders exactly as before when the schema declares no required array', () => {
    // Vacuity guard: every app WITHOUT a required array must be untouched by this change.
    render(<AppConfigFields appName="t" props={PROPS} cur={{}} set={() => {}} />)
    expect(screen.getByText('Task Title')).toBeTruthy()
    expect(screen.queryByText('Task Title *')).toBeNull()
    expect(document.getElementById('app-cfg-t-title_template')!.getAttribute('aria-required')).toBeNull()
  })
})

// ── the hook refuses the write, so neither consumer can post an incomplete config ───────────────

function mockApi(schema: unknown, config: unknown, secretSet: string[], saveAppConfig: unknown) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      appConfig: () => Promise.resolve({ schema, config, _secret_set: secretSet }),
      saveAppConfig,
    },
  }))
}

async function mountProbe() {
  const { useAppConfig } = await import('./appConfigForm')
  function Probe() {
    const cfg = useAppConfig('create-task-action')
    return (
      <div>
        <span data-testid="loading">{String(cfg.loading)}</span>
        <span data-testid="missing">{cfg.missingLabels.join('|')}</span>
        <button onClick={() => cfg.save()}>save</button>
        <span data-testid="err">{cfg.err ?? ''}</span>
      </div>
    )
  }
  render(<Probe />)
  await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'))
}

const REQUIRED_SCHEMA = {
  properties: { title_template: { type: 'string', 'x-meta': { label: 'Task Title' } } },
  required: ['title_template'],
}

describe('an incomplete config is refused by the hook, not by the server', () => {
  it('refuses the save and names the LABEL, not the schema key', async () => {
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    mockApi(REQUIRED_SCHEMA, { title_template: '' }, [], saveAppConfig)
    await mountProbe()
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    await waitFor(() => expect(screen.getByTestId('err').textContent).toContain('Task Title'))
    // 🔑 #491's actual complaint: the old message quoted `title_template`.
    expect(screen.getByTestId('err').textContent).not.toContain('title_template')
    expect(saveAppConfig, 'an incomplete config must not be posted').not.toHaveBeenCalled()
  })

  it('exposes the missing labels so a button can say what is needed', async () => {
    mockApi(REQUIRED_SCHEMA, {}, [], vi.fn())
    await mountProbe()
    expect(screen.getByTestId('missing').textContent).toBe('Task Title')
  })

  it('saves once the required field is filled', async () => {
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    mockApi(REQUIRED_SCHEMA, { title_template: 'Ship it' }, [], saveAppConfig)
    await mountProbe()
    expect(screen.getByTestId('missing').textContent).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    await waitFor(() => expect(saveAppConfig).toHaveBeenCalledTimes(1))
  })

  it('does NOT block on a required sensitive field whose secret is already stored', async () => {
    // The input is blank by design here; blank means keep. Blocking would strand the user.
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    const schema = {
      properties: { api_key: { type: 'string', 'x-meta': { label: 'API key', sensitive: true } } },
      required: ['api_key'],
    }
    mockApi(schema, { api_key: '__stored__' }, ['api_key'], saveAppConfig)
    await mountProbe()
    expect(screen.getByTestId('missing').textContent, 'a stored secret satisfies its field').toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    await waitFor(() => expect(saveAppConfig).toHaveBeenCalledTimes(1))
  })

  it('does NOT block on a required boolean that is false', async () => {
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    const schema = {
      properties: { accept: { type: 'boolean', 'x-meta': { label: 'Accept' } } },
      required: ['accept'],
    }
    mockApi(schema, { accept: false }, [], saveAppConfig)
    await mountProbe()
    expect(screen.getByTestId('missing').textContent).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    await waitFor(() => expect(saveAppConfig).toHaveBeenCalledTimes(1))
  })

  it('ignores a required entry naming a property the schema never declares', async () => {
    // Otherwise Save is gated on a field the form does not render — an unsavable form with nothing
    // to fill in, which is strictly worse than the bug being fixed.
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    mockApi({ properties: { a: { type: 'string' } }, required: ['ghost'] }, { a: 'x' }, [], saveAppConfig)
    await mountProbe()
    expect(screen.getByTestId('missing').textContent).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    await waitFor(() => expect(saveAppConfig).toHaveBeenCalledTimes(1))
  })
})

// ── both Save buttons gate, pinned structurally (the components are internal) ───────────────────

describe('the Save affordance matches the guard on both surfaces', () => {
  const disabledExpr = (src: string, marker: string) => {
    const idx = src.indexOf(marker)
    expect(idx, `${marker} must exist`).toBeGreaterThan(-1)
    const open = src.lastIndexOf('<Button', idx)
    const el = src.slice(open, idx)
    return /disabled=\{([\s\S]*?)\}\s/.exec(el)?.[1] ?? ''
  }

  it('the Configure modal disables Save while a required field is blank', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/apps/AppsSection.tsx'), 'utf8')
    const d = disabledExpr(src, '>Save</Button>')
    expect(d, 'gated on the missing-required list').toContain('cfg.missing')
  })

  it('the Settings > Apps row does the same', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/AppsPanel.tsx'), 'utf8')
    expect(src, 'one family, one gate').toContain('cfg.missing.length > 0')
    expect(src, 'and it says which fields').toContain('cfg.missingLabels')
  })
})

// ── the fix has real targets: shipped apps declare required fields ──────────────────────────────

describe('the shipped apps this affects', () => {
  it('several natives declare a required field with a label, read from the real manifests', () => {
    // Read from disk so the census cannot rot into a comment that used to be true. The issue listed
    // 7 apps; asserting a floor rather than an exact set keeps this honest as apps come and go.
    const root = join(process.cwd(), '../src/personalclaw/apps/native')
    const found: string[] = []
    for (const dir of readdirSync(root)) {
      let manifest: Record<string, unknown>
      try { manifest = JSON.parse(readFileSync(join(root, dir, 'app.json'), 'utf8')) } catch { continue }
      const provider = manifest.provider as { settingsSchema?: { required?: string[] } } | undefined
      const setup = manifest.setup as { configSchema?: { required?: string[] } } | undefined
      const schema = setup?.configSchema?.required ? setup.configSchema : provider?.settingsSchema
      if (schema?.required?.length) found.push(dir)
    }
    expect(found.length, `apps with a required field: ${found.join(', ')}`).toBeGreaterThanOrEqual(6)
    // The issue's headline example must still be one of them.
    expect(found).toContain('create-task-action')
  })
})
