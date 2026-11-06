import type { Page } from '@playwright/test'

/** The e2e specs' own `/api` reads and writes, with the sign-in's pair the harness stored.
 *
 *  A gateway that pairs its sign-ins refuses the session cookie without the pair the page keeps
 *  in its origin storage (`web/src/lib/pair.ts`). `page.request` shares the page's cookie jar but
 *  not its storage, so a spec's direct call reads the stored pair and sends it. A call made before
 *  the page has opened the gateway (on `about:blank`, as a surface's `arrange` is) finds no pair in
 *  the page, so it reads the one the harness's storage state seeded into the context. On a gateway
 *  that doesn't pair, nothing is stored and the call is exactly what it was. */
export async function storedPair(page: Pick<Page, 'evaluate' | 'context'>): Promise<string> {
  let pair = ''
  try {
    pair = await page.evaluate(() => {
      try { return window.localStorage.getItem('pc_pair') || '' } catch { return '' }
    })
  } catch {
    pair = ''
  }
  if (pair) return pair
  try {
    const { origins } = await page.context().storageState()
    for (const origin of origins) {
      const kept = origin.localStorage.find((item) => item.name === 'pc_pair')?.value
      if (kept) return kept
    }
  } catch {
    // No context storage to read: the call goes without a pair, as it did before pairing.
  }
  return ''
}

async function headers(page: Pick<Page, 'evaluate' | 'context'>, extra: Record<string, string> = {}): Promise<Record<string, string>> {
  const pair = await storedPair(page)
  return pair ? { ...extra, 'X-PersonalClaw-Pair': pair } : extra
}

export async function apiGet(page: Pick<Page, 'request' | 'evaluate' | 'context'>, path: string) {
  return page.request.get(path, { headers: await headers(page) })
}

export async function apiDelete(page: Pick<Page, 'request' | 'evaluate' | 'context'>, path: string, extra: Record<string, string> = {}) {
  return page.request.delete(path, { headers: await headers(page, extra) })
}
