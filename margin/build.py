"""Render the static site from data/: today (latest successful edition), every past edition, archive, saved, sources.

  python -m margin.build [--out _site]

Pages use relative links only, so the same build works from disk and under any Pages path.
"""
import argparse
import datetime as dt
import html
import json
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .select import BUCKETS

ROOT = Path(__file__).resolve().parents[1]
BERLIN = ZoneInfo("Europe/Berlin")
WPM = 230


def esc(s):
    return html.escape(s or "", quote=True)


def berlin(iso):
    return dt.datetime.fromisoformat(iso).astimezone(BERLIN)


def long_date(d):
    return f"{d:%A} {d.day} {d:%B %Y}"


def short_dt(iso):
    t = berlin(iso)
    return f"{t.day} {t:%b} {t:%H:%M}"


def page(title, rel, body, active, desc="A daily reading edition"):
    cur = ' aria-current="page"'
    nav = "".join(f'<a href="{rel}{href}"{cur if key == active else ""}>{label}</a>'
                  for key, href, label in (("today", "", "Today"), ("archive", "archive/", "Archive"),
                                           ("saved", "saved/", "Saved"), ("sources", "sources/", "Sources")))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="description" content="{esc(desc)}">
<meta name="color-scheme" content="light dark"><title>{esc(title)}</title>
<link rel="preload" href="{rel}assets/fonts/source-serif-4-latin-wght-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{rel}assets/style.css"><link rel="icon" href="{rel}assets/icon.svg" type="image/svg+xml">
</head><body><a class="skip" href="#main">Skip to the edition</a>
<header class="top wrap"><a class="brand" href="{rel}">The Margin</a><nav aria-label="Sections">{nav}</nav></header>
<main id="main" class="wrap">{body}</main>
<script src="{rel}assets/app.js" defer></script></body></html>"""


def story(i, n, rel_lead=False):
    t = berlin(i["published"])
    by = f' · {esc(", ".join(i["authors"]))}' if i.get("authors") else ""
    mins = f'≈ {max(1, round(i["full_words"] / WPM))} min read' if i.get("full_words") else ""
    access = {"paywalled": "Paywalled", "partly paywalled": "May be paywalled"}.get(i.get("access"), "")
    facts = " · ".join(x for x in (mins, access) if x)
    origin = "from the feed’s summary" if i.get("excerpt_origin") == "feed summary" else "opening paragraph, from the feed"
    exc = (f'<div class="exc"><p class="label">Publisher’s excerpt <span>{origin}</span></p>'
           f'<p lang="{esc(i["lang"])}">{esc(i["excerpt"])}</p></div>' if i.get("excerpt") else
           '<div class="exc none"><p class="label">No excerpt in the publisher’s feed</p></div>')
    also = ""
    if i.get("also_in"):
        links = ", ".join(f'<a href="{esc(a["link"])}">{esc(a["source"])}</a>' for a in i["also_in"][:3])
        also = f'<p class="also">Also covered by {links}</p>'
    data = esc(json.dumps({k: i[k] for k in ("id", "title", "link", "source", "published")}, ensure_ascii=False))
    return f"""<article class="story{' lead' if rel_lead else ''}" id="a-{esc(i['id'])}">
<div class="rail"><span class="no">{n:02d}</span><span class="strand">{esc(BUCKETS[i['bucket']])}</span></div>
<div class="txt"><p class="meta"><span class="src">{esc(i['source'])}</span> · <time datetime="{esc(i['published'])}">{t.day} {t:%b}</time>{by}</p>
<h2 lang="{esc(i['lang'])}"><a href="{esc(i['link'])}">{esc(i['title'])}</a></h2>{exc}{also}
<p class="foot"><a class="go" href="{esc(i['link'])}">Read at {esc(i['source'])} <span aria-hidden="true">→</span></a>
{f'<span class="facts">{facts}</span>' if facts else ''}<button class="save" type="button" data-item="{data}" aria-pressed="false">Save</button></p></div>
</article>"""


def edition_body(ed, number, status, latest, rel):
    d = dt.date.fromisoformat(ed["date"])
    n = len(ed["items"])
    notice = ""
    la = status.get("last_attempt") or {}
    if latest and la.get("result") == "failed" and la.get("edition_date", "") > ed["date"]:
        notice = (f'<p class="notice" role="status"><b>This morning’s update failed</b> ({esc(la.get("reason"))}, {short_dt(la["at"])}). '
                  f'You are reading the last complete edition, from {d.day} {d:%B}.</p>')
    failed = ed.get("sources_failed") or []
    health = (f'{ed["sources_ok"]} of {ed["sources_active"]} sources responded'
              + (f' · <a href="{rel}sources/">{len(failed)} failing</a>' if failed else ""))
    strands = sorted({i["bucket"] for i in ed["items"]}, key=list(BUCKETS).index)
    lead, rest = ed["items"][0], ed["items"][1:]
    body = [f"""<section class="mast" aria-label="Edition">
<p class="dateline"><span>{long_date(d)}</span><span>No. {number}</span></p>
<h1>Today’s edition</h1>
<p class="standfirst">{n} articles worth your time, across {', '.join(BUCKETS[s] for s in strands[:-1]) + (' and ' if len(strands) > 1 else '') + BUCKETS[strands[-1]]}. Chosen from {ed['candidates']} candidates; the rest can wait.</p>
<p class="health">Updated {short_dt(ed['generated_at'])} Berlin · {health}</p>{notice}</section>""",
            story(lead, 1, True), '<div class="grid">']
    body += [story(i, k + 2) for k, i in enumerate(rest)]
    nxt = d + dt.timedelta(days=1)
    body.append(f"""</div><section class="end" aria-label="End of edition"><p class="orn" aria-hidden="true">◆</p>
<h2>That’s the edition.</h2><p>{n} articles. Nothing more is coming today — the next edition arrives {('tomorrow' if latest else f'on {nxt.day} {nxt:%B}')} at 07:00, Berlin time.</p>
<p class="endnav"><a href="{rel}saved/">Your saved articles</a><a href="{rel}archive/">Past editions</a></p>
<p class="colophon">Selected by fixed rules, not by a language model: a balance of four strands, one article per source, duplicates merged,
announcements and roundups pushed down. Every excerpt is the publisher’s own text from its feed, shown with its source and linked to
the original; this version writes no summaries of its own. Saved articles stay on this device.</p></section>""")
    return "".join(body)


def archive_body(eds):
    rows, month = [], None
    for k, ed in enumerate(eds):
        d = dt.date.fromisoformat(ed["date"])
        if (d.year, d.month) != month:
            month = (d.year, d.month)
            rows.append(f'<h2 class="month">{d:%B %Y}</h2>')
        titles = "".join(f"<li>{esc(i['title'])} <span>{esc(i['source'])}</span></li>" for i in ed["items"][:3])
        rows.append(f'<a class="ed" href="../e/{ed["date"]}/"><span class="d">{d:%a} {d.day}</span>'
                    f'<span class="no">No. {len(eds) - k}</span><span class="n">{len(ed["items"])} articles</span><ul>{titles}</ul></a>')
    return ('<section class="mast"><h1>Archive</h1><p class="standfirst">Every edition is kept exactly as it was published.</p></section>'
            + "".join(rows))


def sources_body(status, registry):
    feeds = (status.get("feeds") or {}).get("reports", [])
    by = {r["name"]: r for r in feeds}
    ok = [r for r in feeds if r["ok"]]
    bad = [r for r in feeds if not r["ok"]]
    off = [s for s in registry if not s.get("active")]
    checked = status.get("feeds", {}).get("checked_at")
    la, ls = status.get("last_attempt") or {}, status.get("last_success") or {}

    def row(name, detail, cls=""):
        return f'<li class="{cls}"><span class="nm">{esc(name)}</span><span class="dt">{detail}</span></li>'

    parts = ['<section class="mast"><h1>Sources</h1>',
             f'<p class="standfirst">{len(ok)} of {len(feeds)} active feeds answered at the last check'
             + (f' ({short_dt(checked)} Berlin)' if checked else "") + '.</p>',
             f'<p class="health">Last successful update: {short_dt(ls["at"]) + " · edition " + ls["edition_date"] if ls else "none yet"}'
             + (f' · last attempt {short_dt(la["at"])}: {esc(la["result"])}' + (f' — {esc(la["reason"])}' if la.get("reason") else "") if la else "")
             + '</p></section>']
    if bad:
        parts.append('<h2 class="month">Failing now</h2><ul class="srcs">'
                     + "".join(row(r["name"], esc(r["error"] or "error"), "bad") for r in bad) + "</ul>")
    for b, label in BUCKETS.items():
        rs = [r for r in ok if r["bucket"] == b]
        if rs:
            parts.append(f'<h2 class="month">{label}</h2><ul class="srcs">'
                         + "".join(row(r["name"], f'{r["items"]} items · newest {short_dt(r["newest"]) if r["newest"] else "—"}') for r in rs) + "</ul>")
    parts.append('<h2 class="month">Not fetched</h2><p class="small">Library sources without a working, publisher-advertised feed, '
                 'and earlier sources left out of the reading edition. No feed address was guessed.</p><ul class="srcs">'
                 + "".join(row(s["name"], esc(s.get("note") or "inactive"), "off") for s in off if s["name"] not in by) + "</ul>")
    return "".join(parts)


def build(data_dir, out, sources_file):
    eds = sorted((json.loads(p.read_text()) for p in (data_dir / "editions").glob("*.json")), key=lambda e: e["date"], reverse=True)
    status = json.loads((data_dir / "status.json").read_text()) if (data_dir / "status.json").exists() else {}
    registry = yaml.safe_load(sources_file.read_text())
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "assets", out / "assets")
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (out / ".nojekyll").write_text("")
    for k, ed in enumerate(eds):
        num = len(eds) - k
        d = dt.date.fromisoformat(ed["date"])
        (out / "e" / ed["date"]).mkdir(parents=True)
        (out / "e" / ed["date"] / "index.html").write_text(
            page(f"The Margin — {d.day} {d:%B %Y}", "../../", edition_body(ed, num, status, k == 0, "../../"), "today" if k == 0 else "archive"))
    if eds:
        d = dt.date.fromisoformat(eds[0]["date"])
        (out / "index.html").write_text(page(f"The Margin — {d.day} {d:%B %Y}", "", edition_body(eds[0], len(eds), status, True, ""), "today"))
    else:
        (out / "index.html").write_text(page("The Margin", "", '<section class="mast"><h1>No edition yet</h1></section>', "today"))
    for name, title, body in (("archive", "Archive", archive_body(eds)), ("sources", "Sources", sources_body(status, registry)),
                              ("saved", "Saved", '<section class="mast"><h1>Saved</h1><p class="standfirst">Articles you saved, newest first. '
                                                 'They are kept in this browser only.</p></section><div id="saved-list" class="saved"></div>')):
        (out / name).mkdir()
        (out / name / "index.html").write_text(page(f"The Margin — {title}", "../", body, name))
    (out / "status.json").write_text(json.dumps({k: status.get(k) for k in ("last_attempt", "last_success")}, indent=1))
    return len(eds)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    ap.add_argument("--sources", type=Path, default=ROOT / "margin" / "sources.yaml")
    a = ap.parse_args(argv)
    print(f"build: {build(a.data, a.out, a.sources)} editions → {a.out}")


if __name__ == "__main__":
    main()
