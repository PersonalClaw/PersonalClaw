"""CLI subcommand handlers — cron, spawn, app, agent, security, eval, learn, memory."""

import argparse
import json
import sys
import time as _time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.config import config_dir
from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.embedding_providers.registry import get_active_embedding_dim
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
from personalclaw.validation import CHANNEL_ID_RE, CHANNEL_MAX_LEN
from personalclaw.vector_memory import VectorMemoryStore


def _spawn(args: argparse.Namespace) -> None:
    """Dispatch spawn subcommands: run, list."""
    base = f"http://localhost:{args.port}"
    action = getattr(args, "spawn_action", None)

    if action == "list":
        try:
            with urllib.request.urlopen(f"{base}/api/spawn", timeout=5) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, OSError):
            print("Error: gateway not running (cannot reach dashboard on port %d)" % args.port)
            sys.exit(1)
        agents = data.get("agents", [])
        if not agents:
            print("No subagents.")
            return
        for a in agents:
            status = "✅" if a.get("done") else "⏳"
            print(f"  {status} {a['id']}  {a.get('task', '')[:60]}")
        return

    if action == "run":
        _spawn_run(args, base)
        return

    print("Usage: personalclaw spawn {run|list}")


def _spawn_run(args: argparse.Namespace, base: str) -> None:
    """Spawn a subagent via the dashboard API."""
    data = json.dumps({"task": args.task}).encode()
    req = urllib.request.Request(
        f"{base}/api/spawn", data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            print(f"Error: {body.get('error', e.reason)}")
        except Exception:
            print(f"Error: {e.code} {e.reason}")
        sys.exit(1)
    except (urllib.error.URLError, OSError):
        print("Error: gateway not running (cannot reach dashboard on port %d)" % args.port)
        sys.exit(1)

    agent_id = result["id"]

    if args.fire_and_forget:
        print(f"Spawned subagent {agent_id}: {result['task']}")
        return

    # Block: poll until done

    print(f"Spawned subagent {agent_id}, waiting for result...", file=sys.stderr)
    poll_url = f"{base}/api/spawn/{agent_id}"
    while True:
        _time.sleep(2)
        try:
            with urllib.request.urlopen(poll_url, timeout=5) as resp:
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


def _handle_agent(args: argparse.Namespace) -> None:
    """Dispatch agent subcommands: list, create, update, delete."""

    action = getattr(args, "agent_action", None)
    cfg = AppConfig.load()

    if action == "list":
        default = cfg.default_agent
        print(f"{'NAME':<20} {'PROVIDER_AGENT':<20} {'DEFAULT_DIR':<15} {'MEMORY_STORE':<15}")
        for name, agent in cfg.agents.items():
            marker = " *" if name == default else ""
            print(
                f"{name + marker:<20} {agent.provider_agent:<20} "
                f"{agent.default_dir:<15} {agent.memory_store:<15}"
            )

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

    else:
        print("Usage: personalclaw agent {list|create|update|delete}")


def _pair(args: argparse.Namespace) -> None:
    """Mint a one-time channel pairing code and print it ONCE (CE-1 T1.5).

    A new sender on a channel (Telegram, Discord, …) redeems this code to start talking to
    the agent: within the 10-minute TTL it allow-lists them once, then the code is spent.
    The code is printed to the owner's terminal only — never persisted in plaintext, never
    logged. Direct store call (no running gateway required): pairing is owner-side setup."""
    from personalclaw.channel_trust import PAIRING_CODE_TTL_SECS, create_pairing_code

    provider = (getattr(args, "provider", "") or "").strip().lower()
    if not provider:
        print("❌ Usage: personalclaw pair <provider>   (e.g. telegram, discord, email)")
        sys.exit(1)
    code = create_pairing_code(provider)
    minutes = PAIRING_CODE_TTL_SECS // 60
    print(f"Pairing code for {provider}: {code}")
    print(
        f"Have the new sender send this code to the {provider} bot within {minutes} minutes. "
        "It works once, then expires."
    )


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
        return
    print("Usage: personalclaw automation verify-migration [--json]")


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
    """
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
            # 🔴 The SHARED projection's `message`, not a hand-read config key. Measured: reading
            # `config["message"]` printed a BLANK column for every `invoke-agent` trigger, because
            # that provider's key is `task_template` — and `run-prompt`/`notify` differ again.
            # `to_schedule_row` already resolves all of them (schedule_view.py:171), which is the
            # whole reason it exists.
            detail = str(_sv.to_schedule_row(trigger).get("message") or "") if row.ok else ""
            print(f"  {status} {trigger.id}  {trigger.name}  ({sched})  {detail[:60]}")

    elif action == "add":
        every = getattr(args, "every", None)
        cron_expr = getattr(args, "cron_expr", None)
        channel = (getattr(args, "channel", None) or "").strip() or None
        approval_mode = getattr(args, "approval_mode", "") or ""
        if channel:
            if len(channel) > CHANNEL_MAX_LEN or not CHANNEL_ID_RE.match(channel):
                print(
                    f"Error: invalid channel ID format (expected {CHANNEL_ID_RE.pattern.strip('^$')})"  # noqa: E501
                )
                return
        if cron_expr:
            spec = {"kind": "cron", "expr": cron_expr}
        elif every:
            spec = {"kind": "interval", "interval_secs": int(every)}
        else:
            print("Provide --every or --cron")
            return

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
        )
        if not result.ok:
            print(result.text)
            sel().log_api_access(
                caller="cli",
                operation="cron.add",
                outcome="denied",
                source="cli",
                resources=f"name={args.name}",
                error=result.text,
            )
            return
        trigger_id = str((result.data.get("trigger") or {}).get("id") or "")
        if channel:
            # Delivery is not a `create` parameter, so it is a follow-up patch through the same
            # allowlist. Done after the create rather than by building the row here, so the CLI
            # never becomes a second write path with its own validation.
            _tools.update(store, trigger_id=trigger_id, patch={"delivery": f"channel:{channel}"})
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
                if len(val) > CHANNEL_MAX_LEN or not CHANNEL_ID_RE.match(val):
                    print(
                        f"Error: invalid channel ID format (expected {CHANNEL_ID_RE.pattern.strip('^$')})"  # noqa: E501
                    )
                    return
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
            print("Provide at least one field to update")
            return
        if getattr(args, "every_secs", None) is not None and (
            getattr(args, "cron_expr", None) is not None
        ):
            print("Provide --every or --cron, not both")
            return

        existing = store.get(args.job_id)
        if existing is None:
            sel().log_api_access(
                caller="cli",
                operation="cron.update",
                outcome="not_found",
                source="cli",
                resources=f"job_id={args.job_id} reason=not_found",
            )
            print(f"Job not found: {args.job_id}")
            return

        if spec_update:
            # Carry the quietly-losable spec keys (`timezone`/`skip_dates`/`strict`) rather than
            # replacing the spec wholesale — the contract §1.3 and S101 both record. The re-arm
            # happens AFTER the patch lands (see below), because `next_fire_at` is engine state the
            # patch allowlist deliberately refuses.
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

        result = _tools.update(store, trigger_id=args.job_id, patch=patch)
        if result.ok and spec_update:
            # 🔴 RE-ARM AFTER A CADENCE CHANGE. Found by driving: `--cron "30 7 * * *"` reported
            # success and the list showed 07:30, but `next_fire_at` still held the OLD 09:00 — so
            # the job would have fired on the schedule the user had just replaced. `next_fire_at` is
            # deliberately NOT in `PATCHABLE` (it is engine state, not user input), so the arm is a
            # separate clear-then-arm — the shape S101 established for the API's PUT.
            from personalclaw.triggers.arm import arm as _arm

            fresh = store.get(args.job_id)
            if fresh is not None:
                fresh.trigger.next_fire_at = ""
                armed = _arm(fresh.trigger)
                if armed:
                    fresh.trigger.next_fire_at = armed
                store.upsert(fresh.trigger)
        sel().log_api_access(
            caller="cli",
            operation="cron.update",
            outcome="allowed" if result.ok else "denied",
            source="cli",
            resources=f"job_id={args.job_id} fields={','.join(sorted(patch))}",
            error="" if result.ok else result.text,
        )
        print(result.text)

    elif action == "remove":
        # `confirm=True`: the flag exists so a TOOL CALL cannot delete by accident. A human who
        # typed `cron remove <id>` has already expressed the intent, and prompting again for what
        # the command literally says would be theatre.
        result = _tools.delete(store, trigger_id=args.job_id, confirm=True)
        print(result.text if result.ok else f"Job not found: {args.job_id}")

    elif action == "pause":
        result = _tools.set_paused(store, trigger_id=args.job_id, paused=True)
        print(result.text if result.ok else f"Job not found: {args.job_id}")

    elif action == "resume":
        result = _tools.set_paused(store, trigger_id=args.job_id, paused=False)
        # The text is printed on failure too: `set_paused` REFUSES to resume a row with a parse
        # error and names the error, which is strictly more useful than "Job not found" — and the
        # row does exist, so the old message would have been wrong as well as unhelpful.
        print(result.text)

    elif action == "trigger":
        # Fire via the RUNNING gateway (a CLI process has no clock loop).
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
        print(message if ok else f"Error: {message}")

    else:
        print("Usage: personalclaw cron {list|add|update|remove|pause|resume|trigger}")


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
            if e.get("downstream_service"):
                print(f"    downstream: {e['downstream_service']}")
    elif action == "verify":

        # CLI verify is an explicit offline audit — check the entire chain.
        total, valid = sel().verify_integrity(max_entries=None)
        if total == 0:
            print("No security events to verify.")
        elif total == valid:
            print(f"✅ HMAC chain intact: {total} entries verified.")
        else:
            print(
                f"⚠️  HMAC chain COMPROMISED: {valid}/{total} entries valid, {total - valid} tampered."  # noqa: E501
            )
    else:
        print("Usage: personalclaw security {audit|deny-list|events|verify}")


async def _run_eval(args: argparse.Namespace) -> None:
    """Run multi-session evaluation scenarios."""

    # ES-2: the scenario set is no longer packaged-read-only under eval/scenarios/ —
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
                print(f"Error: scenario '{name}' not found.")
                print(f"Available scenarios: {', '.join(available)}")
                return
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
    json_path.write_text(json.dumps(json_data, indent=2) + "\n")

    print(f"\nResults saved to:\n  {report_path}\n  {json_path}")


def _learn(args: argparse.Namespace) -> None:
    """Save, list, or remove learned corrections in memory.db ``lesson.*``."""

    from personalclaw.memory_service import MemoryService

    # memory.db is the sole lesson store; a store with no embedder still persists
    # lessons (vector optional). ``write_lesson`` returning False means the lesson
    # was a dedup/supersession no-op, not that the store was unavailable.
    vs = VectorMemoryStore(embedding_dim=get_active_embedding_dim() or 384)
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
            if svc.delete_lesson(args.query):
                print(f"Removed lessons matching: {args.query}")
            else:
                print(f"No lessons match: {args.query}")

        else:
            print("Usage: personalclaw learn {add|list|remove}")
    finally:
        vs.close()


def _memory_cmd(args: argparse.Namespace) -> None:
    """Manage the memory system (record store) via the service."""
    from personalclaw.memory_service import MemoryService

    store = VectorMemoryStore(embedding_dim=get_active_embedding_dim() or 384)
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
            print(f"  FAISS index: {stats['faiss_index_size']} vectors")
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
                Path(out_file).write_text(output, encoding="utf-8")
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
                print("Usage: personalclaw memory import <file>")
                return
            path = Path(import_file)
            if not path.is_file():
                print(f"File not found: {import_file}")
                return
            data = json.loads(safe_read_file(str(path)))
            if not isinstance(data, dict):
                print(f"Error: {import_file} must contain a JSON object", file=sys.stderr)
                return
            counts = store.import_memory(data)
            print("Import complete:")
            print(f"  Semantic: {counts['semantic']}")
            print(f"  Episodic: {counts['episodic']}")
            print(f"  Skipped:  {counts['skipped']}")

        else:
            print("Usage: personalclaw memory {list|search|stats|audit|export|migrate|import}")
    finally:
        store.close()
