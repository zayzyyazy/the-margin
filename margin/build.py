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


def page(title, rel, body, active, desc="A daily reading edition", home=None, extra_nav=""):
    cur = ' aria-current="page"'
    nav = "".join(f'<a href="{home if (key == "today" and home is not None) else rel + href}"{cur if key == active else ""}>{label}</a>'
                  for key, href, label in (("today", "", "Today"), ("archive", "archive/", "Archive"),
                                           ("saved", "saved/", "Saved"), ("sources", "sources/", "Sources")))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="description" content="{esc(desc)}">
<meta name="color-scheme" content="light dark"><title>{esc(title)}</title>
<link rel="preload" href="{rel}assets/fonts/source-serif-4-latin-wght-normal.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{rel}assets/style.css"><link rel="icon" href="{rel}assets/icon.svg" type="image/svg+xml">
</head><body><a class="skip" href="#main">Skip to the edition</a>
<header class="top wrap"><a class="brand" href="{home if home is not None else rel}">The Margin</a><nav aria-label="Sections">{nav}{extra_nav}</nav></header>
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


WORLD_PARTS = {"happened": "What happened", "background": "The background", "matters": "Why it matters",
               "uncertain": "What is uncertain or disputed"}
KIND = {"reporting": "Reporting", "analysis": "Analysis", "opinion": "Opinion"}


def sechead(key, numeral, title, desc):
    return (f'<section class="blk sechead" id="s-{key}" data-sec="{esc(title)}"><p class="secno">{numeral}</p>'
            f'<h2>{esc(title)}</h2><p class="secdesc">{desc}</p></section>')


def world_blocks(w):
    if not w:
        return []
    devs = w.get("developments") or []
    if not devs:
        why = esc(w.get("error") or "no development was covered by enough independent outlets")
        return [sechead("world", "1", "The World, Explained", "Significant developments, with the background you need."),
                f'<div class="blk wx-none"><p>No world section today: {why}.</p></div>']
    out = [sechead("world", "1", "The World, Explained",
                   "Significant developments, chosen by consequence, spread and how widely they are reported — with the background you need.")]
    for d in devs:
        srcs = {s["id"]: (k + 1, s) for k, s in enumerate(d.get("sources", []))}
        if d["mode"] == "explained":
            ex = d["explained"]

            def para(sents):
                html_ = []
                for x in sents:
                    cites = ",".join(f'<a href="{esc(srcs[c][1]["link"])}" title="{esc(srcs[c][1]["outlet"])}">{srcs[c][0]}</a>'
                                     for c in x["cites"] if c in srcs)
                    t = esc(x["text"])
                    html_.append(f'<span class="interp">{t}</span>' if x["kind"] == "interp" else f'{t}<sup class="c">{cites}</sup>')
                return " ".join(html_)
            first, *rest = list(WORLD_PARTS)
            out.append(f'<article class="blk wx" id="w-{esc(d["id"])}"><p class="kicker">The Margin’s synthesis · from {len(srcs)} reports</p>'
                       f'<h3 class="wx-h">{esc(ex["headline"])}</h3><h4>{WORLD_PARTS[first]}</h4><p>{para(ex["parts"][first])}</p></article>')
            for k in rest:
                sents = ex["parts"][k]
                for j in range(0, len(sents), 2):
                    head = f'<h4>{WORLD_PARTS[k]}</h4>' if j == 0 else ""
                    out.append(f'<div class="blk wx-part{" cont" if j else ""}">{head}<p>{para(sents[j:j + 2])}</p></div>')
            terms = "".join(f'<dt>{esc(t["term"])}</dt><dd>{esc(t["definition"])}</dd>' for t in ex.get("terms", []))
            src_list = "".join(f'<li><span class="n">{n}</span> <a href="{esc(s["link"])}">{esc(s["title"])}</a> '
                               f'<span class="m">{esc(s["outlet"])} · {KIND.get(s["kind"], "Reporting")} · {berlin(s["published"]).day} {berlin(s["published"]):%b}</span></li>'
                               for n, s in srcs.values())
            out.append(f'<div class="blk wx-src">' + (f'<dl class="terms"><p class="label">Terms <span>general definitions by The Margin, not from today’s reports</span></p>{terms}</dl>' if terms else "")
                       + f'<p class="label">Sources read for this explanation</p><ol class="srcl">{src_list}</ol></div>')
        else:
            reps = (d.get("brief") or {}).get("reports", [])
            quotes = "".join(f'<blockquote lang="{esc(r["lang"])}"><p>{esc(r["excerpt"])}</p><cite>— <a href="{esc(r["link"])}">{esc(r["outlet"])}</a>, '
                             f'{KIND.get(r["kind"], "Reporting").lower()}, {berlin(r["published"]).day} {berlin(r["published"]):%b}</cite></blockquote>' for r in reps)
            quoted, also_l = {r["outlet"] for r in reps}, []
            for a in d.get("also", []):                 # each other outlet once; not those already quoted above
                if a["kind"] != "opinion" and a["outlet"] not in quoted:
                    quoted.add(a["outlet"])
                    also_l.append(f'<a href="{esc(a["link"])}">{esc(a["outlet"])}</a>')
            also = ", ".join(also_l[:4])
            head = reps[0]["title"] if reps else ", ".join(d.get("names", []))
            out.append(f'<article class="blk wx brief" id="w-{esc(d["id"])}"><p class="kicker">Brief · in the publishers’ own words</p>'
                       f'<h3 class="wx-h" lang="{esc(reps[0]["lang"] if reps else "en")}">{esc(head)}</h3>{quotes}'
                       + (f'<p class="also">Also reported by {also}</p>' if also else "")
                       + f'<p class="why">Not explained today: {esc(d.get("why") or "")}. A brief keeps the publishers’ words rather than '
                         f'publishing an explanation that did not survive checking whole.</p></article>')
    return out


def personal_blocks(p, error=None):
    out = [sechead("personal", "2", "Your margin", "From your Notion records, as you wrote them. Read live, shown only to you.")]
    if error:
        return out + [f'<div class="blk me"><p class="why">Your Notion pages could not be read just now ({esc(error)}). Nothing is shown rather than '
                      f'anything stale or invented.</p></div>']

    def rec(r, rows, kicker):
        body = "".join(f'<p><span class="fld">{label}</span> {esc(r[k])}</p>' for k, label in rows if r.get(k))
        return (f'<article class="blk me"><p class="kicker">{kicker}</p><h3><a href="{esc(r["url"])}">{esc(r["name"] or "Untitled")}</a></h3>{body}'
                f'<p class="src">From Notion · edited {berlin(r["edited"]).day} {berlin(r["edited"]):%b}</p></article>')
    if p["left_off"]:
        out += [rec(r, (("state", "Current state"), ("next", "Your next step"), ("blockers", "Blocked by")), "Where you left off")
                for r in p["left_off"]]
    else:
        out.append('<div class="blk me"><p class="why">No project is marked Active in Notion.</p></div>')
    out += [rec(r, (("learned", "What you learned"), ("gap", "Reminder — your next gap"), ("applied", "Where applied")), "Learning thread")
            for r in p["learning"]]
    out += [rec(r, (("learned", "What you found"), ("evidence", "Evidence"), ("gap", "Open question")),
                f'Research rediscovered · saved {r["date"]}' if r.get("date") else "Research rediscovered") for r in p["rediscovered"]]
    return out


def reading_minutes(ed, personal=None):
    words = 0
    for d in (ed.get("world") or {}).get("developments", []):
        if d["mode"] == "explained":
            words += sum(len(x["text"].split()) for part in d["explained"]["parts"].values() for x in part)
        else:
            words += sum(len(r["excerpt"].split()) for r in (d.get("brief") or {}).get("reports", []))
    words += sum(len(i.get("excerpt", "").split()) + 12 for i in ed["items"])
    if personal:
        words += 220
    return max(1, round(words / WPM))


def edition_body(ed, number, status, latest, rel, personal_html=None):
    d = dt.date.fromisoformat(ed["date"])
    n = len(ed["items"])
    notice = ""
    la = status.get("last_attempt") or {}
    if latest and la.get("result") == "failed" and la.get("at", "") > ed["generated_at"]:
        notice = (f'<p class="notice" role="status"><b>The latest update failed</b> ({esc(la.get("reason"))}, {short_dt(la["at"])}). '
                  f'You are reading the last complete edition, from {d.day} {d:%B}.</p>')
    failed = ed.get("sources_failed") or []
    health = (f'{ed["sources_ok"]} of {ed["sources_active"]} sources responded'
              + (f' · <a href="{rel}sources/">{len(failed)} failing</a>' if failed else ""))
    w = ed.get("world")
    deeper = [i for i in ed["items"] if i["bucket"] != "discovery"]
    disc = [i for i in ed["items"] if i["bucket"] == "discovery"]
    nworld = len((w or {}).get("developments", []))
    mins = reading_minutes(ed, personal_html)
    secs = ([("world", "The World, Explained")] if w else []) + ([("personal", "Your margin")] if personal_html else []) \
        + [("deeper", "Deeper reading"), ("discovery", "Discovery"), ("end", "The end")]
    toc = "".join(f'<li><a href="#s-{k}" data-toc="{k}"><span class="t">{t}</span><span class="pn"></span></a></li>' for k, t in secs)
    standfirst = (f"{nworld} world developments explained or briefed, " if w else "") + f"{n} articles worth your time"
    body = [f"""<div class="paper" data-date="{ed['date']}" data-label="The Margin · {d.day} {d:%b %Y}">
<section class="blk cover" id="s-front" data-sec="Front"><p class="dateline"><span>{long_date(d)}</span><span>No. {number}</span></p>
<h1>Today’s edition</h1>
<p class="standfirst">{standfirst}. About {mins} minutes here, longer if you follow the links.</p>
<p class="health">Updated {short_dt(ed['generated_at'])} Berlin · {health}</p>{notice}
<nav class="toc" aria-label="Contents"><p class="label">Contents</p><ol>{toc}</ol></nav></section>"""]
    body += world_blocks(w)
    if personal_html:
        body.append(personal_html)
    body.append(sechead("deeper", "3" if personal_html else "2", "Deeper reading",
                        "Specialist writing on how things work — AI and engineering, companies and economics, mind and society."))
    num = 0
    if deeper:
        num += 1
        body.append(story(deeper[0], num, True).replace('<article class="story', '<article class="blk story', 1))
    body.append('<div class="grid">')
    for i in deeper[1:]:
        num += 1
        body.append(story(i, num).replace('<article class="story', '<article class="blk story', 1))
    body.append("</div>")
    if disc:
        body.append(sechead("discovery", "4" if personal_html else "3", "Discovery", "Science, invention and the unexpected."))
        body.append('<div class="grid">')
        for i in disc:
            num += 1
            body.append(story(i, num).replace('<article class="story', '<article class="blk story', 1))
        body.append("</div>")
    nxt = d + dt.timedelta(days=1)
    body.append(f"""<section class="blk end" id="s-end" data-sec="The end" aria-label="End of edition"><p class="orn" aria-hidden="true">◆</p>
<h2>That’s the edition.</h2><p>Nothing more is coming today — the next edition arrives {('tomorrow' if latest else f'on {nxt.day} {nxt:%B}')} at 07:00, Berlin time.</p>
<p class="endnav"><a href="{rel}saved/">Your saved articles</a><a href="{rel}archive/">Past editions</a></p>
<p class="colophon">Articles are selected by fixed rules: four strands, one article per source, duplicates merged, announcements and roundups
pushed down; their excerpts are the publishers’ own words. “The World, Explained” is The Margin’s synthesis, drafted by a language model
only from the reports it links, then checked: every quote must exist in its source, every number must match, and a second model judged
each claim and the piece as a whole. These checks are safeguards, not proof — read the linked reporting for anything that matters to you.
Italic sentences are The Margin’s interpretation. Anything that failed a check is published as a brief in the publishers’ words instead.
Saved articles stay on this device.</p></section></div>""")
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
    (out / "data" / "editions").mkdir(parents=True)
    for ed in eds:
        (out / "data" / "editions" / f'{ed["date"]}.json').write_text(json.dumps(ed, ensure_ascii=False))
    if eds:
        (out / "data" / "latest.json").write_text(json.dumps({"edition": eds[0], "number": len(eds),
                                                              "status": {k: status.get(k) for k in ("last_attempt", "last_success")}}, ensure_ascii=False))
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
