import { useEffect, useRef, useState } from 'react'
import { api, type SemanticEntry } from '../lib/api'
import { failureSentence } from './reportingWrite'
import { chatFindPath, searchCoverage } from '../pages/chat/searchDeepLink'

/** ⌘K's CONTENT half (F-52): what the palette finds inside chats, memory, knowledge and tasks.
 *
 *  The palette used to search its own list of pages and actions and nothing else, so "the chat
 *  where I asked about the budget" was a trip to four different pages. Each source is searched
 *  through the API its own page already uses, so a hit here is the same hit that page would show,
 *  and each opens where that page would open it:
 *
 *   · chats: `GET /api/sessions/search` (the chat list's content search), opened with the query
 *     in the find bar (`chatFindPath`);
 *   · memory: episodic memories through `GET /api/memory/episodic/search`, and facts matched by
 *     key or value from `GET /api/memory/semantic` (the Memory studio's own filter), opened as the
 *     selected record in Settings → Memory;
 *   · knowledge: `GET /api/knowledge/items?q=`, opened on the item's page;
 *   · tasks: `POST /api/tasks/search`, opened in the task list's side panel.
 *
 *  A source that fails says so in its own words, beside the others' results, rather than looking
 *  like a source with nothing in it. That is the failure F-41 was about on the chat list. */

export type ContentSource = 'chats' | 'memory' | 'knowledge' | 'tasks'

/** In display order. */
export const CONTENT_SOURCES: readonly ContentSource[] = ['chats', 'memory', 'knowledge', 'tasks'] as const

export const SOURCE_LABEL: Record<ContentSource, string> = {
  chats: 'Chats', memory: 'Memory', knowledge: 'Knowledge', tasks: 'Tasks',
}

/** What a failed search is called in its sentence: "Couldn't search your memory: …". */
const SOURCE_WHAT: Record<ContentSource, string> = {
  chats: 'search your chats', memory: 'search your memory', knowledge: 'search your knowledge', tasks: 'search your tasks',
}

export interface ContentHit {
  id: string
  source: ContentSource
  label: string
  /** A second line: the matching passage, a date or a status. */
  detail?: string
  /** Where the hit opens, as a route the shell's `navigate` takes. */
  path: string
}

export interface ContentSearch {
  /** The query these results answer; `''` when no content search applies (under two characters). */
  query: string
  searching: boolean
  hits: ContentHit[]
  /** The sentence for each source whose search failed. */
  failures: Partial<Record<ContentSource, string>>
  /** The sentence for each source whose search looked in only part of what it holds — chats,
   *  while the search index is still being built — so a short list does not read as all there is. */
  notes: Partial<Record<ContentSource, string>>
}

/** Below this, a query names a page more often than it names content. */
export const MIN_CONTENT_QUERY = 2
/** Per source. The palette is a jump list, not a results page. */
export const HITS_PER_SOURCE = 5
const DEBOUNCE_MS = 250

const IDLE: ContentSearch = { query: '', searching: false, hits: [], failures: {}, notes: {} }

/** A chat key as the chat routes spell it (the search answers `dashboard_`-prefixed keys). */
const chatKey = (key: string) => key.replace(/^dashboard[_:]/, '')
/** The index marks a match as `<<word>>`; a one-line detail shows the words without the marks. */
const unmark = (s: string) => s.replace(/<<|>>/g, '')
const oneLine = (s: string, cap = 120) => {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > cap ? `${t.slice(0, cap - 1)}…` : t
}
const factValue = (json?: string) => {
  if (!json) return ''
  try {
    const v: unknown = JSON.parse(json)
    return typeof v === 'string' ? v : JSON.stringify(v)
  } catch { return json }
}
const memoryPath = (uid: string) => `settings/memory?tab=studio&sel=${encodeURIComponent(uid)}`

/** A source's hits, and the sentence it owes when they come from only part of what it holds. */
interface SourceAnswer { hits: ContentHit[]; note?: string }

async function searchChats(q: string): Promise<SourceAnswer> {
  const answer = await api.sessionsSearch(q)
  const { sessions } = answer
  const reach = searchCoverage(answer)
  // One chat can answer under both spellings of its history key (`dashboard:` and `dashboard_`),
  // the way the chat list dedupes it: by the key the chat routes use.
  const seen = new Set<string>()
  const hits: ContentHit[] = []
  for (const s of sessions) {
    const key = chatKey(s.key)
    if (seen.has(key)) continue
    seen.add(key)
    hits.push({
      id: `chat:${key}`, source: 'chats', label: s.title || 'Untitled chat',
      detail: s.snippet ? oneLine(unmark(s.snippet)) : undefined,
      path: chatFindPath(key, q),
    })
  }
  return {
    hits: hits.slice(0, HITS_PER_SOURCE),
    note: reach
      ? `Searched ${reach.shown.toLocaleString()} of ${reach.total.toLocaleString()} chats — ${reach.detail}`
      : undefined,
  }
}

async function searchMemory(q: string, facts: () => Promise<SemanticEntry[]>): Promise<ContentHit[]> {
  const needle = q.toLowerCase()
  const [episodic, all] = await Promise.all([api.searchEpisodic(q), facts()])
  const factHits: ContentHit[] = all
    .filter((f) => f.key.toLowerCase().includes(needle) || factValue(f.value_json).toLowerCase().includes(needle))
    .map((f) => ({ id: `fact:${f.key}`, source: 'memory', label: f.key, detail: oneLine(factValue(f.value_json)) || undefined, path: memoryPath(`fact:${f.key}`) }))
  const episodicHits: ContentHit[] = episodic.map((e) => ({
    id: `epi:${e.id}`, source: 'memory', label: oneLine(e.text, 80), path: memoryPath(`epi:${e.id}`),
  }))
  return [...factHits, ...episodicHits].slice(0, HITS_PER_SOURCE)
}

async function searchKnowledge(q: string): Promise<ContentHit[]> {
  const { items } = await api.knowledgeItems({ q, limit: HITS_PER_SOURCE })
  return items.slice(0, HITS_PER_SOURCE).map((k) => ({
    id: `knowledge:${k.id}`, source: 'knowledge', label: k.title || 'Untitled item',
    detail: k.summary ? oneLine(k.summary) : undefined, path: `knowledge/item/${encodeURIComponent(k.id)}`,
  }))
}

async function searchTasks(q: string): Promise<ContentHit[]> {
  const { tasks } = await api.searchTasks({ query: q, limit: HITS_PER_SOURCE })
  return tasks.slice(0, HITS_PER_SOURCE).map((t) => ({
    id: `task:${t.id}`, source: 'tasks', label: t.title, detail: t.status || undefined,
    path: `tasks?open=${encodeURIComponent(t.id)}`,
  }))
}

/** The palette's content results for `q`, while the palette is `open`.
 *
 *  Debounced, and a later query supersedes an earlier one: an answer for a query the user has
 *  already typed past is dropped rather than painted over the newer one. The fact list is read
 *  once per opening and filtered as the query changes, the way the Memory studio filters it. */
export function useContentSearch(q: string, open: boolean): ContentSearch {
  const [state, setState] = useState<ContentSearch>(IDLE)
  const facts = useRef<Promise<SemanticEntry[]> | null>(null)
  const latest = useRef(0)

  useEffect(() => { if (!open) facts.current = null }, [open])

  useEffect(() => {
    const query = q.trim()
    const run = ++latest.current
    if (!open || query.length < MIN_CONTENT_QUERY) { setState(IDLE); return }
    const readFacts = () => (facts.current ??= api.memorySemantic())
    const timer = window.setTimeout(() => {
      // The previous query's hits stay up while this one is searched, rather than the list
      // emptying on every pause in typing.
      setState((s) => ({ ...s, searching: true }))
      const whole = (hits: Promise<ContentHit[]>): Promise<SourceAnswer> => hits.then((h) => ({ hits: h }))
      const searches: Record<ContentSource, Promise<SourceAnswer>> = {
        chats: searchChats(query),
        memory: whole(searchMemory(query, readFacts)),
        knowledge: whole(searchKnowledge(query)),
        tasks: whole(searchTasks(query)),
      }
      void Promise.allSettled(CONTENT_SOURCES.map((s) => searches[s])).then((settled) => {
        if (run !== latest.current) return
        const hits: ContentHit[] = []
        const failures: Partial<Record<ContentSource, string>> = {}
        const notes: Partial<Record<ContentSource, string>> = {}
        settled.forEach((r, i) => {
          const source = CONTENT_SOURCES[i]
          if (r.status === 'fulfilled') {
            hits.push(...r.value.hits)
            if (r.value.note) notes[source] = r.value.note
          } else failures[source] = failureSentence(SOURCE_WHAT[source], r.reason)
        })
        // A failed memory search does not keep its fact read: the next query reads again.
        if (failures.memory) facts.current = null
        setState({ query, searching: false, hits, failures, notes })
      })
    }, DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [q, open])

  return state
}
