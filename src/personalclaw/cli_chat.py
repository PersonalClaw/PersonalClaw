"""``personalclaw chat`` — your assistant in the terminal, in a chat of the running gateway.

The terminal is a client of the gateway's chat, as ``personalclaw run`` is, and reaches it the
same way (``cli_run``: the liveness probe, the token minted with the home's local secret, the
turn socket). The chat is opened with ``POST /api/chat/sessions``, as the dashboard's New chat
opens one, and each message is a turn of it, posted with ``POST /api/chat`` and streamed back over
``/api/ws``. So the turn engine assembles every turn as it does any chat's, with the name you gave
your assistant, what it remembers and the platform's safety rules, and the chat stays in the
dashboard's list afterwards, where it can go on.

The chat is attended, where ``run``'s turn is not. It is an ordinary chat, under the interactive
posture and the approval mode a new chat gets, so a call that asks for approval asks you: it is
held in the gateway's approval registry, where the dashboard, your phone and a chat channel your
approvals go to list it and answer it, and the terminal says what is waiting and how it ended.
``run``'s ``inbound:cli:`` turn is unattended and declines what it cannot ask.

The agent works in the gateway, not in this process, so letting go of the terminal would leave
it working. A Ctrl-C during a turn stops the turn in the gateway too.

With no gateway running there is no chat to talk to: it says so, with the command that starts
one, and exits 1.
"""

from __future__ import annotations

import asyncio
import sys
import time
import urllib.parse
from typing import Any

from personalclaw import cli_run
from personalclaw.approval_brief import RISK_LABELS
from personalclaw.cli_run import RunError
from personalclaw.config import loader as config_loader
from personalclaw.constants import BANNER, DATA_WARNING
from personalclaw.textfmt import clip_words

#: What ends the interactive chat, besides Ctrl-D.
_EXIT_WORDS = frozenset({"exit", "quit", "/exit", "/quit", ":q"})

#: How an approval ended — the ``outcome`` its ``approval_resolved`` frame carries, one of the four
#: the channel contract names — in the word the Inbox titles a settled approval with.
_ENDED = {
    "approved": "Approved",
    "rejected": "Denied",
    "expired": "Expired",
    "cancelled": "Cancelled",
}

#: Where an approval is answered: every one is listed in the dashboard and on your phone, and a
#: chat channel asks too when your approvals go to one.
_ANSWER_IT = (
    "Answer it in PersonalClaw (the dashboard or your phone) or on your paired chat channel."
)

#: How long a turn stopped with Ctrl-C is followed until the gateway says it has ended.
_STOP_WAIT_SECS = 30.0

#: How old the chat's token may be when a request starts: a quarter of an hour short of the hour
#: ``run``'s token lasts (``cli_run._TOKEN_TTL``). A chat can stay open all day.
_REFRESH_AFTER_SECS = 45 * 60


def config_path():
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


def _chat(args) -> None:
    """``personalclaw chat`` — dispatched from ``cli.main``."""
    raise SystemExit(_chat_main(args))


def _chat_main(args) -> int:
    """One message (``-m``) or the interactive chat. Returns the exit status: 0 when the message's
    turn completed (or the interactive chat was left), 1 when there is no gateway to chat with or
    a turn did not complete, 2 for a blank ``-m``."""
    message = getattr(args, "message", None)
    if message is not None and not message.strip():
        print("personalclaw chat: -m/--message must be a non-empty message.", file=sys.stderr)
        return 2

    from personalclaw.cli_server import resolve_client_port

    port = resolve_client_port(getattr(args, "port", None))
    if not cli_run.probe_gateway(port):
        print(_no_gateway(port), file=sys.stderr)
        return 1
    model = getattr(args, "model", None) or ""
    sign_in = _SignIn(port)
    try:
        sign_in.token()  # a home this process does not share is refused before anything else
        if message is None:
            return _interactive(sign_in, model)
        key = _open_chat(sign_in, model)
        return 0 if _say(sign_in, key, message.strip()) == "complete" else 1
    except RunError as exc:
        _failed(port, exc)
        return 1
    except KeyboardInterrupt:
        # A second Ctrl-C while a stopped turn was still ending: the first one already asked the
        # gateway to stop it.
        print(file=sys.stderr)
        return 1


def _failed(port: int, exc: RunError) -> None:
    """Say why a request to the gateway failed: a gateway that has gone away is said as it is
    when there was none to begin with, with how to start it."""
    gone = not cli_run.probe_gateway(port, attempts=1)
    print(_no_gateway(port) if gone else f"personalclaw chat: {exc}", file=sys.stderr)


def _no_gateway(port: int) -> str:
    """Why there is nothing to chat with, and the command that starts it: the service installed
    for this home when there is one (``restart`` starts it), else ``gateway`` — what ``status``
    says of a gateway that is not running."""
    try:
        from personalclaw.service import controller as service_controller

        installed = service_controller.this_homes_service() is not None
    except Exception:  # noqa: BLE001 — naming the start command must not hide the refusal
        installed = False
    start = "personalclaw restart" if installed else "personalclaw gateway"
    return (
        f"personalclaw chat: no gateway is running on port {port}, and your chat runs in it.\n"
        f"  Start it with: {start}"
    )


class _SignIn:
    """The token the chat's requests carry: ``run``'s, minted with the home's local secret
    (:func:`~personalclaw.cli_run.mint_local_token`), and minted again before a request once it
    is :data:`_REFRESH_AFTER_SECS` old. A turn's socket is signed in once, when it opens."""

    def __init__(self, port: int) -> None:
        self.port = port
        self._token = ""
        self._minted = 0.0

    def token(self) -> str:
        if not self._token or time.monotonic() - self._minted >= _REFRESH_AFTER_SECS:
            self._token = cli_run.mint_local_token(self.port)
            self._minted = time.monotonic()
        return self._token


def _open_chat(sign_in: _SignIn, model: str) -> str:
    """Open a chat in the gateway, as the dashboard's New chat does, and return its key. *model*
    is the model for this chat; empty, the chat model bound in Settings → Models."""
    created = cli_run._api(
        sign_in.port, sign_in.token(), "/api/chat/sessions", {"model": model} if model else {}
    )
    key = str(created.get("key") or "")
    if not key:
        raise RunError("the gateway opened no chat")
    return key


def _interactive(sign_in: _SignIn, model: str) -> int:
    """Each line you type is the next turn of one chat. The chat is opened by your first message,
    so a prompt left without one leaves no empty chat in the dashboard's list."""
    print(BANNER)
    print(DATA_WARNING)
    print()
    print("Type your message (Ctrl+D or 'exit' to quit)\n")
    key = ""
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return 0
        if not message:
            continue
        if message.lower() in _EXIT_WORDS:
            print("Bye!")
            return 0
        try:
            key = key or _open_chat(sign_in, model)
            _say(sign_in, key, message)
        except RunError as exc:
            _failed(sign_in.port, exc)
        print()


def _say(sign_in: _SignIn, key: str, message: str) -> str:
    """Post *message* as the next turn of chat *key*, show the turn until it ends, and return how
    it ended: ``complete``, ``stopped``, ``error`` or ``interrupted``, as its ``chat_done`` says.
    """
    turn = _Turn(key)
    try:
        asyncio.run(_converse(sign_in, turn, message))
    finally:
        turn.close_line()
    turn.say_how_it_ended()
    return turn.outcome


async def _converse(sign_in: _SignIn, turn: _Turn, message: str) -> None:
    """Post *message* as the turn's message and show the turn until the gateway says it ended.

    A cancel while it runs is a Ctrl-C (``asyncio.run`` turns the first one into a cancel of this
    task, its only caller), so it stops the turn in the gateway and goes on showing it until it has
    ended there: the call it was waiting on is cancelled and never runs.
    """
    port = sign_in.port
    async with cli_run._turn_socket(port, sign_in.token(), turn.session_key, message) as (http, ws):
        try:
            await cli_run._read_turn(ws, turn, None)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            turn.note("Stopping the turn.")
            quoted = urllib.parse.quote(turn.session_key, safe="")
            # The turn may have run longer than the token it was posted with lasts.
            fresh = cli_run.owner_headers(await asyncio.to_thread(sign_in.token))
            async with http.post(
                f"http://127.0.0.1:{port}/api/chat/sessions/{quoted}/stop", json={}, headers=fresh
            ) as resp:
                if resp.status != 200:
                    raise RunError(f"the gateway did not stop the turn: HTTP {resp.status}")
            try:
                await cli_run._read_turn(ws, turn, _STOP_WAIT_SECS)
            except RunError:
                turn.note(
                    "The gateway is still stopping the turn; the chat in the dashboard shows "
                    "when it has stopped."
                )


class _Turn(cli_run._Collector):
    """One turn of the chat as the terminal shows it.

    The answer goes to stdout as it streams. What waits for your decision, how that ended, and the
    turn's notices and errors go to stderr, so ``chat -m … > reply.txt`` keeps the answer alone.
    How the turn ended is read as ``run`` reads it (:class:`~personalclaw.cli_run._Collector`).
    """

    def __init__(self, session_key: str) -> None:
        super().__init__(session_key, "plain")
        #: Whether stdout's last line is still open: an answer streams without a newline.
        self._open = False
        #: The approvals this turn asked for, by registry id, and the tool each is about.
        self._asked: dict[str, str] = {}
        #: The rows already shown, as (role, text): the gateway sends some of a turn's twice.
        self._shown: set[tuple[str, str]] = set()

    def feed(self, envelope: dict) -> None:
        data = envelope.get("data")
        if isinstance(data, dict) and data.get("session") == self.session_key:
            self._show(str(envelope.get("type", "")), data)
        super().feed(envelope)

    def _show(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "chat_chunk":
            text = str(data.get("content", ""))
            if text:
                sys.stdout.write(text)
                sys.stdout.flush()
                self._open = not text.endswith("\n")
        elif kind in ("chat_segment", "tool_call"):
            # The answer so far is settled: what streams after a call is a new paragraph.
            self.close_line()
        elif kind == "approval":
            approval = str(data.get("id") or "")
            if approval and approval not in self._asked:
                self._asked[approval] = str(data.get("tool") or "")
                self.note(_asks(data))
        elif kind == "approval_resolved":
            approval = str(data.get("id") or "")
            if approval in self._asked:
                self.note(_ended(self._asked.pop(approval), data))
        elif kind == "chat_message":
            self._row(str(data.get("role", "")), str(data.get("content", "")).strip())

    def _row(self, role: str, text: str) -> None:
        """A row the gateway added to the chat: a reply that did not stream (a slash command's),
        a notice, or an error."""
        if role not in ("assistant", "notice", "error") or not text or (role, text) in self._shown:
            return
        self._shown.add((role, text))
        if role == "assistant":
            self.close_line()
            print(text, flush=True)
        else:
            self.note(text)

    def note(self, text: str) -> None:
        """Say *text* on stderr, on a line of its own, in printable characters only: what it says
        of a call is words a model chose (the tool, its input), and a control character in them
        could move the cursor or rewrite what the terminal shows."""
        self.close_line()
        print(_printable(text), file=sys.stderr, flush=True)

    def close_line(self) -> None:
        """End the answer's open line on stdout, if there is one."""
        if self._open:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._open = False

    def say_how_it_ended(self) -> None:
        """Say how the turn ended when it did not complete and none of its rows said why."""
        if self.outcome == "stopped":
            self.note("The turn was stopped before it finished.")
        elif self.outcome != "complete" and not any(r == "error" for r, _ in self._shown):
            self.note("The turn did not finish, and the gateway gave no reason.")


def _asks(entry: dict[str, Any]) -> str:
    """What the terminal says of a call that waits for your decision, from its registry entry: the
    tool and its risk (as the Inbox row names them), why it is called and with what, and where it
    is answered."""
    tool = str(entry.get("tool") or "A tool call")
    risk = RISK_LABELS.get(str(entry.get("risk") or ""), "").lower()
    lines = [f"{tool} is waiting for your decision" + (f" (risk: {risk})." if risk else ".")]
    for detail in (entry.get("tool_purpose"), entry.get("tool_input")):
        text = clip_words(str(detail or ""), 200)
        if text:
            lines.append(f"  {text}")
    lines.append(f"  {_ANSWER_IT}")
    return "\n".join(lines)


def _ended(tool: str, frame: dict[str, Any]) -> str:
    """How an approval ended, from its ``approval_resolved`` frame: "Approved: write_file.", and why
    when nobody answered it ("Expired: write_file (nobody answered within 5 minutes).")."""
    word = _ENDED.get(str(frame.get("outcome") or ""), "Ended")
    why = str(frame.get("ended") or "")
    return f"{word}: {tool or 'the call'}" + (f" ({why})" if why else "") + "."


def _printable(text: str) -> str:
    """*text* with every character a terminal would act on rather than show removed, its line
    breaks kept."""
    return "".join(ch for ch in text if ch == "\n" or ch.isprintable())


def _ensure_default_agent_in_config() -> str | None:
    """Ensure config.json includes a default PersonalClaw agent for fresh installs.

    In the config transaction. An unreadable config.json is left alone, and why is returned for
    `setup` to report: this used to read it as `{}` and write the default agent over every
    setting it held. None when the agent is there.
    """
    from personalclaw.config.loader import ConfigWriteError
    from personalclaw.config.transactions import mutate_config

    def _seed(data: dict) -> None:
        if data.get("agents"):
            return
        data["agents"] = {
            "default": {
                "provider_agent": "personalclaw",
                "workspace": "default",
                "memory_store": "default",
            }
        }
        data["default_agent"] = "default"

    try:
        mutate_config(_seed, path=config_path())
    except ConfigWriteError as exc:
        return f"could not add the default agent: {exc}"
    return None
