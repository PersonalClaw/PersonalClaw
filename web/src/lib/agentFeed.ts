/** Helpers that turn a worker/planner's raw streamed WS events into legible
 *  activity-feed lines. Shared by every surface that renders the live agent loop
 *  from `tool_call` + `chat_chunk` frames — the Code/Goal Loop planning
 *  walkthrough AND the Code cockpit's worker-activity panel — so they stay
 *  consistent (a fix in one is a fix in both). */

/** Condense a tool call's raw input into a legible chip. The agent streams tool
 *  args as a JSON blob (`{"path":"plan_steps.json","content":"…"}`); showing that
 *  verbatim turns the activity feed into a wall of escaped JSON. Pull the ONE
 *  field that says what the tool is doing (command / query / pattern / url / path),
 *  falling back to the purpose, then the first non-empty line — capped. */
export function toolDetail(inputPreview: string, purpose: string): string {
  const raw = inputPreview.trim()
  if (raw.startsWith('{')) {
    try {
      const j = JSON.parse(raw) as Record<string, unknown>
      // Action fields (command / query / pattern / url) describe what the tool is
      // DOING and win over a bare path when both are present (a grep over `src` is
      // best summarized by its query, not the dir); a read/write only has a path.
      const key = ['command', 'cmd', 'query', 'pattern', 'url', 'path', 'file_path', 'file', 'name']
        .find((k) => typeof j[k] === 'string' && (j[k] as string).trim())
      if (key) return String(j[key]).replace(/\s+/g, ' ').trim().slice(0, 140)
      // No telltale field — fall back to the purpose rather than dumping the blob.
      if (purpose.trim()) return purpose.trim().slice(0, 140)
      return ''
    } catch {
      // Incomplete mid-stream JSON (a write_file's big `content` value hasn't fully
      // arrived, so JSON.parse throws). Salvage a path/known field with a cheap regex
      // — for write_file the path streams before content, so the filename is already
      // there — then fall back to the purpose. NEVER dump the raw escaped-JSON blob.
      const m = raw.match(/"(?:command|cmd|query|pattern|url|path|file_path|file|name)"\s*:\s*"([^"]+)"/)
      if (m) return m[1].replace(/\s+/g, ' ').trim().slice(0, 140)
      if (purpose.trim()) return purpose.trim().slice(0, 140)
      return ''
    }
  }
  const src = raw || purpose.trim()
  return src.split('\n').map((s) => s.trim()).filter(Boolean)[0]?.slice(0, 140) ?? ''
}

/** Reduce an agent's raw streamed prose to clean narration. Activity feeds render
 *  plain text, but the agent interleaves source the feed shouldn't show verbatim:
 *  HTML/widget markup (it streams an artifact as `<widget …><div …>`), fenced code
 *  blocks (it echoes the JSON file it just wrote), and markdown heading/list/
 *  emphasis markers. Strip all of that. A trailing UNCLOSED ``` fence (mid-stream,
 *  the block hasn't finished arriving) is dropped too so a half-streamed blob
 *  doesn't flash in before it completes. */
export function cleanSay(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')      // closed fenced code blocks → gone
    .replace(/```[\s\S]*$/, ' ')          // trailing unclosed fence (still streaming)
    // Only strip things that look like REAL HTML/widget tags — `<` followed by an
    // optional `/` then a tag-name letter (e.g. <div>, </span>, <widget …>). A bare
    // `/<[^>]*>/` ate prose with comparison operators / generics too ("if x < 5 and y >
    // 3" → "if x  3"; "Array<string>" → "Array"), silently deleting narration. Math/
    // generics where `<` isn't followed by a letter now survive.
    .replace(/<\/?[a-zA-Z][^>]*>/g, ' ')  // HTML/widget tags (not bare < / > in prose)
    .replace(/`([^`]+)`/g, '$1')          // inline code → its text
    .replace(/^#{1,6}\s+/gm, '')          // markdown headings
    .replace(/^\s*[-*+]\s+/gm, '')        // bullet markers
    .replace(/\*\*(.+?)\*\*/g, '$1')      // bold → text (non-greedy; tolerates inner *)
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
    .slice(-1200)
}
