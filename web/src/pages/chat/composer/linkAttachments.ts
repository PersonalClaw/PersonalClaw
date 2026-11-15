import { useEffect, useRef } from 'react'
import { api } from '../../../lib/api'
import { parseItemRef, type ItemRef } from '../../../lib/itemRef'
import { investigate } from '../../../lib/investigate'
import { failureSentence } from '../../../app/reportingWrite'
import { MAX_LINK_ATTACH } from '../../../app/linkState'
import type { ComposerNoticeState } from '../../../ui/composer/ComposerNotice'

/** ── A link's `attach`, as chips in the composer ─────────────────────────────────────────────────
 *
 *  `#/chat/new?attach=knowledge:k1,artifact:lisbon` (and `#/chat/<key>?attach=…`) names library
 *  items for the chat to hold (`app/linkState.ts`). Each one the library can read becomes a chip,
 *  the same chip an @-mention or the pickers make (`mentionedKnowledge`, `mentionedArtifacts`): it
 *  joins whatever the draft already holds, the draft's text stays, and nothing is sent. The person
 *  sends it with their message, or takes it out.
 *
 *  - a knowledge item and an artifact attach as themselves, by the name the library shows;
 *  - a report attaches as its write-up, the knowledge item its runs rewrite (`finding_item_id`);
 *  - what an intent found (`findings:`) is a log, not an item, so it is not a chip: the line above
 *    the composer says so and offers Ask about this, a new chat that reads it;
 *  - a ref that names nothing the library holds, or one that can't be read now, is said on that
 *    line, counted, rather than dropped without a word.
 *
 *  The address gives up `attach` as soon as the refs are read (`onRead`, which replaces the address
 *  without it), as the shell rewrites a link's one-shot words: the chips are the draft's now, so a
 *  chip taken out stays out on a reload, on Back to the chat, or after a send. A link to the chat
 *  already open names refs again, and they join what the draft holds; a chip already there is not
 *  added twice. */

export interface ResolvedAttachments {
  knowledge: { id: string; name: string }[]
  artifacts: { slug: string; name: string }[]
  /** The intents whose findings the link named: asked about, never attached. */
  findings: ItemRef[]
  /** How many refs named nothing that could be attached: no such thing, unreadable now, a report
   *  with no write-up yet, a ref of no known shape, or past the most a link attaches. */
  missing: number
}

/** What the library's reads give for `refs`: the chips to add, the findings to offer asking about,
 *  and how many attached nothing. Reads run side by side; a report's write-up is looked up once. */
export async function resolveAttachments(refs: readonly string[]): Promise<ResolvedAttachments> {
  const out: ResolvedAttachments = { knowledge: [], artifacts: [], findings: [], missing: 0 }
  const named = refs.slice(0, MAX_LINK_ATTACH)
  out.missing += refs.length - named.length
  let reports: Promise<Map<string, { name: string; writeUp: string }>> | null = null
  const writeUps = () => (reports ??= api.researchReports().then(({ reports: all }) => new Map(
    all.map((r) => [r.id, { name: r.name, writeUp: r.finding_item_id || '' }]),
  )))
  const reads = named.map(async (raw): Promise<void> => {
    const ref = parseItemRef(raw)
    if (!ref) { out.missing += 1; return }
    switch (ref.kind) {
      case 'knowledge': {
        const item = await api.knowledgeItem(ref.id)
        out.knowledge.push({ id: item.id || ref.id, name: item.title || 'Untitled item' })
        return
      }
      case 'artifact': {
        const art = await api.artifact(ref.id)
        out.artifacts.push({ slug: art.slug || ref.id, name: art.name || ref.id })
        return
      }
      case 'report': {
        const report = (await writeUps()).get(ref.id)
        if (!report?.writeUp) { out.missing += 1; return }
        const item = await api.knowledgeItem(report.writeUp)
        out.knowledge.push({ id: item.id || report.writeUp, name: report.name || item.title || 'Untitled report' })
        return
      }
      case 'findings':
        out.findings.push(ref)
    }
  })
  // A read that fails attaches nothing and is counted; the others still attach.
  for (const r of await Promise.allSettled(reads)) if (r.status === 'rejected') out.missing += 1
  // In the link's order, each once.
  const order = new Map(named.map((raw, i) => [raw, i]))
  const at = (kind: string, id: string) => order.get(`${kind}:${id}`) ?? Number.MAX_SAFE_INTEGER
  out.knowledge = dedupe(out.knowledge, (k) => k.id).sort((a, b) => at('knowledge', a.id) - at('knowledge', b.id))
  out.artifacts = dedupe(out.artifacts, (a) => a.slug).sort((a, b) => at('artifact', a.slug) - at('artifact', b.slug))
  return out
}

function dedupe<T>(xs: T[], key: (x: T) => string): T[] {
  const seen = new Set<string>()
  return xs.filter((x) => (seen.has(key(x)) ? false : (seen.add(key(x)), true)))
}

/** What the line above the composer says about a link's attach: the refs that attached nothing,
 *  and the findings it can only ask about. null when every ref became a chip. */
export function attachSentence(r: Pick<ResolvedAttachments, 'findings' | 'missing'>): string | null {
  const parts: string[] = []
  if (r.missing === 1) parts.push("Couldn't attach one of the items this link names. It isn't in your library, or couldn't be read.")
  else if (r.missing > 1) parts.push(`Couldn't attach ${r.missing} of the items this link names. They aren't in your library, or couldn't be read.`)
  if (r.findings.length) parts.push("What an intent found can't be attached to a chat. You can ask about it in a new chat instead.")
  return parts.length ? parts.join(' ') : null
}

/** The notice's source, so the next link's notice takes down this one's and no one else's. */
export const ATTACH_NOTICE = 'link-attach'

/** Hold the refs a chat's address names (`attach`) as chips in its composer, never sending them. */
export function useLinkAttachments({ refs, addKnowledge, addArtifact, notice, onRead }: {
  refs: readonly string[]
  addKnowledge: (item: { id: string; name: string }) => void
  addArtifact: (art: { slug: string; name: string }) => void
  notice: Pick<ComposerNoticeState, 'showError' | 'clear'>
  /** Take `attach` off the address: the refs are read, and the chips they make are the draft's. */
  onRead: () => void
}): void {
  const key = refs.join(',')
  // The latest callbacks, so a re-render's new functions don't read the link again.
  const latest = useRef({ addKnowledge, addArtifact, notice, onRead })
  latest.current = { addKnowledge, addArtifact, notice, onRead }
  // The chips land after the reads answer, by which time the address no longer names the refs; only
  // leaving the chat drops what is still on its way.
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    if (!key) return
    latest.current.onRead()
    void resolveAttachments(key.split(',')).then((r) => {
      if (!mounted.current) return
      const { addKnowledge: k, addArtifact: a, notice: n } = latest.current
      r.knowledge.forEach(k)
      r.artifacts.forEach(a)
      const said = attachSentence(r)
      if (!said) { n.clear(ATTACH_NOTICE); return }
      const ask = r.findings[0]
      n.showError(said, ATTACH_NOTICE, ask ? {
        label: 'Ask about this',
        run: () => {
          investigate('findings', ask.id).catch((e) => latest.current.notice.showError(failureSentence('ask about it', e), ATTACH_NOTICE))
        },
      } : undefined)
    })
  }, [key])
}
