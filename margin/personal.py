"""Read-only access to the owner's Notion for the private "Your day" section of the emailed edition.

Nothing here is generated or summarised by a model: every line is quoted from the owner's own pages.
Only read endpoints are called (GET blocks, POST data-source query). Errors carry HTTP codes only — never record
content — so nothing personal can reach the (public) Actions log.

Sources, all in the workspace the integration was shared with:
  · Hub "Right now" callout           — next up, deadlines, open items
  · "This week" page                  — today's row of the catch-up table, else of the standard-week table
  · Actions database                  — open actions with a deadline in the next 14 days
  · Courses database                  — current topic and next study action per course
  · Daily Review database             — the latest day before today: summary and carry-forward
  · Activity Log database             — the three newest entries (what happened lately)
"""
import datetime as dt
import json
import urllib.error
import urllib.request

API, VERSION = "https://api.notion.com/v1", "2025-09-03"
HUB_PAGE = "3f045841ab658148a2edefede23c6849"
THIS_WEEK_PAGE = "3f045841ab6581fa9187cf0701540ced"
ACTIONS = "b29dc8ca-53cb-4312-86f5-431a1da0418d"
COURSES = "ef037d8c-6922-4684-acb4-f57b9fb1a5cd"
DAILY_REVIEW = "eac8983e-ed63-44b0-a7dc-151d666e5869"
ACTIVITY_LOG = "a9a1032a-173c-4e84-84c7-d8f5edd1fac9"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class NotionError(RuntimeError):
    pass


def _call(method, path, token, body=None):
    req = urllib.request.Request(f"{API}{path}", method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        hint = {401: "token rejected", 403: "integration lacks access", 404: "page or database not shared with the integration"}.get(e.code, "")
        raise NotionError(f"HTTP {e.code} {hint}".strip()) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise NotionError(f"network: {type(e).__name__}") from None


def _children(block_id, token):
    out, cursor = [], None
    while True:
        res = _call("GET", f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""), token)
        out += res["results"]
        if not res.get("has_more"):
            return out
        cursor = res["next_cursor"]


def _query(source, token, body):
    return _call("POST", f"/data_sources/{source}/query", token, body)["results"]


def _rt(parts):
    return "".join(x.get("plain_text", "") for x in parts or []).strip()


def prop(page, name):
    """Flatten one property to text (relations resolve to a list of page ids)."""
    p = (page.get("properties") or {}).get(name)
    if not p:
        return ""
    t = p["type"]
    v = p.get(t)
    if t in ("title", "rich_text"):
        return _rt(v)
    if t in ("select", "status"):
        return (v or {}).get("name", "")
    if t == "date":
        return (v or {}).get("start", "") or ""
    if t == "number":
        return "" if v is None else str(v)
    if t == "multi_select":
        return ", ".join(x["name"] for x in v)
    if t == "relation":
        return [x["id"] for x in v]
    return ""


def _block_text(b):
    t = b["type"]
    return _rt((b.get(t) or {}).get("rich_text"))


def _hub_right_now(token):
    """The first callout of the hub page, with its child lines, exactly as written."""
    for b in _children(HUB_PAGE, token):
        if b["type"] == "callout":
            lines = [_block_text(b)]
            if b.get("has_children"):
                lines += [_block_text(c) for c in _children(b["id"], token)]
            return [ln for ln in (s.strip() for s in "\n".join(lines).split("\n")) if ln]
    return []


def _tables(page_id, token):
    """Every table on the page as (header_cells, [row_cells])."""
    out = []
    for b in _children(page_id, token):
        if b["type"] == "table":
            rows = [[_rt(c) for c in r["table_row"]["cells"]] for r in _children(b["id"], token) if r["type"] == "table_row"]
            if rows:
                out.append((rows[0], rows[1:]))
    return out


def _today_plan(token, today):
    wd, dm = WEEKDAYS[today.weekday()], f"{today.day} {MONTHS[today.month - 1]}"
    best = None
    for header, rows in _tables(THIS_WEEK_PAGE, token):
        for row in rows:
            first = row[0].strip()
            if first.startswith(wd) and dm in first:                  # dated row (catch-up table) wins
                return [(h, c) for h, c in zip(header[1:], row[1:]) if c.strip() and c.strip() != "—"]
            if first == wd and best is None:                          # undated standard-week row
                best = [(h, c) for h, c in zip(header[1:], row[1:]) if c.strip() and c.strip() != "—"]
    return best or []


def _courses(token):
    rows = _query(COURSES, token, {"page_size": 50})
    return {r["id"]: {"name": prop(r, "Course"), "status": prop(r, "Status"), "topic": prop(r, "Current topic"),
                      "next": prop(r, "Next study action")} for r in rows}


def _deadlines(token, today, names):
    soon = (today + dt.timedelta(days=14)).isoformat()
    rows = _query(ACTIONS, token, {"page_size": 50, "filter": {"and": [
        {"property": "Deadline", "date": {"on_or_before": soon}},
        {"property": "Status", "select": {"does_not_equal": "Done"}},
        {"property": "Status", "select": {"does_not_equal": "Dropped"}}]},
        "sorts": [{"property": "Deadline", "direction": "ascending"}]})
    out = []
    for r in rows:
        due = prop(r, "Deadline")
        if not due:
            continue
        course = ", ".join(names.get(i, {}).get("name", "") for i in prop(r, "Course") or [] if names.get(i))
        out.append({"title": prop(r, "Action"), "due": due, "course": course, "status": prop(r, "Status")})
    return out


def _yesterday(token, today):
    rows = _query(DAILY_REVIEW, token, {"page_size": 1, "filter": {"property": "Date", "date": {"before": today.isoformat()}},
                                        "sorts": [{"property": "Date", "direction": "descending"}]})
    if not rows:
        return None
    r = rows[0]
    return {"day": prop(r, "Day"), "date": prop(r, "Date"), "summary": prop(r, "Summary"), "carry": prop(r, "Carry forward")}


def _recent(token):
    rows = _query(ACTIVITY_LOG, token, {"page_size": 3, "sorts": [{"property": "Occurred", "direction": "descending"}]})
    return [{"title": prop(r, "Title"), "date": prop(r, "Occurred"), "changed": prop(r, "What changed"), "next": prop(r, "Next step")}
            for r in rows]


def read(token, today):
    """The personal section as plain data. Each part is read on its own, so one failure leaves the others intact.
    `errors` maps a part name to a status-code-only message (never content)."""
    out = {"right_now": [], "today": [], "deadlines": [], "courses": [], "yesterday": None, "recent": [], "errors": {}}

    def part(name, fn):
        try:
            return fn()
        except NotionError as ex:
            out["errors"][name] = str(ex)
        except (KeyError, TypeError, ValueError) as ex:          # a property was renamed or has another type
            out["errors"][name] = f"unexpected shape ({type(ex).__name__})"
        return None

    courses = part("courses", lambda: _courses(token)) or {}
    out["courses"] = [c for c in courses.values() if c["next"] and c["status"].lower() not in ("done", "dropped", "mastered")]
    out["right_now"] = part("hub", lambda: _hub_right_now(token)) or []
    out["today"] = part("this-week", lambda: _today_plan(token, today)) or []
    out["deadlines"] = part("actions", lambda: _deadlines(token, today, courses)) or []
    out["yesterday"] = part("daily-review", lambda: _yesterday(token, today))
    out["recent"] = part("activity-log", lambda: _recent(token)) or []
    return out


def days_until(iso, today):
    try:
        return (dt.date.fromisoformat(iso[:10]) - today).days
    except ValueError:
        return None

