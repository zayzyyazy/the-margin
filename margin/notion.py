"""Read-only Notion access for the private personal pages. Runs only on the private server (private/api/index.py).

Three sections, each quoting the owner's own records — nothing is generated, summarised by a model, or inferred:
  · Where you left off   — Projects with Status = Active (never Parked, Completed or Archived), most recently edited first:
                           their "Current state", "Next action" and "Blockers" exactly as written
  · Learning thread      — the newest Learning Notes entry of type Learning: "What I learned", with its "Next gap" as the reminder
  · Research rediscovered — one Learning Notes entry of type Research dated at least REDISCOVER_AFTER days ago, rotating daily
Ideas are never read, so a parked idea cannot be promoted by this page. Only GET-equivalent query endpoints are called.
Errors carry HTTP status codes only — never record content — so nothing personal reaches logs.
"""
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request

API, VERSION = "https://api.notion.com/v1", "2022-06-28"
PROJECTS_DB = "449aa5941bca44e589a2593e9defd685"      # workspace › Projects
LEARNING_DB = "55f57027f5de4f5bbbe37944bb7a137b"      # workspace › Skills & Learning › Learning Notes
MAX_PROJECTS, REDISCOVER_AFTER, MAX_WORDS = 3, 10, 70


class NotionError(RuntimeError):
    pass


def _query(db, body, token):
    req = urllib.request.Request(f"{API}/databases/{db}/query", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)["results"]
    except urllib.error.HTTPError as e:
        hint = {401: "token rejected", 403: "integration lacks access", 404: "database not shared with the integration"}.get(e.code, "")
        raise NotionError(f"HTTP {e.code} {hint}".strip()) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise NotionError(f"network: {type(e).__name__}") from None


def _text(prop):
    if not prop:
        return ""
    t = prop.get("type")
    if t in ("rich_text", "title"):
        return "".join(x.get("plain_text", "") for x in prop[t]).strip()
    if t == "select":
        return (prop["select"] or {}).get("name", "")
    if t == "date":
        return (prop["date"] or {}).get("start", "") or ""
    return ""


def clip(s, words=MAX_WORDS):
    """Shorten on a sentence boundary; '…' marks that the full record is longer (link to it is always shown)."""
    w = s.split()
    if len(w) <= words:
        return s, False
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", s):
        if out and len(" ".join(out + [sent]).split()) > words:
            break
        out.append(sent)
    text = " ".join(out)
    if len(text.split()) > words * 1.3:
        text = " ".join(w[:words])
    return text + " …", True


def _rec(page, fields):
    p = page["properties"]
    out = {"url": page["url"], "edited": page["last_edited_time"]}
    for key, name in fields.items():
        out[key] = clip(_text(p.get(name)))[0]
    return out


def personal(today=None, token=None):
    token = token or os.environ.get("NOTION_TOKEN")
    if not token:
        raise NotionError("NOTION_TOKEN is not set on the server")
    today = today or dt.date.today()
    projects = _query(PROJECTS_DB, {"filter": {"property": "Status", "select": {"equals": "Active"}},
                                    "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}], "page_size": MAX_PROJECTS}, token)
    learning = _query(LEARNING_DB, {"filter": {"property": "Note type", "select": {"equals": "Learning"}},
                                    "sorts": [{"property": "Date", "direction": "descending"}], "page_size": 1}, token)
    cutoff = (today - dt.timedelta(days=REDISCOVER_AFTER)).isoformat()
    research = _query(LEARNING_DB, {"filter": {"and": [{"property": "Note type", "select": {"equals": "Research"}},
                                                       {"property": "Date", "date": {"on_or_before": cutoff}}]},
                                    "sorts": [{"property": "Date", "direction": "ascending"}], "page_size": 50}, token)
    pick = research[today.toordinal() % len(research)] if research else None
    return {
        "read_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "left_off": [_rec(x, {"name": "Name", "state": "Current state", "next": "Next action", "blockers": "Blockers",
                              "reviewed": "Last reviewed"}) for x in projects],
        "learning": [_rec(x, {"name": "Note", "learned": "What I learned", "gap": "Next gap", "applied": "Where applied", "date": "Date"})
                     for x in learning],
        "rediscovered": [_rec(pick, {"name": "Note", "learned": "What I learned", "evidence": "Result / evidence", "gap": "Next gap",
                                     "date": "Date"})] if pick else [],
        "counts": {"active_projects": len(projects), "research_notes_eligible": len(research)},
    }
