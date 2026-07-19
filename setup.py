"""PersonalClaw packaging — copies web assets, then builds the Python package."""

import locale
import os
import shutil

# Normalize the locale so setuptools can encode package metadata. Prefer the
# environment's own locale ("") and fall back to UTF-8/C when it is unset or
# invalid (common in minimal build containers), which otherwise raises during
# the build.
for _fallback in ("", "C.UTF-8", "C"):
    try:
        locale.setlocale(locale.LC_ALL, _fallback)
        if _fallback:
            os.environ["LC_ALL"] = _fallback
            os.environ.pop("LC_CTYPE", None)
        break
    except locale.Error:
        continue

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithWeb(build_py):
    """build_py that copies the web ``dist/`` into the package static dir."""

    def run(self) -> None:
        super().run()
        base = os.path.dirname(__file__) or "."
        self._copy_dist(base)

    def _copy_dist(self, base: str) -> None:
        src_dist = self._find_web_dist(base)
        if src_dist and os.path.isdir(src_dist):
            build_dist = os.path.join(self.build_lib, "personalclaw", "static", "dist")
            if os.path.isdir(build_dist):
                shutil.rmtree(build_dist)
            shutil.copytree(src_dist, build_dist)

    @staticmethod
    def _find_web_dist(base: str) -> str | None:
        # Repo-root web build
        top_level = os.path.join(base, "web", "dist")
        if os.path.isdir(top_level):
            return os.path.abspath(top_level)

        # Pre-built in-tree fallback
        local = os.path.join(base, "src", "personalclaw", "static", "dist")
        if os.path.isdir(local):
            return os.path.abspath(local)

        return None


setup(
    cmdclass={
        "build_py": BuildWithWeb,
    },
)
