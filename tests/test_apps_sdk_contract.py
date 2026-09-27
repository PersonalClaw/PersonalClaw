"""The apps' SDK contract runs on every change that can alter the SDK, and charges it fairly.

``ci.yml``'s ``apps-contract`` job checks out PersonalClawApps and runs its SDK contract checks
against the core under review (``scripts/apps_sdk_contract.py``); the ``changes`` job decides
whether it runs (``scripts/ci_touches_sdk.py``). Railed here in three parts:

* the trigger — every SDK path runs it, other paths do not, an unanswered question runs it, and
  the workflow wires the verdict to the job (a classifier nobody calls decides nothing);
* the charge — a problem on an SDK symbol this change did not touch is the app's own and does
  not red a core PR (a scan of the apps must not make one app's bug every PR's red), while one
  on a touched symbol, or one that maps to nothing, does;
* the CHANGELOG rule — an SDK change has an entry, and its headline names every app that uses
  what changed: by bundle name in backticks, or by a backticked glob that matches only those
  apps.

The testkit itself lives in the apps repository, so the end-to-end test drives ``main()`` over a
fake apps checkout whose ``apps_testkit`` reports one problem — the shape of the real one.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from scripts import apps_sdk_contract as job
from scripts import ci_touches_sdk
from scripts import sdk_signature_snapshot as snap

REPO = Path(__file__).resolve().parents[1]


# ── the trigger ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "src/personalclaw/sdk/channel.py",
        "src/personalclaw/sdk/signatures.json",
        "scripts/apps_sdk_contract.py",
        "scripts/sdk_signature_snapshot.py",
        ".github/workflows/ci.yml",
    ],
)
def test_a_path_that_can_change_the_sdk_runs_the_job(path):
    assert ci_touches_sdk.touches_sdk([path])


@pytest.mark.parametrize(
    "path", ["src/personalclaw/context.py", "web/src/App.tsx", "docs/guides/x.md", "tests/t.py"]
)
def test_other_paths_alone_do_not(path):
    """`context.py` among them: a signature change there must regenerate the snapshot, which is
    what puts the change under src/personalclaw/sdk/."""
    assert not ci_touches_sdk.touches_sdk([path])


def test_an_unenumerated_diff_runs_the_job():
    assert ci_touches_sdk.touches_sdk([]) and ci_touches_sdk.touches_sdk(["", "  "])


def test_the_classifier_runs_as_a_cli():
    out = subprocess.run(
        [sys.executable, "scripts/ci_touches_sdk.py"],
        input="src/personalclaw/sdk/cli.py\n",
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "sdk=true"


def _ci() -> dict:
    return yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_the_workflow_wires_the_verdict_to_the_job():
    jobs = _ci()["jobs"]
    assert jobs["changes"]["outputs"]["sdk"] == "${{ steps.sdk.outputs.sdk }}"
    step = next(s for s in jobs["changes"]["steps"] if s.get("id") == "sdk")
    assert (
        "scripts/ci_touches_sdk.py" in step["run"] and 'verdict="sdk=true"' in step["run"]
    ), "the verdict must fall back to RUNNING the job when the classifier gives no answer"
    contract = jobs["apps-contract"]
    assert contract["needs"] == ["changes"]
    assert "needs.changes.outputs.sdk == 'true'" in contract["if"]
    runs = " ".join(str(s.get("run", "")) for s in contract["steps"])
    assert "scripts/apps_sdk_contract.py" in runs
    assert "--base-ref" in runs and "--install-deps" in runs
    repos = [s.get("with", {}).get("repository") for s in contract["steps"]]
    assert "PersonalClaw/PersonalClawApps" in repos


# ── the charge ────────────────────────────────────────────────────────────────────────────────

_UNCHANGED = "personalclaw.sdk.credentials.CredentialStore"
_CHANGED = "personalclaw.sdk.channel.compress_thread_history"


def _problem(*symbols: str) -> job.Problem:
    return job.Problem("demo-app", "demo-app/x.py:3", "demo-app/x.py:3: …", frozenset(symbols))


def test_a_problem_on_an_untouched_symbol_is_the_apps_own():
    """vector-store-qdrant's `CredentialStore()` without `home` was one, until PersonalClawApps
    #125: a core PR that did not touch `CredentialStore` must not be charged with it."""
    assert not job.charged_to_change(_problem(_UNCHANGED), {_CHANGED}, have_base=True)


def test_a_problem_on_a_touched_symbol_is_charged():
    assert job.charged_to_change(_problem(_CHANGED), {_CHANGED}, have_base=True)


def test_a_problem_that_maps_to_no_symbol_is_charged():
    """Nothing shows it predates the change."""
    assert job.charged_to_change(_problem(), {_CHANGED}, have_base=True)


def test_without_a_base_snapshot_nothing_is_charged():
    assert not job.charged_to_change(_problem(_CHANGED), {_CHANGED}, have_base=False)


def test_an_alias_of_a_changed_symbol_is_changed():
    snapshot = {"a.X": {"kind": "class"}, "b.X": {"kind": "alias", "of": "a.X"}}
    assert job.expand_aliases({"a.X"}, snapshot) == {"a.X", "b.X"}


# ── the CHANGELOG rule ───────────────────────────────────────────────────────────────────────


def _change(symbol: str = _CHANGED) -> snap.Change:
    return snap.Change(symbol, "changed", ["signature: …"], False, [])


#: A bundle list with a family and an app whose name is also an English word.
_APPS = ["discord-channel", "notes", "openai-models", "slack-channel", "telegram-channel"]


def _sites(*apps: str) -> dict[str, list[str]]:
    return {app: [f"{app}/runtime.py:7"] for app in apps}


def test_an_sdk_change_needs_a_changelog_entry():
    (violation,) = job.changelog_violations([_change()], {}, [], _APPS)
    assert "no new entry" in violation


def test_the_headline_must_name_every_app_that_uses_what_changed():
    affected = {"slack-channel": ["slack-channel/slack_runtime/handler.py:2117"]}
    (violation,) = job.changelog_violations([_change()], affected, ["Something changed."], _APPS)
    assert "`slack-channel`" in violation and "handler.py:2117" in violation
    named = ["`compress_thread_history` takes a list of turns, and `slack-channel` passes one."]
    assert job.changelog_violations([_change()], affected, named, _APPS) == []


def test_only_a_backticked_bundle_name_names_an_app():
    """`notes` is an app and a word: "the release notes" in a headline names nothing."""
    affected = _sites("notes")
    assert job.changelog_violations([_change()], affected, ["The release notes say so."], _APPS)
    assert (
        job.changelog_violations([_change()], affected, ["`notes` passes the new id."], _APPS) == []
    )


def test_a_family_glob_names_every_app_it_matches():
    """The compact form of a long list: `*-channel` names all four channel apps at once."""
    affected = _sites("discord-channel", "slack-channel", "telegram-channel")
    headline = ["`guard_inbound` takes the channel's name (`*-channel`)."]
    assert job.changelog_violations([_change()], affected, headline, _APPS) == []


def test_a_glob_that_also_names_an_unaffected_app_is_refused():
    """A glob is an exact list written short, not a blanket: `*-channel` claims telegram-channel
    is affected, and it is not."""
    affected = _sites("discord-channel", "slack-channel")
    (violation,) = job.changelog_violations([_change()], affected, ["`*-channel` update."], _APPS)
    assert "`*-channel` also names telegram-channel" in violation


def test_a_blanket_glob_cannot_stand_in_for_the_list():
    (violation,) = job.changelog_violations([_change()], _sites("notes"), ["`*` update."], _APPS)
    assert "also names discord-channel, openai-models, slack-channel" in violation


def test_a_glob_that_names_no_app_is_refused():
    (violation,) = job.changelog_violations([_change()], {}, ["`*-chanel` gain a field."], _APPS)
    assert "`*-chanel` names no app" in violation


def test_no_sdk_change_needs_no_entry():
    assert job.changelog_violations([], {"slack-channel": ["x"]}, [], _APPS) == []


def test_added_headlines_are_the_entries_the_tree_has_over_the_base():
    head = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert job.added_headlines(head) == []
    entry = next(line for line in head.splitlines() if line.startswith("- **"))
    base = "\n".join(line for line in head.splitlines() if line != entry)
    assert job.added_headlines(base) == [entry[len("- **") : -len("**")]]


def test_only_the_headline_is_read(tmp_path, monkeypatch):
    """Text after a headline and a continuation line are bodies, and a body names nothing."""
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n"
        "- **`compress_thread_history` takes a list of turns.**\n"
        "- **`guard_inbound` changed.** `slack-channel` must update.\n"
        "  `discord-channel` too.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(job, "REPO", tmp_path)
    assert job.added_headlines("") == ["`compress_thread_history` takes a list of turns."]


# ── end to end, over a fake apps checkout ───────────────────────────────────────────────────

_FAKE_TESTKIT = textwrap.dedent('''
    """A stand-in for PersonalClawApps' apps_testkit.sdk_contract, reporting one import problem."""
    from dataclasses import dataclass
    from pathlib import Path


    @dataclass
    class Imp:
        path: Path
        lineno: int
        module: str
        name: str


    @dataclass
    class Census:
        imports: list
        calls: list


    def census(app_dir):
        return Census([Imp(app_dir / "handler.py", 3, "personalclaw.sdk.channel",
                           "compress_thread_history")], [])


    def unpublished_imports(app_dir):
        return [f"{app_dir.name}/handler.py:3: personalclaw.sdk.channel.compress_thread_history "
                "is broken, in the fake"]


    def unbindable_calls(app_dir):
        return []
    ''')


def _fake_apps(tmp_path: Path) -> Path:
    apps = tmp_path / "apps-checkout"
    (apps / "apps_testkit").mkdir(parents=True)
    (apps / "apps_testkit" / "__init__.py").write_text("", encoding="utf-8")
    (apps / "apps_testkit" / "sdk_contract.py").write_text(_FAKE_TESTKIT, encoding="utf-8")
    (apps / "demo-app").mkdir()
    (apps / "demo-app" / "app.json").write_text(json.dumps({"name": "demo-app"}), "utf-8")
    return apps


def _run(monkeypatch, tmp_path, base: dict | None, changelog_base: str | None) -> tuple[int, str]:
    shown = {
        job.SNAPSHOT_REL: json.dumps(base) if base is not None else None,
        "CHANGELOG.md": changelog_base,
    }
    monkeypatch.setattr(job, "git_show", lambda ref, rel: shown[rel])
    monkeypatch.delitem(sys.modules, "apps_testkit", raising=False)
    monkeypatch.delitem(sys.modules, "apps_testkit.sdk_contract", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    code = job.main(["--apps-dir", str(_fake_apps(tmp_path)), "--base-ref", "base"])
    return code


def test_an_apps_problem_on_an_unchanged_sdk_is_reported_and_not_charged(
    monkeypatch, tmp_path, capsys
):
    live = snap.snapshot()
    code = _run(monkeypatch, tmp_path, live, (REPO / "CHANGELOG.md").read_text("utf-8"))
    out = capsys.readouterr().out
    assert code == 0, out
    assert "pre-existing demo-app/handler.py:3" in out and "RESULT: pass" in out


def _base_before_the_type_change() -> dict:
    """The live snapshot with `compress_thread_history`'s first parameter as it was before
    #3599 — so the live SDK reads as that change."""
    base = snap.snapshot()
    base[_CHANGED] = {
        **base[_CHANGED],
        "params": [
            {
                "annotation": "ConversationLog",
                "kind": "positional_or_keyword",
                "name": "conversation_log",
            },
            *base[_CHANGED]["params"][1:],
        ],
    }
    return base


def test_the_same_problem_after_the_symbol_changed_is_charged_and_names_the_app(
    monkeypatch, tmp_path, capsys
):
    base = _base_before_the_type_change()
    code = _run(monkeypatch, tmp_path, base, (REPO / "CHANGELOG.md").read_text("utf-8"))
    out = capsys.readouterr().out
    assert code == 1, out
    assert "❌ demo-app/handler.py:3" in out
    assert "affected: demo-app" in out
    assert "silent break: compress_thread_history: position 0 replaced" in out
    assert "CHANGELOG" in out and "no new entry" in out


def test_a_new_headline_naming_the_affected_app_satisfies_the_changelog_rule(
    monkeypatch, tmp_path, capsys
):
    """End to end: the headlines come from the tree's CHANGELOG, the app names from the apps
    checkout, and a headline that names `demo-app` leaves only the charged problem."""
    core = tmp_path / "core"
    core.mkdir()
    (core / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n"
        "- **`compress_thread_history` takes a list of turns, and `demo-app` passes one.**\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(job, "REPO", core)
    code = _run(monkeypatch, tmp_path, _base_before_the_type_change(), "")
    out = capsys.readouterr().out
    assert code == 1 and "❌ demo-app/handler.py:3" in out, out
    assert "no new entry" not in out and "names `demo-app`" not in out, out
