"""🔴 THE SECOND VARIANT of the stale-SPA bug-class — a correct symlink over an OLD build.

`doctor._probe_serving_fs` has always called itself "the stale-SPA bug-class" check, and it saw
exactly one variant: a `static/dist` real-directory copy shadowing the runtime symlink. The other
variant defeats every signal it had. The symlink is correct, its target resolves, `index.html` is
right there — and the bundle behind it was built from sources that are no longer checked out. The
API is current, the browser is handed an old dashboard, and anything driving that dashboard reports
a shipped feature ABSENT. Honestly, and wrongly.

Measured, not hypothesised. The standing validation gateway on 127.0.0.1:10011 served a bundle two
commits behind its own checkout for a full day (2026-09-18/19). Its launcher decided "already
built" from `test -f web/dist/index.html`, which is true of a build of any age, so every confirm
pass that read a frontend clause against it was reading a false negative.

🪤 MTIME CANNOT ANSWER THIS, and the rig is the proof. Its checkout landed at 23:41 and a vite
build finished at 23:44, leaving every file in `web/dist` NEWER than every file in `web/src` — while
the bundle had in fact been built from the PRE-checkout sources, because the build started before
the checkout landed. A "is dist newer than src?" check calls that rig fresh. `test_a_build_that_is
_newer_by_mtime_but_built_from_older_sources_is_stale` is that exact race, and it is the reason
freshness is keyed off a content digest of the build inputs instead.
"""

from pathlib import Path

import pytest

from personalclaw.frontend import (
    spa_build_input_digest,
    spa_dist_freshness,
    write_spa_build_stamp,
)


def _fake_repo(root: Path, *, app_body: str = "export const App = () => null;\n") -> Path:
    """A repo-shaped tree: `src/personalclaw` beside `web/`, plus a root lockfile."""
    (root / "src" / "personalclaw" / "static").mkdir(parents=True)
    web = root / "web"
    (web / "src").mkdir(parents=True)
    (web / "src" / "App.tsx").write_text(app_body, encoding="utf-8")
    (web / "src" / "App.test.tsx").write_text("it('x', () => {});\n", encoding="utf-8")
    (web / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (web / "vite.config.ts").write_text("export default {};\n", encoding="utf-8")
    (web / "package.json").write_text('{"name":"web"}', encoding="utf-8")
    (root / "package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")
    return root


def _build(root: Path, *, marker: str = "v1") -> None:
    """Stand in for `vite build`: emit a dist, then stamp what it was built from."""
    dist = root / "web" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(f"<script src=/assets/{marker}.js></script>", encoding="utf-8")
    write_spa_build_stamp(root)


# ── the predicate ──────────────────────────────────────────────────────────


def test_a_freshly_built_bundle_is_fresh(tmp_path):
    root = _fake_repo(tmp_path)
    _build(root)
    state, ev = spa_dist_freshness(root)
    assert state == "fresh"
    assert ev["inputs_sha256"] == ev["built_from_sha256"]


def test_a_source_edit_after_the_build_is_stale(tmp_path):
    """🔴 THE RED PROOF: this is the check failing. Without it the bundle below still serves."""
    root = _fake_repo(tmp_path)
    _build(root)
    assert spa_dist_freshness(root)[0] == "fresh"

    (root / "web" / "src" / "App.tsx").write_text("export const App = () => 'new';\n", "utf-8")

    state, ev = spa_dist_freshness(root)
    assert state == "stale"
    assert ev["inputs_sha256"] != ev["built_from_sha256"]


def test_a_build_that_is_newer_by_mtime_but_built_from_older_sources_is_stale(tmp_path):
    """🪤 THE MEASURED RACE that rules mtime out — see this module's docstring.

    Sources are edited FIRST, then the stale bundle's files are touched to be strictly newer than
    every source file, reproducing the rig where dist mtime (23:44) beat src mtime (23:41) and the
    bundle was still two commits behind. Any "dist newer than src" heuristic passes this tree.
    """
    root = _fake_repo(tmp_path)
    _build(root, marker="old")
    (root / "web" / "src" / "App.tsx").write_text("export const App = () => 'new';\n", "utf-8")

    newest_src = max(p.stat().st_mtime for p in (root / "web" / "src").rglob("*") if p.is_file())
    for path in (root / "web" / "dist").rglob("*"):
        if path.is_file():
            import os

            os.utime(path, (newest_src + 60, newest_src + 60))
    # The mtime heuristic's own verdict on this tree: "fresh". It is wrong.
    assert (
        min(p.stat().st_mtime for p in (root / "web" / "dist").rglob("*") if p.is_file())
        > newest_src
    )

    assert spa_dist_freshness(root)[0] == "stale"


def test_a_test_only_edit_does_not_read_as_stale(tmp_path):
    """Precision: vite's entry graph never imports `*.test.tsx`, so the bundle cannot change."""
    root = _fake_repo(tmp_path)
    _build(root)
    (root / "web" / "src" / "App.test.tsx").write_text("it('y', () => {});\n", encoding="utf-8")
    assert spa_dist_freshness(root)[0] == "fresh"


def test_a_lockfile_change_is_stale(tmp_path):
    """Dependency versions land in the emitted bundle, so the lockfile is a build input."""
    root = _fake_repo(tmp_path)
    _build(root)
    (root / "package-lock.json").write_text('{"lockfileVersion":3,"x":1}', encoding="utf-8")
    assert spa_dist_freshness(root)[0] == "stale"


def test_an_unstamped_build_is_reported_but_not_called_stale(tmp_path):
    """Raw `npm run build` — what CI runs — leaves no stamp, and a CI build cannot be stale."""
    root = _fake_repo(tmp_path)
    (root / "web" / "dist").mkdir(parents=True)
    (root / "web" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    assert spa_dist_freshness(root)[0] == "unstamped"


def test_an_installed_wheel_is_not_checkable(tmp_path):
    """No `web/src` ships in a wheel, so freshness is not a question that can be failed there."""
    (tmp_path / "src" / "personalclaw").mkdir(parents=True)
    assert spa_build_input_digest(tmp_path) is None
    assert spa_dist_freshness(tmp_path)[0] == "no-sources"


def test_no_build_at_all_is_distinguished_from_a_stale_one(tmp_path):
    root = _fake_repo(tmp_path)
    assert spa_dist_freshness(root)[0] == "no-dist"


# ── the doctor probe that owns this bug-class ──────────────────────────────


@pytest.mark.asyncio
async def test_the_doctor_reports_a_stale_build_behind_a_correct_symlink(tmp_path, monkeypatch):
    """The blind spot, closed. Every OLD signal here says healthy: `static/dist` is a real symlink,
    it resolves, and the `index.html` behind it exists. Only the freshness check dissents.
    """
    import personalclaw
    from personalclaw.resilience import doctor
    from personalclaw.resilience.doctor import DoctorContext

    root = _fake_repo(tmp_path)
    _build(root)
    pkg = root / "src" / "personalclaw"
    (pkg / "static" / "dist").symlink_to(root / "web" / "dist")
    monkeypatch.setattr(personalclaw, "__file__", str(pkg / "__init__.py"))

    fresh = await doctor._probe_serving_fs(DoctorContext(home=tmp_path))
    assert fresh.evidence["dist"]["kind"] == "symlink"
    assert fresh.evidence["dist"]["target_ok"] is True
    assert fresh.evidence["dist_freshness"]["state"] == "fresh"
    assert fresh.ok is True

    (root / "web" / "src" / "App.tsx").write_text("export const App = () => 'new';\n", "utf-8")

    stale = await doctor._probe_serving_fs(DoctorContext(home=tmp_path))
    # The symlink is still perfect — that is the whole point.
    assert stale.evidence["dist"]["kind"] == "symlink"
    assert stale.evidence["dist"]["target_ok"] is True
    assert stale.evidence["dist_freshness"]["state"] == "stale"
    assert stale.ok is False
    assert "STALE" in stale.detail
    assert "make web-build" in stale.detail


# ── the CLI the build path and the validation rig both call ────────────────


def test_the_cli_exits_nonzero_only_on_a_provable_mismatch(tmp_path, monkeypatch, capsys):
    """A check that cannot fail is not a check: this asserts the exit code, both ways."""
    import scripts.spa_dist_freshness as cli

    root = _fake_repo(tmp_path)
    _build(root)
    monkeypatch.setattr(cli, "REPO_ROOT", root)

    assert cli.main(["check"]) == 0
    assert "FRESH" in capsys.readouterr().out

    (root / "web" / "src" / "App.tsx").write_text("export const App = () => 'new';\n", "utf-8")

    assert cli.main(["check"]) == 1
    out = capsys.readouterr().out
    assert "STALE" in out
    assert "make web-build" in out


def test_the_cli_can_demand_provenance_for_a_caller_that_can_rebuild(tmp_path, monkeypatch):
    import scripts.spa_dist_freshness as cli

    root = _fake_repo(tmp_path)
    (root / "web" / "dist").mkdir(parents=True)
    (root / "web" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(cli, "REPO_ROOT", root)

    assert cli.main(["check"]) == 0
    assert cli.main(["check", "--require-stamp"]) == 1


def test_stamping_makes_a_previously_unprovable_build_provable(tmp_path, monkeypatch):
    import scripts.spa_dist_freshness as cli

    root = _fake_repo(tmp_path)
    (root / "web" / "dist").mkdir(parents=True)
    (root / "web" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(cli, "REPO_ROOT", root)

    assert cli.main(["check", "--require-stamp"]) == 1
    assert cli.main(["stamp"]) == 0
    assert cli.main(["check", "--require-stamp"]) == 0
