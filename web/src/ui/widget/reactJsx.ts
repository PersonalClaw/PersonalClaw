import * as monaco from 'monaco-editor'

// ── A react artifact's JSX, compiled by the TypeScript this app already ships ─────────────────
//
// The frame used to fetch Babel from a CDN and transform the JSX inside the frame. The code
// editor's own language worker (monaco-editor's TypeScript service, bundled and served by this
// app) compiles JSX just as well, so it does, here, before the frame is built.
//
// As a CommonJS module with the `esModuleInterop` helpers, so `import React, { useState } from
// 'react'` and `export default function App()` both work against the frame's `require` and
// `module.exports`. The JavaScript defaults are the editor's too: `jsx: React` is what a `.jsx`
// file in the editor wants anyway, and nothing else in them is changed.

let configured = false
let sequence = 0

function configure(): void {
  if (configured) return
  const defaults = monaco.typescript.javascriptDefaults
  defaults.setCompilerOptions({
    ...defaults.getCompilerOptions(),
    allowJs: true,
    jsx: monaco.typescript.JsxEmit.React,
    module: monaco.typescript.ModuleKind.CommonJS,
    esModuleInterop: true,
  })
  configured = true
}

const messageOf = (text: string | { messageText: string }): string =>
  typeof text === 'string' ? text : text.messageText

/** How long to wait for the JavaScript service to come up, and how often to ask. */
const SERVICE_WAIT_MS = 5000
const SERVICE_POLL_MS = 100

/** The JavaScript service's worker accessor, once the service exists.
 *
 *  Monaco sets the service up when a JavaScript model is first created, but asynchronously (once
 *  its language module has loaded), and until then refuses with "JavaScript not registered!" —
 *  offering no event to wait on. Measured in the built app: the first ask, made right after the
 *  model, was refused and the next, 100 ms later, succeeded. So that one refusal is asked again,
 *  briefly; any other failure is reported at once. */
async function javascriptService(): Promise<Awaited<ReturnType<typeof monaco.typescript.getJavaScriptWorker>>> {
  const until = Date.now() + SERVICE_WAIT_MS
  for (;;) {
    try {
      return await monaco.typescript.getJavaScriptWorker()
    } catch (e) {
      if (!String(e).includes('not registered') || Date.now() >= until) throw e instanceof Error ? e : new Error(String(e))
      await new Promise((r) => setTimeout(r, SERVICE_POLL_MS))
    }
  }
}

/** *jsx* as plain JavaScript, or the first syntax error in it (with the line it is on). */
export async function transformJsx(jsx: string): Promise<{ code: string } | { error: string }> {
  configure()
  const uri = monaco.Uri.parse(`file:///react-preview/${++sequence}.jsx`)
  const model = monaco.editor.createModel(jsx, 'javascript', uri)
  try {
    const worker = await (await javascriptService())(uri)
    const syntax = await worker.getSyntacticDiagnostics(uri.toString())
    if (syntax.length) {
      const first = syntax[0]
      const where = first.start === undefined ? '' : `Line ${model.getPositionAt(first.start).lineNumber}: `
      return { error: `${where}${messageOf(first.messageText)}` }
    }
    const out = await worker.getEmitOutput(uri.toString())
    const js = out.outputFiles.find((f) => f.name.endsWith('.js'))
    return js && !out.emitSkipped ? { code: js.text } : { error: 'The component could not be compiled.' }
  } finally {
    model.dispose()
  }
}
