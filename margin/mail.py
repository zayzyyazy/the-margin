"""Render today's edition as an email and send it. Personal data goes to the owner's inbox only — never to the repo, site or logs.

  python -m margin.mail [--date YYYY-MM-DD] [--preview out.html] [--no-personal]

Environment (all optional; with no MAIL_TO / MAIL_FROM / GMAIL_APP_PASSWORD the step exits quietly without sending):
  MAIL_TO, MAIL_FROM, GMAIL_APP_PASSWORD   Gmail SMTP with an app password
  NOTION_TOKEN                             read-only integration shared with the hub page; without it "Your day" is left out
  MARGIN_SITE_URL                          public edition link shown in the footer
"""
import argparse
import datetime as dt
import html
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from zoneinfo import ZoneInfo

from . import personal

ROOT = Path(__file__).resolve().parents[1]
BERLIN = ZoneInfo("Europe/Berlin")
INK, MUTED, RULE, ACCENT, PAPER = "#1b1b1b", "#6b6b6b", "#d9d4c8", "#8a2b1d", "#faf7f0"
SERIF = "Georgia,'Times New Roman',serif"
SANS = "-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
BUCKETS = {"economy": "Economy", "tech": "Technology", "ai": "AI & engineering", "mind": "Mind & society", "discovery": "Discovery"}


def e(s):
    return html.escape(str(s or ""), quote=True)


def _label(text):
    return f'<p style="margin:22px 0 6px;font:700 11px {SANS};letter-spacing:.12em;text-transform:uppercase;color:{ACCENT}">{e(text)}</p>'


def _h(text, size=20):
    return f'<h2 style="margin:0 0 8px;font:700 {size}px/1.25 {SERIF};color:{INK}">{text}</h2>'


def _p(text, muted=False, size=15):
    return f'<p style="margin:0 0 8px;font:{size}px/1.5 {SERIF};color:{MUTED if muted else INK}">{text}</p>'


def _rule():
    return f'<hr style="border:0;border-top:1px solid {RULE};margin:26px 0 0">'


def _when(iso, today):
    n = personal.days_until(iso, today)
    d = dt.date.fromisoformat(iso[:10])
    base = f"{personal.WEEKDAYS[d.weekday()]} {d.day} {personal.MONTHS[d.month - 1]}"
    if n is None:
        return base
    return base + (" — overdue" if n < 0 else " — today" if n == 0 else " — tomorrow" if n == 1 else f" — in {n} days")


def your_day(p, today):
    """Returns (html, text). Quotes the owner's own Notion lines; adds nothing."""
    h, t = [], []
    h.append(_label("Your day"))
    t.append("YOUR DAY")
    if p["today"]:
        h.append(_h(f"{personal.WEEKDAYS[today.weekday()]} {today.day} {personal.MONTHS[today.month - 1]}"))
        rows = "".join(f'<tr><td style="padding:4px 12px 4px 0;vertical-align:top;font:600 13px {SANS};color:{MUTED};white-space:nowrap">{e(k)}</td>'
                       f'<td style="padding:4px 0;font:15px/1.45 {SERIF};color:{INK}">{e(v)}</td></tr>' for k, v in p["today"])
        h.append(f'<table role="presentation" style="border-collapse:collapse;margin:0 0 6px">{rows}</table>')
        t += [f"  {k}: {v}" for k, v in p["today"]]
    if p["right_now"]:
        h.append(f'<div style="margin:12px 0;padding:10px 14px;background:#f1ece0;border-left:3px solid {ACCENT}">'
                 + "".join(_p(e(x), size=14) for x in p["right_now"]) + "</div>")
        t += ["", "RIGHT NOW"] + [f"  {x}" for x in p["right_now"]]
    if p["deadlines"]:
        h.append(_label("Deadlines · next 14 days"))
        t += ["", "DEADLINES"]
        for d in p["deadlines"]:
            tail = f' <span style="color:{MUTED}">· {e(d["course"])}</span>' if d["course"] else ""
            h.append(_p(f'<b>{e(d["title"])}</b>{tail}<br><span style="font:13px {SANS};color:{MUTED}">{e(_when(d["due"], today))}</span>'))
            t.append(f'  {d["title"]} — {_when(d["due"], today)}')
    if p["yesterday"] and (p["yesterday"]["summary"] or p["yesterday"]["carry"]):
        y = p["yesterday"]
        h.append(_label(f"Yesterday · {y['day'] or y['date']}"))
        t += ["", f"YESTERDAY ({y['day'] or y['date']})"]
        if y["summary"]:
            h.append(_p(e(y["summary"])))
            t.append(f"  {y['summary']}")
        if y["carry"]:
            h.append(_p(f'<b>Carried forward:</b> {e(y["carry"])}'))
            t.append(f"  Carried forward: {y['carry']}")
    if p["courses"]:
        h.append(_label("Where each course stands"))
        t += ["", "COURSES"]
        for c in p["courses"]:
            topic = f' <span style="color:{MUTED}">· {e(c["topic"])}</span>' if c["topic"] else ""
            h.append(_p(f'<b>{e(c["name"])}</b>{topic}<br>Next: {e(c["next"])}', size=14))
            t.append(f'  {c["name"]}: {c["next"]}')
    if p["recent"]:
        h.append(_label("Lately"))
        t += ["", "LATELY"]
        for r in p["recent"]:
            line = r["title"] + (f' — {r["changed"]}' if r["changed"] else "")
            h.append(_p(e(line), muted=True, size=14))
            t.append(f"  {line}")
    return "".join(h), "\n".join(t)


def world(w):
    h, t = [], []
    devs = (w or {}).get("developments") or []
    if not devs:
        return "", ""
    h.append(_label("The world"))
    t += ["", "THE WORLD"]
    for d in devs:
        if d["mode"] == "explained":
            ex = d["explained"]
            first = next(iter(ex["parts"]))
            lede = " ".join(x["text"] for x in ex["parts"][first][:2])
            link = d["sources"][0]["link"] if d.get("sources") else ""
            h.append(_h(f'<a href="{e(link)}" style="color:{INK};text-decoration:none">{e(ex["headline"])}</a>', 18))
            h.append(_p(e(lede)))
            t += [ex["headline"], f"  {lede}", f"  {link}"]
        else:
            reps = (d.get("brief") or {}).get("reports", [])
            if not reps:
                continue
            r = reps[0]
            h.append(_h(f'<a href="{e(r["link"])}" style="color:{INK};text-decoration:none">{e(r["title"])}</a>', 18))
            h.append(_p(e(r["excerpt"])))
            others = [o for o in dict.fromkeys(x["outlet"] for x in reps + d.get("also", [])) if o != r["outlet"]]
            h.append(_p(e(r["outlet"]) + (f' · also reported by {e(", ".join(others))}' if others else ""), muted=True, size=13))
            t += [r["title"], f'  {r["excerpt"]}', f'  {r["outlet"]} — {r["link"]}']
    h.append(_p("Excerpts are the publishers’ own words. Follow the link for the full report.", muted=True, size=12))
    return "".join(h), "\n".join(t)


def reading(items):
    h, t = [], []
    if not items:
        return "", ""
    h.append(_label("Worth your time"))
    t += ["", "WORTH YOUR TIME"]
    for i in items:
        mins = max(1, round((i.get("full_words") or 0) / 230)) if i.get("full_words") else None
        meta = " · ".join(x for x in (i["source"], BUCKETS.get(i["bucket"], i["bucket"]), f"{mins} min" if mins else "", "" if i.get("access") in (None, "free") else i["access"]) if x)
        h.append(_h(f'<a href="{e(i["link"])}" style="color:{INK};text-decoration:none">{e(i["title"])}</a>', 17))
        h.append(_p(e(i.get("excerpt", ""))))
        h.append(_p(e(meta), muted=True, size=13))
        t += [i["title"], f'  {meta}', f'  {i["link"]}']
    return "".join(h), "\n".join(t)


def render(ed, pers=None, pers_error=None, site_url="", today=None):
    d = dt.date.fromisoformat(ed["date"])
    today = today or d
    long = f'{personal.WEEKDAYS[d.weekday()]} {d.day} {d:%B %Y}'
    parts_h, parts_t = [], []
    if pers is not None:
        ph, pt = your_day(pers, today)
        parts_h.append(ph), parts_t.append(pt)
    elif pers_error:
        parts_h.append(_label("Your day") + _p(f"Your Notion pages could not be read this morning ({e(pers_error)}). Nothing is shown rather than anything stale.", muted=True))
        parts_t.append(f"YOUR DAY\n  Notion could not be read ({pers_error}).")
    wh, wt = world(ed.get("world"))
    rh, rt = reading(ed["items"])
    for ph_, pt_ in ((wh, wt), (rh, rt)):
        if ph_:
            parts_h.append(_rule() + ph_)
            parts_t.append(pt_)
    foot = f'<a href="{e(site_url)}" style="color:{ACCENT}">Read it on the web</a> · ' if site_url else ""
    ok = f'{ed.get("sources_ok", "?")} of {ed.get("sources_active", "?")} sources responded'
    body = "".join(parts_h)
    note = ' "Your day" is quoted from your own Notion; nothing in it is generated.' if pers is not None else ""
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Margin — {e(long)}</title></head><body style="margin:0;background:{PAPER}">
<div style="max-width:640px;margin:0 auto;padding:28px 20px 40px">
<p style="margin:0;font:700 11px {SANS};letter-spacing:.14em;text-transform:uppercase;color:{MUTED}">{e(long)}</p>
<h1 style="margin:4px 0 0;font:700 34px/1.1 {SERIF};color:{INK}">The Margin</h1>
<hr style="border:0;border-top:2px solid {INK};margin:14px 0 0">
{body}
{_rule()}
<p style="margin:16px 0 0;font:12px/1.5 {SANS};color:{MUTED}">{foot}{e(ok)} · Sent {dt.datetime.now(BERLIN):%H:%M} Berlin.{note}</p>
</div></body></html>"""
    text = f"THE MARGIN — {long}\n\n" + "\n\n".join(parts_t) + f"\n\n{site_url}\n{ok}\n"
    return f"The Margin — {long}", doc, text


def load_edition(date=None):
    files = sorted((ROOT / "data" / "editions").glob("*.json"))
    if date:
        files = [f for f in files if f.stem == date]
    if not files:
        raise SystemExit("no edition found")
    return json.loads(files[-1].read_text())


def send(subject, doc, text, sender, to, password):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"], msg["Date"] = subject, f"The Margin <{sender}>", to, formatdate(localtime=True)
    msg.set_content(text)
    msg.add_alternative(doc, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
        s.login(sender, password)
        s.send_message(msg)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--preview", type=Path, help="write the HTML here instead of sending")
    ap.add_argument("--no-personal", action="store_true")
    ap.add_argument("--personal-json", type=Path, help="use this saved personal.read() result (testing)")
    a = ap.parse_args(argv)
    ed = load_edition(a.date)
    today = dt.date.fromisoformat(ed["date"])
    pers, err = None, None
    token = os.environ.get("NOTION_TOKEN")
    if a.personal_json:
        pers = json.loads(a.personal_json.read_text())
    elif token and not a.no_personal:
        try:
            pers = personal.read(token, today)
        except personal.NotionError as ex:
            err = str(ex)          # status code only, never content
    site = os.environ.get("MARGIN_SITE_URL", "")
    subject, doc, text = render(ed, pers, err, site, today)
    if a.preview:
        a.preview.write_text(doc)
        print(f"preview → {a.preview}")
        return 0
    sender, to, pw = (os.environ.get(k) for k in ("MAIL_FROM", "MAIL_TO", "GMAIL_APP_PASSWORD"))
    if not (sender and to and pw):
        print("mail: not configured (MAIL_FROM / MAIL_TO / GMAIL_APP_PASSWORD) — skipped")
        return 0
    try:
        send(subject, doc, text, sender, to, pw)
    except (smtplib.SMTPException, OSError) as ex:
        print(f"::error::mail failed: {type(ex).__name__}")
        return 1
    print("mail: sent" + ("" if pers is not None else " (without Your day)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
