/** Reading a `"provider_name:model_id"` model ref as something a user can be shown.
 *
 *  **Why this is a module and not an inline expression.** #3528: onboarding's recap told a
 *  first-time user `Chat model: ollama-models` — the name of the *app* that provides the
 *  model, because that is the only thing the flow had persisted about the model lane. The
 *  bound model was right the whole time (`active_models.json` held
 *  `{"chat": ["Local Ollama:qwen2.5vl:7b"]}`); only the sentence was wrong. The fix reads
 *  the live binding instead of a stored proxy for it, and two surfaces on two different
 *  screens have to turn that ref into the same words — so the reading lives in one place
 *  rather than being derived twice and drifting.
 *
 *  🪤 **A model id may itself contain colons** (`qwen2.5vl:7b`, `gpt-oss:20b`), so the
 *  provider/model split is on the FIRST colon only — the same rule
 *  `personalclaw.providers.use_cases.split_ref` applies server-side. Splitting on the last
 *  one turns `Local Ollama:qwen2.5vl:7b` into `7b`.
 */

/** The model half of one ref — what a surface labelled "model" may show.
 *
 *  An UNQUALIFIED ref (no colon) is a bare model id, so it is returned whole: that is the
 *  shape a provider with a pinned model writes, and dropping it would leave the surface
 *  with nothing to say about a binding that exists.
 */
export function modelIdOf(ref: string): string {
  const at = ref.indexOf(':')
  return (at === -1 ? ref : ref.slice(at + 1)).trim()
}

/** The bound chat model's name from an active-model chain, or `''` when nothing names one.
 *
 *  Position 0 is the default and the rest of the chain is fallback (see
 *  `active_model_refs`), so this reports the default — the model an ordinary turn uses.
 *  `''` is the honest answer for an empty chain: resolution then comes from the implicit
 *  "first capable configured provider" rule, and a caller must say so rather than name a
 *  model the user never chose.
 */
export function boundModelLabel(refs: readonly string[] | undefined): string {
  for (const ref of refs ?? []) {
    const label = modelIdOf(ref)
    if (label) return label
  }
  return ''
}
