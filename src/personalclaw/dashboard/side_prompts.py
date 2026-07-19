"""Side-chat prompt envelope.

The side turn answers against a frozen snapshot of the parent conversation. The
boundary prompt tells the model it is in a read-only side channel: answer the
side question, do NOT continue the parent task, do NOT call tools.
"""

from __future__ import annotations

# Fallback used only if the prompt system can't resolve the bound prompt (e.g. a
# stripped-down environment). The live envelope is the bundled ``task-side-chat``
# prompt, bindable in Settings → Prompts.
_SIDE_SYSTEM_FALLBACK = (
    "You are answering a SIDE QUESTION about an ongoing conversation. "
    "Below is a read-only snapshot of that conversation, followed by the user's "
    "side question. Answer the side question concisely and directly. "
    "Do NOT continue or resume the main task. Do NOT call any tools or take any "
    "actions — this is a read-only side channel. If the question can't be answered "
    "from the snapshot, say so briefly."
)


def build_side_prompt(snapshot: str, question: str, prior_side: str = "") -> str:
    """Assemble the full side-turn prompt: system envelope + parent snapshot +
    any prior side Q&A in this side chat + the new question.

    The system envelope is rendered from the prompt system (bundled
    ``task-side-chat``), falling back to the inline text if it can't resolve."""
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    system = render_use_case_prompt("side_chat", {}) or _SIDE_SYSTEM_FALLBACK
    parts = [system, "", "=== CONVERSATION SNAPSHOT (read-only) ===", snapshot]
    if prior_side.strip():
        parts += ["", "=== EARLIER IN THIS SIDE CHAT ===", prior_side]
    parts += ["", "=== SIDE QUESTION ===", question]
    return "\n".join(parts)
