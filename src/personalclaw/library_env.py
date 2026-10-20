"""What the libraries PersonalClaw's features load are told in every process it starts.

Some things a library does by itself, for any caller, inside its own calls, where no app can reach
them: it looks a token up, keeps a cache or a record of the machine in a folder of its own outside
the home, reports on its use to the people who make it, or downloads with a client of its own that
the egress guard never sees. PersonalClaw reads and writes only in its home, sends no telemetry, and
sends every download through the egress guard, so each of these is turned off, or its folder moved
into the home, with the library's own setting:

* **huggingface_hub** (model and voice downloads). For a call that passes no token it reads the
  environment, then the token file ``huggingface-cli login`` writes in the Hugging Face folder
  other tools share, and sends it. That file is outside the home, and PersonalClaw reads it only
  once the owner allows that folder (``local_models/hf_token.py``); the library's lookup does not
  ask. Turned off (``HF_HUB_DISABLE_IMPLICIT_TOKEN``). A file stored with Xet it hands to its
  native transfer client, which opens connections of its own where no guard can be put in front of
  them, and keeps a chunk cache in that shared folder: turned off (``HF_HUB_DISABLE_XET``), so every
  file downloads over the library's own HTTP client, which asks the egress guard first
  (``net/libraries.py``). And before its first request it fetches a list of AI tools from the Hub,
  to name the one it runs under in its User-Agent, and keeps that list in the shared folder
  (``.agent_harnesses.json``). Turned off with its usage reports (``HF_HUB_DISABLE_TELEMETRY``),
  which also silences the pings a library built on it sends.
* **onnxruntime** (speech-to-text, voices, text recognition). When it loads, it starts its maker's
  telemetry: a device identifier and a queue of events describing the machine (its model,
  processor, memory and operating system), kept in a folder of the maker's outside the home and
  uploaded from there. Turned off before it can start (``ORT_DISABLE_TELEMETRY``): the library
  reads the setting once, as it loads.
* **tree-sitter-language-pack** (the code map). It downloads each language's grammar the first
  time it parses that language, with a client of its own, into a folder of its own in the user's
  cache. Its folder is moved into the home, beside what the installers keep
  (``TREE_SITTER_LANGUAGE_PACK_CACHE_DIR``), and it reads the list of what to download from a file
  in that folder that only PersonalClaw writes, once it has fetched the grammars through the egress
  guard (``TREE_SITTER_LANGUAGE_PACK_MANIFEST_URL``, ``codegraph/grammars.py``). With no file
  there it fails without reaching the network.

:func:`keep_the_libraries_in_the_home` sets all of them in every ``personalclaw`` command (the
gateway included) as soon as it knows its home (a ``.env`` may name it), so each is in place before
an app imports a library. A child process gets the same values: :data:`LIBRARY_ENV_NAMES` are in
the child allowlist (``sandbox.CHILD_ENV_BASE_NAMES``). An app's test suite sets them too
(``personalclaw.sdk.testing.library_env``), since its tests load the same libraries.
"""

from __future__ import annotations

import os

#: The libraries' own names for the settings, in the order :func:`library_env` answers them.
LIBRARY_ENV_NAMES: tuple[str, ...] = (
    "HF_HUB_DISABLE_IMPLICIT_TOKEN",
    "HF_HUB_DISABLE_XET",
    "HF_HUB_DISABLE_TELEMETRY",
    "ORT_DISABLE_TELEMETRY",
    "TREE_SITTER_LANGUAGE_PACK_CACHE_DIR",
    "TREE_SITTER_LANGUAGE_PACK_MANIFEST_URL",
)

#: The code map's grammars, under the installers' folder in the home: the language pack's cache,
#: and what PersonalClaw fetched for it (:data:`GRAMMAR_MANIFEST` and the bundle it names).
GRAMMARS_DIRNAME = "tree-sitter-grammars"

#: The manifest the language pack reads, which only ``codegraph/grammars.py`` writes.
GRAMMAR_MANIFEST = "parsers.json"


def library_env() -> dict[str, str]:
    """The settings, for the home this process runs on. Working them out makes nothing, not even
    the home: every command asks for them before it knows whether it needs a home at all. Each
    library makes its folder the first time it writes there; the grammars land in
    ``tree-sitter-language-pack`` inside the folder the language pack is given."""
    from personalclaw._installer import INSTALLER_CACHE_DIRNAME
    from personalclaw.config.loader import resolve_config_dir

    grammars = resolve_config_dir() / INSTALLER_CACHE_DIRNAME / GRAMMARS_DIRNAME
    return {
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "HF_HUB_DISABLE_XET": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "ORT_DISABLE_TELEMETRY": "1",
        "TREE_SITTER_LANGUAGE_PACK_CACHE_DIR": str(grammars),
        "TREE_SITTER_LANGUAGE_PACK_MANIFEST_URL": (grammars / GRAMMAR_MANIFEST).as_uri(),
    }


def keep_the_libraries_in_the_home() -> None:
    """Set :func:`library_env` in this process, over whatever the process that started it set: the
    owner's allow switch decides whether a file outside the home is read, and nothing writes there,
    reports on its use or downloads past the egress guard, whatever an inherited variable says."""
    os.environ.update(library_env())
