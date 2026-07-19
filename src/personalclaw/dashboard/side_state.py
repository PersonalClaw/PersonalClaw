"""Side-chat ephemeral buffer.

A side chat is a non-blocking Q&A against a frozen read-only snapshot of a
parent session. Its messages live ONLY here — never in ``_ChatSession.messages``,
the JSONL transcript, or any memory store. Dropping this buffer (on close)
destroys all side state; nothing is persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SideMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class SideState:
    """Per-parent-session side-chat buffer. Created on /side open, dropped on close."""

    open: bool = False
    messages: list[SideMessage] = field(default_factory=list)
    # Identifies the in-flight side turn; a late ``side_result`` frame whose
    # run_id != last_run_id is stale (the turn was superseded or closed).
    last_run_id: str = ""
    # True once the current turn's final delta has been emitted.
    is_complete: bool = True

    def append(self, role: str, content: str) -> None:
        self.messages.append(SideMessage(role=role, content=content))

    def to_dict(self) -> dict:
        return {
            "open": self.open,
            "messages": [{"role": m.role, "content": m.content} for m in self.messages],
            "last_run_id": self.last_run_id,
            "is_complete": self.is_complete,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SideState":
        """Rebuild a side buffer from persisted meta (chat_persistence sidecar).
        Restored buffers are always settled (is_complete=True, no in-flight run)."""
        msgs = d.get("messages") if isinstance(d, dict) else None
        out = cls(open=bool(d.get("open")) if isinstance(d, dict) else False)
        if isinstance(msgs, list):
            for m in msgs:
                if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
                    out.append(str(m["role"]), str(m.get("content", "")))
        return out
