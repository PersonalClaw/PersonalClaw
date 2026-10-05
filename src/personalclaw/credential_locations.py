"""Where credentials and secrets live, as text: one set for every check that reads text for them.

Two checks look for these in text rather than resolving a path: the content scanner's rule for a
credential that is read and then sent out (``supply_chain``), and skill extraction, which makes no
skill of a session whose tool calls named one (``history``). The guard that refuses a read resolves
the path it is asked about instead (``security.is_sensitive_path``).

A leaf with no imports: the content scan's child imports the scanner for every scan, so whatever
this imported, every Knowledge write, upload and artifact save would wait for.
"""

#: A credential folder or file under a home, PersonalClaw's own ``.env``, and the address that
#: serves a cloud instance its credentials: each a piece of text its reader looks for.
CREDENTIAL_LOCATIONS: tuple[str, ...] = (
    ".aws/",
    ".ssh/",
    ".gnupg/",
    ".gpg/",
    ".docker/config",
    ".kube/config",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    ".personalclaw/.env",
    "169.254.169.254",  # the instance metadata service
)
