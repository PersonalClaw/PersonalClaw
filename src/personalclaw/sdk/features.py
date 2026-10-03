"""SDK: what this PersonalClaw offers apps, by name (core features).

Every PersonalClaw built between two releases reads the same version, so ``minPersonalClawVersion``
cannot tell a build that has a contract from one made before it, and an app that relies on the newer
contract would install on the older build and quietly do less. A core feature names one contract an
app relies on, and a build offers the name exactly when it offers the contract.

* Declare the features your app needs in ``app.json``, by name:
  ``"requiresCoreFeatures": ["approval-answers"]``. A PersonalClaw that lacks one refuses to
  review, install, update or switch the app on, and says which feature it lacks.
* Ask :func:`core_has` about a feature your app can do without, and say so when it is missing rather
  than quietly doing less.

``APPROVAL_ANSWERS``: a channel's approval prompt offers the answers PersonalClaw hands it in the
approval brief (``personalclaw.sdk.channel.approval_brief_for(event)["answers"]``). A channel app
whose prompt offers them declares it.
"""

from personalclaw.apps.core_features import APPROVAL_ANSWERS, CORE_FEATURES, core_has

__all__ = ["APPROVAL_ANSWERS", "CORE_FEATURES", "core_has"]
