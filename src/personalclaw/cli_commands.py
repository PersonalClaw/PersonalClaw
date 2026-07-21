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
from personalclaw.learn import Lesson, LessonStore
from personalclaw.schedule import ScheduleDefinition, ScheduleService, format_schedule
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


def _format_schedule(schedule: object) -> str:
    """Human-readable schedule description (CLI shows full date for 'at' jobs)."""

    if not isinstance(schedule, ScheduleDefinition):
        return str(schedule)
    if schedule.kind == "at" and schedule.at_ts:

        dt = datetime.fromtimestamp(schedule.at_ts)
        return f"at {dt:%Y-%m-%d %H:%M}"
    return format_schedule(schedule)


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


def _cron(args: argparse.Namespace) -> None:
    """Dispatch cron subcommands: list, add, remove, pause, resume."""

    svc = ScheduleService(base_dir=config_dir())

    action = getattr(args, "cron_action", None)
    if action == "list":
        jobs = svc.list_jobs(include_disabled=True)
        if not jobs:
            print("No cron jobs.")
            return
        for j in jobs:
            status = "✅" if j.enabled else "⏸️"
            sched = _format_schedule(j.schedule)
            print(f"  {status} {j.id}  {j.name}  ({sched})  {j.message[:60]}")

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
        from personalclaw.schedule import make_agent_action

        action = make_agent_action(message=args.message, approval_mode=approval_mode)
        if cron_expr:
            job = svc.add_job(
                name=args.name,
                action=action,
                cron_expr=cron_expr,
                channel=channel,
            )
        elif every:
            job = svc.add_job(
                name=args.name,
                action=action,
                every_secs=every,
                channel=channel,
            )
        else:
            print("Provide --every or --cron")
            return
        sched_desc = _format_schedule(job.schedule)

        sel().log_api_access(
            caller="cli",
            operation="cron.add",
            outcome="allowed",
            source="cli",
            resources=f"job_id={job.id} approval_mode={approval_mode or 'default'}",
        )
        print(f"Added job: {job.id} ({job.name}) [{sched_desc}]")

    elif action == "update":
        kwargs: dict = {}
        for field in ("name", "message", "every_secs", "cron_expr", "channel"):
            val = getattr(args, field, None)
            if val is not None:
                if field == "channel":

                    val = val.strip() or None
                    if val is None:
                        continue
                    if len(val) > CHANNEL_MAX_LEN or not CHANNEL_ID_RE.match(val):
                        print(
                            f"Error: invalid channel ID format (expected {CHANNEL_ID_RE.pattern.strip('^$')})"  # noqa: E501
                        )
                        return
                kwargs[field] = val
        if getattr(args, "approval_mode", None) is not None:
            kwargs["approval_mode"] = "" if args.approval_mode == "default" else args.approval_mode
        if not kwargs:
            print("Provide at least one field to update")
            return
        if "every_secs" in kwargs and "cron_expr" in kwargs:
            print("Provide --every or --cron, not both")
            return
        updated = svc.update_job(args.job_id, **kwargs)
        if updated:

            sel().log_api_access(
                caller="cli",
                operation="cron.update",
                outcome="allowed",
                source="cli",
                resources=f"job_id={args.job_id} fields={','.join(sorted(kwargs))}",
            )
            print(f"Updated job: {updated.id} ({updated.name})")
        else:

            sel().log_api_access(
                caller="cli",
                operation="cron.update",
                outcome="not_found",
                source="cli",
                resources=f"job_id={args.job_id} reason=not_found",
            )
            print(f"Job not found: {args.job_id}")

    elif action == "remove":
        if svc.remove_job(args.job_id):
            print(f"Removed job: {args.job_id}")
        else:
            print(f"Job not found: {args.job_id}")

    elif action == "pause":
        if svc.enable_job(args.job_id, enabled=False):
            print(f"Paused job: {args.job_id}")
        else:
            print(f"Job not found: {args.job_id}")

    elif action == "resume":
        if svc.enable_job(args.job_id, enabled=True):
            print(f"Resumed job: {args.job_id}")
        else:
            print(f"Job not found: {args.job_id}")

    elif action == "trigger":
        # Fire via the RUNNING gateway (the local svc has no live timer).
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

    scenarios_dir = Path(__file__).resolve().parent / "eval" / "scenarios"

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
    """Save, list, or remove learned corrections."""

    from personalclaw.memory_service import MemoryService

    jsonl_store = LessonStore()
    vs = VectorMemoryStore(embedding_dim=get_active_embedding_dim() or 384)
    vs.init()
    svc = MemoryService.over_vector_store(vs)
    try:
        action = getattr(args, "learn_action", None)

        if action == "add":
            rule = args.rule
            category = args.category
            negative = getattr(args, "negative", None)
            if svc.write_lesson(rule, category, negative):
                neg = f" ({negative})" if negative else ""
                print(f"Saved: {rule}{neg} [{category}]")
            else:
                lesson = Lesson(
                    ts=datetime.now(timezone.utc).isoformat(),
                    rule=rule,
                    category=category,
                    negative=negative,
                )
                jsonl_store.save(lesson)
                neg = f" ({lesson.negative})" if lesson.negative else ""
                print(f"Saved: {lesson.rule}{neg} [{lesson.category}]")

        elif action == "list":
            vs_lessons = svc.get_lessons()
            if vs_lessons:
                for e in vs_lessons:
                    val = json.loads(e["value_json"])
                    print(f"  [knowledge] {val}")
            else:
                lessons = jsonl_store.load_all()
                if not lessons:
                    print("No lessons.")
                    return
                for le in lessons:
                    neg = f" — {le.negative}" if le.negative else ""
                    print(f"  [{le.category}] {le.rule}{neg}")

        elif action == "remove":
            if svc.get_lessons() and svc.delete_lesson(args.query):
                print(f"Removed lessons matching: {args.query}")
            elif jsonl_store.remove(args.query):
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
            counts = store.import_memory(data)
            print("Import complete:")
            print(f"  Semantic: {counts['semantic']}")
            print(f"  Episodic: {counts['episodic']}")
            print(f"  Skipped:  {counts['skipped']}")

        else:
            print("Usage: personalclaw memory {list|search|stats|audit|export|migrate|import}")
    finally:
        store.close()
