"""Offline tests: duplicates, volume caps, strand balance, failure recovery, archive persistence, idempotent reruns.

  python -m unittest discover -s tests -v
Items here are synthetic test fixtures; they never reach data/.
"""
import datetime as dt
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from margin import build, select, update

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 10, 21, 5, 10, tzinfo=dt.timezone.utc)          # 07:10 Berlin (CEST)
WORDS = ("glacier ledger falcon harbour quartz meadow lantern cipher orchard pylon saffron tundra violin walrus ember "
         "mosaic nickel oyster prairie quiver rhubarb sonnet tapestry umbrella vortex willow yarrow zephyr anvil bramble "
         "cobalt dune fjord gable hemlock iris juniper kelp lichen marble").split()
EXC = "This is a long enough publisher excerpt about how a mechanism really works, with several clauses and real substance in it."


def item(n, source, bucket, title=None, link=None, hours=3, at=None, **kw):
    at = at or NOW
    w = [WORDS[(n * 7 + k * 11) % len(WORDS)] + str(n) for k in range(4)]
    i = {"id": f"id{n}", "title": title or " ".join(w).capitalize(),
         "link": link or f"https://{source.lower().replace(' ', '')}.example/{n}", "source": source, "bucket": bucket, "tier": 1,
         "role": "explainer", "lang": "en", "access": "free", "authors": [], "published": (at - dt.timedelta(hours=hours)).isoformat(),
         "excerpt": EXC, "excerpt_origin": "feed summary", "excerpt_shortened": False, "full_words": 0, "categories": []}
    i.update(kw)
    return i


def day(n_per_bucket=3, at=None, start=0):
    out, n = [], start
    for b in select.BUCKETS:
        for k in range(n_per_bucket):
            n += 1
            out.append(item(n, f"{b} source {k}", b, at=at))
    return out


SOURCES = [{"name": f"S{k}", "feed": f"https://s{k}.example/feed", "bucket": b, "active": True}
           for k, b in enumerate(list(select.BUCKETS) * 3)]


def fake_fetch(items, ok=True):
    def f(sources):
        reps = [{"name": s["name"], "feed": s["feed"], "bucket": s["bucket"], "ok": ok, "status": 200 if ok else None,
                 "items": 3 if ok else 0, "newest": NOW.isoformat() if ok else None, "error": None if ok else "URLError: timed out"}
                for s in sources]
        return reps, (items if ok else [])
    return f


class Selection(unittest.TestCase):
    def test_same_link_with_tracking_is_one_item(self):
        a = item(1, "Alpha", "ai", link="https://www.x.example/story/?utm_source=rss")
        b = item(2, "Beta", "ai", link="http://x.example/story", title="Something else entirely different here")
        pool = select.candidates([a, b], NOW, set())
        self.assertEqual(len(pool), 1)

    def test_same_story_from_two_sources_collapses_and_credits_both(self):
        a = item(1, "Alpha", "economy", title="Central bank raises interest rates to fight stubborn inflation", tier=1)
        b = item(2, "Beta", "economy", title="Stubborn inflation: central bank raises interest rates again", tier=3)
        pool = select.candidates([a, b], NOW, set())
        self.assertEqual([p["source"] for p in pool], ["Alpha"])
        self.assertEqual(pool[0]["also_in"][0]["source"], "Beta")

    def test_previously_featured_never_returns(self):
        a = item(1, "Alpha", "ai")
        self.assertEqual(select.candidates([a], NOW, {"alpha.example/1"}), [])

    def test_high_volume_feed_cannot_dominate(self):
        flood = [item(300 + k, "Wire", "economy", hours=k + 1, role="explainer", tier=1) for k in range(60)]
        chosen = select.compose(select.candidates(flood + day(), NOW, set()))
        self.assertLessEqual(sum(c["source"] == "Wire" for c in chosen), 1)
        pool = select.candidates(flood, NOW, set())
        self.assertLessEqual(len(pool), select.PER_SOURCE_POOL)

    def test_edition_is_finite_and_balanced(self):
        chosen = select.compose(select.candidates(day(), NOW, set()))
        self.assertTrue(select.MIN_ITEMS <= len(chosen) <= select.MAX_ITEMS)
        self.assertEqual({c["bucket"] for c in chosen}, set(select.BUCKETS))
        self.assertTrue(all(sum(c["bucket"] == b for c in chosen) <= select.MAX_PER_BUCKET + 1 for b in select.BUCKETS))
        self.assertEqual(len({c["source"] for c in chosen}), len(chosen))

    def test_announcements_and_roundups_rank_below_explainers(self):
        ex = item(1, "A", "ai", title="Why retrieval quality decides agent reliability")
        an = item(2, "B", "ai", title="Acme launches v2.1 of its agent platform, now available")
        ro = item(3, "C", "ai", title="Assorted links")
        order = [p["id"] for p in select.candidates([an, ro, ex], NOW, set())]
        self.assertEqual(order[0], "id1")
        self.assertEqual(order[-1], "id3")

    def test_hard_filters(self):
        bad = [item(1, "A", "ai", title="Live: markets as it happened"), item(2, "B", "ai", title="Podcast: episode 12 on agents"),
               item(3, "C", "ai", link="https://c.example/video/agents")]
        self.assertEqual(select.candidates(bad, NOW, set()), [])

    def test_old_items_fall_out_of_window(self):
        self.assertEqual(select.candidates([item(1, "A", "ai", hours=24 * 9)], NOW, set()), [])


class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.src = self.tmp / "sources.yaml"
        import yaml
        self.src.write_text(yaml.safe_dump(SOURCES))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_failure_keeps_last_edition_and_reports_it(self):
        r, _ = update.run(self.data, self.src, now=NOW - dt.timedelta(days=1), fetch=fake_fetch(day(at=NOW - dt.timedelta(days=1))))
        self.assertEqual(r, "ok")
        before = (self.data / "editions" / "2026-10-20.json").read_text()
        r, why = update.run(self.data, self.src, now=NOW, fetch=fake_fetch([], ok=False))
        self.assertEqual(r, "failed")
        self.assertIn("sources responded", why)
        self.assertFalse((self.data / "editions" / "2026-10-21.json").exists())
        self.assertEqual((self.data / "editions" / "2026-10-20.json").read_text(), before)
        st = json.loads((self.data / "status.json").read_text())
        self.assertEqual(st["last_success"]["edition_date"], "2026-10-20")
        self.assertEqual(st["last_attempt"]["result"], "failed")
        out = self.tmp / "site"
        build.build(self.data, out, self.src)
        html = (out / "index.html").read_text()
        self.assertIn("The latest update failed", html)
        self.assertIn("20 October", html)
        self.assertIn("URLError: timed out", (out / "sources" / "index.html").read_text())

    def test_too_few_articles_is_a_failure_not_a_thin_edition(self):
        r, why = update.run(self.data, self.src, now=NOW, fetch=fake_fetch([item(1, "A", "ai"), item(2, "B", "mind")]))
        self.assertEqual(r, "failed")
        self.assertIn("passed selection", why)

    def test_archive_persists_and_editions_do_not_repeat(self):
        yesterday = NOW - dt.timedelta(days=1)
        items1 = day(4, at=yesterday)
        self.assertEqual(update.run(self.data, self.src, now=yesterday, fetch=fake_fetch(items1))[0], "ok")
        self.assertEqual(update.run(self.data, self.src, now=NOW, fetch=fake_fetch(items1 + day(2, start=100)))[0], "ok")
        e1 = json.loads((self.data / "editions" / "2026-10-20.json").read_text())
        e2 = json.loads((self.data / "editions" / "2026-10-21.json").read_text())
        self.assertFalse({i["link"] for i in e1["items"]} & {i["link"] for i in e2["items"]})
        out = self.tmp / "site"
        self.assertEqual(build.build(self.data, out, self.src), 2)
        arch = (out / "archive" / "index.html").read_text()
        self.assertIn('href="../e/2026-10-20/"', arch)
        self.assertIn('href="../e/2026-10-21/"', arch)
        self.assertTrue((out / "e" / "2026-10-20" / "index.html").exists())

    def test_rerun_same_day_does_not_replace_edition(self):
        update.run(self.data, self.src, now=NOW, fetch=fake_fetch(day()))
        first = (self.data / "editions" / "2026-10-21.json").read_text()
        r, why = update.run(self.data, self.src, now=NOW + dt.timedelta(hours=1), fetch=fake_fetch(day(4, start=200)))
        self.assertEqual(r, "skip")
        self.assertEqual((self.data / "editions" / "2026-10-21.json").read_text(), first)

    def test_scheduled_run_waits_for_seven_berlin(self):
        winter_early = dt.datetime(2026, 11, 2, 5, 0, tzinfo=dt.timezone.utc)      # 06:00 CET
        self.assertEqual(update.run(self.data, self.src, now=winter_early, scheduled=True, fetch=fake_fetch(day(at=winter_early)))[0], "skip")
        winter_ok = dt.datetime(2026, 11, 2, 6, 0, tzinfo=dt.timezone.utc)          # 07:00 CET
        self.assertEqual(update.run(self.data, self.src, now=winter_ok, scheduled=True, fetch=fake_fetch(day(at=winter_ok)))[0], "ok")


if __name__ == "__main__":
    unittest.main()
