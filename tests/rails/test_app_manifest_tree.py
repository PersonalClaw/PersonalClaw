"""The source-tree checks split from
``tests/test_app_manifest.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import source_index
from app_manifest_shared import _valid_manifest
from rails_repo import REPO

from personalclaw.apps.manifest import AppManifest


class TestIconValidation:
    """An app's icon reaches the dashboard SHELL: the sidebar nav resolves it through
    ``web/src/pages/apps/appIcon.tsx``, so a value that cannot name a lucide component used to
    crash the entire dashboard rather than one app's card. The render site is fail-safe now; this
    is the outer layer, catching at INSTALL the shapes that can be proven wrong without knowing
    lucide's export list — and giving the app author a message instead of a silent Blocks glyph.
    """

    def test_every_BUNDLED_icon_value_still_validates(self):
        """The census, kept executable: a rule that refused an icon a shipped app already uses
        would be a worse bug than #579. Scoped to the BUNDLED apps because that is what this repo
        can assert about; the separate first-party apps repo carries 58 values in total, one of
        which (meta-muse-spark's lowercase "brain") is why this rule is case-insensitive."""
        import json

        repo = REPO
        seen: dict[str, str] = {}
        apps = repo / "src" / "personalclaw" / "apps"
        for app_json in source_index.under(source_index.package_any_files(), apps):
            if app_json.name != "app.json":
                continue
            try:
                data = json.loads(source_index.text(app_json))
            except (json.JSONDecodeError, OSError):
                continue
            for value in [data.get("icon", "")] + [
                p.get("icon", "") for p in (data.get("ui", {}) or {}).get("pages", []) or []
            ]:
                if value:
                    seen[value] = app_json.parent.name
        assert seen, "found no bundled app icons — has the bundled-apps path moved?"
        for value, app in sorted(seen.items()):
            errors = AppManifest.from_dict(_valid_manifest(icon=value)).validate()
            assert errors == [], f"{app} ships icon {value!r}, which this rule would refuse"
