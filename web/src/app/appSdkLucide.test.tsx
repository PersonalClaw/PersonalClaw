/** The SDK's module PROMISE and its module MAP cannot drift apart — the parity rail.
 *
 *  `resolvableAppSpecs()` is the promise: the bare specifiers a contributed
 *  bundle's imports are rewritten against. `installAppSdk()` is the delivery:
 *  the `window.__personalclaw_modules` map those rewrites resolve from. A spec
 *  listed in the promise with no entry in the map is the WORST failure shape —
 *  `appModuleShimUrl` returns null, the bare specifier survives into the blob
 *  module, and the app's `import()` throws: the page does not degrade, it does
 *  not mount at all.
 *
 *  That was not hypothetical. `lucide-react` sat in the ungated head with no map
 *  entry; growth's vite config documents measuring the break and shipping with
 *  lucide "external and unused" — steering apps to emoji glyphs the host's own
 *  design language forbids. This file pins the parity for EVERY spec (present and
 *  future), and the shape of the lucide vend specifically.
 */

import { describe, it, expect, beforeAll } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { installAppSdk, resolvableAppSpecs } from './appSdk'

type ModuleMap = Record<string, unknown>
const mapOf = (): ModuleMap =>
  (window as unknown as { __personalclaw_modules: ModuleMap }).__personalclaw_modules

beforeAll(() => {
  installAppSdk()
})

describe('promise/map parity — every resolvable spec resolves', () => {
  it('every spec in the FULL promise (all capabilities) has a module map entry', () => {
    const specs = resolvableAppSpecs({ uiCapabilities: ['shell-primitives', 'generative-widget'] })
    const map = mapOf()
    const broken = specs.filter((s) => !(s in map))
    expect(broken, `promised to apps but absent from the module map (mount-breaking): ${broken.join(', ')}`).toEqual([])
  })

  it('the ungated head resolves for an app declaring NOTHING', () => {
    const specs = resolvableAppSpecs(undefined)
    const map = mapOf()
    for (const s of specs) expect(map[s], `${s} must resolve for every app`).toBeTruthy()
  })
})

describe('the lucide-react vend', () => {
  it('serves the icons the shipped apps mark with emoji today', () => {
    const lucide = mapOf()['lucide-react'] as Record<string, unknown>
    // growth's KIND_META concepts + minutes' ROLE_ICON/digest concepts.
    for (const name of [
      'MessageSquare', 'FolderKanban', 'BookOpen', 'ListChecks', 'Link2', 'GitBranch',
      'Target', 'Sparkles', 'Check', 'Mic', 'Video', 'NotebookPen', 'FileText',
      'Presentation', 'Calendar', 'Users', 'SquareCheck',
    ]) {
      expect(lucide[name], `lucide vend is missing ${name}`).toBeTypeOf('object')
    }
  })

  it('is a curated vocabulary, not the whole namespace', () => {
    const lucide = mapOf()['lucide-react'] as Record<string, unknown>
    const count = Object.keys(lucide).length
    // The full namespace is ~6,000 exports; the vend is the host vocabulary.
    // Growth means editing APP_SDK_LUCIDE deliberately, not inheriting a
    // namespace import that defeats the shell bundle's tree-shaking.
    expect(count).toBeGreaterThanOrEqual(40)
    expect(count).toBeLessThan(120)
  })

  it('vends the HOST identities — the same component the host bundle renders', async () => {
    const lucide = mapOf()['lucide-react'] as Record<string, unknown>
    const host = await import('lucide-react')
    expect(lucide.MessageSquare).toBe(host.MessageSquare)
    expect(lucide.Loader2).toBe(host.Loader2)
  })
})

describe('the promise itself still lists lucide (the head this rail is about)', () => {
  it('resolvableAppSpecs keeps lucide-react in the ungated head', () => {
    expect(resolvableAppSpecs(undefined)).toContain('lucide-react')
  })

  it("the SOURCE keeps the map entry adjacent to the subpath entries (one install site)", () => {
    const src = readFileSync(join(process.cwd(), 'src/app/appSdk.tsx'), 'utf8')
    expect(src).toMatch(/'lucide-react': APP_SDK_LUCIDE,/)
  })
})
