"""The chrome shared by the gateway's two STANDALONE HTML pages.

``/login`` (REMOTE-USER-AUTH C3) and ``/pair`` (COMPANION-APPS C2) are served as plain
documents rather than SPA routes, for one reason each shares: they must render for a browser
that has **no session yet**, before any authenticated bundle fetch can succeed. That makes them
the only two surfaces in the product which cannot inherit the design system from ``web/`` — so
the tokens below are the ONE copy they share. A second hand-written copy of them is how two
doors into the same house end up different colours, and neither page has a visual-regression
test that would notice.

Nothing here is page-specific: no copy, no field, no endpoint. A page supplies its own title,
card body and script; the shell supplies the document, the tokens and the logo.
"""

from __future__ import annotations

#: The token block + component rules for a standalone page. Deliberately the SAME variables the
#: SPA theme uses, so the two doors read as the same product rather than a bolted-on gate.
PAGE_STYLE = """\
:root{--canvas:#0f0f0f;--surface:#1e1f20;--surface-high:#282a2c;--ink:#e3e3e3;
--ink-low:#9a9b9c;--outline:#444746;--primary:#9d8bff;--on-primary:#21134f;
--primary-emphasis:#b6bdff;--danger:#f55e57;--radius-card:28px;--radius-field:12px;
--ease:cubic-bezier(0.2,0,0,1);
--font:'Google Sans Flex','Google Sans',system-ui,-apple-system,sans-serif;
--mono:'Google Sans Code',ui-monospace,'SF Mono',monospace}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:var(--font);display:flex;align-items:center;justify-content:center;
min-height:100vh;background:var(--canvas);color:var(--ink);
-webkit-font-smoothing:antialiased;overflow:hidden}
body::before{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;
background:radial-gradient(60% 55% at 50% 38%,
color-mix(in srgb,var(--primary) 22%,transparent),transparent 70%);filter:blur(8px)}
.c{position:relative;z-index:1;text-align:center;width:100%;max-width:420px;margin:24px;
padding:40px 32px;background:var(--surface);border:1px solid var(--outline);
border-radius:var(--radius-card);box-shadow:0 16px 40px rgb(0 0 0 / 0.42)}
.logo{margin-bottom:20px}.logo svg{width:60px;height:60px;display:inline-block}
h1{font-size:26px;line-height:1.15;margin-bottom:10px;
font-variation-settings:'wght' 360;letter-spacing:-0.01em}
p{color:var(--ink-low);font-size:14px;line-height:1.6;margin-bottom:24px}
code{font-family:var(--mono);background:var(--surface-high);padding:2px 7px;
border-radius:6px;color:var(--primary-emphasis);font-size:13px}
input{width:100%;padding:13px 15px;border-radius:var(--radius-field);
border:1px solid var(--outline);background:var(--canvas);color:var(--ink);
font-family:var(--font);font-size:14px;margin-bottom:12px;outline:none;
transition:border-color .2s var(--ease),box-shadow .2s var(--ease)}
input::placeholder{color:var(--ink-low)}
input:focus{border-color:var(--primary);
box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 28%,transparent)}
button{width:100%;padding:13px 24px;border-radius:9999px;border:none;cursor:pointer;
background:var(--primary);color:var(--on-primary);font-family:var(--font);font-size:15px;
font-variation-settings:'wght' 600;transition:background .2s var(--ease),
transform .1s var(--ease),box-shadow .2s var(--ease)}
button:hover{background:var(--primary-emphasis);
box-shadow:0 0 28px -6px color-mix(in srgb,var(--primary) 55%,transparent)}
button:active{transform:scale(0.985)}
button[disabled]{opacity:.6;cursor:default}
.err{color:var(--danger);font-size:13px;margin-top:14px;min-height:18px}
.hint{margin-top:18px;font-size:12px;color:var(--ink-low)}
.hint a{color:var(--primary-emphasis);text-decoration:none}
.hint a:hover{text-decoration:underline}
@media(prefers-color-scheme:light){:root{--canvas:#f0f4f8;--surface:#ffffff;
--surface-high:#e6eaef;--ink:#1f1f1f;--ink-low:#5f6368;--outline:#e1e3e1;
--primary:#6a4fd0;--on-primary:#ffffff;--primary-emphasis:#563bbf}
.c{box-shadow:0 16px 40px rgb(96 110 130 / 0.22)}
input:focus{box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 18%,transparent)}}
@media(prefers-reduced-motion:reduce){*{transition-duration:.001ms!important}}
"""

#: The wordless product mark. Carries its own ``aria-label`` because it is the only branding on
#: the page and a decorative mark here would leave the document with no accessible identity.
LOGO_MARK = """\
<div class='logo'><svg viewBox='0 0 512 512' xmlns='http://www.w3.org/2000/svg'
aria-label='PersonalClaw'><defs><linearGradient id='cg' x1='0' y1='0' x2='512' y2='512'
gradientUnits='userSpaceOnUse'><stop stop-color='#8e75b2'/>
<stop offset='0.45' stop-color='#9d8bff'/><stop offset='0.75' stop-color='#c597ff'/>
<stop offset='1' stop-color='#d8627e'/></linearGradient></defs>
<path fill='url(#cg)' d='M256 16C106 76 46 226 46 226c0 45 60 90 90 90 90 0 180-195
135-285l-15-15zm45 15c30 60 0 135 0 135 120 30 120 180 75 330 75-75 90-150 90-210
0-90-15-225-165-255z'/></svg></div>"""


def page_document(*, title: str, body: str, script: str = "") -> str:
    """Compose one standalone page: shared shell + this page's card body and script.

    *body* is the inner content of the card (the shell opens and closes the card itself) and
    *script* is a bare JS body, wrapped here so no page can forget the tags.
    """
    tail = f"<script>\n{script}</script>" if script else ""
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>\n"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>\n"
        f"<title>{title}</title><style>\n"
        f"{PAGE_STYLE}</style></head><body><div class='c'>\n"
        f"{LOGO_MARK}\n{body}\n</div>{tail}</body></html>"
    )
