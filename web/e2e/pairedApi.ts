import type { Page } from '@playwright/test'

/** The e2e specs' own `/api` reads and writes, with the sign-in's pair the harness stored.
 *
 *  A gateway that pairs its sign-ins refuses the session cookie without the pair the page keeps
 *  in its origin storage (`web/src/lib/pair.ts`). `page.request` shares the page's cookie jar but
 *  not its storage, so a spec's direct call reads the stored pair and sends it. On a gateway that
 *  doesn't pair, nothing is stored and the call is exactly what it was. */
export async function storedPair(page: Pick<Page, 'evaluate'>): Promise<string> {
  try {
    return await page.evaluate(() => {
      try { return window.localStorage.getItem('pc_pair') || '' } catch { return '' }
    })
  } catch {
    return ''
  }
}

async function headers(page: Pick<Page, 'evaluate'>, extra: Record<string, string> = {}): Promise<Record<string, string>> {
  const pair = await storedPair(page)
  return pair ? { ...extra, 'X-PersonalClaw-Pair': pair } : extra
}

export async function apiGet(page: Pick<Page, 'request' | 'evaluate'>, path: string) {
  return page.request.get(path, { headers: await headers(page) })
}

export async function apiDelete(page: Pick<Page, 'request' | 'evaluate'>, path: string, extra: Record<string, string> = {}) {
  return page.request.delete(path, { headers: await headers(page, extra) })
}
