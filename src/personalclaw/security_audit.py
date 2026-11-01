"""The scans ``personalclaw security audit`` and ``personalclaw memory audit`` run: recent history's
tool use, read against the suspicious-command patterns (``security.audit_bash_command``), and
memory's entries, read for injection patterns.
"""

from __future__ import annotations

import json
from pathlib import Path

from personalclaw.security import audit_bash_command


def scan_history(history_dir: Path, last_n: int = 100) -> list[dict]:
    """Scan recent conversation history for suspicious tool usage.

    Returns list of findings: [{file, line, tool, command, warning}]
    """
    findings: list[dict] = []
    if not history_dir.is_dir():
        return findings

    files = sorted(history_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    checked = 0
    for f in files:
        try:
            for line in f.read_text().splitlines():
                if checked >= last_n:
                    return findings
                checked += 1
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = entry.get("content", "")
                role = entry.get("role", "")
                if role != "assistant" or not isinstance(content, str):
                    continue
                # Check for bash commands in tool calls
                warning = audit_bash_command(content)
                if warning:
                    findings.append(
                        {
                            "file": f.name,
                            "warning": warning,
                            "snippet": content[:200],
                        }
                    )
        except OSError:
            continue
    return findings


def scan_memory() -> list[dict]:
    """Scan memory for suspicious content via the memory service. Returns findings."""
    from personalclaw.memory_service import MemoryService
    from personalclaw.vector_memory import VectorMemoryStore, _contains_injection

    findings: list[dict] = []
    try:
        store = VectorMemoryStore()
        store.init()
    except Exception:
        return findings
    svc = MemoryService.over_vector_store(store)

    # Scan semantic values
    for entry in svc.get_all_semantic():
        val = entry.get("value_json", "")
        if _contains_injection(val):
            findings.append(
                {
                    "type": "semantic",
                    "key": entry["key"],
                    "value": val[:200],
                    "warning": "Injection pattern detected",
                }
            )

    # Scan episodic texts
    for entry in svc.episodic_list(limit=1000):
        text = entry.get("text", "")
        if _contains_injection(text):
            findings.append(
                {
                    "type": "episodic",
                    "key": entry["id"],
                    "value": text[:200],
                    "warning": "Injection pattern detected",
                }
            )

    store.close()
    return findings
