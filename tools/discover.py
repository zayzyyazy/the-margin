#!/usr/bin/env python3
"""Find and live-test feeds for the library candidates and the v1 feeds. Nothing is guessed:
a feed URL is either printed in the PDF, already in weekly/sources.yaml, or advertised by the
publisher's own page via <link rel="alternate" type="application/rss+xml|atom+xml">.

  python tools/discover.py            # writes reports/feed-validation.json and .md
"""
import concurrent.futures as cf
import datetime as dt
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import feedparser
import yaml

ROOT = Path(__file__).resolve().parents[1]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X) TheMargin/2.0 (personal reading edition)"
NOW = dt.datetime.now(dt.timezone.utc)
ALT = re.compile(r"<link\b[^>]*>", re.I)


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.read(2_000_000).decode("utf-8", "replace")


def advertised(page_url):
    """Feeds the page itself declares, in document order, comment feeds dropped."""
    status, final, body = get(page_url)
    out = []
    for tag in ALT.findall(body):
        attrs = dict((k.lower(), html.unescape(v)) for k, _, v in re.findall(r'([\w-]+)\s*=\s*(["\'])(.*?)\2', tag))
        if "alternate" in attrs.get("rel", "").lower() and re.search(r"(rss|atom)\+xml", attrs.get("type", ""), re.I) and attrs.get("href"):
            href = urllib.parse.urljoin(final, attrs["href"])
            if not re.search(r"comment", href + attrs.get("title", ""), re.I) and href not in out:
                out.append(href)
    return status, out


def test_feed(url):
    f = feedparser.parse(url, agent=UA)
    dates = [dt.datetime(*(e.get("published_parsed") or e.get("updated_parsed"))[:6], tzinfo=dt.timezone.utc)
             for e in f.entries if e.get("published_parsed") or e.get("updated_parsed")]
    newest = max(dates) if dates else None
    week = sum(1 for d in dates if (NOW - d).days < 7)
    summ = sum(1 for e in f.entries if len(re.sub(r"<[^>]+>", " ", e.get("summary", "")).split()) >= 12)
    return {"status": f.get("status"), "entries": len(f.entries), "dated": len(dates), "newest": newest.isoformat() if newest else None,
            "age_days": (NOW - newest).days if newest else None, "per_week": week, "with_summary": summ,
            "title": f.feed.get("title"), "error": (str(f.get("bozo_exception"))[:140] if f.bozo and not f.entries else None)}


def verdict(t):
    if t.get("error") or not t.get("entries"):
        return "fail", t.get("error") or "no entries"
    if (t.get("status") or 0) >= 400:
        return "fail", f"HTTP {t['status']}"
    if not t["dated"]:
        return "fail", "entries have no dates"
    if t["age_days"] > 90:
        return "stale", f"newest item {t['age_days']} days old"
    if t["with_summary"] == 0:
        return "weak", "no usable summaries"
    return "ok", ""


def run(c):
    rec = dict(c)
    try:
        if c.get("feed"):
            rec["feed_found"] = c["feed"]
        else:
            st, feeds = advertised(c["site"])
            rec["page_status"], rec["advertised"] = st, feeds
            if not feeds:
                rec.update(result="fail", reason="page advertises no RSS/Atom feed (not guessed)")
                return rec
            rec["feed_found"] = feeds[0]
        rec["test"] = test_feed(rec["feed_found"])
        rec["result"], rec["reason"] = verdict(rec["test"])
    except Exception as e:  # noqa: BLE001 — one bad site must not stop the census
        rec.update(result="fail", reason=f"{type(e).__name__}: {str(e)[:120]}")
    return rec


def main():
    lib = yaml.safe_load((ROOT / "tools/candidates.yaml").read_text())
    v1 = yaml.safe_load((ROOT.parent / "weekly/sources.yaml").read_text())["feeds"]
    lib_feeds = {c.get("feed") for c in lib}
    v1_only = [{"name": f["name"], "feed": f["url"], "how": "v1", "v1_category": f["category"], "lang": f.get("lang", "en"),
                "paywall": bool(f.get("paywall"))} for f in v1 if f["url"] not in lib_feeds]
    todo = [dict(c, group="library") for c in lib] + [dict(c, group="v1-only") for c in v1_only]
    with cf.ThreadPoolExecutor(12) as ex:
        res = list(ex.map(run, todo))
    (ROOT / "reports/feed-validation.json").write_text(json.dumps({"checked_at": NOW.isoformat(), "results": res}, indent=1, ensure_ascii=False))
    for r in res:
        t = r.get("test", {})
        print(f"{r['result']:5} {r['group']:8} {r['name'][:28]:28} {r.get('how',''):8} age={t.get('age_days')} wk={t.get('per_week')} "
              f"sum={t.get('with_summary')}/{t.get('entries')} {r.get('feed_found','')[:70]} {r.get('reason','')}")


if __name__ == "__main__":
    sys.exit(main())
