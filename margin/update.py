"""Daily update: fetch → select → persist. The last successful edition is never overwritten or deleted.

  python -m margin.update                 # build today's edition (Europe/Berlin date) unless it already exists
  python -m margin.update --scheduled     # same, but do nothing before 07:00 Berlin (the cron fires at 05:00 and 06:00 UTC)
  python -m margin.update --force         # rebuild today's edition even if one exists

Outcomes are written to data/status.json on every run, and to $GITHUB_OUTPUT when it is set.
Exit codes: 0 = new edition or nothing to do · 2 = update failed (previous edition kept).
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import ingest, llm, select, world

ROOT = Path(__file__).resolve().parents[1]
BERLIN = ZoneInfo("Europe/Berlin")
MIN_SOURCES_OK = 0.5        # fewer than half of the active feeds answering means the network, not the news, is the problem
FEATURED_MEMORY_DAYS = 90


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    tmp.replace(path)                                  # atomic: a crash never leaves half a file


def featured_before(edir, today):
    keys = set()
    for p in edir.glob("*.json"):
        if p.stem >= today.isoformat() or (today - dt.date.fromisoformat(p.stem)).days > FEATURED_MEMORY_DAYS:
            continue
        for i in json.loads(p.read_text())["items"]:
            keys |= {i["id"], ingest.norm_link(i["link"])}
    return keys


def gh_output(**kv):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.writelines(f"{k}={v}\n" for k, v in kv.items())


def run(data_dir, sources_file, now=None, force=False, scheduled=False, fetch=ingest.collect, world_enabled=True):
    now = now or dt.datetime.now(dt.timezone.utc)
    local = now.astimezone(BERLIN)
    today = local.date()
    edir = data_dir / "editions"
    status_p = data_dir / "status.json"
    status = json.loads(status_p.read_text()) if status_p.exists() else {}
    target = edir / f"{today.isoformat()}.json"

    if scheduled and local.hour < 7:
        return "skip", f"{local:%H:%M} Berlin is before 07:00"
    if target.exists() and not force:
        return "skip", f"edition {today} already exists"

    sources = [s for s in yaml.safe_load(sources_file.read_text()) if s.get("active")]
    reports, items = fetch(sources)
    ok = [r for r in reports if r["ok"]]
    attempt = {"at": now.isoformat(), "edition_date": today.isoformat(), "sources_active": len(sources), "sources_ok": len(ok),
               "items_fetched": len(items)}
    status["feeds"] = {"checked_at": now.isoformat(), "reports": sorted(reports, key=lambda r: (r["ok"], r["name"]))}

    reason = None
    if len(ok) < MIN_SOURCES_OK * len(sources):
        reason = f"only {len(ok)} of {len(sources)} sources responded"
    else:
        pool = select.candidates(items, now, featured_before(edir, today))
        chosen = select.compose(pool)
        attempt["candidates"] = len(pool)
        if len(chosen) < select.MIN_ITEMS:
            reason = f"only {len(chosen)} articles passed selection (minimum {select.MIN_ITEMS})"

    if reason:
        status["last_attempt"] = dict(attempt, result="failed", reason=reason)
        write_json(status_p, status)
        return "failed", reason

    keep = ("id", "title", "link", "source", "authors", "published", "bucket", "excerpt", "excerpt_origin", "excerpt_shortened",
            "full_words", "access", "lang", "score", "why", "also_in")
    edition = {"date": today.isoformat(), "generated_at": now.isoformat(), "candidates": len(pool),
               "sources_active": len(sources), "sources_ok": len(ok),
               "sources_failed": [r["name"] for r in reports if not r["ok"]],
               "items": [{k: i.get(k) for k in keep} for i in chosen]}
    try:                                        # the world section never blocks the edition; its failures are reported in it
        cfg = llm.config()
        ledger = llm.Ledger(data_dir / "ledger.jsonl", cfg["daily_cap_usd"], cfg["monthly_cap_usd"], cfg["paid_approved"])
        edition["world"] = world.build_world(now, cfg, ledger, edir=edir) if world_enabled else None
    except Exception as e:  # noqa: BLE001
        edition["world"] = {"developments": [], "error": f"{type(e).__name__}: {str(e)[:160]}"}
    write_json(target, edition)
    status["last_attempt"] = dict(attempt, result="ok", reason=None)
    status["last_success"] = {"at": now.isoformat(), "edition_date": today.isoformat(), "articles": len(chosen)}
    write_json(status_p, status)
    return "ok", f"{len(chosen)} articles from {len(pool)} candidates; {len(ok)}/{len(sources)} sources"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--sources", type=Path, default=ROOT / "margin" / "sources.yaml")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--scheduled", action="store_true")
    a = ap.parse_args(argv)
    result, msg = run(a.data, a.sources, force=a.force, scheduled=a.scheduled)
    print(f"update: {result} — {msg}")
    gh_output(result=result, changed=str(result != "skip").lower())
    return 2 if result == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
