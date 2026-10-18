"""The source-tree checks split from
``tests/test_scripted_provider_binding.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import scripted_provider_binding_shared
import source_index
from rails_repo import REPO
from scripted_provider_binding_shared import RETIRED_ENV_NAMES

_isolated_registry_and_home = scripted_provider_binding_shared._isolated_registry_and_home


def test_no_retired_env_name_is_still_read_by_anything() -> None:
    """The floor under the allowlist above: a retirement has to be real.

    Without this, `RETIRED_ENV_NAMES` is a way to make the drift sweep quiet — declare the
    stale name and the assertion goes green while a module still reads it. So the retirement is
    checked against the shipped source rather than trusted.
    """
    root = REPO
    src = root / "src"
    live = sorted(
        p.name
        for p in source_index.package_files()
        if p.is_relative_to(src)
        for n in RETIRED_ENV_NAMES
        if n in source_index.text(p)
    )
    assert not live, (
        f"these shipped modules still name a RETIRED env var {sorted(RETIRED_ENV_NAMES)}: "
        f"{live}. It is not retired; either the allowlist is wrong or the module is."
    )
    assert RETIRED_ENV_NAMES, "the retirement list is empty — the sweep above allows nothing extra"
