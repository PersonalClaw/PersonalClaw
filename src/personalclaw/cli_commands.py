"""CLI subcommand handlers — cron, spawn, app, agent, security, eval, learn, memory."""

import argparse
import json
import sys
import time as _time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from personalclaw.atomic_write import atomic_json_write, write_private_file
from personalclaw.cli_run import RunError, mint_local_token, owner_headers, probe_gateway
from personalclaw.config import config_dir
from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.eval.judge import LLMJudge
from personalclaw.eval.runner import EvalRunner, format_results, score_by_dimension
from personalclaw.eval.scenario import AssertionType, load_scenario, load_scenarios
from personalclaw.hooks import safe_read_file
from personalclaw.security import (
    BUILTIN_DENY_PATTERNS,
    redact_credentials,
    redact_exfiltration_urls,
    scan_history,
    scan_memory,
)
from personalclaw.sel import sel
from personalclaw.vector_memory import NO_INDEX_NOTE, VectorMemoryStore


def _refuse(sentence: str) -> NoReturn:
    """Say on stderr why the command did nothing, and exit 1.

    A command that refuses and exits 0 reads as success to every script that runs it:
    `personalclaw cron resume <id>` printed "…so it was not switched on" and exited 0, so
    `cron resume <id> && …` carried on as if the trigger were on. `NoReturn`, so a caller cannot
    read the line after it as reachable.
    """
    print(sentence, file=sys.stderr)
    sys.exit(1)


def _usage_error(sentence: str) -> NoReturn:
    """Say on stderr what the command line is missing, and exit 2, the usage-error status.

    The status argparse gives a command line it cannot parse, for the checks only the command
    can make: an argument given as an empty string, "at least one field", one of three flags.
    """
    print(sentence, file=sys.stderr)
    sys.exit(2)


def _spawn(args: argparse.Namespace) -> None:
    """Dispatch spawn subcommands: run, list.

    Authenticates through the same trio ``personalclaw run`` uses rather than inventing
    a second mechanism (#2947): ``probe_gateway`` for liveness (it hits ``/api/healthz``,
    which ``token_auth`` always bypasses, so a 403 from an auth-on gateway can never be
    misread as absent) and ``mint_local_token`` for the credential itself (reads the
    shared ``.local_secret`` and exchanges it at ``/api/token/local`` — the same handshake
    ``personalclaw token``/``status``/``logout`` use). The minted token then rides every
    request in the ``Authorization: Bearer`` header (``owner_headers``), never in the URL.

    Before this fix ``_spawn`` sent no credential at all, so a 403 from the default
    auth-on gateway raised ``urllib.error.HTTPError`` (a ``URLError`` subclass) and landed
    on the "gateway not running" arm below — reporting a running gateway as down.
    """
    action = getattr(args, "spawn_action", None)
    port = args.port
    base = f"http://localhost:{port}"

    if not probe_gateway(port):
        print(
            "Error: gateway not running (cannot reach dashboard on port %d)" % port, file=sys.stderr
        )
        sys.exit(1)
    try:
        token = mint_local_token(port)
    except RunError as exc:
        # Liveness is confirmed (probe_gateway passed above) — a mint failure here is a
        # DIFFERENT fact than "not running" (e.g. this process does not share the
        # gateway's PERSONALCLAW_HOME, so it holds no `.local_secret`) and must read as one.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if action == "list":
        _spawn_list(base, port, token)
    elif action == "run":
        _spawn_run(args, base, port, token)


def _spawn_list(base: str, port: int, token: str) -> None:
    """``spawn list`` — GET ``/api/spawn`` carrying the caller's minted token."""
    try:
        req = urllib.request.Request(f"{base}/api/spawn", headers=owner_headers(token))
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            print(f"Error: {body.get('error', e.reason)}", file=sys.stderr)
        except Exception:
            print(f"Error: {e.code} {e.reason}", file=sys.stderr)
        sys.exit(1)
    except (urllib.error.URLError, OSError):
        print(
            "Error: gateway not running (cannot reach dashboard on port %d)" % port, file=sys.stderr
        )
        sys.exit(1)
    agents = data.get("agents", [])
    if not agents:
        print("No subagents.")
        return
    for a in agents:
        status = "✅" if a.get("done") else "⏳"
        print(f"  {status} {a['id']}  {a.get('task', '')[:60]}")


def _spawn_run(args: argparse.Namespace, base: str, port: int, token: str) -> None:
    """Spawn a subagent via the dashboard API."""
    data = json.dumps({"task": args.task}).encode()
    req = urllib.request.Request(
        f"{base}/api/spawn",
        data=data,
        headers={"Content-Type": "application/json", **owner_headers(token)},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            print(f"Error: {body.get('error', e.reason)}", file=sys.stderr)
        except Exception:
            print(f"Error: {e.code} {e.reason}", file=sys.stderr)
        sys.exit(1)
    except (urllib.error.URLError, OSError):
        print(
            "Error: gateway not running (cannot reach dashboard on port %d)" % port, file=sys.stderr
        )
        sys.exit(1)

    agent_id = result["id"]

    if args.fire_and_forget:
        print(f"Spawned subagent {agent_id}: {result['task']}")
        return

    # Block: poll until done

    print(f"Spawned subagent {agent_id}, waiting for result...", file=sys.stderr)
    poll = urllib.request.Request(f"{base}/api/spawn/{agent_id}", headers=owner_headers(token))
    while True:
        _time.sleep(2)
        try:
            with urllib.request.urlopen(poll, timeout=5) as resp:
                status = json.loads(resp.read())
        except Exception:
            print("Error: lost connection to gateway", file=sys.stderr)
            sys.exit(1)
        if status.get("done"):
            if status.get("error"):
                print(f"Error: {status['error']}", file=sys.stderr)
                sys.exit(1)
            print(status.get("result", ""))
            return


_AGENT_TABLE_HEADERS = ("NAME", "PROVIDER_AGENT", "DEFAULT_DIR", "MEMORY_STORE")


def render_agent_table(cfg: AppConfig) -> str:
    """Render ``personalclaw agent list`` with widths measured from the rows (#2949).

    Fixed widths misaligned the table on a fresh install, because three of the
    shipped default agent names are longer than the old 20-char NAME column. The
    default marker is part of the measured NAME cell, so it cannot eat the name's
    budget either.
    """

    rows = [
        (
            name + (" *" if name == cfg.default_agent else ""),
            agent.provider_agent,
            agent.default_dir,
            agent.memory_store,
        )
        for name, agent in cfg.agents.items()
    ]
    widths = [
        max(len(head), *(len(row[i]) for row in rows)) if rows else len(head)
        for i, head in enumerate(_AGENT_TABLE_HEADERS)
    ]
    lines = []
    for cells in (_AGENT_TABLE_HEADERS, *rows):
        padded = [f"{cell:<{widths[i]}}" for i, cell in enumerate(cells[:-1])]
        lines.append(" ".join([*padded, cells[-1]]).rstrip())
    return "\n".join(lines)


def _handle_agent(args: argparse.Namespace) -> None:
    """Dispatch agent subcommands: list, create, update, delete."""

    action = getattr(args, "agent_action", None)
    cfg = AppConfig.load()

    if action == "list":
        print(render_agent_table(cfg))

    elif action == "create":
        if args.name in cfg.agents:
            print(f"Error: agent '{args.name}' already exists", file=sys.stderr)
            sys.exit(1)
        cfg.agents[args.name] = AgentProfile(
            provider_agent=args.provider_agent,
            default_dir=args.default_dir,
            memory_store=args.memory_store,
        )
        cfg.save()
        print(f"Created agent: {args.name}")

    elif action == "update":
        if args.name not in cfg.agents:
            print(f"Error: agent '{args.name}' not found", file=sys.stderr)
            sys.exit(1)
        agent = cfg.agents[args.name]
        if args.provider_agent is not None:
            agent.provider_agent = args.provider_agent
        if args.default_dir is not None:
            agent.default_dir = args.default_dir
        if args.memory_store is not None:
            agent.memory_store = args.memory_store
        cfg.save()
        print(f"Updated agent: {args.name}")

    elif action == "delete":
        if args.name not in cfg.agents:
            print(f"Error: agent '{args.name}' not found", file=sys.stderr)
            sys.exit(1)
        if args.name == cfg.default_agent:
            print(
                f"Error: cannot delete default agent '{args.name}'",
                file=sys.stderr,
            )
            sys.exit(1)
        del cfg.agents[args.name]
        cfg.save()
        print(f"Deleted agent: {args.name}")


def _pair(args: argparse.Namespace) -> None:
    """Mint a one-time channel pairing code and print it ONCE (CE-1 T1.5).

    A new sender on a channel (Telegram, Discord, …) redeems this code to start talking to
    the agent: within the 10-minute TTL it allow-lists them once, then the code is spent.
    The code is printed to the owner's terminal only — never persisted in plaintext, never
    logged. Direct store call (no running gateway required): pairing is owner-side setup.

    No channel is loaded here, so the sentence claims nothing a channel does (a bot, a mailbox):
    the code goes to the agent on that channel. A code lets someone in only while the channel's
    rule for strangers asks for one, so under any other rule the output says so."""
    from personalclaw.channel_trust import (
        PAIRING_CODE_TTL_SECS,
        create_pairing_code,
        trust_policies,
    )

    provider = (getattr(args, "provider", "") or "").strip().lower()
    if not provider:
        _usage_error("❌ Usage: personalclaw pair <provider>   (e.g. telegram, discord, email)")
    code = create_pairing_code(provider)
    minutes = PAIRING_CODE_TTL_SECS // 60
    print(f"Pairing code for {provider}: {code}")
    print(
        f"Have the person you're letting in send it to your agent on {provider} within "
        f"{minutes} minutes. It works once, then expires."
    )
    if trust_policies(provider).get("dm") != "pairing":
        print(
            f"It lets nobody in until {provider}'s rule for strangers asks for a code again "
            "(Settings › Sender trust)."
        )


def _discover(args: argparse.Namespace) -> None:
    """Look for PersonalClaw gateways advertising themselves on this network (CA-5).

    The client half of COMPANION-APPS C3, and the reason the resolver is a shared function
    rather than something each wrapper writes: a phone app, the desktop shell and this
    command all need the same answer, in the same shape.

    Finding nothing is a normal result and exits 0 — multicast is filtered on plenty of
    networks, and the fallback (type the URL) is the path that always works. Exiting
    non-zero would turn "your Wi-Fi drops multicast" into a script failure."""
    from personalclaw.companion.discovery import SERVICE_TYPE, resolve

    timeout = max(0.5, float(getattr(args, "timeout", 2.0) or 2.0))
    found = resolve(timeout=timeout)
    if getattr(args, "as_json", False):
        print(json.dumps([i.to_dict() for i in found], indent=2))
        return
    if not found:
        print(f"No gateways found advertising {SERVICE_TYPE} in {timeout:g}s.")
        print(
            "That is not necessarily a problem: discovery is off by default, is a no-op on a\n"
            "loopback-only gateway, and many networks filter multicast. On the machine running\n"
            "the gateway, turn on Settings → Companion apps → LAN discovery, then open the\n"
            "dashboard by typing its LAN address."
        )
        return
    print(f"Found {len(found)} gateway{'s' if len(found) != 1 else ''}:")
    for inst in found:
        print(f"\n  {inst.name or '(unnamed)'}")
        print(f"    url:     {inst.base_url or '(no address advertised)'}")
        if inst.requires_pairing:
            print("    pairing: required — run `personalclaw auth enroll` on that machine")
            print("             for a single-use code, then redeem it from this device.")


def _automation(args: argparse.Namespace) -> None:
    """Dispatch `automation` subcommands (AUTOMATION-SUBSTRATE §7 step 2).

    `verify-migration` exits NON-ZERO when the migration needs attention, so it composes into
    a script
    or a pre-cutover check. A read-only diff that always exited 0 could not gate anything, and
    gating
    the cutover is the reason §8 lists this command as the migration-trust mitigation.
    """
    action = getattr(args, "automation_action", None)
    if action == "verify-migration":
        from personalclaw.triggers.verify import render, verify_home

        report = verify_home()
        if getattr(args, "as_json", False):
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(render(report))
        if not report.ok:
            sys.exit(1)


def _cron_channel_problem(channel: str | None) -> str:
    """Why ``--channel`` can't be delivered to, or ``""``: the check the schedule API makes.

    ``--channel`` is a chat channel's name (the owner's DM there) or ``<name>:<id>``. This process
    is not the gateway, so no channel is registered in it: the installed channels are built to be
    asked, exactly as the gateway would build them.
    """
    if not channel:
        return ""
    from personalclaw.triggers import delivery as _delivery

    return _delivery.channel_route_problem(
        f"{_delivery.CHANNEL_ROUTE_PREFIX}{channel}", transports=_cli_chat_channels()
    )


def _cli_chat_channels() -> dict[str, Any]:
    """The installed chat channels, built to be asked: this process is not the gateway, so none
    is registered in it. What a channel route or a `send-message` action is checked against."""
    from personalclaw.providers.loader import build_channel_transports

    return {t.name: t for t in build_channel_transports()}


def _cron_questions(candidate: Any, *, before: Any, stored: dict) -> list[str]:
    """What saving *candidate* needs the owner's yes for, in the sentences the Triggers page's
    dialog shows: a grant for what its action runs (`triggers.grants.question`, *before* being the
    row as stored or None for a new one), and a posture that loosens whether its agent asks
    (`automation_posture`) over the *stored* action config."""
    from personalclaw.automation_posture import unconsented_step_loosening
    from personalclaw.triggers import grants

    sentences: list[str] = []
    grant = grants.question(candidate, before=before)
    if grant is not None:
        sentences.append(grant.sentence)
    inline = (candidate.workflow or {}).get("inline") or {}
    raw = inline.get("config")
    config: dict = raw if isinstance(raw, dict) else {}
    loosened = unconsented_step_loosening(
        "action",
        current=stored,
        new=config,
        body={},
        provider=str(inline.get("provider") or ""),
    )
    if loosened is not None:
        sentences.append(loosened[1])
    return sentences


def _cron_refuse(questions: list[str], *, operation: str, resources: str, nothing: str) -> NoReturn:
    """Say on stderr what a `cron` write would need the owner's yes for, change nothing, and
    exit 1.

    The CLI's form of the Triggers page's consent dialog: the same sentences, and ``--yes`` for the
    Allow. The CLI's other confirmations work the same way (``skills install`` refuses a warning
    and says "re-run with --force"): the command says what it needs and changes nothing, never
    doing the write quietly.
    """
    print("This needs your yes:", file=sys.stderr)
    for sentence in questions:
        print(f"  {sentence}", file=sys.stderr)
    print(f"{nothing} Re-run with --yes to allow it.", file=sys.stderr)
    sel().log_api_access(
        caller="cli",
        operation=operation,
        outcome="denied",
        source="cli",
        resources=resources,
        error="needs --yes",
    )
    sys.exit(1)


def _cron(args: argparse.Namespace) -> None:
    """Dispatch cron subcommands: list, add, update, remove, pause, resume, trigger.

    🔴 S108 — every write here went to `crons.json`, so a cron created from the CLI DID NOT FIRE.
    The clock engine (`triggers.service.tick`) reads the unified store and nothing else, and the
    boot migration that imports `crons.json` runs only at gateway startup. Measured: `cron add`
    wrote the legacy file with `triggers.json` untouched, so the job stayed inert until the user
    restarted the gateway — a create that reported success and scheduled nothing.

    Writes go through `triggers.tools`, the same functions the chat tools and the API use, so the
    CLI inherits their contracts rather than re-deriving them: the id-collision guard, arming on
    creation, the patch allowlist, the refusal to resume a row that failed to parse, and the
    confirm-before-delete gate.

    Every refusal is said on stderr and exits 1 (`_refuse`): a script can only tell "done" from
    "not done" by the exit status.
    """
    from personalclaw.security import redact_for_display
    from personalclaw.triggers import schedule_view as _sv
    from personalclaw.triggers import tools as _tools
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=config_dir())

    action = getattr(args, "cron_action", None)
    if action == "list":
        rows = store.load()
        if not rows:
            print("No cron jobs.")
            return
        for row in rows:
            trigger = row.trigger
            # A row that failed to parse is shown as BROKEN rather than omitted: the legacy list
            # could not represent one at all, and silently hiding a trigger the user created is how
            # "where did my automation go" happens.
            status = "⚠️" if not row.ok else ("✅" if trigger.enabled else "⏸️")
            sched = _sv.describe_cadence(trigger) if row.ok else (row.errors[0].message)
            # 🔴 The SHARED projection's `message`, not a hand-read config key. Reading
            # `config["message"]` printed a BLANK column for every `invoke-agent` trigger, because
            # that provider's key is `task_template` — and `run-prompt`/`notify` differ again.
            # `to_schedule_row` already resolves all of them (schedule_view.py:171), which is the
            # whole reason it exists.
            # Masked like every read of a schedule (`schedule_view.MASKED_FIELDS`): a terminal is a
            # screen too, and a scrollback keeps what it showed.
            if row.ok:
                shown = _sv.to_schedule_row(trigger)
            else:
                shown = {"name": redact_for_display(trigger.name or "")}
            detail = str(shown.get("message") or "")
            print(f"  {status} {trigger.id}  {shown.get('name') or ''}  ({sched})  {detail[:60]}")

    elif action == "add":
        cron_expr = getattr(args, "cron_expr", None)
        channel = (getattr(args, "channel", None) or "").strip() or None
        approval_mode = getattr(args, "approval_mode", "") or ""
        problem = _cron_channel_problem(channel)
        if problem:
            _refuse(f"Error: {problem}")
        # The parser requires exactly one of the two (`cron_cadence` in `cli.build_parser`), and
        # `tools.create` refuses an interval that would never fire (`arm.semantic_spec_issues`).
        if cron_expr is not None:
            spec = {"kind": "cron", "expr": cron_expr}
        else:
            spec = {"kind": "interval", "interval_secs": int(args.every)}

        workflow = {
            "inline": {
                "provider": "invoke-agent",
                "config": {
                    "task_template": args.message,
                    "agent": "",
                    "model": "",
                    "approval_mode": approval_mode,
                },
            }
        }
        from personalclaw.triggers.models import Trigger

        # The questions the Triggers page's create dialog asks, asked here the CLI's way: an agent
        # job needs the owner's yes to run unattended, and `--approval-mode auto` needs it again.
        yes = bool(getattr(args, "yes", False))
        questions = _cron_questions(
            Trigger(id="", name=args.name, kind="clock", workflow=workflow), before=None, stored={}
        )
        if questions and not yes:
            _cron_refuse(
                questions,
                operation="cron.add",
                resources=f"name={args.name}",
                nothing="Nothing was created.",
            )
        # `--channel` is `<name>` or `<name>:<chat id>`: the channel `create` sends results on, as
        # the chat's `via` and `to` name it, so the row is saved with its route and the sentence
        # printed below says where results go.
        via, _, chat = (channel or "").partition(":")
        result = _tools.create(
            store,
            name=args.name,
            kind="clock",
            spec=spec,
            workflow=workflow,
            # `created_by="user"`, not "agent": the agent cap (decision 5d) exists to bound what the
            # ASSISTANT creates unprompted. A human typing the command is the user acting directly,
            # and capping their own CLI at the agent limit would be a rule aimed at the wrong party.
            created_by="user",
            owner_consented=yes,
            via=via,
            to=chat,
            chat_channels=_cli_chat_channels() if channel else None,
        )
        if not result.ok:
            sel().log_api_access(
                caller="cli",
                operation="cron.add",
                outcome="denied",
                source="cli",
                resources=f"name={args.name}",
                error=result.text,
            )
            _refuse(result.text)
        trigger_id = str((result.data.get("trigger") or {}).get("id") or "")
        granted = ((result.data.get("trigger") or {}).get("capabilities") or {}).get("providers")
        if granted:
            # The grant decision is written to the security audit, as the Triggers page writes it.
            sel().log_api_access(
                caller="cli",
                operation="trigger.grant",
                outcome="success",
                source="cli",
                resources=f"trigger:{trigger_id}: {', '.join(granted)}",
            )
        sel().log_api_access(
            caller="cli",
            operation="cron.add",
            outcome="allowed",
            source="cli",
            resources=f"job_id={trigger_id} approval_mode={approval_mode or 'default'}",
        )
        print(result.text)

    elif action == "update":
        patch: dict = {}
        spec_update: dict = {}
        for field in ("name", "message", "every_secs", "cron_expr", "channel"):
            val = getattr(args, field, None)
            if val is None:
                continue
            if field == "channel":
                val = val.strip() or None
                if val is None:
                    continue
                problem = _cron_channel_problem(val)
                if problem:
                    _refuse(f"Error: {problem}")
                patch["delivery"] = f"channel:{val}"
            elif field == "name":
                patch["name"] = val
            elif field == "message":
                patch["message"] = val
            elif field == "every_secs":
                spec_update = {"kind": "interval", "interval_secs": int(val)}
            elif field == "cron_expr":
                spec_update = {"kind": "cron", "expr": val}
        approval = getattr(args, "approval_mode", None)
        if not patch and not spec_update and approval is None:
            _usage_error("Provide at least one field to update")

        existing = store.get(args.job_id)
        if existing is None:
            sel().log_api_access(
                caller="cli",
                operation="cron.update",
                outcome="not_found",
                source="cli",
                resources=f"job_id={args.job_id} reason=not_found",
            )
            _refuse(f"Job not found: {args.job_id}")

        if spec_update:
            # Carry the quietly-losable spec keys (`timezone`/`skip_dates`/`strict`) rather than
            # replacing the spec wholesale — the contract §1.3 and S101 both record. The next fire
            # moves with it in `tools.update`, as it does for the Triggers page and the chat.
            current = existing.trigger.spec if isinstance(existing.trigger.spec, dict) else {}
            carried = {
                k: v for k, v in current.items() if k in ("timezone", "skip_dates", "strict")
            }
            patch["spec"] = {**carried, **spec_update}

        # `message` and `approval_mode` live inside the action, not on the trigger, so they are
        # folded into a `workflow` patch. Read-modify-write of the EXISTING action, because
        # replacing it would drop the agent/model the user set when they created the job.
        if "message" in patch or approval is not None:
            action_wf: dict = dict(existing.trigger.workflow or {})
            inline: dict = dict(action_wf.get("inline") or {})
            action_cfg: dict = dict(inline.get("config") or {})
            if "message" in patch:
                action_cfg["task_template"] = patch.pop("message")
            if approval is not None:
                action_cfg["approval_mode"] = "" if approval == "default" else approval
            inline["config"] = action_cfg
            inline.setdefault("provider", "invoke-agent")
            action_wf["inline"] = inline
            patch["workflow"] = action_wf

        # The questions the Triggers page's editor asks about the same change: what the action
        # runs (a new message is a new instruction for an agent that runs unattended) and whether
        # its agent stops asking. Asked only of a change that carries the action, as the editor
        # asks only of a save that does.
        yes = bool(getattr(args, "yes", False))
        if "workflow" in patch:
            import copy

            candidate = copy.deepcopy(existing.trigger)
            candidate.workflow = patch["workflow"]
            stored_inline = (existing.trigger.workflow or {}).get("inline") or {}
            stored_cfg = stored_inline.get("config")
            questions = _cron_questions(
                candidate,
                before=existing.trigger,
                stored=stored_cfg if isinstance(stored_cfg, dict) else {},
            )
            if questions and not yes:
                _cron_refuse(
                    questions,
                    operation="cron.update",
                    resources=f"job_id={args.job_id} fields={','.join(sorted(patch))}",
                    nothing="Nothing was changed.",
                )

        result = _tools.update(
            store,
            trigger_id=args.job_id,
            patch=patch,
            owner_consented=yes,
            chat_channels=_cli_chat_channels() if _tools.asks_chat_channels(patch) else None,
        )
        if result.ok and result.data.get("granted"):
            sel().log_api_access(
                caller="cli",
                operation="trigger.grant",
                outcome="success",
                source="cli",
                resources=f"trigger:{args.job_id}: {', '.join(result.data['granted'])}",
            )
        sel().log_api_access(
            caller="cli",
            operation="cron.update",
            outcome="allowed" if result.ok else "denied",
            source="cli",
            resources=f"job_id={args.job_id} fields={','.join(sorted(patch))}",
            error="" if result.ok else result.text,
        )
        if not result.ok:
            _refuse(result.text)
        print(result.text)

    elif action == "remove":
        # `confirm=True`: the flag exists so a TOOL CALL cannot delete by accident. A human who
        # typed `cron remove <id>` has already expressed the intent, and prompting again for what
        # the command literally says would be theatre.
        result = _tools.delete(store, trigger_id=args.job_id, confirm=True)
        if not result.ok:
            _refuse(f"Job not found: {args.job_id}")
        print(result.text)

    elif action == "pause":
        result = _tools.set_paused(store, trigger_id=args.job_id, paused=True)
        if not result.ok:
            _refuse(f"Job not found: {args.job_id}")
        print(result.text)

    elif action == "resume":
        result = _tools.set_paused(store, trigger_id=args.job_id, paused=False)
        # A refusal is said in `set_paused`'s own words: a trigger not allowed to run its action
        # says which action and where it is allowed, one with a parse error names the error. Either
        # is strictly more useful than "Job not found", and the row does exist, so that message
        # would be wrong as well as unhelpful.
        if not result.ok:
            _refuse(result.text)
        print(result.text)

    elif action == "trigger":
        # Fire via the RUNNING gateway (a CLI process has no clock loop). The id is looked up in
        # the store `list` reads, as `update`, `remove`, `pause` and `resume` do: every id a writer
        # minted is there, and anything else is refused before a request is sent. This was a
        # pattern for the old 6-16 hex ids, which refused every id `add` makes (`clock:<name>`).
        if store.get(args.job_id) is None:
            sel().log_api_access(
                caller="cli",
                operation="cron.trigger",
                outcome="not_found",
                source="cli",
                resources=f"job_id={args.job_id} reason=not_found",
            )
            _refuse(f"Job not found: {args.job_id}")
        from personalclaw.schedule_trigger import trigger_schedule_job

        ok, message = trigger_schedule_job(args.job_id)
        sel().log_api_access(
            caller="cli",
            operation="cron.trigger",
            outcome="allowed" if ok else "denied",
            source="cli",
            resources=f"job_id={args.job_id}",
            error="" if ok else message,
        )
        if not ok:
            _refuse(f"Error: {message}")
        print(message)


def _security(args: argparse.Namespace) -> None:
    """Security audit and deny list commands."""

    action = getattr(args, "sec_action", None)
    if action == "deny-list":
        print("🔒 Built-in deny patterns (always enforced):")
        for p in BUILTIN_DENY_PATTERNS:
            print(f"  ✗ {p}")
        cfg_path = config_dir() / "config.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text())
            extra = data.get("hooks", {}).get("auto_deny_tools", [])
            if extra:
                print("\n🔧 User-configured deny patterns:")
                for p in extra:
                    print(f"  ✗ {p}")
    elif action == "audit":
        history_dir = config_dir() / "history"
        findings = scan_history(history_dir)
        if findings:
            print(f"⚠️  {len(findings)} suspicious entries found:\n")
            for f in findings:
                print(f"  📄 {f['file']}")
                print(f"     {f['warning']}")
                print(f"     {f['snippet'][:120]}…\n")
        else:
            print("✅ No suspicious tool usage found in recent history.")

        mem_findings = scan_memory()
        if mem_findings:
            print(f"\n⚠️  {len(mem_findings)} suspicious memory entries:\n")
            for f in mem_findings:
                print(f"  [{f['type']}] {f['key']}: {f['warning']}")
                print(f"    {f['value'][:120]}\n")
        elif not findings:
            pass
        else:
            print("✅ No suspicious content in vector memory.")
    elif action == "events":

        limit = getattr(args, "limit", 20)
        events = sel().recent(limit=limit)
        if not events:
            print("No security events recorded.")
            return
        print(f"📋 Last {len(events)} security event(s):\n")
        for e in events:
            ts = e.get("timestamp", "?")[:19]
            etype = e.get("event_type", "?")
            op = e.get("operation", "?")
            outcome = e.get("outcome", "?")
            src = e.get("source", "?")
            caller = e.get("caller_identity", "?")
            print(f"  {ts}  [{src}] {etype}: {op} → {outcome}  (caller: {caller})")
            if e.get("error"):
                print(f"    error: {e['error'][:120]}")
            reason = (e.get("metadata") or {}).get("reason")
            if reason:
                print(f"    reason: {str(reason)[:120]}")
            if e.get("downstream_service"):
                print(f"    downstream: {e['downstream_service']}")
    elif action == "verify":

        # CLI verify is an explicit offline audit — check the entire chain. A broken chain exits 1,
        # as `backup validate` does over a corrupt export: a scheduled check that exits 0 over
        # tampered rows reports them as intact to whatever runs it.
        total, valid = sel().verify_integrity(max_entries=None)
        if total == 0:
            print("No security events to verify.")
        elif total == valid:
            print(f"✅ HMAC chain intact: {total} entries verified.")
        else:
            print(
                f"⚠️  HMAC chain COMPROMISED: {valid}/{total} entries valid, {total - valid} tampered."  # noqa: E501
            )
            sys.exit(1)


async def _run_eval(args: argparse.Namespace) -> None:
    """Run multi-session evaluation scenarios."""

    # The scenario set is no longer packaged-read-only under eval/scenarios/ —
    # it is the versioned library installed at ~/.personalclaw/evals/scenarios/, so
    # `personalclaw eval` runs the same files (and the same user additions) the matrix
    # runner pins. install_library() is an idempotent backfill: safe on every call.
    from personalclaw.evals.scenarios import install_library, installed_dir

    install_library()
    scenarios_dir = installed_dir()

    if args.all_scenarios:
        scenarios = load_scenarios(scenarios_dir)
    elif args.scenarios:
        scenarios = []
        for name in args.scenarios:
            resolved = None
            for ext in (".json", ".yaml", ".yml"):
                candidate = scenarios_dir / f"{name}{ext}"
                if candidate.exists():
                    resolved = candidate
                    break
            if resolved is None:
                available = sorted(
                    f.stem
                    for f in scenarios_dir.iterdir()
                    if f.suffix in (".json", ".yaml", ".yml")
                )
                _refuse(
                    f"Error: scenario '{name}' not found.\n"
                    f"Available scenarios: {', '.join(available)}"
                )
            scenarios.append(load_scenario(resolved))
    else:
        scenarios = [load_scenario(scenarios_dir / "smoke_test.json")]

    total_turns = sum(len(sess.turns) for s in scenarios for sess in s.sessions)
    names = ", ".join(s.name for s in scenarios)
    print(f"Running: {names} ({total_turns} turns)\n")

    config = AppConfig.load()
    provider_factory = config.create_provider_factory()

    runner = EvalRunner(
        provider_factory=provider_factory, judge_enabled=getattr(args, "judge", False)
    )
    results = await runner.run_scenarios(scenarios)

    # LLM Judge scoring
    if getattr(args, "judge", False):
        judge = LLMJudge(provider_factory=provider_factory)
        await judge.start()
        try:
            for scenario, result in zip(scenarios, results):
                criteria = scenario.judge_criteria or scenario.description
                for sr in result.sessions:
                    for tr in sr.turns:
                        for idx, (a, _) in enumerate(tr.assertion_results):
                            if a.type == AssertionType.JUDGE:
                                try:
                                    verdict = await judge.judge_turn(
                                        scenario.description,
                                        a.value or criteria,
                                        tr.user_message,
                                        tr.agent_response,
                                    )
                                    tr.assertion_results[idx] = (
                                        a,
                                        verdict.score >= judge.pass_threshold,
                                    )
                                    reason, _ = redact_exfiltration_urls(verdict.reason)
                                    reason, _ = redact_credentials(reason)
                                    print(f"  🧑‍⚖️ Judge: {verdict.score}/5 — {reason}")
                                except Exception as exc:
                                    print(f"  ⚠️ Judge failed for turn: {exc}")
                                    tr.assertion_results[idx] = (a, False)
        finally:
            await judge.shutdown()

    report = format_results(results)
    print("\n" + report)

    dims = score_by_dimension(results)
    if dims:
        print("## Dimension Summary")
        for dim, s in sorted(dims.items()):
            status = "✅" if s["rate"] >= 0.75 else "❌"
            print(f"  {status} {dim}: {s['passed']}/{s['total']} ({s['rate']:.0%})")

    overall = sum(1 for r in results if r.passed)
    print(f"\nOverall: {overall}/{len(results)} scenarios passed")

    # Save results
    results_dir = Path.cwd() / "eval_results"
    results_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    report_path = results_dir / f"eval_{ts}.md"
    report_path.write_text(report + "\n")

    json_path = results_dir / f"eval_{ts}.json"
    json_data = {
        "timestamp": ts,
        "scenarios": [r.summary() for r in results],
        "dimensions": dims,
        "overall_passed": overall,
        "overall_total": len(results),
    }
    atomic_json_write(json_path, json_data)

    print(f"\nResults saved to:\n  {report_path}\n  {json_path}")


def _parse_csv_ints(raw: str, default: tuple[int, ...]) -> tuple[int, ...]:
    """``"1,3"`` → ``(1, 3)``. A blank falls back to ``default``; a non-integer raises,
    because silently dropping ``"3x"`` would run a narrower matrix than the user asked
    for and report it as the one they asked for."""
    if not raw:
        return default
    return tuple(int(part.strip()) for part in raw.split(",") if part.strip())


def _parse_csv(raw: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if not raw:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


async def _judge_bench(args: argparse.Namespace) -> None:
    """Run the judge benchmark and print the tier-recommendation table (ES-4).

    Prints the spend preflight FIRST and honours ``--dry-run``, because the full shipped
    matrix is 540 judge calls: a user who sees the count can narrow ``--tiers``/``--samples``
    before paying for a matrix they did not want.
    """
    from personalclaw.evals import judge_bench as jb

    if getattr(args, "list_sets", False):
        for name in jb.list_fixture_sets():
            print(name)
        return

    try:
        tiers = _parse_csv(getattr(args, "tiers", "") or "", jb.TIERS)
        samples = _parse_csv_ints(getattr(args, "samples", "") or "", jb.SAMPLE_COUNTS)
        fixture_set = jb.load_fixture_set(args.fixture_set)
        paired, unpaired = jb.build_specs(fixture_set, tiers=tiers, sample_counts=samples)
    except (jb.JudgeBenchError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    cells = jb.bench_cells(paired, unpaired)
    calls = sum(int(c["judge_samples"]) for c in cells)
    print(
        f"Judge benchmark '{fixture_set.name}' "
        f"({len(fixture_set.fixtures)} fixtures, {', '.join(fixture_set.rubric_classes())})\n"
        f"  tiers:   {', '.join(tiers)}\n"
        f"  samples: {', '.join(str(s) for s in samples)}\n"
        f"  cells:   {len(cells)}\n"
        f"  judge calls (the spend): {calls}\n"
    )
    if getattr(args, "dry_run", False):
        print("--dry-run: nothing was called.")
        return

    result = await jb.run_judge_bench(
        args.fixture_set, tiers=tiers, sample_counts=samples, budget_usd=args.budget
    )
    print(jb.render_table_tsv(result.table))
    print("## Recommendations")
    for rec in result.recommendations:
        if rec.verdict == jb.REC_RECOMMENDED:
            print(
                f"  {rec.rubric_class}: bind '{rec.use_case}' to {rec.model_ref or '<unbound>'} "
                f"(tier {rec.tier}, judge_samples {rec.samples}, ${rec.cost_usd})"
            )
        else:
            print(f"  {rec.rubric_class}: {rec.verdict}")
        for note in rec.notes:
            print(f"    - {note}")
    from personalclaw.evals import store

    print(f"\nArtifacts: {store.matrix_dir(result.bench_id)}")


def _eval_harvest(args: argparse.Namespace) -> None:
    """Harvest real runs into scenario-library cases (the harvested regression suite).

    Exits 1 on a REFUSAL — an empty population — and 0 on a harvest that looked at runs and kept
    none of them. The two are different statements and the exit code says which: "nothing to
    measure" must not be indistinguishable from "measured nothing", because a caller wiring this
    into a study would read the second as a green.
    """
    from personalclaw.evals import harvest as hv

    if getattr(args, "list_suite", False):
        try:
            suite = hv.load_harvested_suite(workflow_name=getattr(args, "workflow", "") or "")
        except hv.EmptyHarvestError as exc:
            print(f"Refusing: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"Harvested suite: {len(suite)} case(s)")
        for installed in suite:
            block = installed.get("harvest") or {}
            print(
                f"  {installed.get('name')}  run={block.get('run_id')}  "
                f"workflow={block.get('workflow_name')}  status={block.get('status')}"
            )
        return

    limit = int(getattr(args, "limit", 0) or 0) or hv.DEFAULT_LIMIT
    dry_run = bool(getattr(args, "dry_run", False))
    report = hv.harvest(
        workflow_name=getattr(args, "workflow", "") or "",
        limit=limit,
        write=not dry_run,
    )

    if report.is_refusal:
        print(f"Refusing: {report.refusal}", file=sys.stderr)
        raise SystemExit(1)

    wrote = sum(1 for c in report.cases if c.written)
    print(
        f"Considered {report.considered} terminal run(s); harvested {report.population} case(s)"
        + (" (--dry-run: nothing written)" if dry_run else f"; wrote {wrote} new/changed")
    )
    for case in report.cases:
        mark = "+" if case.written else "=" if not dry_run else " "
        print(f"  {mark} {case.name}  run={case.run_id}  sha256={case.sha256[:12]}")
    if report.skipped:
        print("Skipped:")
        for reason, count in sorted(report.skipped_by_reason().items()):
            print(f"  {count} x {reason}")
    if not report.cases:
        # NOT a refusal: runs existed and every one was disqualified, with a reason each.
        print(
            "No case qualified. This is a measured result over "
            f"{report.considered} run(s), not an empty population."
        )
        return
    from personalclaw.evals import scenarios as sc

    print(f"\nLibrary: {sc.installed_dir()}")


async def _study(args: argparse.Namespace) -> None:
    """Run (or preview) a pre-registered template A/B study (ES-5 / §2).

    The invocation surface §2 had none of. Without it the instrument was complete and
    unreachable: `run_study` had no production caller at all, so a pre-registered study could
    be listed on the Learning page and never executed.

    ``--dry-run`` prints the spend FIRST for exactly the reason ``judge-bench`` does, only
    more so: a study is ``cases x k x 2`` ARM calls plus twice that many JUDGE calls, so a
    ten-case suite at k=5 is 100 arm + 300 judge calls. A user who sees that number can
    narrow it; a user who does not, pays for it.
    """
    from personalclaw.evals import studies, study_arms

    if getattr(args, "list", False):
        rows = studies.study_index()
        if not rows:
            print(
                "No study has been registered yet. One is pre-registered whenever the "
                "template refiner files a diff (`propose_template_diff`)."
            )
            return
        for row in rows:
            verdict = row.get("verdict") or "not run"
            power = " [low_power]" if row.get("low_power") else ""
            print(
                f"{row['study_id']}\t{row.get('kind')}\t"
                f"{(row.get('subject') or {}).get('template_id', '')}\t"
                f"k={row.get('k')}\t{verdict}{power}"
            )
        return

    view_id = str(getattr(args, "view", "") or "")
    if view_id:
        view = studies.study_view(view_id)
        if view is None:
            print(f"Error: no registered study {view_id!r}", file=sys.stderr)
            raise SystemExit(1)
        print(json.dumps(view, indent=2, sort_keys=True))
        return

    study_id = str(getattr(args, "run", "") or "")
    if not study_id:
        _usage_error("Nothing to do. Pass --list, --view <id> or --run <id>.")

    from personalclaw.evals import store as evals_store

    raw = evals_store.read_study_registration(study_id)
    if raw is None:
        print(f"Error: no registered study {study_id!r}", file=sys.stderr)
        raise SystemExit(1)
    reg = studies.registration_from_dict(raw)

    samples = int(getattr(args, "samples", 0) or 0) or studies.DEFAULT_JUDGE_SAMPLES
    try:
        old_body, new_body = await study_arms.arm_bodies_for_study(reg)
    except studies.StudyError as exc:
        # A study whose arms cannot be built is refused BEFORE the preflight, so the printed
        # spend is never for a matrix that could not have run.
        print(f"Refusing: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    suite = study_arms.harvested_study_cases(
        workflow_name=str(reg.subject.get("template_id") or "")
    )
    pre = study_arms.preflight(
        reg,
        cases=suite.cases,
        old_template_body=old_body,
        new_template_body=new_body,
        samples=samples,
        refusal=suite.refusal,
    )
    print(
        f"Study {reg.study_id} ({reg.kind}) on "
        f"{reg.subject.get('template_id') or '<unnamed template>'}\n" + pre.render() + "\n"
    )
    if pre.refusal:
        raise SystemExit(1)
    if getattr(args, "dry_run", False):
        print("--dry-run: nothing was called.")
        return

    try:
        result = await study_arms.run_registered_study(
            study_id,
            old_template_body=old_body,
            new_template_body=new_body,
            samples=samples,
        )
    except studies.StudyError as exc:
        print(f"Refusing: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    agreement = "unmeasurable" if result.agreement is None else f"{result.agreement:.2f}"
    print(f"Verdict: {result.verdict}" + (" [low_power]" if result.low_power else ""))
    print(f"  win rate:  {result.win_rate}")
    print(f"  agreement: {agreement} (floor {result.agreement_floor})")
    if result.fail_reason:
        print(f"  fail reason: {result.fail_reason}")
    for hit in result.locked_regressions:
        print(f"  locked regression: {hit}")
    if result.evidence_ref:
        print(f"  evidence: {result.evidence_ref}")
    if result.demotion_proposal_id:
        print(f"  demotion proposal: {result.demotion_proposal_id}")
    if result.calibration_ref:
        print(f"  judge calibration filed: {result.calibration_ref}")
    print(f"\nArtifacts: {evals_store.study_dir(study_id)}")


def _ablation(args: argparse.Namespace) -> None:
    """Run (or preview) the harness-ablation runner / skills bench (ES-7 §3.1 + §3.3).

    Without this the only trigger is the monthly cadence, which is a control the operator
    cannot exercise — and a measurement you have to wait 30 days to see is one nobody trusts.
    ``--dry-run`` prints the cell count FIRST, for the same reason ``judge-bench`` does: an
    ablation replays a scenario once per arm per trial, and a user who sees the count can lower
    ``--trials`` before paying for a matrix they did not want.
    """
    from personalclaw.evals import ablation

    if getattr(args, "list_components", False):
        rows = ablation.registry()
        if not rows:
            print(
                f"No components registered. Add rows to {ablation.registry_path()} "
                '({"components": [{"component_id": ..., "kind": ..., "target": ..., '
                '"subject": ...}]}).'
            )
            return
        for comp in rows:
            print(
                f"{comp.component_id}\t{comp.kind}\t{comp.target}\t{comp.subject}\t"
                f"arms={','.join(comp.arms())}"
            )
        return

    skill = str(getattr(args, "skill", "") or "")
    if skill:
        _ablation_bench_skill(args, skill)
        return

    component_id = str(getattr(args, "component", "") or "")
    if component_id:
        matches = [c for c in ablation.registry() if c.component_id == component_id]
        if not matches:
            print(f"Error: no registered component {component_id!r} (try --list).", file=sys.stderr)
            raise SystemExit(1)
        component: ablation.AblationComponent = matches[0]
    else:
        picked = ablation.pick_component()
        if picked is None:
            print(f"No components registered. See --list and {ablation.registry_path()}.")
            return
        component = picked

    trials = max(1, int(getattr(args, "trials", 3) or 3))
    try:
        # Before the preflight print, so a typo'd target is a message and not a matrix.
        ablation.validate_component(component)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    arms = component.arms()
    print(
        f"Ablation '{component.component_id}' ({component.kind} → {component.target})\n"
        f"  subject: {component.subject}\n"
        f"  arms:    {', '.join(arms)}\n"
        f"  trials:  {trials} per arm\n"
        f"  cells (the spend): {len(arms) * trials}\n"
        f"  cadence: every {ablation._cadence_days()}d "
        f"(due now: {ablation.due()})\n"
    )
    if getattr(args, "dry_run", False):
        print("--dry-run: nothing was called.")
        return
    if not getattr(args, "force", False) and not ablation.due():
        print("Not due yet. Pass --force to measure anyway.")
        return

    from personalclaw.evals import scenarios as scenario_lib
    from personalclaw.evals import store as evals_store

    try:
        report = ablation.run_ablation(
            component, trials=trials, budget_usd=float(getattr(args, "budget", 0.0) or 0.0)
        )
    except ablation.LiveStateMutatedError as exc:
        # Loud, not swallowed: the run altered the operator's config, which is the one thing
        # §3.1 forbids outright.
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (scenario_lib.ScenarioLibraryError, evals_store.PinRequiredError) as exc:
        # A misregistered subject or an incomplete pin is a registry mistake, not a crash. The
        # message already names what is installed — a traceback on top of it only hides it.
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    _print_ablation_report(report)
    if report.verdict == ablation.REMOVE:
        _verdict, proposal = ablation.file_retirement_proposal(report)
        if proposal is not None:
            print(f"\nFiled retirement proposal {proposal.id} (evidence: {report.evidence_ref()})")


def _ablation_bench_skill(args: argparse.Namespace, skill: str) -> None:
    """The §3.3 half: one skill, surfaced vs suppressed, over its consulted runs."""
    from personalclaw.evals import skills_bench

    subject = str(getattr(args, "subject", "") or "")
    if getattr(args, "dry_run", False):
        runs = skills_bench.consulted_runs(skill)
        # The preflight resolves the subject the SAME way the scored path will, so `--dry-run`
        # cannot print `<none>` for an invocation that would have found one (or the reverse).
        population = (
            skills_bench.ReplayPopulation(skill=skill, subject=subject, candidates=(subject,))
            if subject
            else skills_bench.replay_population(skill)
        )
        origin = "operator" if subject else "harvested"
        print(
            f"Skill bench '{skill}'\n"
            f"  consulted runs: {len({r['run_id'] for r in runs})}\n"
            f"  subject:        {population.subject or '<none>'} ({origin})\n"
            f"  harvested candidates: {len(population.candidates)}\n"
            + (f"  {population.reason}\n" if population.reason else "")
            + "--dry-run: nothing was called."
        )
        return
    report = skills_bench.bench_skill(
        skill,
        subject=subject,
        trials=max(1, int(getattr(args, "trials", 3) or 3)),
        budget_usd=float(getattr(args, "budget", 0.0) or 0.0),
    )
    print(f"Skill bench '{skill}' → {report.verdict}")
    print(f"  consulted runs: {len(report.consulted_run_ids)}")
    if report.subject:
        replayed = f"  replayed: {report.subject} ({report.subject_origin or 'operator'}"
        replayed += f", run {report.subject_run_id})" if report.subject_run_id else ")"
        print(replayed)
    if len(report.subject_candidates) > 1:
        # Say what was NOT scored. One subject per MatrixSpec is the current shape; printing the
        # population makes an under-scored bench visible instead of reading as the whole history.
        print(
            f"  harvested candidates: {len(report.subject_candidates)} "
            "(one scored — MatrixSpec carries a single subject)"
        )
    if report.suppression:
        print(f"  suppression verified: {report.suppression.get('verified')}")
    if report.delta is not None:
        print(f"  delta (surfaced − suppressed): {report.delta}")
    if report.reason:
        print(f"  {report.reason}")


def _eval_gate(args: argparse.Namespace) -> None:
    """Run the Loop-2 cheap gate for one proposal (ES-6 / amendment E2).

    Lives on the CLI rather than behind an endpoint because that is what every sibling eval
    does: ``study``, ``ablation``, ``judge-bench`` and ``retrieval-eval`` all RUN from here and
    the ``/api/evals/*`` routes only READ the artifacts. A gate run is minutes and real money —
    an HTTP handler holding a request open for it would be a new shape nothing else in the
    substrate has.

    ``--list`` prints the subset AND the tagged scenarios excluded from it with their reasons,
    because a subset that silently drops what the operator tagged is a subset whose cost and
    coverage nobody can explain.
    """
    from personalclaw.evals import gate

    subset = gate.gate_subset()
    if getattr(args, "list_subset", False):
        if not subset.members:
            print(
                'The gate subset is EMPTY: no installed scenario declares `"tiers": ["gate"]`.\n'
                f"  library: {gate.scenario_lib.installed_dir()}"
            )
        for member in subset.members:
            print(
                f"{member.name}\tturns={member.turns}\tassertions={member.hard_assertions}\t"
                f"{member.sha256[:12]}"
            )
        for name, reason in subset.excluded:
            print(f"excluded\t{name}\t{reason}")
        print(
            f"\n{len(subset.members)} scenario(s), {subset.turns} turns per arm "
            f"({subset.turns * 2} for before+after), subset {subset.sha256()[:12]}"
        )
        return

    pid = str(getattr(args, "proposal", "") or "").strip()
    if not pid:
        _usage_error("Error: name a proposal id to gate (or pass --list).")

    from personalclaw.learning import proposals as queue

    prop = queue.get(pid)
    if prop is None:
        print(f"Error: no proposal {pid!r}.", file=sys.stderr)
        raise SystemExit(1)

    budget = float(getattr(args, "budget", 0.0) or 0.0)
    trials = max(1, int(getattr(args, "trials", 1) or 1))
    if getattr(args, "dry_run", False):
        arms = gate.arms_for_proposal(prop.to_dict())
        staged = sorted(arms[1].files) if arms else []
        # The ceiling the real run would use, and what a ZERO means for it. An unbudgeted gate does
        # not run unbounded — it does not run — so the preflight has to say so rather than print a
        # number that reads like "free".
        ceiling = budget or gate._default_budget_usd()
        ceiling_line = (
            f"${ceiling:.4g}"
            if ceiling > 0
            else "unset — this run would be UNGATED (set evals.default_budget_usd)"
        )
        print(
            f"Gate '{pid}' ({prop.kind})\n"
            f"  scenarios: {len(subset.members)} ({', '.join(subset.names) or '<none>'})\n"
            f"  cells (the spend): {len(subset.members) * 2 * trials}\n"
            f"  candidate files:   {', '.join(staged) or '<none — this proposal is ungateable>'}\n"
            f"  budget: {ceiling_line}\n"
            "--dry-run: nothing was called."
        )
        return

    report = gate.gate_proposal(pid, budget_usd=budget or None, trials=trials)
    if report is None:
        print(f"Error: no proposal {pid!r}.", file=sys.stderr)
        raise SystemExit(1)
    _print_gate_report(report)


def _print_gate_report(report) -> None:
    """Print a gate report. An unmeasured arm prints "not measured", never 0.0.

    Same string the Learning panels use (`JudgeBenchPanel`, `AblationPanel`, `StudiesPanel`), so
    the CLI and the UI say the same word about the same absence.
    """

    def fmt(value) -> str:
        return "not measured" if value is None else f"{float(value):.4f}"

    print(f"Gate '{report.run_id}': {report.state}")
    if report.reason:
        print(f"  {report.reason}")
    if report.state != "gated":
        return
    print(
        f"  before: {fmt(report.before.get('mean_score'))} "
        f"(scored {report.before.get('scored')}, unmeasured {report.before.get('absent')})"
    )
    print(
        f"  after:  {fmt(report.after.get('mean_score'))} "
        f"(scored {report.after.get('scored')}, unmeasured {report.after.get('absent')})"
    )
    print(f"  delta:  {fmt(report.delta)}{'  ← REGRESSION' if report.regressed else ''}")
    spend = report.spend or {}
    if spend.get("observed"):
        from personalclaw.guardrails.budgets import unpriced_clause

        estimated = " (estimated)" if spend.get("estimated") else ""
        left_out = unpriced_clause(int(spend.get("unpriced_attempts") or 0))
        print(
            f"  spend:  ${float(spend.get('dollars_est') or 0.0):.4f}{estimated} "
            f"over {spend.get('attempts')} model call(s)" + (f", {left_out}" if left_out else "")
        )
    else:
        print("  spend:  not measured (no cell reported one)")
    bound = report.bound or {}
    if bound.get("halted"):
        print(f"  HALTED on budget: {bound.get('halt_reason')}")
        print(f"  not run: {', '.join(bound.get('not_run') or [])}")
    pin = report.pin or {}
    subset_hash = str(pin.get("scenario_sha256") or "")[:12]
    print(f"  pin:    model_fp={pin.get('model_fp') or 'n/a'} subset={subset_hash or 'n/a'}")


def _print_ablation_report(report) -> None:
    print(f"Verdict: {report.verdict}")
    for arm, agg in sorted(report.arms.items()):
        mean = agg.get("mean_score")
        print(
            f"  {arm}: mean={'n/a' if mean is None else round(float(mean), 4)} "
            f"scored={agg.get('scored_count')} of {agg.get('total')}"
        )
    print(f"  delta (on − off): {report.delta}  (threshold {report.epsilon})")
    if report.cheap_delta is not None:
        print(f"  delta (on − cheap): {report.cheap_delta}")
    print(f"  report: evals/ablation/{report.matrix_id}.json")


def _retrieval_eval(args: argparse.Namespace) -> None:
    """Per-arm P@k/R@k ablation over BOTH retrieval stores (ES-3 / §5).

    ``--store`` defaults to ``both`` and every other input has a working default, so the
    command a user actually types — ``personalclaw retrieval-eval`` with no flags — mines
    the qrels, scores both stores separately and prints two tables. That default is the
    point: ES-7's ``--subject`` defaulted to ``""`` and the bench refused on it, so the
    bare command could never score anything while the unit tests stayed green.
    """
    from personalclaw.evals import retrieval_bench as rb

    picked = str(getattr(args, "store", "both") or "both")
    stores = rb.STORES if picked == "both" else (picked,)
    k = max(1, int(getattr(args, "k", rb.DEFAULT_K) or rb.DEFAULT_K))
    label_path = str(getattr(args, "label", "") or "")

    labels: dict = {}
    if label_path:
        try:
            card = json.loads(Path(label_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Error: unreadable card {label_path}: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        # Accept the card shape this command PRINTS, so the round-trip is copy-edit-apply
        # and not "now transform it into some other schema".
        card_store = str(card.get("store", "") or "")
        if card_store and card_store not in rb.STORES:
            print(f"Error: card declares unknown store {card_store!r}.", file=sys.stderr)
            raise SystemExit(1)
        if card_store:
            stores = (card_store,)
        for entry in card.get("queries") or []:
            # KEY PRESENCE, not truthiness: `"relevant": []` is the human saying "none of
            # these answer it", which is a real judgement. `or already_relevant` would make
            # an empty list fall through and silently re-inherit the MINED label — the one
            # thing the hand pass exists to overrule.
            if "relevant" in entry:
                marked = entry.get("relevant") or []
            elif "already_relevant" in entry:
                marked = entry.get("already_relevant") or []
            else:
                continue
            labels[str(entry.get("query", ""))] = [str(i) for i in marked]
        if not labels:
            print(
                'Error: no labels in the card. Mark each query\'s answers under "relevant": '
                '["<id>", ...] before applying it.',
                file=sys.stderr,
            )
            raise SystemExit(1)

    for store_kind in stores:
        print(f"\n=== {store_kind} store ===")
        handle, db_path = rb.open_store(store_kind)
        try:
            benchmark = rb.build_benchmark(store_kind, handle)
            if labels:
                benchmark = rb.apply_hand_labels(benchmark, labels)
            path = rb.save_benchmark(benchmark)
            hand = sum(1 for q in benchmark.queries if q.source == rb.SOURCE_HAND_LABEL)
            print(
                f"qrels: {len(benchmark.queries)} queries "
                f"({hand} hand-labelled, {len(benchmark.queries) - hand} mined)\n"
                f"corpus: {benchmark.corpus_snapshot_ref}\n"
                f"saved:  {path}"
            )
            if getattr(args, "mine", False) or labels:
                continue
            if getattr(args, "card", False):
                retriever = rb.retriever_for(store_kind, handle)
                print(json.dumps(rb.hand_label_card(benchmark, retriever), indent=2))
                continue
            try:
                result = rb.run_retrieval_bench(
                    store_kind, benchmark=benchmark, handle=handle, db_path=db_path, k=k
                )
            except rb.EmptyBenchmarkError as exc:
                # Not a crash and NOT a zero score: a store with no labels has no P@k, and
                # printing 0.0 here would file "retrieval is broken" as a finding.
                print(f"Not measured: {exc}")
                continue
            except (rb.MaskNotAppliedError, rb.StoreMutatedError) as exc:
                print(f"REFUSED: {exc}", file=sys.stderr)
                raise SystemExit(1) from exc
            _print_retrieval_report(result)
        finally:
            closer = getattr(handle, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 - a close fault must not hide a report
                    pass


def _print_retrieval_report(result) -> None:
    """The published table + per-arm contributions, with absences spelled out."""
    from personalclaw.evals import retrieval_bench as rb

    def _num(value) -> str:
        return "n/a" if value is None else f"{float(value):.4f}"

    if result.corpus_drifted:
        print(
            "  ⚠ corpus DRIFTED since these labels were made — R@k's denominator is no "
            "longer the one that was judged."
        )
    dead = sorted(arm for arm, live in (result.executors or {}).items() if not live)
    if dead:
        # Not "no executor": the arm's component can be present, bound and reporting itself
        # available while retrieving nothing (an expired embedding credential does exactly
        # this). The observation is about the RUN's output, so the sentence has to be too —
        # and there is no delta to describe, because one is not published for a dead arm.
        print(
            f"  ⚠ no candidates from: {', '.join(dead)} — that arm returned nothing under "
            "its own mask, so it never ran and has no measured contribution. Check that "
            "the arm's model is bound AND reachable; an expired credential leaves it bound "
            "and embedding nothing, which looks identical here."
        )
    print(f"  run: evals/matrices/{result.bench_id}/")
    print(f"  {'mask':<24}{'P@k':>9}{'R@k':>9}{'scored':>8}{'no-cand':>9}")
    for row in result.table:
        print(
            f"  {row.mask:<24}{_num(row.p_at_k):>9}{_num(row.r_at_k):>9}"
            f"{row.scored_queries:>8}{row.no_candidate_queries:>9}"
        )
    print("  per-arm marginal contribution (full − leave-one-out):")
    for contrib in result.contributions:
        print(
            f"    {contrib.arm:<10} ΔP@k={_num(contrib.contribution_p):>9} "
            f"ΔR@k={_num(contrib.contribution_r):>9} solo P@k={_num(contrib.solo_p_at_k):>9} "
            f"→ {contrib.verdict}" + (f"  ({contrib.reasons[0]})" if contrib.reasons else "")
        )
    print(
        f"  enable threshold: ΔP@k >= {rb.MIN_ARM_CONTRIBUTION} over "
        f">= {rb.MIN_SCORED_QUERIES} scored queries"
    )


def _learn(args: argparse.Namespace) -> None:
    """Save, list, or remove learned corrections in memory.db ``lesson.*``."""

    from personalclaw.memory_service import MemoryService

    # memory.db is the sole lesson store; a store with no embedder still persists
    # lessons (vector optional). ``write_lesson`` returning False means the lesson
    # was a dedup/supersession no-op, not that the store was unavailable.
    vs = VectorMemoryStore()
    vs.init()
    svc = MemoryService.over_vector_store(vs)
    try:
        action = getattr(args, "learn_action", None)

        if action == "add":
            rule = args.rule
            category = args.category
            negative = getattr(args, "negative", None)
            svc.write_lesson(rule, category, negative)
            neg = f" ({negative})" if negative else ""
            print(f"Saved: {rule}{neg} [{category}]")

        elif action == "list":
            vs_lessons = svc.get_lessons()
            if not vs_lessons:
                print("No lessons.")
                return
            for e in vs_lessons:
                val = json.loads(e["value_json"])
                print(f"  [knowledge] {val}")

        elif action == "remove":
            if not svc.delete_lesson(args.query):
                _refuse(f"No lessons match: {args.query}")
            print(f"Removed lessons matching: {args.query}")
    finally:
        vs.close()


def _memory_cmd(args: argparse.Namespace) -> None:
    """Manage the memory system (record store) via the service."""
    from personalclaw.memory_service import MemoryService

    store = VectorMemoryStore()
    store.init()
    svc = MemoryService.over_vector_store(store)
    try:
        action = getattr(args, "mem_action", None)

        if action == "list":
            entries = svc.get_all_semantic()
            if not entries:
                print("No semantic memory entries.")
                return
            for e in entries:
                try:
                    val = json.loads(e["value_json"])
                except Exception:
                    val = e["value_json"]
                print(f"  {e['key']}: {val}  (confidence={e['confidence']}, source={e['source']})")

        elif action == "search":
            results = svc.search_episodic(query_text=args.query, limit=10)
            if not results:
                print("No episodic memories found.")
                return
            for r in results:
                tags = (
                    json.loads(r.get("tags", "[]"))
                    if isinstance(r.get("tags"), str)
                    else r.get("tags", [])
                )
                print(f"  [{r.get('importance', 0):.1f}] {r['text'][:120]}")
                if tags:
                    print(f"        tags: {', '.join(tags)}")

        elif action == "stats":
            stats = store.memory_stats()
            print(
                f"  Semantic: {stats['semantic_active']} active, {stats['semantic_deleted']} deleted"  # noqa: E501
            )
            print(
                f"  Episodic: {stats['episodic_active']} active, {stats['episodic_deleted']} deleted"  # noqa: E501
            )
            if "faiss_index_size" in stats:
                print(f"  FAISS index: {stats['faiss_index_size']} vectors")
            else:
                print(f"  FAISS index: none ({NO_INDEX_NOTE})")
            print(f"  Audit events: {stats['events_count']}")

        elif action == "audit":
            findings = scan_memory()
            if findings:
                print(f"⚠️  {len(findings)} suspicious entries:\n")
                for f in findings:
                    print(f"  [{f['type']}] {f['key']}: {f['warning']}")
                    print(f"    {f['value'][:120]}\n")
            else:
                print("✅ No suspicious content in memory.")

        elif action == "export":
            data = {
                "semantic": store.get_all_semantic(),
                "episodic": store.get_episodic_list(limit=10000),
                "events": store.get_events(limit=1000),
            }
            output = json.dumps(data, indent=2, default=str)
            out_file = getattr(args, "output", None)
            if out_file:
                # Every memory: private from its first byte, wherever the user writes it.
                write_private_file(out_file, output)
                print(f"Exported to {out_file}")
            else:
                print(output)

        elif action == "migrate":
            counts = store.migrate_from_markdown()
            print("Migration complete:")
            print(f"  Semantic: {counts['semantic']}")
            print(f"  Episodic: {counts['episodic']}")
            print(f"  Skipped:  {counts['skipped']}")

        elif action == "import":
            import_file = getattr(args, "file", None)
            if not import_file:
                _usage_error("Usage: personalclaw memory import <file>")
            path = Path(import_file)
            if not path.is_file():
                _refuse(f"File not found: {import_file}")
            data = json.loads(safe_read_file(str(path)))
            if not isinstance(data, dict):
                _refuse(f"Error: {import_file} must contain a JSON object")
            counts = store.import_memory(data)
            print("Import complete:")
            print(f"  Semantic: {counts['semantic']}")
            print(f"  Episodic: {counts['episodic']}")
            print(f"  Skipped:  {counts['skipped']}")
    finally:
        store.close()
