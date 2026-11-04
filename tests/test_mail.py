import datetime as dt
import json
import unittest
from pathlib import Path
from unittest import mock

from margin import mail, personal

ROOT = Path(__file__).resolve().parents[1]
ED = json.loads(sorted((ROOT / "data" / "editions").glob("*.json"))[-1].read_text())
PERS = {"right_now": ["Next up: X"], "today": [("Block 1", "Step 0")], "deadlines": [{"title": "Blatt 2", "due": "2026-11-09", "course": "MCI", "status": "To do"}],
        "courses": [], "yesterday": None, "recent": []}


class MailTests(unittest.TestCase):
    def test_render_with_and_without_personal(self):
        today = dt.date(2026, 11, 4)
        _, doc, text = mail.render(ED, PERS, None, "https://example.test/", today)
        self.assertIn("Your day", doc)
        self.assertIn("Blatt 2", text)
        self.assertIn("in 5 days", doc)
        _, doc2, _ = mail.render(ED, None, None, "", today)
        self.assertNotIn("Your day", doc2)

    def test_error_shows_code_only(self):
        _, doc, _ = mail.render(ED, None, "HTTP 401 token rejected", "", dt.date(2026, 11, 4))
        self.assertIn("HTTP 401", doc)

    def test_html_is_escaped(self):
        bad = dict(PERS, right_now=["<script>x</script>"])
        _, doc, _ = mail.render(ED, bad, None, "", dt.date(2026, 11, 4))
        self.assertNotIn("<script>x", doc)

    def test_not_configured_skips_send(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch("margin.mail.send") as s:
            self.assertEqual(mail.main([]), 0)
            s.assert_not_called()

    def test_today_plan_prefers_dated_row(self):
        tables = [(["Day", "Job", "Study"], [["Thu", "—", "GPT"]]), (["Day", "B1"], [["Thu 5 Nov (to 17:00)", "Step 0"]])]
        with mock.patch.object(personal, "_tables", return_value=tables):
            self.assertEqual(personal._today_plan("t", dt.date(2026, 11, 5)), [("B1", "Step 0")])
            self.assertEqual(personal._today_plan("t", dt.date(2026, 11, 12)), [("Study", "GPT")])

    def test_notion_error_has_no_content(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 403, "secret body", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(personal.NotionError) as c:
                personal._call("GET", "/x", "tok")
        self.assertNotIn("secret", str(c.exception))


if __name__ == "__main__":
    unittest.main()
