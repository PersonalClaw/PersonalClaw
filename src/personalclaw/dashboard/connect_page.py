"""The page a browser with no sign-in gets for a dashboard page: a 403 that says how to connect.

Served by the auth gate (``token_auth``) and by a pairing's refusals (``pair_refusals``) before any
bundle loads, so its look is inlined. Kept out of ``token_auth`` because it is a page, not a check:
``token_auth`` formats it with the port and the gateway's sentences.
"""

from __future__ import annotations

CONNECT_PAGE_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'><meta name='viewport' "
    "content='width=device-width,initial-scale=1'><title>Connect — PersonalClaw</title>"
    "<style>"
    # Theme token mirror (dark default; .light via prefers-color-scheme).
    # Standalone gate served before the React bundle, so values are inlined rather
    # than imported from tokens.css — but the palette/shape/motion match NE exactly.
    ":root{{--canvas:#0f0f0f;--surface:#1e1f20;--surface-high:#282a2c;"
    "--ink:#e3e3e3;--ink-low:#9a9b9c;--outline:#444746;"
    "--primary:#9d8bff;--on-primary:#21134f;--primary-emphasis:#b6bdff;"
    "--danger:#f55e57;--radius-card:28px;--radius-field:12px;"
    "--ease:cubic-bezier(0.2,0,0,1);"
    "--font:'Google Sans Flex','Google Sans',system-ui,-apple-system,sans-serif;"
    "--mono:'Google Sans Code',ui-monospace,'SF Mono',monospace}}"
    "*{{margin:0;padding:0;box-sizing:border-box}}"
    "body{{font-family:var(--font);display:flex;align-items:center;"
    "justify-content:center;min-height:100vh;background:var(--canvas);"
    "color:var(--ink);-webkit-font-smoothing:antialiased;overflow:hidden}}"
    # expressive lavender→pink bloom behind the card (NE signature glow)
    "body::before{{content:'';position:fixed;inset:0;z-index:0;pointer-events:none;"
    "background:radial-gradient(60% 55% at 50% 38%,"
    "color-mix(in srgb,var(--primary) 22%,transparent),transparent 70%);"
    "filter:blur(8px)}}"
    ".c{{position:relative;z-index:1;text-align:center;width:100%;max-width:420px;"
    "margin:24px;padding:40px 32px;background:var(--surface);"
    "border:1px solid var(--outline);border-radius:var(--radius-card);"
    "box-shadow:0 16px 40px rgb(0 0 0 / 0.42)}}"
    ".logo{{margin-bottom:20px}}.logo svg{{width:60px;height:60px;display:inline-block}}"
    "h1{{font-size:26px;line-height:1.15;margin-bottom:10px;"
    "font-variation-settings:'wght' 360;letter-spacing:-0.01em}}"
    "p{{color:var(--ink-low);font-size:14px;line-height:1.6;margin-bottom:24px}}"
    "code{{font-family:var(--mono);background:var(--surface-high);padding:2px 7px;"
    "border-radius:6px;color:var(--primary-emphasis);font-size:13px}}"
    "input{{width:100%;padding:13px 15px;border-radius:var(--radius-field);"
    "border:1px solid var(--outline);background:var(--canvas);color:var(--ink);"
    "font-family:var(--font);font-size:14px;margin-bottom:12px;outline:none;"
    "transition:border-color .2s var(--ease),box-shadow .2s var(--ease)}}"
    "input::placeholder{{color:var(--ink-low)}}"
    "input:focus{{border-color:var(--primary);"
    "box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 28%,transparent)}}"
    "button{{width:100%;padding:13px 24px;border-radius:9999px;border:none;"
    "cursor:pointer;background:var(--primary);color:var(--on-primary);"
    "font-family:var(--font);font-size:15px;font-variation-settings:'wght' 600;"
    "transition:background .2s var(--ease),transform .1s var(--ease),"
    "box-shadow .2s var(--ease)}}"
    "button:hover{{background:var(--primary-emphasis);"
    "box-shadow:0 0 28px -6px color-mix(in srgb,var(--primary) 55%,transparent)}}"
    "button:active{{transform:scale(0.985)}}"
    ".err{{color:var(--danger);font-size:13px;margin-top:14px;display:none}}"
    "@media(prefers-color-scheme:light){{:root{{--canvas:#f0f4f8;--surface:#ffffff;"
    "--surface-high:#e6eaef;--ink:#1f1f1f;--ink-low:#5f6368;--outline:#e1e3e1;"
    "--primary:#6a4fd0;--on-primary:#ffffff;--primary-emphasis:#563bbf}}"
    ".c{{box-shadow:0 16px 40px rgb(96 110 130 / 0.22)}}"
    "input:focus{{box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 18%,transparent)}}}}"
    "@media(prefers-reduced-motion:reduce){{*{{transition-duration:.001ms!important}}}}"
    "</style></head><body>"
    "<div class='c'>"
    "<div class='logo'><svg viewBox='0 0 512 512' xmlns='http://www.w3.org/2000/svg' aria-label='PersonalClaw'>"  # noqa: E501
    "<defs><linearGradient id='cg' x1='0' y1='0' x2='512' y2='512' gradientUnits='userSpaceOnUse'>"
    "<stop stop-color='#8e75b2'/><stop offset='0.45' stop-color='#9d8bff'/>"
    "<stop offset='0.75' stop-color='#c597ff'/><stop offset='1' stop-color='#d8627e'/>"
    "</linearGradient></defs>"
    "<path fill='url(#cg)' d='M256 16C106 76 46 226 46 226c0 45 60 90 90 90 90 0 180-195 135-285l-15-15zm45 15c30 60 0 135 0 135 120 30 120 180 75 330 75-75 90-150 90-210 0-90-15-225-165-255z'/></svg></div>"  # noqa: E501
    "<h1>{heading}</h1>"
    "<p>{explanation}</p>"
    "<input id='u' type='text' placeholder='Paste token URL or raw token…' autofocus>"
    "<button onclick='go()'>Connect</button>"
    "<div class='err' id='e'>Invalid URL</div>"
    "</div>"
    # The owner-token helpers (owner_token_url.js), inlined — this gate renders before any
    # authenticated asset can load. They scrub a refused token out of the URL, and build the
    # Connect target: this origin, the pasted token, and the dashboard route this gate was
    # opened at. It used to be `origin + '?token='`, which dropped the route, so every deep
    # link landed on the dashboard once the token was accepted.
    "{owner_token_script}"
    "<script>"
    "function go(){{var v=document.getElementById('u').value.trim();if(!v)return;"
    "var t=PersonalClawOwnerToken.connectUrl(v);"
    "if(t){{window.location.assign(t)}}"
    "else{{document.getElementById('e').style.display='block'}}}}"
    "document.getElementById('u').addEventListener('keydown',"
    "function(e){{if(e.key==='Enter')go()}});"
    "</script>"
    "</body></html>"
)
