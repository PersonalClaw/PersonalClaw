/** A react artifact renders from what this app ships: its JSX compiled by the TypeScript the code
 *  editor carries, React inlined from the installed packages, and nothing fetched.
 *
 *  The compile runs in Monaco's TypeScript worker, which jsdom cannot host, so the worker is stood
 *  in for by the TypeScript compiler it wraps, driven with exactly the options the preview sets.
 *  The document is then run for real in its own window — every inline script, the runtime's
 *  module loader and the harness — and what reaches `#root` is what a user would see. */
import { afterEach, describe, it, expect, vi } from 'vitest'
// jsdom ships no type declarations and none are installed; this file uses two of its constructors,
// and types what it touches below.
// @ts-expect-error -- untyped module
import { JSDOM, VirtualConsole } from 'jsdom'
import { transformJsx } from './reactJsx'
import { REACT_FRAME_RUNTIME } from './reactFrameRuntime'
import { buildReactSrcdoc } from './widgetSrcdoc'

/** How the stand-in's JavaScript service answers: Monaco's own refuses until it has been set up,
 *  which it does asynchronously after the first JavaScript model is created. */
const service = vi.hoisted(() => ({ refusals: 0, failure: '' }))

vi.mock('monaco-editor', async () => {
  const ts = (await import('typescript')).default
  const models = new Map<string, string>()
  // Monaco's own defaults for the JavaScript service; the preview merges its options over them.
  let options: Record<string, unknown> = { allowNonTsExtensions: true, allowJs: true, target: ts.ScriptTarget.Latest }
  const transpile = (uri: string) => ts.transpileModule(models.get(uri) ?? '', {
    compilerOptions: options as import('typescript').CompilerOptions,
    fileName: uri.replace('file://', ''),
    reportDiagnostics: true,
  })
  return {
    Uri: { parse: (s: string) => ({ toString: () => s }) },
    editor: {
      createModel: (text: string, _language: string, uri: { toString(): string }) => {
        models.set(uri.toString(), text)
        return {
          getPositionAt: (offset: number) => ({ lineNumber: text.slice(0, offset).split('\n').length, column: 1 }),
          dispose: () => { models.delete(uri.toString()) },
        }
      },
    },
    typescript: {
      JsxEmit: ts.JsxEmit,
      ModuleKind: ts.ModuleKind,
      javascriptDefaults: {
        getCompilerOptions: () => options,
        setCompilerOptions: (next: Record<string, unknown>) => { options = next },
      },
      getJavaScriptWorker: async () => {
        if (service.failure) throw new Error(service.failure)
        // Monaco rejects with a bare string here, not an Error.
        if (service.refusals > 0) { service.refusals--; return Promise.reject('JavaScript not registered!') }
        return worker
      },
    },
  }
  function worker() {
    return {
      getSyntacticDiagnostics: async (uri: string) => transpile(uri).diagnostics ?? [],
      getEmitOutput: async (uri: string) => ({
        emitSkipped: false,
        outputFiles: [{ name: uri.replace(/\.jsx$/, '.js'), text: transpile(uri).outputText }],
      }),
    }
  }
})

/** One document's own window, as jsdom hands it back. */
type Frame = { window: Window }
const windows: Frame[] = []
afterEach(() => {
  while (windows.length) windows.pop()!.window.close()
  service.refusals = 0
  service.failure = ''
})

/** Compile *jsx* the way the preview does, build its document, and run that document. */
async function run(jsx: string) {
  const compiled = await transformJsx(jsx)
  if ('error' in compiled) throw new Error(`did not compile: ${compiled.error}`)
  const doc = buildReactSrcdoc({ code: compiled.code, runtime: REACT_FRAME_RUNTIME, css: '', themeVars: {}, mode: 'light' })
  const posted: Array<{ type?: string; message?: string }> = []
  const dom: Frame = new JSDOM(doc, {
    runScripts: 'dangerously',
    virtualConsole: new VirtualConsole(),
    beforeParse(w: Window) {
      // jsdom has no ResizeObserver; the harness only needs one to exist.
      Object.assign(w, { ResizeObserver: class { observe() {} disconnect() {} } })
      w.addEventListener('message', (e: MessageEvent) => { posted.push(e.data) })
    },
  })
  windows.push(dom)
  return { root: dom.window.document.getElementById('root') as HTMLElement, posted }
}

describe('a react artifact renders from what this app ships', () => {
  it('a component written against the React globals', async () => {
    const { root } = await run(`function App() {
  const [n] = React.useState(3)
  return <p>Count {n}</p>
}`)
    await vi.waitFor(() => expect(root.textContent).toBe('Count 3'))
  })

  it('a component written as a module, importing what it uses', async () => {
    const { root } = await run(`import React, { useState } from 'react'
export default function Widget() {
  const [n] = useState(2)
  return <><b>n=</b>{n}</>
}`)
    await vi.waitFor(() => expect(root.textContent).toBe('n=2'))
  })

  it('a component that brings its own ReactDOM binding', async () => {
    // Compiles to a top-level `const ReactDOM`, which a bare `ReactDOM` in the harness would find
    // instead of the frame's — and react-dom's own module has no createRoot.
    const { root } = await run(`import * as ReactDOM from 'react-dom'
export default function App() {
  return <i>{typeof ReactDOM.createPortal}</i>
}`)
    await vi.waitFor(() => expect(root.textContent).toBe('function'))
  })

  it('an import the preview does not offer is said in the frame and to the host', async () => {
    const { root, posted } = await run(`import chunk from 'lodash/chunk'
export default function App() { return <p>{chunk([1, 2], 1).length}</p> }`)
    const said = 'This preview can import react and react-dom, not "lodash/chunk".'
    await vi.waitFor(() => expect(root.textContent).toBe(said))
    await vi.waitFor(() => expect(posted).toContainEqual({ type: 'widget-error', message: said }))
  })

  it('a component that throws while rendering shows its error instead of a blank frame', async () => {
    const { root, posted } = await run(`function App() { throw new Error('no data yet') }`)
    await vi.waitFor(() => expect(root.textContent).toBe('no data yet'))
    await vi.waitFor(() => expect(posted).toContainEqual({ type: 'widget-error', message: 'no data yet' }))
  })

  it('waits for the JavaScript service Monaco sets up after the model, rather than failing', async () => {
    // Measured in the built app: the first ask, right after the model, was refused.
    service.refusals = 2
    const out = await transformJsx('function App() { return <b>ok</b> }')
    expect(out).toHaveProperty('code')
    expect(service.refusals).toBe(0)
  })

  it('reports any other failure of the service at once', async () => {
    service.failure = 'the language worker crashed'
    await expect(transformJsx('function App() { return null }')).rejects.toThrow('the language worker crashed')
  })

  it('a syntax error is named with the line it is on', async () => {
    const out = await transformJsx('function App() {\n  return <p>unclosed\n}\n')
    expect('error' in out && out.error).toMatch(/^Line \d+: /)
  })
})

describe('the inlined runtime', () => {
  it('cannot end the <script> element it sits in', () => {
    expect(REACT_FRAME_RUNTIME).not.toMatch(/<\/script/i)
  })

  it('is the React this app runs on', async () => {
    const { version } = await import('react')
    expect(REACT_FRAME_RUNTIME).toContain(`"${version}"`)
  })
})
