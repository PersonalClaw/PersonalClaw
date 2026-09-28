"""What ``huggingface_hub`` is told in every process PersonalClaw starts: stay inside the home.

Two things the library does by itself for any caller, and no app can reach, because libraries
built on the hub (a tokenizer listing a model's files, a model card asking about its base model,
the Xet transfer client) make calls of their own:

* **It looks a token up.** For a call that passes none it reads the environment, then the token
  file ``huggingface-cli login`` writes in the Hugging Face folder other tools share, and sends
  it. That file is outside the home, and PersonalClaw reads it only once the owner allows that
  folder (``local_models/hf_token.py``); the library's lookup does not ask. Turned off.
* **It keeps a chunk cache for Xet transfers** in that same shared folder (``xet`` beside its
  model cache), written on every download from a repository stored with Xet, whatever folder the
  download itself was told to fill. Moved into the home, beside what the installers keep.

:func:`keep_the_hub_in_the_home` sets both in every ``personalclaw`` command (the gateway
included) as soon as it knows its home (a ``.env`` may name it), so each is in place before an app
imports the library, which reads them once.
A child process gets the same values: :data:`HUB_ENV_NAMES` are in the child allowlist
(``sandbox.CHILD_ENV_BASE_NAMES``).
"""

from __future__ import annotations

import os

#: The library's own names for the two settings, in the order :func:`hub_env` answers them.
HUB_ENV_NAMES: tuple[str, ...] = ("HF_HUB_DISABLE_IMPLICIT_TOKEN", "HF_XET_CACHE")

#: Where Xet transfers keep their chunk cache, under the installers' folder in the home.
XET_CACHE_DIRNAME = "huggingface-xet"


def hub_env() -> dict[str, str]:
    """The two settings, for the home this process runs on. The library makes the cache folder
    the first time it writes there."""
    from personalclaw._installer import INSTALLER_CACHE_DIRNAME
    from personalclaw.config.loader import config_dir

    xet = config_dir() / INSTALLER_CACHE_DIRNAME / XET_CACHE_DIRNAME
    return {"HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "HF_XET_CACHE": str(xet)}


def keep_the_hub_in_the_home() -> None:
    """Set :func:`hub_env` in this process, over whatever the process that started it set: the
    owner's allow switch decides whether a file outside the home is read, and nothing writes
    there, whatever an inherited variable says."""
    os.environ.update(hub_env())
