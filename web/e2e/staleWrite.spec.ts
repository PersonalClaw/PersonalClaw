import { test, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import { gotoRoute, openHeaderOverflowIfNeeded } from './helpers'

// ── Two tabs, one list: a save from the stale tab never overwrites the other tab's change ──────
//
// Settings surfaces that save a WHOLE list build it from the copy the page read. With two tabs open
// on the same list, tab A's save landed, and tab B — still painting the list from before — then
// saved its own copy over it: tab A's change was gone, and neither tab said a word. The same
// happened when something other than the page wrote the list in between.
//
// The contract under test (`personalclaw/stale_write.py`, `web/src/lib/staleWrite.ts`): a stale
// whole-list save is refused with `409 stale_write`, the stale tab keeps its change and offers to
// reload and re-apply it; and a list of names is edited one name at a time, so two tabs' edits both
// land without any refusal at all. Two real pages in one browser context are the two tabs.

const RULES = 'settings/tool-output'

/** A same-origin call from inside a tab — the SPA's own cookie and CSRF posture, not a side door. */
async function inTab<T>(page: Page, path: string, init?: { method: string; body?: unknown; headers?: Record<string, string> }): Promise<T> {
  return page.evaluate(async ({ path, init }) => {
    const r = await fetch(path, {
      method: init?.method ?? 'GET',
      headers: { 'Content-Type': 'application/json', 'X-Session-Key': 'dashboard:ui', ...(init?.headers ?? {}) },
      body: init?.body === undefined ? undefined : JSON.stringify(init.body),
    })
    if (!r.ok) throw new Error(`${init?.method ?? 'GET'} ${path} → ${r.status} ${await r.text()}`)
    return r.json()
  }, { path, init }) as Promise<T>
}

type Cfg = { tools: { projection_rules: { name: string }[] }; security: { mcp_elicitation_servers: string[] }; revisions: Record<string, string> }
const config = (page: Page) => inTab<Cfg>(page, '/api/config/personalclaw')
const ruleNames = async (page: Page) => (await config(page)).tools.projection_rules.map((r) => r.name)

async function addRule(page: Page, name: string, regex: string) {
  await page.getByLabel('New rule name').fill(name)
  await page.getByLabel('Match regex for the new rule').fill(regex)
  await page.getByRole('button', { name: 'Add rule' }).click()
}

/** What the two MCP servers run: a path that resolves to nothing, so a server exists, is allowed,
 *  and never starts — its row (and its grant switch) still renders. */
const SERVER_COMMAND = '/nonexistent/e2e-mcp-server'

/** Add an MCP server from the Tools page as a person does: the Add tool server form, then the
 *  gateway's question about what the server will run, answered Allow. A new server runs only once
 *  its owner says yes to what it runs, so the add asks before anything is saved. */
async function addServer(page: Page, name: string) {
  expect(await openHeaderOverflowIfNeeded(page, 'Add tool server'), 'the Tools page offers Add tool server').toBe(true)
  await page.getByRole('button', { name: 'Add tool server', exact: true }).click()
  const form = page.getByRole('dialog', { name: 'Add tool server' })
  await form.getByLabel('Name', { exact: true }).fill(name)
  await form.getByLabel('Command', { exact: true }).fill(SERVER_COMMAND)
  await form.getByRole('button', { name: 'Add server' }).click()
  const ask = page.getByRole('alertdialog', { name: 'Allow this MCP server to run?' })
  await expect(ask, 'the question names the server and what it runs').toContainText(
    `Saving “${name}” lets PersonalClaw run ${SERVER_COMMAND} as you`,
  )
  await ask.getByRole('button', { name: 'Allow', exact: true }).click()
  await expect(form, 'the form closes once the server is saved').toHaveCount(0)
}

/** Remove an MCP server if a crashed earlier run left it behind; a missing one is fine. */
async function removeServer(page: Page, name: string) {
  const status = await page.evaluate(async (n) => (await fetch(`/api/mcp/servers/${n}`, {
    method: 'DELETE', headers: { 'X-Session-Key': 'dashboard:ui' },
  })).status, name)
  expect([200, 204, 404], `DELETE /api/mcp/servers/${name} → ${status}`).toContain(status)
}

/** Put the rules back to none, over their revision, so no other spec in this run sees ours. */
async function clearRules(page: Page) {
  const c = await config(page)
  await inTab(page, '/api/config/personalclaw', {
    method: 'PATCH',
    body: { path: 'tools.projection_rules', value: [] },
    headers: { 'If-Match': `"${c.revisions['tools.projection_rules']}"` },
  })
}

test.describe('a save from a stale tab', () => {
  test.describe.configure({ mode: 'serial' })

  test('tab B is refused with the reload offer, and tab A’s rule survives', async ({ context }) => {
    const a = await context.newPage()
    const b = await context.newPage()
    await gotoRoute(a, RULES)
    await clearRules(a)
    await gotoRoute(a, RULES)
    await gotoRoute(b, RULES) // both tabs now paint the same, empty list

    await addRule(a, 'from-a', '^\\[A\\]')
    await expect(a.getByLabel('Match regex for from-a')).toBeVisible()

    await addRule(b, 'from-b', '^\\[B\\]')
    const notice = b.locator('[data-stale-write="true"]')
    await expect(notice).toContainText('Your projection rules changed elsewhere')
    await expect(notice).toContainText('It’s kept until you reapply or discard it')
    // …and what was typed is still in the form, not lost to the refusal.
    await expect(b.getByLabel('New rule name')).toHaveValue('from-b')
    // The refusal wrote nothing: tab A's rule is the only one stored.
    expect(await ruleNames(a)).toEqual(['from-a'])

    // Review first — the difference names tab A's rule as the change made elsewhere.
    await notice.getByRole('button', { name: 'Review the difference' }).click()
    const dialog = b.getByRole('dialog', { name: 'Review the difference' })
    await expect(dialog.getByLabel('What changed elsewhere')).toContainText('"name": "from-a"')
    await expect(dialog.getByLabel('Your change, re-applied')).toContainText('"name": "from-b"')
    await dialog.getByRole('button', { name: 'Reapply my change' }).click()

    await expect(b.locator('[data-stale-write="true"]')).toHaveCount(0)
    await expect(b.getByLabel('Match regex for from-a')).toBeVisible()
    await expect(b.getByLabel('Match regex for from-b')).toBeVisible()
    expect(await ruleNames(a)).toEqual(['from-a', 'from-b'])
    await clearRules(a)
  })

  test('a write from outside the page between its read and its save is not undone', async ({ context }) => {
    const page = await context.newPage()
    await gotoRoute(page, RULES)
    await clearRules(page)
    await gotoRoute(page, RULES) // the page paints no rules

    // Something other than this page writes the same list: the CLI, a separate process over the
    // gateway's own home — the home and binary the harness gateway runs on (playwright.config.ts).
    const home = join((process.env.TMPDIR || '/tmp').replace(/\/$/, ''), 'personalclaw-e2e-home')
    execFileSync('../.venv/bin/personalclaw', [
      'config', 'set', 'tools.projection_rules',
      JSON.stringify([{ name: 'from-cli', match_regex: '^\\[CLI\\]', strategy: 'log' }]),
    ], { env: { ...process.env, PERSONALCLAW_HOME: home, PYTHONPATH: join(process.cwd(), '..', 'src') }, stdio: 'pipe' })

    await addRule(page, 'from-page', '^\\[PAGE\\]')
    const notice = page.locator('[data-stale-write="true"]')
    await expect(notice).toContainText('Your projection rules changed elsewhere')
    expect(await ruleNames(page)).toEqual(['from-cli'])

    await notice.getByRole('button', { name: 'Reload and reapply' }).click()
    await expect(notice).toHaveCount(0)
    expect(await ruleNames(page)).toEqual(['from-cli', 'from-page'])
    await clearRules(page)
  })

  test('two tabs granting different MCP servers keep both grants', async ({ context }) => {
    const a = await context.newPage()
    const b = await context.newPage()
    // Removed first, from another route, if a crashed earlier run left them: adding a name that is
    // already configured is refused, and the Tools page should first paint without them.
    await gotoRoute(a, 'settings')
    for (const name of ['e2e-alpha', 'e2e-beta']) await removeServer(a, name)
    // Two servers that exist but never connect: their rows (and grant switches) still render.
    await gotoRoute(a, 'tools')
    for (const name of ['e2e-alpha', 'e2e-beta']) await addServer(a, name)
    await gotoRoute(b, 'tools') // both tabs paint "nobody may ask"

    for (const [page, server] of [[a, 'e2e-alpha'], [b, 'e2e-beta']] as const) {
      await page.getByRole('button', { name: `Let ${server} ask you questions` }).click()
      await page.getByRole('button', { name: 'Allow questions' }).click()
      await expect(page.getByRole('button', { name: new RegExp(`${server}.*questions`) })).toHaveAttribute('aria-pressed', 'true')
    }
    // Tab B's grant did not revoke tab A's, and no tab was refused: one server's grant in, not a list.
    const granted = (await config(a)).security.mcp_elicitation_servers
    expect(granted).toEqual(expect.arrayContaining(['e2e-alpha', 'e2e-beta']))
    await expect(b.locator('[data-stale-write="true"]')).toHaveCount(0)

    for (const name of ['e2e-alpha', 'e2e-beta']) {
      await inTab(a, '/api/config/personalclaw', { method: 'PATCH', body: { path: 'security.mcp_elicitation_servers', remove: name } })
      await removeServer(a, name)
    }
  })
})
