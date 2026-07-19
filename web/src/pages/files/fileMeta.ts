import {
  FileText, FileCode, Image, FileJson, Table, Globe, File as FileIcon,
  Folder, Box, Code2, Hash, Braces, BarChart3, ScrollText, ImagePlus, type LucideIcon,
} from 'lucide-react'
import type { ArtifactKind } from '../../lib/api'

// ── file type detection (drives the viewer + icon) ──
export type FileViewType = 'image' | 'pdf' | 'csv' | 'json' | 'html' | 'markdown' | 'code'

const IMG_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'ico', 'tiff'])

// Comprehensive ext → Monaco language id (ported from legacy MonacoCodeBlock).
const LANG_MAP: Record<string, string> = {
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
  ts: 'typescript', mts: 'typescript', cts: 'typescript', tsx: 'typescript',
  py: 'python', pyi: 'python', pyw: 'python',
  sh: 'shell', bash: 'shell', zsh: 'shell', ksh: 'shell', fish: 'shell',
  html: 'html', htm: 'html', xhtml: 'html', vue: 'html', svelte: 'html',
  css: 'css', scss: 'scss', sass: 'scss', less: 'less',
  json: 'json', json5: 'json', jsonc: 'json', geojson: 'json', webmanifest: 'json', jsonl: 'json',
  yaml: 'yaml', yml: 'yaml',
  toml: 'toml', ini: 'ini', cfg: 'ini', conf: 'ini', editorconfig: 'ini', properties: 'ini',
  xml: 'xml', svg: 'xml', xsl: 'xml', xsd: 'xml', plist: 'xml', csproj: 'xml', rss: 'xml',
  md: 'markdown', markdown: 'markdown', mdx: 'markdown', rst: 'restructuredtext',
  c: 'cpp', h: 'cpp', cc: 'cpp', cpp: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp', hxx: 'cpp', ino: 'cpp',
  rs: 'rust', go: 'go',
  java: 'java', kt: 'kotlin', kts: 'kotlin', scala: 'scala', sc: 'scala',
  groovy: 'java', gradle: 'java', clj: 'clojure', cljs: 'clojure', cljc: 'clojure',
  cs: 'csharp', fs: 'fsharp', fsx: 'fsharp', vb: 'vb', razor: 'razor', cshtml: 'razor',
  rb: 'ruby', rake: 'ruby',
  php: 'php', php3: 'php', php4: 'php', php5: 'php', phtml: 'php',
  swift: 'swift', m: 'objective-c', mm: 'objective-c',
  dart: 'dart', lua: 'lua', pl: 'perl', pm: 'perl',
  r: 'r', jl: 'julia', ex: 'elixir', exs: 'elixir',
  sql: 'sql', mysql: 'mysql', pgsql: 'pgsql', psql: 'pgsql', graphql: 'graphql', gql: 'graphql',
  dockerfile: 'dockerfile', containerfile: 'dockerfile',
  tf: 'hcl', tfvars: 'hcl', hcl: 'hcl', nomad: 'hcl',
  ps1: 'powershell', psm1: 'powershell', psd1: 'powershell',
  bat: 'bat', cmd: 'bat', proto: 'protobuf', sol: 'solidity', wgsl: 'wgsl', tcl: 'tcl',
  hbs: 'handlebars', handlebars: 'handlebars', mustache: 'handlebars',
  pug: 'pug', jade: 'pug', twig: 'twig', liquid: 'liquid',
  // These grammars ARE bundled in monaco-editor's basic-languages but weren't
  // mapped, so files fell to plaintext (no highlighting) despite the grammar being
  // available for free. coffee=CoffeeScript, bicep=Azure IaC, sv/svh=SystemVerilog,
  // scm/ss/rkt=Scheme/Racket, pas/pp=Pascal, clj already covers Clojure.
  coffee: 'coffeescript', bicep: 'bicep',
  sv: 'systemverilog', svh: 'systemverilog',
  scm: 'scheme', ss: 'scheme', rkt: 'scheme',
  pas: 'pascal', pp: 'pascal',
  csv: 'plaintext', tsv: 'plaintext', env: 'shell', txt: 'plaintext', log: 'plaintext',
}

// Whole-filename (no usable ext) → language.
const FILENAME_MAP: Record<string, string> = {
  dockerfile: 'dockerfile', containerfile: 'dockerfile', makefile: 'shell',
  'cmakelists.txt': 'cpp', gemfile: 'ruby', rakefile: 'ruby', procfile: 'yaml', vagrantfile: 'ruby',
  '.gitignore': 'plaintext', '.gitattributes': 'plaintext', '.dockerignore': 'plaintext',
  '.npmrc': 'ini', '.editorconfig': 'ini', '.env': 'shell',
  '.bashrc': 'shell', '.zshrc': 'shell', '.bash_profile': 'shell', '.profile': 'shell',
}

export function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}

export function fileViewType(name: string): FileViewType {
  const ext = extOf(name)
  if (IMG_EXTS.has(ext) && ext !== 'svg') return 'image'  // svg is editable text too
  if (ext === 'svg') return 'image'
  if (ext === 'pdf') return 'pdf'
  if (ext === 'csv' || ext === 'tsv') return 'csv'
  if (ext === 'json' || ext === 'jsonl') return 'json'
  if (ext === 'html' || ext === 'htm') return 'html'
  // An EXTENSIONLESS file that's a known code filename (Makefile, Dockerfile,
  // Gemfile, Rakefile, Procfile, .bashrc, …) is CODE, not markdown — monacoLang
  // resolves it to a real language. Without this it fell into the ''-is-markdown
  // bucket below and opened in rendered-markdown preview by default (a Makefile's
  // tab-indented rules shown as garbled prose), forcing a manual switch to edit.
  if (ext === '' && monacoLang(name) !== 'plaintext') return 'code'
  // Only TRUE markdown opens in rendered-preview by default. A .txt is PLAIN text, not
  // markdown — defaulting it to the markdown preview mangled it (a line starting '#'
  // became an <h1>, '*' became bullets, leading whitespace collapsed, URLs autolinked),
  // so a log / data dump / requirements.txt rendered as garbled prose and forced a manual
  // switch to edit every open. .txt now opens as plaintext code (monacoLang maps it to
  // 'plaintext' already). The extensionless ('') case stays markdown — that's the prose
  // bucket (READMEs, NOTES, LICENSE) after the code-filename guard above has claimed
  // Makefile/Dockerfile/etc.
  if (['md', 'markdown', 'mdx', ''].includes(ext)) return 'markdown'
  return 'code'
}

/** Monaco language id from a filename (falls back to 'plaintext'). */
export function monacoLang(name: string): string {
  const base = (name.split('/').pop() || name).toLowerCase()
  if (FILENAME_MAP[base]) return FILENAME_MAP[base]
  const ext = extOf(base)
  if (LANG_MAP[ext]) return LANG_MAP[ext]
  // Compound dotfile / variant filenames the exact + extension lookups miss:
  // `.env.local`/`.env.production` → shell, `Dockerfile.dev`/`api.dockerfile` →
  // dockerfile. Common in real repos; without this they fall to plaintext.
  if (base === '.env' || base.startsWith('.env.')) return 'shell'
  if (base.startsWith('dockerfile.') || base.endsWith('.dockerfile')) return 'dockerfile'
  if (base.startsWith('docker-compose') || base.startsWith('compose.')) return 'yaml'
  return 'plaintext'
}

export function fileIcon(name: string, isDir: boolean): LucideIcon {
  if (isDir) return Folder
  const t = fileViewType(name)
  if (t === 'image') return Image
  if (t === 'json') return FileJson
  if (t === 'csv') return Table
  if (t === 'html') return Globe
  if (t === 'markdown') return FileText
  // .txt / .log open as plaintext 'code' now (not markdown) but they're documents, not
  // source — give them the document icon, not the code-angle-brackets icon.
  if (t === 'code') return ['txt', 'log'].includes(extOf(name)) ? FileText : FileCode
  return FileIcon
}

// ── git porcelain badge ──
// Uses the app's defined status tokens (--color-warn/ok/danger) so badges match the
// theme + the cockpit Changes-panel labels. (Was --color-warning/success/error, which
// aren't defined tokens — they silently fell back to off-theme hardcoded hex.)
export function gitBadge(code: string | undefined): { label: string; tone: string } | null {
  if (!code) return null
  const c = code.trim()
  if (c === '??') return { label: 'U', tone: 'var(--color-on-surface-low)' }
  // Precedence MUST match the Changes panel's label() exactly, else the SAME file shows
  // a different state in the tree badge vs the Changes list (e.g. 'AM' → 'M' here but
  // 'added' there). Order: conflict → D → A → R → C → modified-default. `includes`
  // (not startsWith) so the worktree-side letter (Y in porcelain XY) counts too; the
  // dominant change (add/delete/rename) wins over a co-occurring modify.
  // UNMERGED (conflict) FIRST — 'U' on either side, or the AA/DD double forms. Distinct
  // '!' danger marker (matches the Changes panel's 'conflict' label); precedes D/A since
  // DD/AU/UD contain those letters, and a bare-'U' would otherwise collide with '??'.
  if (c.includes('U') || c === 'AA' || c === 'DD') return { label: '!', tone: 'var(--color-danger)' }
  if (c.includes('D')) return { label: 'D', tone: 'var(--color-danger)' }
  if (c.includes('A')) return { label: 'A', tone: 'var(--color-ok)' }
  if (c.includes('R')) return { label: 'R', tone: 'var(--color-primary)' }
  if (c.includes('C')) return { label: 'C', tone: 'var(--color-primary)' }
  return { label: 'M', tone: 'var(--color-warn)' }
}

/** Human tooltip for a git porcelain status code — so a terse badge ('!','U','M')
 *  explains itself on hover instead of leaking the raw XY code. Falls back to the
 *  raw code for anything unmapped. */
export function gitStatusTitle(code: string | undefined): string {
  if (!code) return ''
  const c = code.trim()
  // Same precedence as gitBadge() + the Changes panel label() so the tooltip never
  // disagrees with the badge it explains: conflict → D → A → R → C → modified.
  if (c === '??') return 'Untracked (new file)'
  if (c.includes('U') || c === 'AA' || c === 'DD') return `Merge conflict (${c})`
  if (c.includes('D')) return 'Deleted'
  if (c.includes('A')) return 'Added'
  if (c.includes('R')) return 'Renamed'
  if (c.includes('C')) return 'Copied'
  return 'Modified'
}

// ── artifact kind meta ──
export const ARTIFACT_KINDS: { key: ArtifactKind; label: string; icon: LucideIcon; tone: string }[] = [
  { key: 'widget', label: 'Widget', icon: Box, tone: 'var(--color-primary)' },
  { key: 'html', label: 'HTML', icon: Globe, tone: '#e06c4f' },
  { key: 'react', label: 'React', icon: Box, tone: '#61dafb' },
  { key: 'markdown', label: 'Markdown', icon: Hash, tone: '#4f9be0' },
  { key: 'svg', label: 'SVG', icon: Image, tone: '#3fb950' },
  { key: 'infographic', label: 'Infographic', icon: BarChart3, tone: '#5b8cff' },
  { key: 'document', label: 'Document', icon: ScrollText, tone: '#9d86f5' },
  { key: 'image', label: 'Image', icon: ImagePlus, tone: '#3fb950' },
  { key: 'json', label: 'JSON', icon: Braces, tone: '#d4a017' },
  { key: 'text', label: 'Text', icon: Code2, tone: 'var(--color-on-surface-low)' },
]

export function artifactKindMeta(kind: string) {
  return ARTIFACT_KINDS.find((k) => k.key === kind) ?? ARTIFACT_KINDS[0]
}

// ── formatting ──
export function fmtBytes(n?: number): string {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function relTime(iso: string | number): string {
  if (!iso) return ''
  const t = typeof iso === 'number' ? iso * (iso < 1e12 ? 1000 : 1) : Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const s = Math.floor((Date.now() - t) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 604800) return `${Math.floor(s / 86400)}d ago`
  return new Date(t).toLocaleDateString()
}

export function baseName(path: string): string {
  return path.replace(/\/+$/, '').split('/').pop() || path
}
