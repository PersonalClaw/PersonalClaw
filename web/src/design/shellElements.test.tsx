/**
 * PERSONALITY-THEMES §S2 (PT-3) — the SHELL_ELEMENTS registry contract.
 *
 * A shell element is a component a theme mounts at the App shell, above every page.
 * That is the most dangerous shape in this plan, so the registry is closed and this
 * file asserts the three properties that make "closed" mean something:
 *
 * **1. Unknown ids are refused.** `ShellElementId` is a literal union and
 * `SHELL_ELEMENTS` is a total `Record`, so an unregistered id cannot be written into
 * `behavior.shellElement` and a registered id cannot lack an entry — both are
 * compile errors, which is the strongest form available. `getShellElement` closes
 * the runtime half: a string that arrives from anywhere else (a stale persisted
 * value, a future app-contributed manifest) resolves to `null`, never to a component.
 *
 * **2. Every entry is decorative, proved by RENDERING it.** The invariants
 * (`aria-hidden`, `pointer-events-none`, a `data-shell-element` marker) are checked
 * against the mounted DOM of each entry, not against its source text. A source scan
 * would pass on a component that writes `aria-hidden` in a comment or on the wrong
 * node; the accessibility tree does not care what the source says. The loop covers
 * every present and future member, so a new entry cannot ship without the contract.
 *
 * **3. Lazy means lazy.** Two halves, because either alone is defeatable: every
 * registry value must be a React lazy type (a plain component reference would eagerly
 * bundle), AND no module outside this registry may statically import
 * `ui/personality/` (one such import re-bundles the chunk into the entry graph and
 * the lazy wrapper becomes decoration). The source scan strips comments before
 * matching — a rail in this repo has already been fooled by a match inside a comment
 * — and carries a vacuity floor, so a scan that stops finding anything reds instead
 * of passing.
 */

import { describe, expect, it } from 'vitest'
import { Suspense } from 'react'
import { render, waitFor } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { SHELL_ELEMENTS, getShellElement, PERSONALITIES } from './personalities'

const SRC = join(process.cwd(), 'src')
const IDS = Object.keys(SHELL_ELEMENTS) as (keyof typeof SHELL_ELEMENTS)[]

/** Mount one registry entry and hand back its root node. The entries are lazy, so
 *  the first paint is the Suspense fallback — wait for the real node to arrive. */
async function mountEntry(id: keyof typeof SHELL_ELEMENTS): Promise<HTMLElement> {
  const Element = SHELL_ELEMENTS[id]
  const { container } = render(
    <Suspense fallback={null}>
      <Element />
    </Suspense>,
  )
  await waitFor(() => expect(container.firstElementChild).not.toBeNull())
  return container.firstElementChild as HTMLElement
}

describe('the registry is closed', () => {
  it('has at least one entry (without which every assertion below is vacuous)', () => {
    expect(IDS.length).toBeGreaterThan(0)
  })

  it('getShellElement refuses an id that is not a member', () => {
    // The runtime half of closed. A persisted override from a removed entry, or an
    // id invented by a future caller, must resolve to nothing — never to whatever
    // component a prototype-chain lookup happens to reach.
    expect(getShellElement('terminal-scanlines-v2')).toBeNull()
    expect(getShellElement('../ui/personality/TerminalStrip')).toBeNull()
    expect(getShellElement('')).toBeNull()
    expect(getShellElement(undefined)).toBeNull()
    // INHERITED keys are not members. `map[id] ?? null` reads the prototype chain, so
    // each of these resolved to a real object (`Object` itself) that React would then
    // try to render as a component. This is the case that made the resolver an
    // own-key test rather than an index — and the same hole was live in the sibling
    // `getErrorTreatment`, fixed in the same change.
    for (const inherited of ['constructor', 'toString', 'hasOwnProperty', '__proto__', 'valueOf']) {
      expect(getShellElement(inherited), inherited).toBeNull()
    }
  })

  it('resolves every registered id to its own entry', () => {
    for (const id of IDS) expect(getShellElement(id)).toBe(SHELL_ELEMENTS[id])
  })

  it('no personality declares an id outside the registry', () => {
    for (const p of PERSONALITIES) {
      const id = p.behavior.shellElement
      if (id) expect(IDS as string[], `${p.id} → ${id}`).toContain(id)
    }
  })
})

describe('every entry is decorative — asserted from the rendered DOM', () => {
  it.each(IDS)('%s is aria-hidden, pointer-events-none, and marks its own id', async (id) => {
    const root = await mountEntry(id)
    // Invisible to assistive tech: decoration in the reading order is noise.
    expect(root.getAttribute('aria-hidden'), 'aria-hidden').toBe('true')
    // Invisible to the pointer: a full-shell overlay that swallows clicks is a dead
    // zone over every real control underneath it.
    expect(root.className, 'pointer-events-none').toContain('pointer-events-none')
    // Names which entry mounted, so the shell's decoration is legible generically.
    expect(root.getAttribute('data-shell-element'), 'data-shell-element').toBe(id)
    // No focusable descendant — the other half of "not reachable". `tabindex="-1"`
    // is fine (programmatic only); anything tabbable is a trap inside aria-hidden.
    expect(root.querySelectorAll('a, button, input, select, textarea, [tabindex]:not([tabindex="-1"])'))
      .toHaveLength(0)
  })
})

describe('lazy means lazy', () => {
  it('every registry value is a React lazy component, not a direct reference', () => {
    // A direct reference typechecks against `ComponentType` in most positions but
    // pulls the module into whichever chunk imports the registry — here, the entry.
    const LAZY = Symbol.for('react.lazy')
    for (const id of IDS) {
      const entry = SHELL_ELEMENTS[id] as unknown as { $$typeof?: symbol }
      expect(entry.$$typeof, `${id} must be lazy()`).toBe(LAZY)
    }
  })

  it('only the registry references ui/personality/, and only dynamically', () => {
    const files = walkSource(SRC)
    // Vacuity floor #1: the walk must actually be finding the app's source.
    expect(files.length, 'source walk found nothing').toBeGreaterThan(100)

    const statics: string[] = []
    let dynamicRefs = 0
    for (const file of files) {
      const rel = relative(SRC, file).replace(/\\/g, '/')
      const code = stripComments(readFileSync(file, 'utf8'))
      // Every static form: `import x from '…'`, `export {…} from '…'`, and the bare
      // side-effect `import '…'`. The bare form has no `from` and an earlier draft of
      // this rail missed it — it bundles the module just as thoroughly, so the `from`
      // clause is optional here on purpose. `import type` is erased by the compiler
      // and costs no bytes, but it is refused too: the registry is the one sanctioned
      // reference point, and a shell element exposes no types worth reaching for.
      // A dynamic `import('…')` still does not match: `(` is not a quote and there
      // is no `from`, which the M5 mutation (registry made non-lazy) confirms.
      if (/(?:^|\n)\s*(?:import|export)\s*(?:[^\n;]*?\bfrom\s*)?['"][^'"]*ui\/personality\//.test(code)) {
        statics.push(rel)
      }
      // `import('…/ui/personality/…')` — the form that actually splits a chunk.
      dynamicRefs += (code.match(/\bimport\s*\(\s*['"][^'"]*ui\/personality\//g) ?? []).length
    }

    // Vacuity floor #2: with zero dynamic references the assertion below would pass
    // on a registry that had stopped code-splitting entirely.
    expect(dynamicRefs, 'no dynamic import of ui/personality/ — nothing is code-split')
      .toBeGreaterThanOrEqual(IDS.length)
    expect(
      statics,
      `These modules statically import ui/personality/, which re-bundles a shell\n` +
        `element into their chunk and defeats lazy():\n${statics.join('\n')}`,
    ).toEqual([])
  })
})

/** Every non-test `.ts`/`.tsx` under web/src. Tests are excluded on purpose: a test
 *  importing a component directly is correct and carries no bundle cost. */
function walkSource(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...walkSource(p))
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

/** Drop block and line comments. Prose in this repo cites import paths (this file
 *  does), and a rail here has already been fooled once by matching a comment. */
function stripComments(code: string): string {
  return code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}
