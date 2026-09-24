import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A create form that told you to pick from a list its own read had emptied ──────────────────────
//
// `#/triggers/new` read its action providers with `.catch(() => [])`, so a failed request looked
// exactly like an install that registers no action provider. That empty list then drove the form's
// one REQUIRED choice, and the Save button's reason blamed the user for it.
//
// Driven with `/api/action-providers` at 500, everything else healthy (dev gateway + failure proxy):
//
//   • the Action picker opens on "No action providers"
//   • Save is disabled, reason: **"Pick a provider"**
//   • and nothing on the page — no alert, no error text, no retry — mentions a failed request
//
// So the user does the one thing they can (name the trigger), is told to pick an action, finds none
// to pick, and has no way to learn why. A closed loop. That is worse than the empty-state lies this
// programme has been closing: the lie also disables the exit.
//
// 🪤 THE SAVE BUTTON'S REASON CAN ONLY NAME A REQUIREMENT. `disabledReason` exists to say which of
// four conditions is outstanding; it has no vocabulary for "a request failed". So the fix needs BOTH
// halves — the field says the read failed and offers the retry, and the reason stops asserting a
// choice the user cannot make.
//
// 🪤 Two probe errors on the way to that evidence, both mine: `getByPlaceholder(/Pick an action/i)`
// found nothing (Combobox renders its placeholder as TEXT, not an input attribute) and I nearly
// reported "the picker does not render"; and filling `input[type=text]` first hit the wrong field, so
// the reason still read "Name the trigger first". Select by what the component actually renders.

const boom = () => Promise.reject(new Error('provider registry unavailable'))

/** 🪤 The form renders deep: children of `ActionConfig`'s schema widgets read `api.models`,
 *  `api.savedAgents` and more, and an unmocked one throws before the Action field ever renders —
 *  the drive then fails for a reason that has nothing to do with the defect. The named reads are
 *  the ones whose SHAPE matters here; everything else resolves to `[]` through a proxy so an
 *  incidental picker cannot break the test. */
function mockApi(over: Record<string, unknown>) {
  const named: Record<string, unknown> = {
    actionProviders: () => Promise.resolve([
      { name: 'run_prompt', display_name: 'Run a prompt', supports_blocking: false, settingsSchema: {} },
    ]),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
    appEvents: () => Promise.resolve([]),
    prompts: () => Promise.resolve([]),
    ...over,
  }
  const api = new Proxy(named, {
    get(target, prop: string) {
      if (prop in target) return target[prop]
      return () => Promise.resolve([])
    },
  })
  vi.doMock('../../lib/api', async (orig) => ({ ...(await orig<Record<string, unknown>>()), api }))
}

async function mount() {
  const { TriggerCreatePage } = await import('./TriggerCreatePage')
  render(<TriggerCreatePage onBack={() => {}} onCreated={() => {}} query={{}} setQuery={() => {}} />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/triggers/new says its action list failed instead of blaming the user', () => {
  it('reports the failed read in the Action field, with a retry', async () => {
    mockApi({ actionProviders: boom })
    await mount()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/could ?n.t load the action providers/i)
    expect(alert.textContent, "carries the server's reason").toMatch(/provider registry unavailable/)
    expect(screen.getByRole('button', { name: /Retry/ }), 'and offers a way out').toBeInTheDocument()
  })

  it('stops telling the user to pick a provider that cannot be listed', async () => {
    mockApi({ actionProviders: boom })
    await mount()
    fireEvent.change(screen.getByPlaceholderText('Morning briefing'), { target: { value: 'nightly digest' } })
    const save = await waitFor(() => screen.getByRole('button', { name: /Create trigger/i }))
    await waitFor(() => {
      expect(save.getAttribute('title'), 'the reason must name the failed read, not a choice')
        .toMatch(/could ?n.t load the action providers/i)
    })
    expect(save.getAttribute('title'), 'never the old blame').not.toMatch(/^Pick a provider/)
  })

  it('still offers the picker — and no error — when the registry really is empty', async () => {
    // The distinction the old code could not draw: an install with no action provider is a real
    // state, and it must keep looking like one.
    mockApi({ actionProviders: () => Promise.resolve([]) })
    await mount()
    await waitFor(() => expect(screen.getByText(/Pick an action/i)).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty registry is not a failure').toBeNull()
  })

  it('renders the picker normally when the read succeeds', async () => {
    mockApi({})
    await mount()
    await waitFor(() => expect(screen.getByText(/Pick an action/i)).toBeInTheDocument())
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('the create paths keep their failures visible', () => {
  const SRC = join(process.cwd(), 'src')
  const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('the providers read no longer substitutes an empty list', () => {
    const code = codeOf('pages/triggers/TriggerCreatePage.tsx')
    const at = code.indexOf("useQuery('triggers:action-providers'")
    expect(at, 'the read must still be here').toBeGreaterThan(-1)
    // Paren-matched, not a prefix: appending `.catch(() => [])` again must fail this.
    let i = code.indexOf('(', at) + 1
    let depth = 1
    while (i < code.length && depth > 0) {
      if (code[i] === '(') depth++
      else if (code[i] === ')') depth--
      i++
    }
    expect(code.slice(at, i), 'no fallback list').not.toMatch(/\.catch\(/)
  })

  it("the ingest path's title/tags patch reports instead of vanishing", () => {
    // The ONLY carrier for a typed title and tags on a file upload — ingest takes bytes only. A
    // swallowed rejection produced a successful-looking create that silently dropped both.
    const code = codeOf('pages/knowledge/KnowledgeCreatePage.tsx')
    const at = code.indexOf('updateKnowledge(res.item_id, custom)')
    expect(at, 'the patch must still be here').toBeGreaterThan(-1)
    const seg = code.slice(at, at + 320)
    expect(seg, 'the rejection must not be discarded').not.toMatch(/\.catch\(\(\)\s*=>\s*\{\s*\}\)/)
    expect(seg, 'and must say what was and was not saved').toMatch(/notify\([^)]*Saved the file/)
  })

  it('InlineError gained the retry as an OPTIONAL prop — existing callers are untouched', () => {
    const code = codeOf('ui/InlineError.tsx')
    expect(code).toMatch(/onRetry\?:\s*\(\) => void/)
    expect(code, 'and it renders only when passed').toMatch(/\{onRetry && \(/)
    // Every existing call site must still typecheck without it, i.e. it cannot be required.
    expect(code, 'onRetry must not be a required prop').not.toMatch(/onRetry:\s*\(\) => void/)
  })

  it('the tag SUGGESTIONS read keeps its fallback, deliberately', () => {
    // Judged, not swept: knowledge tags are free text, so losing the suggestion list costs a
    // convenience and blocks nothing. Unlike the action picker, it gates no requirement.
    const code = codeOf('pages/knowledge/KnowledgeCreatePage.tsx')
    expect(code).toMatch(/knowledgeTags\(\)\.catch\(\(\) => \[\]/)
  })
})
