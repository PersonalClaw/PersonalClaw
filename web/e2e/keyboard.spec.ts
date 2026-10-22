/** Every key in the keymap, pressed in the built app, and the approval key held to its rules.
 *  `src/app/everyBindingHasAKeyboardCase.test.ts` holds every keymap chord to a press here (or in the
 *  spec a row names, or an exemption that says why).
 *
 *  The approval cases answer every request in the page, so the test counts exactly what each key
 *  sent: one approve per decision, nothing on Enter alone on the card or Escape, nothing before a
 *  second confirmation, however Allow is pressed again. ⌘⌥A and ⌘1 read lists the page answers too,
 *  so where each key lands is known. */
import { test, expect, type Page, type Request } from '@playwright/test'
import { gotoRoute, driveScriptedTurns } from './helpers'

const MOD = 'ControlOrMeta'

function approvalRow(over: Record<string, unknown> = {}) {
  return {
    id: 'e2e-ap-1', request_id: 'e2e-ap-1', source: 'trigger', tool: 'task_create',
    tool_input: '{"title": "Water the plants"}', tool_purpose: '', session: 'e2e-keys-chat', ts: 100,
    session_title: '', agent: '', risk: 'caution', grant_agent: '', source_label: 'trigger “Plants”',
    confirms: [], ...over,
  }
}

/** *row* as the waiting list carries it (`GET /api/waiting`): the registry row as its brief. */
function waitingOf(row: Record<string, unknown>) {
  const id = String(row.id)
  return {
    id: `approval:${id}`, kind: 'approval', work: '', session: String(row.session ?? ''), brief: row,
    consequence: row.risk === 'destructive' ? 'destructive' : 'changes', risk: String(row.risk ?? ''),
    asked_at: new Date(Number(row.ts ?? 0) * 1000).toISOString(), age_secs: 0, expires_at: null,
    current: true, interrupt: 'deliver', route: '',
    answer: { method: 'POST', path: `/api/approvals/${id}/{answer}`, takes: ['approve', 'reject'], body: {} },
  }
}

/** Answer `GET /api/approvals` with *rows*, and `GET /api/waiting` with them as its approvals (what
 *  Mission Control reads), and record every decision the page sends. */
async function stubApprovals(page: Page, rows: Record<string, unknown>[]): Promise<Request[]> {
  const decisions: Request[] = []
  await page.route('**/api/approvals', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rows) })
      : route.fallback())
  await page.route('**/api/waiting', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ waiting: rows.map(waitingOf), unreadable: [] }),
    }))
  await page.route('**/api/approvals/*/*', (route) => {
    decisions.push(route.request())
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok": true}' })
  })
  return decisions
}

const card = (page: Page, tool: string) => page.getByRole('group', { name: `Permission needed to run ${tool}` })

test.describe('the keymap, in the built app', () => {
  test('⌘K and ⌘⇧P open the palette, and it lists every key', async ({ page }) => {
    await gotoRoute(page, 'dashboard')
    await page.keyboard.press(`${MOD}+K`)
    const list = page.getByRole('listbox', { name: 'Commands and results' })
    await expect(list).toBeVisible()
    await expect(list.getByRole('option', { name: /Approve the focused request/ })).toHaveAttribute('aria-disabled', 'true')
    await page.keyboard.press('Escape')
    await expect(list).toBeHidden()
    await page.keyboard.press(`${MOD}+Shift+P`)
    await expect(list).toBeVisible()
  })

  test('⌘B shows and hides the sidebar', async ({ page }) => {
    await gotoRoute(page, 'dashboard')
    const before = await page.getByRole('button', { name: /(Collapse|Expand) sidebar/ }).getAttribute('aria-label')
    await page.keyboard.press(`${MOD}+B`)
    await expect(page.getByRole('button', { name: /(Collapse|Expand) sidebar/ })).not.toHaveAttribute('aria-label', before!)
    await page.keyboard.press(`${MOD}+B`)
    await expect(page.getByRole('button', { name: /(Collapse|Expand) sidebar/ })).toHaveAttribute('aria-label', before!)
  })

  test('⌘J opens the terminal drawer', async ({ page }) => {
    await gotoRoute(page, 'dashboard')
    await page.keyboard.press(`${MOD}+J`)
    // The corner's toggle, by its exact name: the open drawer has a Hide button of its own.
    await expect(page.getByRole('button', { name: 'Hide terminal', exact: true })).toBeVisible()
    await page.keyboard.press(`${MOD}+J`)
    await expect(page.getByRole('button', { name: 'Open terminal', exact: true })).toBeVisible()
  })

  test('⌘` opens and closes the terminal drawer too', async ({ page }) => {
    await gotoRoute(page, 'dashboard')
    await page.keyboard.press(`${MOD}+Backquote`)
    await expect(page.getByRole('button', { name: 'Hide terminal', exact: true })).toBeVisible()
    await page.keyboard.press(`${MOD}+Backquote`)
    await expect(page.getByRole('button', { name: 'Open terminal', exact: true })).toBeVisible()
  })

  test("⌘⇧E shows and hides the chat's working folder", async ({ page }) => {
    await gotoRoute(page, 'chat/new')
    await driveScriptedTurns(page, 'Say your scripted line for the working folder key.')
    const folder = page.getByRole('region', { name: 'Working folder' })
    await page.keyboard.press(`${MOD}+Shift+E`)
    await expect(folder).toBeVisible()
    await page.keyboard.press(`${MOD}+Shift+E`)
    await expect(folder).toBeHidden()
  })

  test('⌘N starts a new chat', async ({ page }) => {
    await gotoRoute(page, 'dashboard')
    await page.keyboard.press(`${MOD}+N`)
    await expect(page).toHaveURL(/#\/chat\/new$/)
  })

  test('⌘1 opens the first thread in the sidebar', async ({ page }) => {
    // Two chats with no project and no loop: the sidebar's only threads are the two, under No
    // project, the newest first.
    await page.route('**/api/loops', (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ loops: [] }) })
        : route.fallback())
    await page.route('**/api/chat/sessions', (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([
          { key: 'e2e-older', title: 'Older', messages: 2, run_state: 'idle', last_activity_ts: '2026-01-01T00:00:00Z' },
          { key: 'e2e-newest', title: 'Newest', messages: 2, run_state: 'idle', last_activity_ts: '2026-02-01T00:00:00Z' },
        ]) })
        : route.fallback())
    await gotoRoute(page, 'dashboard')
    await page.keyboard.press(`${MOD}+1`)
    await expect(page).toHaveURL(/#\/chat\/e2e-newest$/)
  })

  test('⌘⌥A goes to the oldest chat that needs you', async ({ page }) => {
    await page.route('**/api/inbox/open', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }))
    await stubApprovals(page, [
      approvalRow({ id: 'e2e-ap-new', request_id: 'e2e-ap-new', session: 'e2e-later', ts: 200 }),
      approvalRow({ id: 'e2e-ap-old', request_id: 'e2e-ap-old', session: 'e2e-first', ts: 100 }),
    ])
    await gotoRoute(page, 'dashboard')
    await page.keyboard.press(`${MOD}+Alt+A`)
    await expect(page).toHaveURL(/#\/chat\/e2e-first$/)
  })

  test('↑ in an empty composer restores the last prompt', async ({ page }) => {
    await gotoRoute(page, 'chat/new')
    await driveScriptedTurns(page, 'Say your scripted line for the keyboard test.')
    const composer = page.getByRole('textbox', { name: 'Message input' })
    await composer.focus()
    await page.keyboard.press('ArrowUp')
    await expect(composer).toContainText('Say your scripted line for the keyboard test.')
  })
})

test.describe('the approval key', () => {
  test('⌘Enter on the focused request sends one approve; Enter and Escape send nothing', async ({ page }) => {
    const decisions = await stubApprovals(page, [approvalRow()])
    await gotoRoute(page, 'mission-control')
    const ask = card(page, 'task_create')
    await expect(ask).toBeVisible()
    // Arrival moved no focus onto it.
    await expect(ask.getByRole('button', { name: /^Allow/ })).not.toBeFocused()
    // Focus elsewhere on the page: the key does nothing.
    await page.keyboard.press(`${MOD}+Enter`)
    expect(decisions).toHaveLength(0)
    // Focus on the card itself: Enter alone and Escape decide nothing there.
    await ask.focus()
    await page.keyboard.press('Enter')
    await page.keyboard.press('Escape')
    await ask.focus()
    await page.keyboard.press('Enter')
    // Focus on the card's Deny: ⌘Enter presses the approving choice, never the focused Deny.
    await ask.getByRole('button', { name: /^Deny/ }).focus()
    await page.waitForTimeout(300)
    expect(decisions).toHaveLength(0)
    // ⌘Enter held down: the first press answers, the repeats send nothing more.
    await page.keyboard.down(MOD)
    await page.keyboard.press('Enter')
    await page.keyboard.press('Enter')
    await page.keyboard.up(MOD)
    await expect.poll(() => decisions.length).toBe(1)
    await page.waitForTimeout(300)
    expect(decisions).toHaveLength(1)
    expect(decisions[0].url()).toMatch(/\/api\/approvals\/e2e-ap-1\/approve$/)
  })

  test('a delete waits for its second confirmation, and sends nothing before it', async ({ page }) => {
    const decisions = await stubApprovals(page, [approvalRow({
      tool: 'bash', tool_input: '{"command": "rm -rf build"}', risk: 'destructive', confirms: ['deletes'],
    })])
    await gotoRoute(page, 'mission-control')
    const ask = card(page, 'bash')
    await ask.getByRole('button', { name: /^Allow/ }).focus()
    await page.keyboard.press(`${MOD}+Enter`)
    await expect(ask.getByText(/It deletes files\./)).toBeVisible()
    await page.waitForTimeout(300)
    expect(decisions).toHaveLength(0)
    await ask.getByRole('button', { name: /Yes, run it/ }).focus()
    await page.keyboard.press(`${MOD}+Enter`)
    await expect.poll(() => decisions.length).toBe(1)
  })

  for (const [how, press] of [
    ['Enter pressed twice on a focused Allow', async (page: Page, allow: ReturnType<Page['getByRole']>) => {
      await allow.focus()
      await page.keyboard.press('Enter')
      await page.keyboard.press('Enter')
    }],
    ['Enter held down on a focused Allow', async (page: Page, allow: ReturnType<Page['getByRole']>) => {
      await allow.focus()
      // A second and third keydown with no keyup between them: the browser's auto-repeat.
      await page.keyboard.down('Enter')
      await page.keyboard.down('Enter')
      await page.keyboard.down('Enter')
      await page.keyboard.up('Enter')
    }],
    ['a double-click on Allow', async (_page: Page, allow: ReturnType<Page['getByRole']>) => {
      await allow.dblclick()
    }],
  ] as const) {
    test(`a delete sends nothing before its confirm: ${how}`, async ({ page }) => {
      const decisions = await stubApprovals(page, [approvalRow({
        tool: 'bash', tool_input: '{"command": "rm -rf build"}', risk: 'destructive', confirms: ['deletes'],
      })])
      await gotoRoute(page, 'mission-control')
      const ask = card(page, 'bash')
      await press(page, ask.getByRole('button', { name: /^Allow/ }))
      await expect(ask.getByText(/It deletes files\./)).toBeVisible()
      await page.waitForTimeout(300)
      expect(decisions).toHaveLength(0)
      await ask.getByRole('button', { name: /Yes, run it/ }).click()
      await expect.poll(() => decisions.length).toBe(1)
    })
  }

  test('nothing is answered from the composer of the chat that shows the card', async ({ page }) => {
    // The chat's own pending permission row, so its card is on the page beside the composer.
    const chatDecisions: Request[] = []
    await page.route('**/api/chat/sessions/e2e-keys-chat', (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
          key: 'e2e-keys-chat', title: 'Keys', queue: [], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
          run_state: {
            work: 'chat:e2e-keys-chat', session: 'e2e-keys-chat', kind: 'chat', phase: 'idle', line: '', since: null, turn: null,
            last_step_at: null, stopping: false, steerable: false, last_outcome: null, queued_messages: 0, seq: 0,
          },
          messages: [
            { role: 'user', content: 'Add a task to water the plants.', ts: '2026-10-01T09:00:00+00:00' },
            { role: 'permission', content: 'task_create', meta: {
              approval_id: 'e2e-chat-ap', tool_input: '{"title": "Water the plants"}', risk: 'caution', confirms: [],
            } },
          ],
        }) })
        : route.fallback())
    await page.route('**/api/chat/sessions/e2e-keys-chat/approve', (route) => {
      chatDecisions.push(route.request())
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok": true}' })
    })
    const decisions = await stubApprovals(page, [])
    await gotoRoute(page, 'chat/e2e-keys-chat')
    await expect(card(page, 'task_create')).toBeVisible()
    await page.getByRole('textbox', { name: 'Message input' }).focus()
    await page.keyboard.press(`${MOD}+Enter`)
    await page.waitForTimeout(300)
    expect(chatDecisions).toHaveLength(0)
    expect(decisions).toHaveLength(0)
    // The control: the same key on the card answers it, once.
    await card(page, 'task_create').focus()
    await page.keyboard.press(`${MOD}+Enter`)
    await expect.poll(() => chatDecisions.length).toBe(1)
  })
})
