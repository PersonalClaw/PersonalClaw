"""A scripted ACP agent for tests: it speaks the Agent Client Protocol over stdio.

Run as ``python scripted_acp_agent.py <scenario> <record.jsonl> [spec|id-label] [file]``. It
answers ``initialize``,
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
``deny-only-cancel``
    Asks the way an agent asks for an escalation: its ONLY refusal is the turn-ending ``cancel``
    ("No, and tell me what to do differently"), and using it ends the turn.

In both of those, a prompt that follows a refused one is answered without the command
(:data:`ANSWER_AFTER_CARRY_ON`), asking nothing.
``deny-and-give-up``
    Asks as ``deny-only-cancel`` does, and answers a prompt that follows the refused one by
    ending that turn too (``stopReason: cancelled``), with nothing said.
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
``fills-context``
    Answers every prompt at once, :data:`FIRST_REVIEW` first and :data:`LATER_REVIEW` after, and
    reports its context as :data:`FULL_CONTEXT_PCT` full after each answer (the metadata
    notification that carries ``contextUsagePercentage``), asking nothing.
``edits``
    Asks to edit :data:`EDITED_FILE` in the folder it was started in, naming the file the way
    claude-code's adapter does: the ``tool_call`` opens with no input, an update names it in
    ``locations`` and a ``diff``, and the permission request carries only the call's id and
    title. It writes :data:`EDITED_TEXT` there only once the request is answered yes, and leaves
    the file as it was on a refusal.
``writes-a-file``
    Asks to write the file its fourth argument names, and writes :data:`FILE_TEXT` into it only
    when the call is allowed (:data:`WROTE`); refused, it writes nothing and says so
    (:data:`DID_NOT_WRITE`). Told a mode that lets it approve its own calls
    (``session/set_config_option`` with ``configId: mode``, one of :data:`SELF_APPROVING_MODES`), it
    asks nothing and writes the file at once, as an agent CLI in that mode does. Each write is
    recorded as a ``wrote`` line.

Every scenario advertises ``loadSession`` and answers ``session/load``, so a resume of a session
it served is recorded like any other request.

Standard library only. Nothing outside its arguments is read or written (the record file, and
the file ``writes-a-file`` writes when it may), but the one file ``edits`` edits in the folder it
was started in.
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
#: How full ``fills-context`` says its context is after each answer, and what it answers.
FULL_CONTEXT_PCT = 95.0
FIRST_REVIEW = "The change bumps the version in pyproject.toml and nothing else."
LATER_REVIEW = "Next, check that the changelog names the new version."
#: The file ``edits`` edits, in the folder the agent was started in, and its text after the edit.
EDITED_FILE = "plan.md"
EDITED_TEXT = "The plan, rewritten by the agent.\n"
#: What ``writes-a-file`` writes, and what it says after it did or did not.
FILE_TEXT = "Pantry: flour, rice, lentils.\n"
WROTE = "I saved the pantry list."
DID_NOT_WRITE = "I was not allowed to save the pantry list, so I wrote nothing."
#: The modes in which ``writes-a-file`` approves its own calls, as an agent CLI does in them.
SELF_APPROVING_MODES = frozenset({"bypassPermissions", "acceptEdits", "dontAsk"})

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
    "deny-only-cancel": [
        {"optionId": "accept", "name": "Yes, proceed", "kind": "allow_once"},
        {
            "optionId": "cancel",
            "name": "No, and tell me what to do differently",
            "kind": "reject_once",
        },
    ],
    "wait-for-stop": [
        {"optionId": "allow_once", "name": "Yes", "kind": "allow_once"},
        {"optionId": "reject_once", "name": "No", "kind": "reject_once"},
    ],
}
_OPTIONS["ignores-stop"] = _OPTIONS["wait-for-stop"]
_OPTIONS["edits"] = _OPTIONS["wait-for-stop"]
_OPTIONS["deny-and-give-up"] = _OPTIONS["deny-only-cancel"]
_OPTIONS["writes-a-file"] = _OPTIONS["wait-for-stop"]

ANSWER_WITHOUT_THE_COMMAND = (
    "I did not run the command, so this review reads the commit message only."
)
#: What an agent whose turn a refusal ended answers the prompt that follows.
ANSWER_AFTER_CARRY_ON = "Without git show I read the commit message only: it is a version bump."


class Agent:
    def __init__(
        self, scenario: str, record_path: str, keys: str = "spec", target: str = ""
    ) -> None:
        self.scenario = scenario
        self.keys = keys
        self.target = target
        self.record = open(record_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        self.prompt_id: object = None
        self.prompts_seen = 0
        self.refused = False
        #: The permission mode the client set (``session/set_config_option``, ``configId: mode``).
        self.mode = ""
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

    def start_edit(self, request_id: object) -> None:
        self.prompt_id = request_id
        target = os.path.join(os.getcwd(), EDITED_FILE)
        with open(target, encoding="utf-8") as handle:
            before = handle.read()
        title = f"Edit {EDITED_FILE}"
        self.update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "call-1",
                "title": title,
                "kind": "edit",
                "status": "pending",
                "rawInput": {},
            }
        )
        self.update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "call-1",
                "rawInput": {"file_path": target},
                "locations": [{"path": target}],
                "content": [
                    {"type": "diff", "path": target, "oldText": before, "newText": EDITED_TEXT}
                ],
            }
        )
        self.send(
            {
                "id": PERMISSION_ID,
                "method": "session/request_permission",
                "params": {
                    "sessionId": SESSION_ID,
                    "toolCall": {"toolCallId": "call-1", "title": title},
                    "options": [self.keyed(o) for o in _OPTIONS[self.scenario]],
                },
            }
        )

    def finish_edit(self, allowed: bool) -> None:
        if allowed:
            with open(os.path.join(os.getcwd(), EDITED_FILE), "w", encoding="utf-8") as handle:
                handle.write(EDITED_TEXT)
            self.log("edited", path=EDITED_FILE)
        status = "completed" if allowed else "failed"
        self.update({"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": status})
        self.say("I rewrote the plan." if allowed else "I left the plan as it was.")
        self.end_turn("end_turn")

    def keyed(self, option: dict) -> dict:
        if self.keys != "id-label":
            return dict(option)
        return {"id": option["optionId"], "label": option["name"], "kind": option["kind"]}

    # ── ``writes-a-file``'s turn ───────────────────────────────────────────────
    def ask_to_write(self, request_id: object) -> None:
        self.prompt_id = request_id
        self.update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "call-1",
                "title": f"Write {os.path.basename(self.target)}",
                "kind": "edit",
                "status": "pending",
                "rawInput": {"file_path": self.target, "content": FILE_TEXT},
            }
        )
        if self.mode in SELF_APPROVING_MODES:
            self.write_the_file()
            return
        self.send(
            {
                "id": PERMISSION_ID,
                "method": "session/request_permission",
                "params": {
                    "sessionId": SESSION_ID,
                    "toolCall": {"toolCallId": "call-1", "kind": "edit", "status": "pending"},
                    "options": [self.keyed(o) for o in _OPTIONS[self.scenario]],
                },
            }
        )

    def write_the_file(self) -> None:
        with open(self.target, "w", encoding="utf-8") as handle:
            handle.write(FILE_TEXT)
        self.log("wrote", path=self.target)
        self.update(
            {"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "completed"}
        )
        self.say(WROTE)
        self.end_turn("end_turn")

    def write_answered(self, allowed: bool) -> None:
        if allowed:
            self.write_the_file()
            return
        self.update(
            {"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "failed"}
        )
        self.say(DID_NOT_WRITE)
        self.end_turn("end_turn")

    def permission_answered(self, result: dict) -> None:
        outcome = (result or {}).get("outcome") or {}
        chosen = outcome.get("optionId", "")
        self.log("permission_answer", outcome=outcome.get("outcome", ""), option=chosen)
        if self.prompt_id is None or self.scenario == "ignores-stop":
            return  # the turn already ended (a cancel got here first), or it never ends
        offered = {o["optionId"]: o for o in _OPTIONS[self.scenario]}
        kind = offered.get(chosen, {}).get("kind", "")
        if self.scenario == "edits":
            self.finish_edit(outcome.get("outcome") == "selected" and kind.startswith("allow"))
        elif self.scenario == "writes-a-file":
            self.write_answered(outcome.get("outcome") == "selected" and kind.startswith("allow"))
        elif outcome.get("outcome") != "selected" or not kind:
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
            self.refused = True
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
        elif method == "session/prompt" and self.scenario == "fills-context":
            self.prompt_id = req_id
            self.say(LATER_REVIEW if self.prompts_seen else FIRST_REVIEW)
            self.prompts_seen += 1
            self.send(
                {
                    "method": "_vendor.dev/metadata",
                    "params": {"sessionId": SESSION_ID, "contextUsagePercentage": FULL_CONTEXT_PCT},
                }
            )
            self.end_turn("end_turn")
        elif method == "session/prompt" and self.scenario == "edits":
            self.start_edit(req_id)
        elif method == "session/prompt" and self.scenario == "writes-a-file":
            self.ask_to_write(req_id)
        elif method == "session/prompt" and self.refused:
            self.prompt_id = req_id
            if self.scenario == "deny-and-give-up":
                self.end_turn("cancelled")
            else:
                self.say(ANSWER_AFTER_CARRY_ON)
                self.end_turn("end_turn")
        elif method == "session/prompt":
            self.start_turn(req_id)
        elif method == "session/load":
            self.send({"id": req_id, "result": {"modes": {"availableModes": []}}})
        elif method == "session/cancel":
            if self.scenario != "ignores-stop":
                self.end_turn("cancelled")
        elif method.startswith("session/set_"):
            if params.get("configId") == "mode":
                self.mode = str(params.get("value") or "")
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
    Agent(*sys.argv[1:5]).run()
