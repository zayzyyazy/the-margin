"""Offline tests for V3: world selection and fallback, budget guard, Notion read filters, private-session signing.
Everything here is a mock or a synthetic fixture — the live runs are recorded separately in reports/V3-VERIFICATION.md.
"""
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from margin import llm, notion, world

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 10, 21, 5, 0, tzinfo=dt.timezone.utc)


def news(n, outlet, title, excerpt="", hours=2, link=None, lang="en"):
    return {"id": f"n{n}", "source": outlet, "outlet": outlet, "title": title, "excerpt": excerpt or title + " reported on Tuesday by officials.",
            "link": link or f"https://{outlet.lower().replace(' ', '')}.example/{n}", "lang": lang,
            "published": (NOW - dt.timedelta(hours=hours)).isoformat()}


class WorldSelection(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(world.kind_of(news(1, "G", "x", link="https://g.example/commentisfree/2026/x")), "opinion")
        self.assertEqual(world.kind_of(news(2, "B", "Live: election results")), "live")
        self.assertEqual(world.kind_of(news(3, "B", "Analysis: why the vote matters")), "analysis")
        self.assertEqual(world.kind_of(news(4, "B", "Parliament passes budget")), "reporting")

    def test_hub_name_does_not_chain_unrelated_stories(self):
        items = [news(1, "A", "Trump meets Putin envoy over Ukraine talks"), news(2, "B", "Putin and Trump envoy discuss Ukraine"),
                 news(3, "C", "Trump tariffs hit Canada lumber exports"), news(4, "D", "Canada lumber exporters fear Trump tariffs")]
        items += [news(100 + k, f"F{k % 5}", f"Story about Place{k} and Person{k}") for k in range(80)]   # a realistic pool size
        groups = [sorted(i["id"] for i in c["items"]) for c in world.clusters(items, NOW)]
        self.assertIn(["n1", "n2"], groups)
        self.assertIn(["n3", "n4"], groups)

    def test_syndicated_copy_counts_once(self):
        wire = "Officials in Abuja confirmed the country's first Ebola case on Tuesday, the health ministry said in a statement today."
        items = [news(1, "A", "Nigeria confirms Ebola case in Abuja", wire), news(2, "B", "Nigeria Ebola case confirmed in Abuja", wire)]
        items += [news(100 + k, f"F{k % 5}", f"Story about Place{k} and Person{k}") for k in range(80)]
        self.assertEqual([c for c in world.clusters(items, NOW) if "ebola" in c["names"]], [])          # two copies of one wire report are one outlet: below MIN_OUTLETS

    def test_region_spread_and_novelty_adjust_coverage(self):
        devs = [{"weight": 4.0, "names": ["france", "macron"], "newest": "b"}, {"weight": 3.9, "names": ["germany", "merz"], "newest": "b"},
                {"weight": 3.0, "names": ["egypt", "cairo"], "newest": "b"}, {"weight": 3.5, "names": ["china", "beijing", "xi"], "newest": "b"}]
        picked = world.choose(devs, previous=[{"china", "beijing", "xi"}])
        regions = [world.region(d) for d in picked[:3]]
        self.assertEqual(regions[0], "europe")
        self.assertIn("mena", regions)                              # Egypt beats a second European story…
        self.assertNotEqual(regions[1], "europe")
        china = next(d for d in picked if "china" in d["names"])
        self.assertIn("covered in a recent edition", china["selection"]["adjustments"])

    def test_explanation_with_any_removed_sentence_becomes_a_brief(self):
        texts = {"S1": {"outlet": "A", "title": "t", "link": "l", "published": NOW.isoformat(), "kind": "reporting", "lang": "en",
                        "text": "The parliament approved the budget on Tuesday after a long debate. The vote was 300 to 200. Critics said it cuts schools."}}
        good = {"text": "Parliament approved the budget.", "kind": "fact", "evidence": [{"source": "S1", "quote": "The parliament approved the budget on Tuesday"}]}
        bad = {"text": "The vote was 310 to 200.", "kind": "fact", "evidence": [{"source": "S1", "quote": "The vote was 300 to 200"}]}
        draft = {"headline": "h", "terms": [], "parts": {"happened": [good, bad], "background": [good], "matters": [good], "uncertain": [good]}}
        verdict = {"verdict": "supported", "numbers_ok": True, "causality_ok": True, "scope_ok": True, "attribution_ok": True,
                   "context_ok": True, "on_topic": True, "note": ""}

        def fake(model, system, user, schema, mx, ledger, label):
            if label == "world:draft":
                return json.loads(json.dumps(draft))
            if label == "world:judge":
                return {"verdicts": [dict(verdict, id=i) for i in ("happened.0", "background.0", "matters.0", "uncertain.0")]}
            return {"coherent": True, "problems": []}
        with mock.patch.object(world.llm, "call", side_effect=fake):
            ex = world.explain({"names": ["x"], "outlets": ["A"]}, texts, {"writer": "w", "judge": "j"}, None)
        self.assertFalse(ex["complete"])                             # 310 is not in the evidence → removed → whole piece becomes a brief
        self.assertTrue(any("number" in " ".join(r["why"]) for r in ex["report"]["removed"]))


class Budget(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "ledger.jsonl"

    def test_paid_model_refused_without_approval(self):
        led = llm.Ledger(self.tmp, 0.25, 5.40, paid_approved=False)
        with self.assertRaises(llm.BudgetExceeded):
            led.reserve("claude-sonnet-5-5", 5000, 2000)

    def test_free_tier_allowed_and_costs_nothing(self):
        led = llm.Ledger(self.tmp, 0.25, 5.40, paid_approved=False)
        self.assertTrue(led.reserve("gemini-3.1-flash-lite", 5000, 2000))
        led.settle("gemini-3.1-flash-lite", "t", {"input": 5000, "output": 2000}, True, 1.0, "ok")
        self.assertEqual(led.rows[-1]["usd"], 0.0)
        self.assertGreater(led.rows[-1]["paid_equivalent"], 0)

    def test_caps_refuse_the_call_that_would_cross_them(self):
        led = llm.Ledger(self.tmp, 0.25, 5.40, paid_approved=True)
        led.reserve("claude-haiku-5-5", 10000, 2000)                 # ≈ $0.002 worst case: fine
        led.rows.append({"at": dt.datetime.now(dt.timezone.utc).isoformat(), "usd": 0.24})
        with self.assertRaises(llm.BudgetExceeded):
            led.reserve("claude-sonnet-5-5", 4000, 1500)              # $0.023 worst case would cross $0.25 today

    def test_unknown_model_refused(self):
        with self.assertRaises(llm.BudgetExceeded):
            llm.Ledger(self.tmp, 1, 1, True).reserve("some-model", 1, 1)


class NotionReads(unittest.TestCase):
    def test_only_active_projects_and_learning_notes_are_queried(self):
        bodies = []

        def fake_urlopen(req, timeout=0):
            bodies.append((req.full_url, json.loads(req.data)))
            return io.BytesIO(json.dumps({"results": []}).encode())
        with mock.patch.object(notion.urllib.request, "urlopen", side_effect=fake_urlopen):
            out = notion.personal(dt.date(2026, 10, 21), token="t")
        urls = [u for u, _ in bodies]
        self.assertTrue(all(u.endswith("/query") for u in urls))
        self.assertTrue(all(notion.PROJECTS_DB in u or notion.LEARNING_DB in u for u in urls))   # Ideas database never read
        proj = [b for u, b in bodies if notion.PROJECTS_DB in u][0]
        self.assertEqual(proj["filter"], {"property": "Status", "select": {"equals": "Active"}})
        self.assertNotIn("Parked", json.dumps(bodies))
        self.assertEqual(out["left_off"], [])

    def test_errors_carry_status_codes_not_content(self):
        err = notion.urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b'{"message":"secret page title"}'))
        with mock.patch.object(notion.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(notion.NotionError) as cm:
                notion.personal(dt.date(2026, 10, 21), token="t")
        self.assertNotIn("secret", str(cm.exception))
        self.assertIn("404", str(cm.exception))


class PrivateSession(unittest.TestCase):
    def setUp(self):
        os.environ.update(MARGIN_PASSWORD="correct horse", MARGIN_SESSION_KEY="k" * 40, NOTION_TOKEN="t")
        sys.path.insert(0, str(ROOT / "private" / "api"))
        sys.path.insert(0, str(ROOT))
        import index
        self.ix = index

    def tearDown(self):
        for k in ("MARGIN_PASSWORD", "MARGIN_SESSION_KEY", "NOTION_TOKEN"):
            os.environ.pop(k, None)

    def test_session_signing(self):
        s = self.ix.new_session()
        self.assertTrue(self.ix.valid_session(s))
        exp, nonce, sig = s.split(".")
        self.assertFalse(self.ix.valid_session(f"{int(exp) + 999}.{nonce}.{sig}"))          # tampered expiry
        self.assertFalse(self.ix.valid_session("1.x.y"))
        os.environ["MARGIN_SESSION_KEY"] = "z" * 40
        self.assertFalse(self.ix.valid_session(s))                                          # rotated key signs everyone out

    def test_password_and_rate_limit(self):
        self.assertTrue(self.ix.password_ok("correct horse"))
        self.assertFalse(self.ix.password_ok("correct horse "))
        self.ix._fails.clear()
        for _ in range(self.ix.FAIL_PER_IP):
            self.ix._fails.setdefault("1.2.3.4", []).append(__import__("time").time())
        self.assertTrue(self.ix.limited("1.2.3.4"))
        self.assertFalse(self.ix.limited("5.6.7.8"))

    def test_fails_closed_without_secrets(self):
        os.environ.pop("NOTION_TOKEN")
        self.assertFalse(self.ix.configured())


if __name__ == "__main__":
    unittest.main()
