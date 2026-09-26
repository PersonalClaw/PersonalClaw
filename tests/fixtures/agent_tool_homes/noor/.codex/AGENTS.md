# Global instructions for Codex

I use Codex for second opinions and long refactors; Claude Code is my main driver. Keep that in mind:
when I ask for a review, review. Do not rewrite the code unless I ask.

- Python 3.12, uv, ruff, pytest. Run the narrowest test that covers a change before the suite.
- Reviews: findings as a numbered list with file:line and severity (blocker, should-fix, nit), most
  important first. Say explicitly when you found nothing serious.
- Never push, never force anything, never touch a database that is not on localhost.
- Cartwheel repos (~/work/*) contain customer data in fixtures and logs. Do not quote consignee names
  or addresses back to me.
- feedsmith (~/src/feedsmith) is public: no internal hostnames or tokens in anything you write there.
- Canadian spelling in prose. No emojis.
