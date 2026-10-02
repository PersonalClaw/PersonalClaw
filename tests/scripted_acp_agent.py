"""A scripted ACP agent for tests: it speaks the Agent Client Protocol over stdio.

Run as ``python scripted_acp_agent.py <scenario> <record.jsonl> [spec|id-label]``. It answers
``initialize``,
``session/new`` and the session configuration requests the way an ACP agent does, then plays
one scripted turn per ``session/prompt``. Every frame it receives is appended to the record
file, one JSON object per line, together with a ``spawn`` line per process, so a test can read
what PersonalClaw put on the wire and how many agent processes it started. Permission options go
out keyed the way the ACP specification spells them (``optionId``/``name``), or ``id``/``label``
for an agent that speaks the older shape.

Scenarios (each turn asks to run one command first):

``deny-continues``
    Offers two refusals, the turn-ending one FIRST: ``cancel`` ("No, and tell me what to do
    differently") ends the turn, ``decline`` ("No, continue without running it") lets the agent
    go on and answer without the command. Answering ``cancelled`` ends the turn too.
``deny-ends``
    Offers one refusal; the agent ends its turn (``stopReason: cancelled``) when it is used.
``wait-for-stop``
    Asks, then waits. A ``session/cancel`` ends the turn with ``stopReason: cancelled``.
``ignores-stop``
    Asks, then waits, and never answers a ``session/cancel``: only killing it ends the turn.
``session-refused``
    Refuses ``session/new`` with a JSON-RPC error that carries a message and data, says why on
    stderr, and exits with code 64 — an engine it starts refusing an argument it was handed.
``dies-mid-turn``
    Starts the turn, says why it is leaving on stderr, and exits with code 3 before answering.
``slow-answer``
    Its first turn runs a step, then thinks for :data:`SLOW_SECONDS` in silence before it
    answers (:data:`LATE_ANSWER`); every later turn answers at once (:data:`NEXT_ANSWER`).
``answers``
    Answers every prompt at once (:data:`PLAIN_ANSWER`), asking nothing.

Every scenario advertises ``loadSession`` and answers ``session/load``, so a resume of a session
it served is recorded like any other request.

Standard library only, and nothing outside its two arguments is read or written.
"""

from __future__ import annotations

import json
import os
import sys
import time

SESSION_ID = "scripted-session-1"
PERMISSION_ID = 900
#: What ``session-refused`` prints and answers.
REFUSAL_STDERR = "engine: refusing the argument --example-unsupported-flag"
REFUSAL_MESSAGE = "Internal error"
REFUSAL_DATA = "The engine process exited with code 64"
#: How long ``slow-answer`` thinks after its step, and what it then says.
SLOW_SECONDS = 1.5
LATE_ANSWER = "The review, after a long think: the change is a version bump."
NEXT_ANSWER = "This is the answer to the second question."
#: What ``answers`` says to every prompt.
PLAIN_ANSWER = "The alerts are the carrier adapter timing out; here is what to check first."
#: What ``dies-mid-turn`` prints before it exits.
DYING_STDERR = "engine: the connection to its service was reset"

_OPTIONS = {
    "deny-continues": [
        {
            "optionId": "cancel",
            "name": "No, and tell me what to do differently",
            "kind": "reject_once",
        },
        {"optionId": "accept", "name": "Yes, run it", "kind": "allow_once"},
        {"optionId": "decline", "name": "No, continue without running it", "kind": "reject_once"},
    ],
    "deny-ends": [
        {"optionId": "allow", "name": "Yes", "kind": "allow_once"},
        {"optionId": "no", "name": "No", "kind": "reject_once"},
    ],
    "wait-for-stop": [
        {"optionId": "allow_once", "name": "Yes", "kind": "allow_once"},
        {"optionId": "reject_once", "name": "No", "kind": "reject_once"},
    ],
}
_OPTIONS["ignores-stop"] = _OPTIONS["wait-for-stop"]

ANSWER_WITHOUT_THE_COMMAND = (
    "I did not run the command, so this review reads the commit message only."
)


class Agent:
    def __init__(self, scenario: str, record_path: str, keys: str = "spec") -> None:
        self.scenario = scenario
        self.keys = keys
        self.record = open(record_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        self.prompt_id: object = None
        self.prompts_seen = 0
        self.log("spawn")

    def log(self, kind: str, **fields: object) -> None:
        self.record.write(json.dumps({"kind": kind, "pid": os.getpid(), **fields}) + "\n")

    @staticmethod
    def send(frame: dict) -> None:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", **frame}) + "\n")
        sys.stdout.flush()

    def update(self, update: dict) -> None:
        self.send(
            {
                "method": "session/update",
                "params": {"sessionId": SESSION_ID, "update": update},
            }
        )

    def say(self, text: str) -> None:
        self.update(
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}
        )

    def end_turn(self, stop_reason: str) -> None:
        if self.prompt_id is not None:
            self.send({"id": self.prompt_id, "result": {"stopReason": stop_reason}})
            self.prompt_id = None

    # ── one scripted turn ──────────────────────────────────────────────────────
    def start_turn(self, request_id: object) -> None:
        self.prompt_id = request_id
        self.update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "call-1",
                "title": "Run command",
                "kind": "execute",
                "status": "pending",
                "rawInput": {"command": ["git", "show", "--stat", "HEAD"]},
            }
        )
        self.send(
            {
                "id": PERMISSION_ID,
                "method": "session/request_permission",
                "params": {
                    "sessionId": SESSION_ID,
                    "toolCall": {"toolCallId": "call-1", "kind": "execute", "status": "pending"},
                    "options": [self.keyed(o) for o in _OPTIONS[self.scenario]],
                },
            }
        )

    def keyed(self, option: dict) -> dict:
        if self.keys != "id-label":
            return dict(option)
        return {"id": option["optionId"], "label": option["name"], "kind": option["kind"]}

    def permission_answered(self, result: dict) -> None:
        outcome = (result or {}).get("outcome") or {}
        chosen = outcome.get("optionId", "")
        self.log("permission_answer", outcome=outcome.get("outcome", ""), option=chosen)
        if self.prompt_id is None or self.scenario == "ignores-stop":
            return  # the turn already ended (a cancel got here first), or it never ends
        offered = {o["optionId"]: o for o in _OPTIONS[self.scenario]}
        kind = offered.get(chosen, {}).get("kind", "")
        if outcome.get("outcome") != "selected" or not kind:
            self.end_turn("cancelled")
        elif kind.startswith("allow"):
            self.update(
                {"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "completed"}
            )
            self.update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "I ran it."},
                }
            )
            self.end_turn("end_turn")
        elif self.scenario == "deny-continues" and chosen == "decline":
            self.update(
                {"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "failed"}
            )
            self.update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": ANSWER_WITHOUT_THE_COMMAND},
                }
            )
            self.end_turn("end_turn")
        else:
            self.end_turn("cancelled")

    # ── the protocol loop ──────────────────────────────────────────────────────
    def handle(self, frame: dict) -> None:
        method = frame.get("method")
        req_id = frame.get("id")
        params = frame.get("params") or {}
        self.log(
            "received", method=method or "", id=req_id, params=params, result=frame.get("result")
        )
        if method is None:
            if req_id == PERMISSION_ID:
                self.permission_answered(frame.get("result") or {})
            return
        if method == "initialize":
            self.send(
                {
                    "id": req_id,
                    "result": {
                        "protocolVersion": params.get("protocolVersion", 1),
                        "agentCapabilities": {"loadSession": True},
                    },
                }
            )
        elif method == "session/new" and self.scenario == "session-refused":
            sys.stderr.write(REFUSAL_STDERR + "\n")
            sys.stderr.flush()
            error = {"code": -32603, "message": REFUSAL_MESSAGE, "data": {"details": REFUSAL_DATA}}
            self.send({"id": req_id, "error": error})
            sys.exit(64)
        elif method == "session/new":
            self.send({"id": req_id, "result": {"sessionId": SESSION_ID}})
        elif method == "session/prompt" and self.scenario == "dies-mid-turn":
            self.update(
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "Reading the commit"},
                }
            )
            sys.stderr.write(DYING_STDERR + "\n")
            sys.stderr.flush()
            sys.exit(3)
        elif method == "session/prompt" and self.scenario == "slow-answer":
            self.prompt_id = req_id
            if self.prompts_seen:
                self.say(NEXT_ANSWER)
            else:
                self.update(
                    {
                        "sessionUpdate": "tool_call",
                        "toolCallId": "call-1",
                        "title": "Read the log",
                        "kind": "read",
                        "status": "completed",
                    }
                )
                time.sleep(SLOW_SECONDS)
                self.say(LATE_ANSWER)
            self.prompts_seen += 1
            self.end_turn("end_turn")
        elif method == "session/prompt" and self.scenario == "answers":
            self.prompt_id = req_id
            self.say(PLAIN_ANSWER)
            self.end_turn("end_turn")
        elif method == "session/prompt":
            self.start_turn(req_id)
        elif method == "session/load":
            self.send({"id": req_id, "result": {"modes": {"availableModes": []}}})
        elif method == "session/cancel":
            if self.scenario != "ignores-stop":
                self.end_turn("cancelled")
        elif method.startswith("session/set_"):
            self.send({"id": req_id, "result": {}})
        elif req_id is not None:
            self.send({"id": req_id, "error": {"code": -32601, "message": f"no {method}"}})

    def run(self) -> None:
        for line in iter(sys.stdin.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if isinstance(frame, dict):
                self.handle(frame)


if __name__ == "__main__":
    Agent(*sys.argv[1:4]).run()
