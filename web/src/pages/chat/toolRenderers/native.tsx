/** Native per-tool render overrides + icon/label registry (tool-io-rendering, TC3).
 *
 * Native tools have predictable, well-known schemas, so each high-value one gets
 * a hand-built renderer that displays its I/O the way a human wants to see it:
 * edit_file as an old→new mini-diff, grep as a clickable hit list, git diff as a
 * diff, knowledge/web search as result cards. Everything not matched here falls
 * to the schema-driven default + content-type renderers in the registry.
 */
import { type ReactNode } from 'react'
import {
  Wrench, Terminal, FileText, FilePen, FilePlus, Search, Globe, Bot, List,
  Trash2, FolderInput, Brain, BookOpen, ListChecks, Database, GitBranch,
  MessageSquare, type LucideIcon,
} from 'lucide-react'
import { Markdown } from '../../../ui/Markdown'
import type { ToolSegment } from '../chatTypes'
import { RawBlock } from './primitives'
import type { ToolRenderer } from './registry'

const asObj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {})
const str = (v: unknown): string => (v == null ? '' : String(v))

// ── input overrides ──

/** edit_file: show the replacement as an old→new mini-diff (the intent at a glance). */
function editFileInput(seg: ToolSegment): ReactNode {
  const o = asObj(seg.inputObj)
  const path = str(o.path)
  const oldStr = str(o.old_str)
  const newStr = str(o.new_str)
  if (!oldStr && !newStr) return undefined as unknown as ReactNode  // fall through
  const diff = oldStr.split('\n').map((l) => `-${l}`).join('\n') + '\n' + newStr.split('\n').map((l) => `+${l}`).join('\n')
  return (
    <div className="mb-1.5">
      <div className="mb-1 flex items-center gap-1.5 text-on-surface-low text-[0.65rem]"><FilePen size={12} /> <span className="font-mono">{path}</span>{o.replace_all ? <span className="rounded bg-surface-high px-1 py-0.5">replace all</span> : null}</div>
      <RawBlock label="Change"><Markdown>{`\`\`\`diff\n${diff}\n\`\`\``}</Markdown></RawBlock>
    </div>
  )
}

/** read/write/glob/grep: lead with the path/pattern as a chip + the rest as fields. */
function pathChipInput(label: string) {
  return (seg: ToolSegment): ReactNode => {
    const o = asObj(seg.inputObj)
    const primary = str(o.path || o.pattern || o.query || o.command)
    if (!primary) return undefined as unknown as ReactNode
    const rest = Object.entries(o).filter(([k]) => !['path', 'pattern', 'query', 'command'].includes(k))
    return (
      <div className="mb-1.5">
        <div className="mb-0.5 text-on-surface-low text-[0.6rem] uppercase tracking-wide">{label}</div>
        <code className="block rounded-md bg-surface-low px-2 py-1.5 font-mono text-on-surface text-[0.78rem] break-words">{primary}</code>
        {rest.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {rest.map(([k, v]) => <span key={k} className="rounded-pill bg-surface-high px-2 py-0.5 font-mono text-on-surface-low text-[0.65rem]">{k}: {str(v)}</span>)}
          </div>
        )}
      </div>
    )
  }
}

// ── output overrides ──

/** grep / glob / list_dir: render the path:line hit list as rows. */
function hitListOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!text || text.startsWith('{') || text.startsWith('[')) return undefined as unknown as ReactNode
  const lines = text.split('\n').filter(Boolean)
  if (lines.length === 0 || lines.length > 500) return undefined as unknown as ReactNode
  return (
    <RawBlock label="Matches">
      <div className="flex flex-col font-mono text-[0.72rem]">
        {lines.slice(0, 300).map((ln, i) => {
          const m = ln.match(/^(.+?):(\d+):(.*)$/)  // path:line:content
          return m
            ? <div key={i} className="flex gap-2 py-0.5"><span className="shrink-0 text-primary">{m[1]}</span><span className="shrink-0 text-on-surface-low tabular-nums">{m[2]}</span><span className="truncate text-on-surface-var">{m[3]}</span></div>
            : <div key={i} className="py-0.5 text-on-surface-var">{ln}</div>
        })}
        {lines.length > 300 && <div className="mt-1 text-on-surface-low">…{lines.length - 300} more</div>}
      </div>
    </RawBlock>
  )
}

/** A bash command whose output is a textual diff (e.g. `git diff`) → render with
 *  +/- coloring. Only engages when the output actually looks like a diff, so plain
 *  command output is unaffected. (git/tests/lint run via bash now — no own tools.) */
function diffOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!/^(diff --git |@@ |index [0-9a-f]|--- |\+\+\+ )/m.test(text)) return undefined as unknown as ReactNode
  return <RawBlock label="Diff"><Markdown>{`\`\`\`diff\n${text}\n\`\`\``}</Markdown></RawBlock>
}

/** knowledge_search / web_search: render the JSON result array as cards. */
function searchResultsOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  let data: unknown
  try { data = JSON.parse(text) } catch { return undefined as unknown as ReactNode }
  const arr = Array.isArray(data) ? data : (asObj(data).results as unknown[]) || (asObj(data).items as unknown[])
  if (!Array.isArray(arr) || arr.length === 0) return undefined as unknown as ReactNode
  return (
    <RawBlock label={`${arr.length} result(s)`}>
      <div className="flex flex-col gap-1.5">
        {arr.slice(0, 20).map((r, i) => {
          const o = asObj(r)
          const title = str(o.title || o.name || o.url || o.id)
          const sub = str(o.url || o.type || o.snippet || o.description)
          return (
            <div key={i} className="rounded-md bg-surface px-2 py-1.5">
              <div className="truncate text-on-surface text-[0.8rem]" style={{ fontVariationSettings: '"wght" 550' }}>{title}</div>
              {sub && sub !== title && <div className="truncate text-on-surface-low text-[0.7rem]">{sub}</div>}
            </div>
          )
        })}
      </div>
    </RawBlock>
  )
}

/** web_fetch: render the fetched page as a titled card with its extracted content
 *  as Markdown (the normalized web_fetch output is markdown/text + a title line). */
function webFetchOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!text || text.startsWith('{') || text.startsWith('[')) return undefined as unknown as ReactNode
  const o = asObj(seg.inputObj)
  const url = str(o.url)
  return (
    <RawBlock label="Fetched page">
      {url && <div className="mb-1 truncate text-primary text-[0.7rem]">{url}</div>}
      <div className="max-h-72 overflow-auto"><Markdown>{text.slice(0, 8000)}</Markdown></div>
    </RawBlock>
  )
}

/** task_create / task_update: a compact created/updated-task chip (id · title · status)
 *  parsed from the tool's textual confirmation or JSON. Falls through when unrecognized. */
function taskChipOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!text) return undefined as unknown as ReactNode
  let title = '', status = '', id = ''
  try {
    const d = asObj(JSON.parse(text))
    title = str(d.title || d.subject); status = str(d.status); id = str(d.id || d.task_id)
  } catch {
    // textual "Created task t-abc: Title (status)" style — best-effort, else fall through
    const m = text.match(/\b(t-[\w-]+)\b/)
    if (!m) return undefined as unknown as ReactNode
    id = m[1]; title = text.replace(/^[^:]*:\s*/, '').slice(0, 120)
  }
  if (!id && !title) return undefined as unknown as ReactNode
  return (
    <div className="mt-1 inline-flex flex-wrap items-center gap-1.5 rounded-md bg-surface-container px-2 py-1">
      <ListChecks size={12} className="text-primary" />
      {id && <span className="font-mono text-on-surface-low text-[0.65rem]">{id}</span>}
      {title && <span className="text-on-surface text-[0.76rem]">{title}</span>}
      {status && <span className="rounded-pill bg-surface-high px-1.5 text-on-surface-var text-[0.62rem] uppercase">{status}</span>}
    </div>
  )
}

/** memory_* : a compact "recorded" chip — memory writes return a short confirmation;
 *  show it as a brain-marked note rather than a raw line. */
function memoryChipOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!text || text.length > 240 || text.startsWith('{') || text.startsWith('[')) return undefined as unknown as ReactNode
  // The "recorded" chip implies a memory was WRITTEN — don't show it for an error
  // or declined result (those must fall through to the neutral Result renderer, so
  // a failed memory_forget/remember doesn't masquerade as a success).
  if (/^(error|failed|declined|refus|could ?n.?t|unable|no (memory|such|matching)|not found|invalid)\b/i.test(text)) {
    return undefined as unknown as ReactNode
  }
  return (
    <div className="mt-1 flex items-start gap-1.5 rounded-md bg-surface-container px-2 py-1">
      <Brain size={12} className="mt-0.5 shrink-0 text-primary" />
      <span className="text-on-surface-var text-[0.76rem] leading-snug">{text}</span>
    </div>
  )
}

/** project_run_status / project_run_create: a sub-run status chip (the loop id + state). */
function runStatusOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!text) return undefined as unknown as ReactNode
  let id = '', status = ''
  try { const d = asObj(JSON.parse(text)); id = str(d.id || d.loop_id); status = str(d.status || d.state) } catch { /* fall through to regex */ }
  if (!id) { const m = text.match(/\b([0-9a-f]{8})\b/); if (m) id = m[1] }
  if (!id && !status) return undefined as unknown as ReactNode
  return (
    <div className="mt-1 inline-flex items-center gap-1.5 rounded-md bg-surface-container px-2 py-1">
      <Bot size={12} className="text-primary" />
      {id && <span className="font-mono text-on-surface-low text-[0.65rem]">{id}</span>}
      {status && <span className="rounded-pill bg-surface-high px-1.5 text-on-surface-var text-[0.62rem] uppercase">{status}</span>}
    </div>
  )
}

// ── the registry of native overrides (ordered; first match wins) ──
export const NATIVE_RENDERERS: ToolRenderer[] = [
  { match: (n) => n === 'edit_file', input: editFileInput },
  { match: (n) => n === 'read_file', input: pathChipInput('File') },
  { match: (n) => n === 'write_file', input: pathChipInput('File') },
  { match: (n) => n === 'glob' || n === 'list_dir', input: pathChipInput('Pattern'), output: hitListOutput },
  { match: (n) => n === 'grep', input: pathChipInput('Query'), output: hitListOutput },
  { match: (n) => n === 'bash', input: pathChipInput('Command'), output: diffOutput },
  { match: (n) => n === 'knowledge_search', output: searchResultsOutput },
  { match: (n) => n === 'web_search' || n === 'web', output: searchResultsOutput },
  { match: (n) => n === 'web_fetch', input: pathChipInput('URL'), output: webFetchOutput },
  { match: (n) => n === 'task_create' || n === 'task_update', output: taskChipOutput },
  { match: (n) => n.startsWith('memory_'), output: memoryChipOutput },
  { match: (n) => n === 'project_run_create' || n === 'project_run_status', output: runStatusOutput },
]

/** Strip the `mcp__<server>__` prefix → the bare tool name, so icon/label
 *  matching works on `post_message` rather than `mcp__slack_mcp__post_message`.
 *  MCP names are `mcp__<server>__<tool>` where server may contain underscores,
 *  so split on the `__` delimiter and keep the final segment. */
function bareName(name: string): string {
  if (!name.startsWith('mcp__')) return name.toLowerCase()
  const parts = name.split('__').filter(Boolean)  // ['mcp','server','tool',...]
  return (parts.length >= 3 ? parts.slice(2).join('_') : name).toLowerCase()
}

// ── icon + label registry (replaces the keyword-regex heuristic as the primary) ──

const ICON_BY_NAME: Record<string, LucideIcon> = {
  // files
  read_file: FileText, write_file: FilePlus, edit_file: FilePen,
  list_dir: List, glob: Search, grep: Search, repo_map: FolderInput,
  // exec (git, tests, linters all run via bash now — no dedicated tools)
  bash: Terminal,
  // knowledge
  knowledge_search: BookOpen, knowledge_create: BookOpen, knowledge_get: BookOpen,
  knowledge_update: BookOpen, knowledge_stats: BookOpen,
  // tasks / projects
  task_create: ListChecks, task_get: ListChecks, task_list: ListChecks,
  task_update: ListChecks, task_search: ListChecks, task_ready: ListChecks,
  task_list_create: ListChecks, project_create: FolderInput, project_list: FolderInput,
  // SDLC loops
  project_run_create: Bot, project_run_start: Bot, project_run_status: ListChecks,
  project_run_list: ListChecks,
  // web / memory / misc
  web_search: Globe, web_fetch: Globe, memory_recall: Brain, memory_remember: Brain,
  tool_result_get: FileText, post_to_inbox: MessageSquare,
}

const _BY_KIND: Record<string, LucideIcon> = {
  execute: Terminal, read: FileText, edit: FilePen, delete: Trash2,
  move: FolderInput, search: Search, fetch: Globe, think: Brain, other: Wrench,
}

/** Resolve a tool's icon: explicit native map → ACP kind → keyword regex → Wrench.
 *  MCP tools (`mcp__server__tool`) match on their bare tool name. */
export function iconForTool(seg: ToolSegment): LucideIcon {
  const name = bareName(seg.tool || '')
  if (ICON_BY_NAME[name]) return ICON_BY_NAME[name]
  if (seg.toolKind && _BY_KIND[seg.toolKind]) return _BY_KIND[seg.toolKind]
  if (/terminal|bash|shell|exec|command|run/.test(name)) return Terminal
  if (/edit|write|patch|create|str_?replace/.test(name)) return FilePen
  if (/read|cat|view|open/.test(name)) return FileText
  if (/grep|search|find|glob/.test(name)) return Search
  if (/fetch|web|http|url|browse/.test(name)) return Globe
  if (/list|ls|dir/.test(name)) return List
  if (/agent|task|subagent|delegate|dispatch/.test(name)) return Bot
  if (/memor|recall|lesson/.test(name)) return Brain
  if (/knowledge/.test(name)) return BookOpen
  if (/delete|remove|rm/.test(name)) return Trash2
  if (/git/.test(name)) return GitBranch
  if (/database|memory|store/.test(name)) return Database
  return Wrench
}

/** Friendly display labels for native tools — so the card header reads like a
 *  human action ("Run command") not a raw identifier ("bash"). Every native tool
 *  has an entry; anything unmapped (future/MCP tools) gets humanized by rule. */
const LABEL_BY_NAME: Record<string, string> = {
  // files
  read_file: 'Read', write_file: 'Write', edit_file: 'Edit', list_dir: 'List',
  glob: 'Find files', grep: 'Search code', repo_map: 'Map repo',
  // exec (git / tests / linters run via bash)
  bash: 'Run command',
  // knowledge
  knowledge_search: 'Search Knowledge', knowledge_create: 'Add Knowledge',
  knowledge_get: 'Get Knowledge', knowledge_update: 'Update Knowledge',
  knowledge_stats: 'Knowledge stats',
  // tasks / projects
  task_create: 'Create task', task_get: 'Get task', task_list: 'List tasks',
  task_update: 'Update task', task_search: 'Search tasks', task_ready: 'Ready tasks',
  task_list_create: 'Create task list', project_create: 'Create project',
  project_list: 'List projects',
  // SDLC loops
  project_run_create: 'Create project run', project_run_start: 'Start project run',
  project_run_status: 'Project status', project_run_list: 'List project runs',
  // web / memory / misc
  web_search: 'Web search', web_fetch: 'Fetch page', memory_recall: 'Recall',
  memory_remember: 'Remember', tool_result_get: 'Fetch full result',
  post_to_inbox: 'Notify',
}
export function labelForTool(seg: ToolSegment): string {
  const raw = seg.tool || ''
  const known = LABEL_BY_NAME[bareName(raw)]
  if (known) return known
  // Unmapped tool (a new native tool, or MCP) → humanize its bare name
  // (snake_case → "Title case") so a header never shows a raw identifier or
  // "undefined". MCP names use the bare tool segment (no mcp__server__ noise).
  return humanize(bareName(raw)) || 'Tool'
}

/** snake_case / kebab → "Title case" first word capitalized (e.g. task_ready →
 *  "Task ready"). Keeps it short and readable for an unmapped tool header. */
function humanize(name: string): string {
  const words = name.replace(/[_-]+/g, ' ').trim()
  if (!words) return ''
  return words.charAt(0).toUpperCase() + words.slice(1)
}
