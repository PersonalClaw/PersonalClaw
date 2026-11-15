import ts from 'typescript'

// ── A source file as TypeScript reads it, for the rails that look for a shape in the tree ──────────
//
// A rail that reads a file's text with a regular expression has to guess where its comments and
// strings are, and every guess leaks a shape: a stripper that does not know strings drops the code
// after a `'src/*.ts'` or a `"a//b"`, and a pattern for one way of writing a thing misses the next
// way. The compiler's own reading has no such guesses: a comment is trivia, a string is one token,
// and a name is bound where its scope says. The rails that hold a key to its one reader and writer
// (`app/theNewChatsKeysHaveOneReader`, `ui/widget/widgetBridgeSinglePath`) read the tree through it.
//
// What a static read sees, and where it stops. A key is seen wherever the source spells it out: in a
// string, in a template or a `+` whose parts are spelled out, in a `.join` of an array of them, or
// through a name bound to one. A key assembled at run time from values (a join of names, a key read
// from data, char codes) is beyond any static scan. For a chat's nonce the backstop is the writer's
// own behaviour, which `widgetBridgeSinglePath` checks: whatever shape the code takes,
// `chatLaunchPath` writes no nonce the stage did not stage.

/** `text`'s syntax tree, read as the compiler reads `rel` (TSX for a `.tsx` file). */
export function syntaxOf(rel: string, text: string): ts.SourceFile {
  return ts.createSourceFile(rel, text, ts.ScriptTarget.ESNext, true, rel.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
}

/** Every node under `root` (itself included), depth first, in source order. */
export function nodesOf(root: ts.Node): ts.Node[] {
  const out: ts.Node[] = []
  const visit = (node: ts.Node): void => { out.push(node); ts.forEachChild(node, visit) }
  visit(root)
  return out
}

/** The trimmed text of the line `node` starts on. */
export function lineOf(node: ts.Node): string {
  const sf = node.getSourceFile()
  const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf))
  return sf.text.split('\n')[line].trim()
}

/** `node`'s own text with its runs of white space made one space, cut at `max` characters. */
export function textOf(node: ts.Node, max = 160): string {
  const text = node.getText().replace(/\s+/g, ' ').trim()
  return text.length > max ? `${text.slice(0, max)}…` : text
}

const isPlus = (node: ts.Node): node is ts.BinaryExpression =>
  ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken

/** The words a string expression makes, as far as the source spells them: a string's own text, a
 *  template's with each part it spells out (`` `widget${'_'}action` ``), a `+` of parts joined, and a
 *  `.join` of an array of parts (`['widget', 'action'].join('_')`), with `${…}` for each part that is
 *  not spelled out. null when `node` makes no string the source spells any of (a `+` of numbers, a
 *  name alone). */
export function wordsOf(node: ts.Node): string | null {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text
  if (ts.isTemplateExpression(node)) return node.head.text + node.templateSpans.map((s) => (wordsOf(s.expression) ?? '${…}') + s.literal.text).join('')
  if (ts.isParenthesizedExpression(node)) return wordsOf(node.expression)
  if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === 'join'
    && ts.isArrayLiteralExpression(node.expression.expression) && node.arguments.length <= 1) {
    const parts = node.expression.expression.elements.map((el) => wordsOf(el))
    if (parts.every((w) => w === null)) return null
    const sep = node.arguments[0] ? (wordsOf(node.arguments[0]) ?? '${…}') : ','
    return parts.map((w) => w ?? '${…}').join(sep)
  }
  if (isPlus(node)) {
    const left = wordsOf(node.left)
    const right = wordsOf(node.right)
    return left === null && right === null ? null : (left ?? '${…}') + (right ?? '${…}')
  }
  return null
}

/** Every string expression in `root` that is not a part of a larger one: a string or template, or a
 *  `+` chain read whole (`'&' + 'widget_action=' + n` is one, not three). */
export function stringsOf(root: ts.Node): { node: ts.Node; words: string }[] {
  const out: { node: ts.Node; words: string }[] = []
  for (const node of nodesOf(root)) {
    const words = wordsOf(node)
    if (words === null || ts.isTemplateHead(node)) continue
    let up = node.parent
    while (up && ts.isParenthesizedExpression(up)) up = up.parent
    if (up && isPlus(up) && wordsOf(up) !== null) continue
    out.push({ node, words })
  }
  return out
}

/** The names a binding binds: `q`, or each name a `{ a, b: c }` or `[d, ...e]` pattern takes. */
function boundNames(name: ts.BindingName): string[] {
  if (ts.isIdentifier(name)) return [name.text]
  return name.elements.flatMap((e) => (ts.isOmittedExpression(e) ? [] : boundNames(e.name)))
}

/** The declaration in `scope` itself (not in a function inside it) that binds `name`, or null. */
function declaredIn(scope: ts.Node, name: string): ts.Node | null {
  const lists: ts.VariableDeclarationList[] = []
  if (ts.isForStatement(scope) || ts.isForOfStatement(scope) || ts.isForInStatement(scope)) {
    if (scope.initializer && ts.isVariableDeclarationList(scope.initializer)) lists.push(scope.initializer)
  }
  if (ts.isCatchClause(scope) && scope.variableDeclaration && boundNames(scope.variableDeclaration.name).includes(name)) {
    return scope.variableDeclaration
  }
  const statements = ts.isSourceFile(scope) || ts.isBlock(scope) || ts.isModuleBlock(scope) ? scope.statements
    : ts.isCaseClause(scope) || ts.isDefaultClause(scope) ? scope.statements : []
  for (const st of statements) {
    if (ts.isVariableStatement(st)) lists.push(st.declarationList)
    else if ((ts.isFunctionDeclaration(st) || ts.isClassDeclaration(st) || ts.isEnumDeclaration(st)) && st.name?.text === name) return st
    else if (ts.isImportDeclaration(st) && st.importClause) {
      const c = st.importClause
      if (c.name?.text === name) return c
      const b = c.namedBindings
      if (b && ts.isNamespaceImport(b) && b.name.text === name) return b
      if (b && ts.isNamedImports(b)) for (const el of b.elements) if (el.name.text === name) return el
    }
  }
  for (const list of lists) for (const d of list.declarations) if (boundNames(d.name).includes(name)) return d
  return null
}

/** Where the name `id` reads is bound: the nearest declaration of it in the scopes around it, a
 *  parameter of an enclosing function or a declaration in an enclosing block or the file. null when
 *  nothing in the file binds it. */
export function bindingOf(id: ts.Identifier): ts.Node | null {
  for (let scope: ts.Node | undefined = id.parent; scope; scope = scope.parent) {
    if (ts.isFunctionLike(scope)) {
      for (const p of scope.parameters) if (boundNames(p.name).includes(id.text)) return p
    }
    const found = declaredIn(scope, id.text)
    if (found) return found
  }
  return null
}

/** An expression with its parentheses, `as` casts, `!` and a `?? {}` or `|| {}` fallback taken off:
 *  what `(query ?? {}) as Record<string, string>` reads. */
export function bare(node: ts.Expression): ts.Expression {
  for (;;) {
    if (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isNonNullExpression(node) || ts.isSatisfiesExpression(node)) node = node.expression
    else if (ts.isBinaryExpression(node) && [ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken].includes(node.operatorToken.kind)
      && ts.isObjectLiteralExpression(bare(node.right))) node = node.left
    else return node
  }
}

/** What `expr` holds as the source spells it, through a name bound to it once (`const K = 'seed'`,
 *  `const init = { seed: s }`): the expression itself, or the bound name's initializer, followed
 *  through names and the wrappers `bare` takes off. */
export function heldBy(expr: ts.Expression, depth = 0): ts.Expression {
  const e = bare(expr)
  if (!ts.isIdentifier(e) || depth >= 4) return e
  const bound = bindingOf(e)
  return bound && ts.isVariableDeclaration(bound) && ts.isIdentifier(bound.name) && bound.initializer ? heldBy(bound.initializer, depth + 1) : e
}

/** The string `expr` holds when the source spells all of it (through a name bound to it), else null. */
export function spelledOf(expr: ts.Expression): string | null {
  const words = wordsOf(heldBy(expr))
  return words !== null && !words.includes('${…}') ? words : null
}
