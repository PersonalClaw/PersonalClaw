// Bind @monaco-editor/react to the LOCALLY-bundled monaco-editor package instead of its
// default jsdelivr CDN loader. The CDN path needs network access, is blocked by the app's
// CSP for its web worker (→ main-thread fallback / UI-freeze risk), and breaks entirely in
// an offline / locked-down container. Importing the local ESM build + wiring its workers
// through Vite's `?worker` makes the editor fully self-hosted (same-origin, CSP-clean).
//
// Imported once for side effects from main.tsx, BEFORE any <Editor> mounts.
import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'

import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import cssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker'
import htmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker'
import tsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'

// Monaco asks for a worker by language label; hand back the matching local worker bundle.
// Everything not covered by a language service uses the base editor worker.
;(self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === 'json') return new jsonWorker()
    if (label === 'css' || label === 'scss' || label === 'less') return new cssWorker()
    if (label === 'html' || label === 'handlebars' || label === 'razor') return new htmlWorker()
    if (label === 'typescript' || label === 'javascript') return new tsWorker()
    return new editorWorker()
  },
}

// TOML isn't in Monaco's bundled basic-languages, yet it's a very common file the
// Code worker generates (pyproject.toml, Cargo.toml, config.toml). Register a
// lightweight Monarch tokenizer so it highlights properly instead of falling back
// to the ill-fitting `ini` grammar (which mis-handles [[array.tables]], typed
// values, dates, and multi-line strings).
if (!monaco.languages.getLanguages().some((l) => l.id === 'toml')) {
  monaco.languages.register({ id: 'toml', extensions: ['.toml'], aliases: ['TOML', 'toml'] })
  monaco.languages.setMonarchTokensProvider('toml', {
    tokenizer: {
      root: [
        [/^\s*#.*$/, 'comment'],
        // [table] and [[array of tables]] headers
        [/^\s*\[\[.*?\]\]/, 'type.identifier'],
        [/^\s*\[.*?\]/, 'type.identifier'],
        // key =
        [/[A-Za-z0-9_.-]+(?=\s*=)/, 'key'],
        [/=/, 'operator'],
        // values
        [/"""/, { token: 'string', next: '@mlstring' }],
        [/'''/, { token: 'string', next: '@mlstringq' }],
        [/"/, { token: 'string', next: '@string' }],
        [/'/, { token: 'string', next: '@stringq' }],
        [/\b(true|false)\b/, 'keyword'],
        [/\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?/, 'number.hex'], // dates
        [/[+-]?(\d[\d_]*\.?[\d_]*([eE][+-]?\d+)?|0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+)/, 'number'],
        [/#.*$/, 'comment'],
      ],
      string: [[/[^"]+/, 'string'], [/"/, { token: 'string', next: '@pop' }]],
      stringq: [[/[^']+/, 'string'], [/'/, { token: 'string', next: '@pop' }]],
      mlstring: [[/[^"]+/, 'string'], [/"""/, { token: 'string', next: '@pop' }], [/"/, 'string']],
      mlstringq: [[/[^']+/, 'string'], [/'''/, { token: 'string', next: '@pop' }], [/'/, 'string']],
    },
  })
  monaco.languages.setLanguageConfiguration('toml', {
    comments: { lineComment: '#' },
    brackets: [['[', ']'], ['{', '}']],
    autoClosingPairs: [{ open: '[', close: ']' }, { open: '{', close: '}' }, { open: '"', close: '"' }, { open: "'", close: "'" }],
  })
}

// Point the React wrapper at the imported instance — no network fetch of the AMD loader.
loader.config({ monaco })
