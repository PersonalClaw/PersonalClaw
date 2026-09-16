"""RUM-4 rail: pull-from-main is retired from the git auto/apply paths.

The atom's falsifiable clause: *"a rail test greps BOTH modules and REDS if
``reset --hard origin/`` returns on an auto path."* The two modules that own the
git advance are:

* ``personalclaw.gateway`` — ``GatewayOrchestrator._auto_apply_update`` (the
  unattended path), and
* ``personalclaw.dashboard.handlers.updates`` — ``api_update_apply`` (the
  dashboard "Update & Restart" path).

Both now ride release TAGS (``git fetch --tags`` + ``git checkout <tag>``) or, on
the ``nightly`` channel, fast-forward — never ``git pull`` and never
``git reset --hard origin/<branch>``.

**Why the matcher looks for the operational form, not one prose spelling.** The
pre-RUM-4 destructive reset was spawned as a subprocess ARGUMENT LIST —
``"git", "reset", "--hard", f"origin/{branch}"`` — in which the contiguous string
``reset --hard origin/`` never actually appears. A rail that only searched for
that one contiguous spelling would be VACUOUS against the exact form the code
used. So this rail bans every operational residue: the ``--hard`` subprocess arg,
the ``reset --hard`` command string, the retired ``git_reset_hard`` primitive, and
the ``git pull`` subprocess arg / command string. It reads the installed module
source (never itself), and a companion test proves the matcher is non-vacuous by
firing it on each synthetic reintroduction.
"""

from __future__ import annotations

from pathlib import Path

import personalclaw.dashboard.handlers.updates as updates_mod
import personalclaw.gateway as gateway_mod

# Every operational residue of pull-from-main, in the forms it can actually take.
_BANNED = (
    '"--hard"',  # `git reset --hard` as a subprocess arg (the pre-RUM-4 form)
    "'--hard'",
    "reset --hard",  # the same as a command string / shell invocation / prose
    "--hard origin/",
    "git_reset_hard",  # the retired self_update primitive
    '"pull"',  # `git pull` as a subprocess arg
    "'pull'",
    "git pull",  # `git pull` as a command string / shell invocation
)

_MODULES = (gateway_mod, updates_mod)


def _module_source(module) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


def _residues(text: str) -> list[str]:
    return [needle for needle in _BANNED if needle in text]


def test_auto_and_apply_modules_carry_no_pull_from_main_residue() -> None:
    """REDS if either module reintroduces a branch pull or a hard reset."""
    for module in _MODULES:
        found = _residues(_module_source(module))
        assert not found, (
            f"{module.__name__} reintroduced a pull-from-main residue {found}: "
            "the git kind must ride release tags (fetch --tags + checkout) or "
            "fast-forward on nightly — never git pull / reset --hard origin/."
        )


def test_the_rail_is_not_vacuous_it_fires_on_each_reintroduction() -> None:
    """Vacuity floor: prove the matcher would catch the residue in every form it
    can take, including the arg-list spelling the pre-RUM-4 code actually used."""
    # The exact pre-RUM-4 arg list — the contiguous phrase "reset --hard origin/"
    # is NOT in it, but the "--hard" arg is, which is why the rail bans the arg.
    arg_list_reset = '"git", "reset", "--hard", f"origin/{branch}"'
    assert _residues(arg_list_reset)  # caught via '"--hard"'
    assert _residues("subprocess.run('git reset --hard origin/main', shell=True)")
    assert _residues("self_update.git_reset_hard(proj, branch)")
    assert _residues('"git", "pull"')  # caught via '"pull"'
    assert _residues("os.system('git pull')")
    # And a genuinely clean advance path trips nothing.
    assert not _residues('"git", "fetch", "--tags", "origin"')
    assert not _residues('"git", "checkout", target_tag')
    assert not _residues('"git", "merge", "--ff-only", f"origin/{branch}"')
