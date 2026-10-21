"""The Margin — private edition. One serverless function (Vercel, Python runtime).

GET  /         signed-in: today's paged edition with the personal pages, read live from Notion; otherwise → /login
GET  /login    sign-in form
POST /login    password check → signed session cookie (rate-limited)
POST /logout   clears the session (same-origin only)
GET  /health   "ok" — no content, no auth

Security model
  · Secrets live only in Vercel environment variables: MARGIN_PASSWORD, MARGIN_SESSION_KEY (≥ 32 chars), NOTION_TOKEN.
    If any is missing the server answers 503 — it never falls open.
  · Session: __Host- cookie, Secure, HttpOnly, SameSite=Strict, 30 days; value = expiry.nonce.HMAC-SHA256 — verified on
    every request, server-side. Changing MARGIN_SESSION_KEY signs everyone out.
  · Login rate limit: 5 failures per IP and 20 failures in total per 15 minutes, per running instance, plus a 1-second
    delay on every failure. (Instances are recycled, so this is a brake, not a vault: use a long password.)
  · Personal pages: Cache-Control no-store, noindex, no-referrer, strict CSP, never logged, never sent to any model.
  · Public newspaper data (data/latest.json) is read from the public GitHub Pages site; nothing personal flows back there.
"""
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from margin import build, notion  # noqa: E402

PUBLIC = os.environ.get("MARGIN_PUBLIC_BASE", "https://zayzyyazy.github.io/the-margin/")
COOKIE, SESSION_DAYS = "__Host-margin", 30
FAIL_WINDOW, FAIL_PER_IP, FAIL_TOTAL = 900, 5, 20
_fails = {}                     # ip → [timestamps]; per instance
_cache = {}                     # key → (expires, value); public edition and personal pages, per instance, in memory only
BERLIN = ZoneInfo("Europe/Berlin")


def configured():
    return all(os.environ.get(k) for k in ("MARGIN_PASSWORD", "MARGIN_SESSION_KEY", "NOTION_TOKEN")) \
        and len(os.environ["MARGIN_SESSION_KEY"]) >= 32


def sign(payload):
    return base64.urlsafe_b64encode(hmac.new(os.environ["MARGIN_SESSION_KEY"].encode(), payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def new_session():
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{exp}.{secrets.token_urlsafe(12)}"
    return f"{payload}.{sign(payload)}"


def valid_session(value):
    try:
        exp, nonce, sig = value.split(".")
        return hmac.compare_digest(sig, sign(f"{exp}.{nonce}")) and int(exp) > time.time()
    except (ValueError, AttributeError):
        return False


def password_ok(given):
    a = hashlib.sha256(given.encode()).digest()
    b = hashlib.sha256(os.environ["MARGIN_PASSWORD"].encode()).digest()
    return hmac.compare_digest(a, b)


def limited(ip):
    now = time.time()
    for k in list(_fails):
        _fails[k] = [t for t in _fails[k] if now - t < FAIL_WINDOW]
        if not _fails[k]:
            del _fails[k]
    total = sum(len(v) for v in _fails.values())
    return len(_fails.get(ip, [])) >= FAIL_PER_IP or total >= FAIL_TOTAL


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    val = fn()
    _cache[key] = (time.time() + ttl, val)
    return val


def public_edition():
    with urllib.request.urlopen(PUBLIC + "data/latest.json", timeout=10) as r:
        return json.load(r)


SECURITY_HEADERS = {
    "Cache-Control": "no-store, private", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Strict-Transport-Security": "max-age=31536000",
    "Content-Security-Policy": (f"default-src 'none'; style-src {PUBLIC} 'unsafe-inline'; font-src {PUBLIC}; script-src {PUBLIC}; "
                                f"img-src {PUBLIC} data:; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),
}

LOGIN = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>The Margin — sign in</title><link rel="stylesheet" href="{pub}assets/style.css"></head>
<body><header class="top wrap"><span class="brand">The Margin</span></header><main class="wrap"><section class="mast">
<h1>Private edition</h1><p class="standfirst">Sign in to read today’s paper with your personal pages.</p>
<form method="post" action="/login" class="login"><label for="pw" class="label">Password</label>
<input id="pw" name="password" type="password" autocomplete="current-password" required autofocus>
<button type="submit" class="save" aria-pressed="false">Sign in</button></form>{msg}
<p class="health">The public edition is at <a href="{pub}">{pub}</a>.</p></section></main></body></html>"""


def route(raw):
    """Vercel rewrites every path to /api/index?__path=/<original>; locally the original path arrives as is."""
    u = urllib.parse.urlsplit(raw)
    p = urllib.parse.parse_qs(u.query).get("__path", [u.path])[0] or "/"
    return "/" + p.lstrip("/")


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):          # the default access log would include paths and IPs; keep logs empty
        pass

    def send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
        self.send_response(code)
        for k, v in {**SECURITY_HEADERS, "Content-Type": ctype, **(headers or {})}.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def ip(self):
        return (self.headers.get("x-forwarded-for") or self.headers.get("x-real-ip") or self.client_address[0]).split(",")[0].strip()

    def session(self):
        for part in (self.headers.get("cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return valid_session(v)
        return False

    def login_page(self, code=200, msg=""):
        m = f'<p class="notice" role="alert">{html.escape(msg)}</p>' if msg else ""
        self.send(code, LOGIN.format(pub=PUBLIC, msg=m).encode())

    def do_GET(self):
        path = route(self.path)
        if path == "/health":
            return self.send(200, b"ok", "text/plain")
        if not configured():
            return self.send(503, b"The private edition is not configured yet.", "text/plain")
        if path == "/login":
            return self.redirect("/") if self.session() else self.login_page()
        if path != "/":
            return self.send(404, b"Not found", "text/plain")
        if not self.session():
            return self.redirect("/login")
        try:
            pub = cached("public", 120, public_edition)
        except Exception as e:  # noqa: BLE001
            return self.send(502, f"Could not read the public edition ({type(e).__name__}).".encode(), "text/plain")
        ed = pub["edition"]
        today = datetime.now(timezone.utc).astimezone(BERLIN).date()
        try:
            me = cached("personal", 180, lambda: notion.personal(today))
            personal_html = "".join(build.personal_blocks(me))
        except notion.NotionError as e:
            personal_html = "".join(build.personal_blocks(None, error=str(e)))
        body = build.edition_body(ed, pub["number"], pub.get("status") or {}, True, PUBLIC, personal_html=personal_html)
        signout = '<form method="post" action="/logout" class="lo"><button type="submit">Sign out</button></form>'
        d = datetime.fromisoformat(ed["date"])
        page = build.page(f"The Margin — {d.day} {d:%B %Y} (private)", PUBLIC, body, "today", home="/", extra_nav=signout)
        self.send(200, page.encode())

    def do_POST(self):
        path = route(self.path)
        if not configured():
            return self.send(503, b"The private edition is not configured yet.", "text/plain")
        origin = self.headers.get("origin")
        host = self.headers.get("x-forwarded-host") or self.headers.get("host")
        if origin and urllib.parse.urlsplit(origin).netloc != host:
            return self.send(403, b"Cross-origin request refused", "text/plain")
        if path == "/logout":
            return self.redirect("/login", cookie=f"{COOKIE}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict")
        if path != "/login":
            return self.send(404, b"Not found", "text/plain")
        ip = self.ip()
        if limited(ip):
            return self.send(429, b"Too many attempts. Try again in 15 minutes.", "text/plain", {"Retry-After": str(FAIL_WINDOW)})
        n = min(int(self.headers.get("content-length") or 0), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        if password_ok((form.get("password") or [""])[0]):
            _fails.pop(ip, None)
            return self.redirect("/", cookie=f"{COOKIE}={new_session()}; Max-Age={SESSION_DAYS * 86400}; Path=/; Secure; HttpOnly; SameSite=Strict")
        _fails.setdefault(ip, []).append(time.time())
        time.sleep(1.0)
        return self.login_page(401, "That password is not right.")

    def redirect(self, to, cookie=None):
        h = {"Location": to}
        if cookie:
            h["Set-Cookie"] = cookie
        self.send(303, b"", "text/plain", h)
