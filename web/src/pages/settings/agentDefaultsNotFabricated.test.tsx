/** The settings hub fabricated the two most dangerous values in the product.
 *
 * `AgentDefaultsPanel` and the hub's `Agent defaults` tile share the cache key
 * `settings:agent-defaults`. The panel carries a 🔴 comment recording that it REMOVED
 * `.catch(() => ({}))` from its config read, because *"a settings panel must not present FABRICATED
 * values as saved state"*. **The hub kept it.** So the panel's fix was inert on the journey people
 * actually take:
 *
 *   direct to the panel   cache=null   → "Couldn't load your settings" + Retry   ✅ its fix works
 *   hub → the panel       cache="{…}"  → the full form, no error anywhere        🔴 its fix was inert
 *
 * `persist: true` mirrors the value into `sessionStorage`, so the substituted `{}` survived a full
 * reload, and because the fetcher RESOLVED, the panel's `if (!data && loadErr)` guard never fired —
 * `data` was defined, just empty.
 *
 * 🔴 WHAT THE EMPTY OBJECT CLAIMED, and why this key is worse than the legibility one that was fixed
 * first:
 *   · **Approval mode read "Ask each time"** from `?? 'interactive'`, while the stored default is
 *     `auto` (`AgentConfig.approval_mode`). The UI showed the SAFE mode and the runtime ran the
 *     permissive one — so someone could check their setting, see exactly what they wanted, change
 *     nothing, and have every tool call still execute unprompted.
 *   · **YOLO read OFF** from `!!c.yolo` — a switch whose entire purpose is auto-approve-everything.
 *
 * Both of those controls PATCH on change, so this is not only a wrong readout: a user "correcting" a
 * switch that was never loaded writes the opposite of what they believe is stored.
 *
 * 🪤 WHY THE SOURCE RAILS CARRY MOST OF THIS. `settingsWidgets.tsx` exports `SETTINGS_WIDGETS`, an
 * array of tiles holding hooks and importing the real panels — `appsTileCountsWhatItSays.test.ts`
 * records that importing it "drags the panel tree into a unit test", which is why every hub-tile rail
 * here is source-level. So the hub half is asserted in `configReadNotFabricated.test.ts` (the
 * fabricated value) and `tileLoadFailure.test.ts` (the shimmer/failure line). What THIS file adds is
 * the half that is genuinely behavioural and was never pinned anywhere: that a resolved-but-empty
 * cache entry really does defeat the panel's guard. That is the mechanism the whole fix turns on, and
 * prose is not evidence for it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/** `persist: true` mirrors under `cache:<key>` — `store.ts`'s `_SS_PREFIX`. */
const SS_KEY = 'cache:settings:agent-defaults'
const read = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')
const strip = (s: string) => s
  .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

const cfgMock = vi.fn(async (): Promise<unknown> => ({ agent: { approval_mode: 'auto', yolo: true } }))

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  cfgMock.mockReset()
  cfgMock.mockResolvedValue({ agent: { approval_mode: 'auto', yolo: true } })
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      ...(await orig<{ api: Record<string, unknown> }>()).api,
      personalclawConfig: cfgMock,
      agents: async () => ({ agents: [], default_agent: 'scout' }),
      agentProviders: async () => [],
      patchConfig: async () => ({ ok: true }),
      skills: async () => [],
      tools: async () => [],
      hooks: async () => [],
    },
  }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

async function mountPanel() {
  const { AgentDefaultsPanel } = await import('./AgentDefaultsPanel')
  return render(<AgentDefaultsPanel />)
}

describe('the panel refuses to present values it never loaded', () => {
  it('a failed config read replaces the form with the failure, not with defaults', async () => {
    cfgMock.mockRejectedValue(new Error('boom'))
    await mountPanel()
    // Its own guard: `if (!data && loadErr) return <LoadError …>`.
    await waitFor(() => expect(screen.getByText(/Couldn’t load|Couldn't load/)).toBeInTheDocument())
    // 🔑 And no control is offered. A form rendered here would be editable state the panel never read.
    expect(screen.queryByRole('switch'), 'no switch may be offered on an unloaded config').toBeNull()
  })

  it('🔴 THE MECHANISM: a resolved-but-EMPTY cache entry defeats that guard entirely', async () => {
    // This is what the hub's `.catch(() => ({}))` wrote, and why removing it was the fix rather than
    // hardening the panel. The substitute is not an error the panel can detect — it is a SUCCESS whose
    // payload happens to be empty, so `data` is defined and `if (!data && loadErr)` is skipped.
    //
    // Seeded directly into the persisted mirror, which is exactly how it reached the panel across a
    // reload. The config read still fails, to isolate the cache as the only reason the form appears.
    sessionStorage.setItem(SS_KEY, JSON.stringify({ v: { cfg: {}, defaultAgent: '' }, at: Date.now() }))
    cfgMock.mockRejectedValue(new Error('boom'))
    await mountPanel()
    // The form paints. No failure line. This is the pre-fix hub→panel journey, reproduced.
    await waitFor(() => expect(screen.queryAllByRole('switch').length).toBeGreaterThan(0))
    expect(screen.queryByText(/Couldn’t load|Couldn't load/),
      'the panel cannot tell a substituted success from a real one').toBeNull()
  })
})

describe('the hub can no longer write that entry', () => {
  const widgets = strip(read('pages/settings/settingsWidgets.tsx'))

  it('its fetcher rejects on a failed config read, so nothing is cached', () => {
    // The complement of the test above: given that a resolved substitute is undetectable, the only
    // durable fix is for the shared fetcher never to produce one. Bounded to the hook.
    const at = widgets.indexOf("'settings:agent-defaults'")
    expect(at, 'the hook must still exist').toBeGreaterThan(-1)
    const hook = widgets.slice(at, widgets.indexOf('persist: true', at) + 20)
    expect(hook, 'no substitute on the governing read')
      .not.toMatch(/personalclawConfig\(\)[^\n]*\.catch\(/)
  })

  it('🪤 the DECORATING read keeps its fallback, matching the panel byte for byte', () => {
    // The over-correction this guards: the default agent's NAME renders as '—' and is not a control's
    // claimed state, so blanking the tile for it would be a regression dressed as a fix. The rule the
    // file states is "match the PANEL exactly, or take a key of your own" — and the panel keeps this one.
    const at = widgets.indexOf("'settings:agent-defaults'")
    const hook = widgets.slice(at, widgets.indexOf('persist: true', at) + 20)
    expect(hook).toMatch(/api\.agents\(\)\.then\(\(a\) => a\.default_agent\)\.catch\(\(\) => ''\)/)
    expect(read('pages/settings/AgentDefaultsPanel.tsx'), 'and the panel spells the same fallback')
      .toMatch(/default_agent\)\.catch\(\(\) => ''\)/)
  })
})
