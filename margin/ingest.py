"""Fetch every active feed and keep items with complete, real metadata.

Ported from weekly/collect.py (The Margin v1). Nothing here is generated: titles, links, dates,
authors and excerpts come from the feeds. An excerpt is the publisher's own feed summary, or —
when the summary is only a subtitle — the opening sentences of the full text the publisher put in the feed.
"""
import concurrent.futures as cf
import datetime as dt
import hashlib
import html
import re
import socket
import urllib.parse


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X) TheMargin/2.0 (personal reading edition)"
socket.setdefaulttimeout(30)


def clean(text):
    text = re.sub(r"(?s)<(script|style|figure|figcaption|sup)[^>]*>.*?</\1>", " ", text or "")   # sup: footnote markers
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return re.sub(r"\s+([,.;:!?)])", r"\1", text).replace("( ", "(")


TAILS = re.compile(r"\s*(?:(?:Read|Watch|Listen)(?: now)? (?:on|at) [A-Z][\w &]+|The post .{0,200}? (?:first )?appeared (?:first )?on .{0,80}?\.?|"
                   r"Continue reading\W*|Read more\W*|\[(?:…|\.\.\.)\]|…)\s*$", re.I)


def excerpt(text, max_words=65):
    """Leading sentences of the publisher's text, never cut mid-sentence; returns (text, was_shortened)."""
    stripped = False
    while (t := TAILS.sub("", text).strip()) != text:   # tails can stack: "… […] Read More…"
        text, stripped = t, True
    if stripped and text and text[-1] not in ".!?…\"'”’)":                # publisher's text was cut by the feed itself
        text += " …"
    words = text.split()
    if len(words) <= max_words:
        return text, text.endswith("…")
    out = []
    for s in re.split(r"(?<=[.!?…])\s+", text):
        if out and len(" ".join(out + [s]).split()) > max_words:
            break
        out.append(s)
    if len(" ".join(out).split()) > max_words * 1.3:      # one very long sentence: cut on a word
        return " ".join(words[:max_words]) + " …", True
    return " ".join(out) + " …", True


def norm_link(url):
    """Canonical form for duplicate detection: no scheme, www, tracking query, fragment or trailing slash."""
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not re.match(r"(utm_|fbclid|gclid|ref$|source$|cmpid|mc_)", k, re.I)]
    host = p.netloc.lower().removeprefix("www.")
    return host + p.path.rstrip("/") + ("?" + urllib.parse.urlencode(q) if q else "")


def _item(src, e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    link, title = e.get("link"), clean(e.get("title"))
    if not (t and link and title):
        return None
    summary = clean(e.get("summary"))
    summary = re.sub(r"^[A-Z][\w ]{0,40}, Published online: [^;]{0,40}; ?doi:\S+\s*", "", summary).strip()
    if summary.lower().startswith(title.lower()):
        summary = summary[len(title):].strip(" :–-")
    full = clean(e.content[0].value) if e.get("content") else ""
    full_words = len(full.split())
    if len(summary.split()) >= 18 and not (full_words > 300 and summary == full):
        text, origin = summary, "feed summary"
    elif full_words >= 40:
        text, origin = full, "opening of the feed's full text"
    else:
        text, origin = summary, "feed summary"
    exc, shortened = excerpt(text) if text else ("", False)
    pub = dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
    return {
        "id": hashlib.sha1(norm_link(link).encode()).hexdigest()[:12],
        "source": src["name"], "bucket": src["bucket"], "tier": src.get("tier"), "role": src.get("role", "signal"),
        "lang": src.get("lang", "en"), "access": src.get("access", "free"),
        "title": title, "link": link, "published": pub.isoformat(),
        "authors": [clean(a.get("name")) for a in e.get("authors", []) if a.get("name")][:3],
        "excerpt": exc, "excerpt_origin": origin if exc else None, "excerpt_shortened": shortened,
        "full_words": full_words if full_words > 300 and summary != full else 0,
        "categories": [clean(x.get("term")) for x in e.get("tags", []) if x.get("term")][:6],
    }


def fetch(src):
    """Return (report, items) for one source. Never raises: a failing feed is a report, not a crash."""
    rep = {"name": src["name"], "feed": src["feed"], "bucket": src["bucket"], "ok": False, "status": None, "items": 0, "newest": None, "error": None}
    import feedparser                  # imported here: the private server renders pages without needing the feed parser
    try:
        f = feedparser.parse(src["feed"], agent=UA)
        rep["status"] = f.get("status")
        if (rep["status"] or 0) >= 400:
            rep["error"] = f"HTTP {rep['status']}"
            return rep, []
        if not f.entries:
            rep["error"] = (str(f.get("bozo_exception"))[:140] if f.get("bozo") else "feed returned no entries") or "no entries"
            return rep, []
        items = [i for i in (_item(src, e) for e in f.entries) if i]
        rep.update(ok=bool(items), items=len(items), newest=max((i["published"] for i in items), default=None),
                   error=None if items else "entries lacked title, link or date")
        return rep, items
    except Exception as e:  # noqa: BLE001
        rep["error"] = f"{type(e).__name__}: {str(e)[:140]}"
        return rep, []


def collect(sources, workers=12):
    reports, items = [], []
    with cf.ThreadPoolExecutor(workers) as ex:
        for rep, its in ex.map(fetch, sources):
            reports.append(rep)
            items += its
    return reports, items
